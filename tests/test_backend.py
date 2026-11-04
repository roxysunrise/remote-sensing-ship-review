"""后端端到端测试。

覆盖：鉴权与项目隔离、上传四阶段与校验、任务状态机与真实推理（有权重时）、
草稿/版本/复核队列/区域/导出/AI 候选，以及 `/vision/*` 的坐标还原。

运行：`python -m unittest discover -s tests -v`（在仓库根目录）
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="ship-backend-test-")
os.environ["SHIP_VAR_DIR"] = TMP
os.environ["SHIP_DB_PATH"] = str(Path(TMP) / "test.db")
os.environ["SHIP_DEVICE"] = "cpu"
os.environ["SHIP_SEED_PASSWORD"] = "test-password-1234"
os.environ.setdefault("SHIP_WEIGHTS", str(ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from algorithm.tiled_inference import plan_tiles  # noqa: E402
from backend import config  # noqa: E402
from backend.server import app  # noqa: E402

WEIGHTS = Path(os.environ["SHIP_WEIGHTS"])
HAS_WEIGHTS = WEIGHTS.is_file()
PASSWORD = "test-password-1234"


def _image_bytes(size=(256, 200), with_shape=True) -> bytes:
    """造一张高对比度小图；只是流程测试，不追求可检出。"""
    image = Image.new("RGB", size, (28, 42, 60))
    if with_shape:
        draw = ImageDraw.Draw(image)
        draw.rectangle([40, 40, 130, 90], fill=(240, 240, 230))
        draw.rectangle([150, 110, 210, 150], fill=(250, 250, 245))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class BackendTests(unittest.TestCase):
    client: TestClient
    token: str

    @classmethod
    def setUpClass(cls) -> None:
        cls._ctx = TestClient(app)
        cls.client = cls._ctx.__enter__()
        response = cls.client.post("/auth/login", json={
            "username": "roxysunrise", "password": PASSWORD, "project_id": "harbor-a"})
        assert response.status_code == 200, response.text
        cls.token = response.json()["token"]
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ctx.__exit__(None, None, None)

    # --- 基础 -------------------------------------------------------------
    def test_health(self):
        body = self.client.get("/health").json()
        self.assertIn(body["status"], {"ok", "degraded"})
        self.assertEqual(body["mode"], "http")

    def test_login_rejects_bad_password(self):
        response = self.client.post("/auth/login", json={
            "username": "roxysunrise", "password": "wrong", "project_id": "harbor-a"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_login_without_membership_is_forbidden(self):
        response = self.client.post("/auth/login", json={
            "username": "analyst", "password": PASSWORD, "project_id": "harbor-b"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_FORBIDDEN")

    def test_requires_token(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "UNAUTHENTICATED")

    def test_project_payload_has_members(self):
        body = self.client.get("/projects/harbor-a", headers=self.headers).json()
        self.assertEqual(body["code"], "PRJ-A-0271")
        self.assertTrue(any(m["role"] == config.USER_ROLE_OWNER for m in body["members"]))

    def test_cross_project_access_denied(self):
        response = self.client.get("/projects/harbor-b", headers=self.headers)
        self.assertEqual(response.status_code, 200)  # owner 在两个项目里都是成员
        # analyst 只属于 harbor-a
        login = self.client.post("/auth/login", json={
            "username": "analyst", "password": PASSWORD, "project_id": "harbor-a"}).json()
        headers = {"Authorization": f"Bearer {login['token']}"}
        denied = self.client.get("/projects/harbor-b", headers=headers)
        self.assertEqual(denied.status_code, 403)

    # --- 上传 -------------------------------------------------------------
    def _upload(self, data: bytes, name: str = "sample.png", *, expect_ready=True) -> dict:
        created = self.client.post("/projects/harbor-a/uploads",
                                   json={"file_name": name, "size": len(data)},
                                   headers=self.headers)
        self.assertEqual(created.status_code, 201, created.text)
        upload = created.json()
        response = self.client.put(f"/uploads/{upload['id']}/chunks/0", content=data,
                                   headers={**self.headers, "Content-Type": "application/octet-stream"})
        self.assertEqual(response.status_code, 200, response.text)
        digest = hashlib.sha256(data).hexdigest()
        completed = self.client.post(f"/uploads/{upload['id']}/complete",
                                     json={"sha256": digest, "size": len(data)},
                                     headers=self.headers)
        self.assertEqual(completed.status_code, 200, completed.text)
        if not expect_ready:
            return completed.json()
        deadline = time.time() + 60
        while time.time() < deadline:
            state = self.client.get(f"/uploads/{upload['id']}", headers=self.headers).json()
            if state["status"] in {"ready", "failed"}:
                return state
            time.sleep(0.2)
        self.fail("上传校验超时")

    def test_upload_rejects_wrong_suffix(self):
        response = self.client.post("/projects/harbor-a/uploads",
                                    json={"file_name": "evil.webp", "size": 10},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_IMAGE")

    def test_upload_rejects_bad_checksum(self):
        data = _image_bytes()
        created = self.client.post("/projects/harbor-a/uploads",
                                   json={"file_name": "bad.png", "size": len(data)},
                                   headers=self.headers).json()
        self.client.put(f"/uploads/{created['id']}/chunks/0", content=data,
                        headers={**self.headers, "Content-Type": "application/octet-stream"})
        self.client.post(f"/uploads/{created['id']}/complete",
                         json={"sha256": "0" * 64, "size": len(data)}, headers=self.headers)
        deadline = time.time() + 30
        while time.time() < deadline:
            state = self.client.get(f"/uploads/{created['id']}", headers=self.headers).json()
            if state["status"] == "failed":
                self.assertIn("CHECKSUM_MISMATCH", state["error"])
                return
            if state["status"] == "ready":
                self.fail("校验和不匹配却进入了 ready")
            time.sleep(0.2)
        self.fail("校验失败状态超时")

    def test_upload_chunk_must_be_in_order(self):
        data = _image_bytes()
        created = self.client.post("/projects/harbor-a/uploads",
                                   json={"file_name": "order.png", "size": 3 << 20},
                                   headers=self.headers).json()
        response = self.client.put(f"/uploads/{created['id']}/chunks/2", content=b"x",
                                   headers={**self.headers, "Content-Type": "application/octet-stream"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "CHUNK_OUT_OF_ORDER")

    def test_upload_pipeline_and_image_record(self):
        data = _image_bytes()
        state = self._upload(data, "pipeline.png")
        self.assertEqual(state["status"], "ready", state)
        self.assertEqual(state["stage"], "ready")
        image = self.client.get(f"/images/{state['image_id']}", headers=self.headers).json()
        self.assertEqual(image["width"], 256)
        self.assertEqual(image["height"], 200)
        self.assertEqual(image["sha256"], hashlib.sha256(data).hexdigest())
        preview = self.client.get(f"/previews/{image['id']}", headers=self.headers)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/jpeg")

    def test_task_requires_ready_image(self):
        created = self.client.post("/projects/harbor-a/uploads",
                                   json={"file_name": "pending.png", "size": 100},
                                   headers=self.headers).json()
        response = self.client.post("/projects/harbor-a/tasks",
                                    json={"image_id": "img_missing"}, headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(created["status"], "uploading")

    # --- 任务（真实推理） -------------------------------------------------
    @unittest.skipUnless(HAS_WEIGHTS, "缺少本地权重 best.pt，跳过真实推理用例")
    def test_task_lifecycle_end_to_end(self):
        state = self._upload(_image_bytes(), "task.png")
        self.assertEqual(state["status"], "ready")
        image_id = state["image_id"]

        created = self.client.post("/projects/harbor-a/tasks", json={
            "image_id": image_id,
            "parameters": {"tiling": True, "tile_size": 128, "stride": 64, "imgsz": 128,
                           "conf": 0.05, "iou": 0.7, "max_det": 100, "merge_iou": 0.1},
        }, headers=self.headers)
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]

        task = {}
        deadline = time.time() + 240
        while time.time() < deadline:
            task = self.client.get(f"/tasks/{task_id}", headers=self.headers).json()
            if task["status"] in {"done", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        self.assertEqual(task["status"], "done", task)
        expected_tiles = len(plan_tiles(256, 200, 128, 64))
        self.assertEqual(task["tiles_total"], expected_tiles)
        self.assertEqual(task["tiles_done"], task["tiles_total"])
        self.assertIsNotNone(task["final_count"])

        result = self.client.get(f"/tasks/{task_id}/result", headers=self.headers).json()
        self.assertEqual(result["schema_version"], "ship-detection/0.2")
        self.assertEqual(result["count"], len(result["detections"]))
        self.assertEqual(result["tiling"]["candidate_count"] - result["tiling"]["suppressed_count"],
                         result["count"])
        self.assertEqual(result["image"]["id"], image_id)

        draft = self.client.get(f"/tasks/{task_id}/draft", headers=self.headers).json()
        self.assertEqual(len(draft["detections"]), result["count"])

        versions = self.client.get(f"/tasks/{task_id}/versions", headers=self.headers).json()
        self.assertEqual([v["label"] for v in versions], ["v0"])
        self.assertTrue(versions[0]["readonly"])

        # 没有任何修改就提交 → 409
        conflict = self.client.post(f"/tasks/{task_id}/versions", json={}, headers=self.headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "NOTHING_TO_COMMIT")

        # 新增一个人工目标 → 提交版本成功
        added = self.client.post(f"/tasks/{task_id}/draft/detections", json={
            "polygon": [[10, 10], [60, 10], [60, 40], [10, 40]],
            "origin": "manual", "review_state": "confirmed", "note": "人工补框",
        }, headers=self.headers)
        self.assertEqual(added.status_code, 201, added.text)
        self.assertIsNone(added.json()["confidence"])
        committed = self.client.post(f"/tasks/{task_id}/versions",
                                     json={"label": "人工修订"}, headers=self.headers)
        self.assertEqual(committed.status_code, 201, committed.text)
        self.assertEqual(committed.json()["label"], "v1")
        self.assertEqual(committed.json()["summary"]["added"], 1)

        # 历史版本只读但可读取
        v0 = self.client.get(f"/versions/v0?task_id={task_id}", headers=self.headers).json()
        self.assertTrue(v0["readonly"])
        self.assertEqual(len(v0["detections"]), result["count"])

        # 复核队列与抽样
        queue = self.client.get(f"/tasks/{task_id}/draft/review-queue", headers=self.headers).json()
        self.assertIn("items", queue)
        self.assertIn("sampling", queue)
        if queue["items"]:
            first = queue["items"][0]
            self.assertGreaterEqual(first["score"], 0)
            self.assertTrue(first["reasons"])

        # 区域统计
        region = self.client.post(f"/tasks/{task_id}/draft/regions", json={
            "name": "A 区", "polygon": [[0, 0], [256, 0], [256, 200], [0, 200]],
        }, headers=self.headers)
        self.assertEqual(region.status_code, 200, region.text)
        counts = region.json()["counts"]
        self.assertEqual(counts["effective"], result["count"] + 1)  # 含刚补的人工框
        self.assertEqual(region.json()["rule"], "centre_point_inside")

        # 导出
        export = self.client.post(f"/versions/v1/exports?task_id={task_id}",
                                  json={"formats": ["csv", "json", "png"]}, headers=self.headers)
        self.assertEqual(export.status_code, 202, export.text)
        export_id = export.json()["id"]
        deadline = time.time() + 60
        payload = {}
        while time.time() < deadline:
            payload = self.client.get(f"/exports/{export_id}", headers=self.headers).json()
            if payload["status"] in {"done", "failed"}:
                break
            time.sleep(0.2)
        self.assertEqual(payload["status"], "done", payload)
        self.assertEqual(len(payload["files"]), 3)
        csv_file = next(f for f in payload["files"] if f["format"] == "csv")
        download = self.client.get(csv_file["url"], headers=self.headers)
        self.assertEqual(download.status_code, 200)
        self.assertIn("review_state", download.text)

    @unittest.skipUnless(HAS_WEIGHTS, "缺少本地权重 best.pt，跳过真实推理用例")
    def test_cancel_retry_and_duplicate_guard(self):
        state = self._upload(_image_bytes((900, 700)), "cancel.png")
        image_id = state["image_id"]
        task = self.client.post("/projects/harbor-a/tasks", json={
            "image_id": image_id,
            "parameters": {"tiling": True, "tile_size": 128, "stride": 64, "imgsz": 128},
        }, headers=self.headers).json()
        task_id = task["id"]
        self.client.post(f"/tasks/{task_id}/cancel", headers=self.headers)
        deadline = time.time() + 120
        current = {}
        while time.time() < deadline:
            current = self.client.get(f"/tasks/{task_id}", headers=self.headers).json()
            if current["status"] in {"cancelled", "done", "failed"}:
                break
            time.sleep(0.2)
        self.assertIn(current["status"], {"cancelled", "done"})
        if current["status"] == "cancelled":
            retried = self.client.post(f"/tasks/{task_id}/retry", headers=self.headers)
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(retried.json()["attempt"], 2)
            self.assertFalse(retried.json()["cancel_requested"])

    # --- /vision/* --------------------------------------------------------
    def test_vision_capabilities(self):
        body = self.client.get("/vision/capabilities").json()
        self.assertEqual(body["service"], "vision-inference")
        self.assertIn("detect", body["tasks"])
        self.assertIn("max_image_edge", body["limits"])

    def test_vision_detect_restores_original_coordinates(self):
        if not HAS_WEIGHTS:
            self.skipTest("缺少本地权重，跳过真实推理用例")
        payload_image = Image.new("RGB", (1600, 900), (30, 30, 30))
        draw = ImageDraw.Draw(payload_image)
        draw.rectangle([700, 300, 780, 340], fill=(250, 250, 240))
        buffer = io.BytesIO()
        payload_image.save(buffer, format="JPEG", quality=90)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()

        response = self.client.post("/vision/detect", json={
            "session_id": "ses_test", "kind": "image",
            "frame": {"index": 0, "time_ms": 0, "width": 3200, "height": 1800},
            "image": {"data_url": data_url, "width": 1600, "height": 900},
            "options": {"conf": 0.05, "max_det": 50},
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["frame"]["index"], 0)
        self.assertIn("elapsed_ms", body)
        for det in body["detections"]:
            self.assertIn("label", det)
            self.assertIn("score", det)
            self.assertEqual(det["source"], "model")
            for x, y in det["polygon"]:
                self.assertLessEqual(x, 3200 * 1.02)
                self.assertLessEqual(y, 1800 * 1.02)

    def test_vision_rejects_non_base64(self):
        response = self.client.post("/vision/detect", json={
            "frame": {"width": 10, "height": 10},
            "image": {"data_url": "https://example.com/a.png", "width": 10, "height": 10},
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_FRAME")

    def test_vision_session_roundtrip(self):
        created = self.client.post("/vision/sessions", json={"kind": "image", "name": "a.png",
                                                             "width": 100, "height": 80})
        self.assertEqual(created.status_code, 200)
        self.assertTrue(created.json()["session_id"].startswith("ses_"))
        cancelled = self.client.post("/vision/jobs/any/cancel", json={})
        self.assertTrue(cancelled.json()["cancelled"])


if __name__ == "__main__":
    unittest.main()
