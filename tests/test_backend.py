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
# 两个测试模块共享同一套环境变量：谁先导入 backend 谁绑定配置，
# 因此这里一律用 setdefault + 进程级路径，避免 import 顺序导致 DB/口令不一致。
_TMP = os.environ.setdefault("SHIP_VAR_DIR",
                             str(Path(tempfile.gettempdir()) / f"ship-test-{os.getpid()}"))
os.environ.setdefault("SHIP_DB_PATH", str(Path(_TMP) / "test.db"))
os.environ.setdefault("SHIP_DEVICE", "cpu")
os.environ.setdefault("SHIP_SEED_PASSWORD", "test-password-1234")
os.environ.setdefault("SHIP_MAX_ACTIVE_TASKS", "1")
os.environ.setdefault("SHIP_CHUNK_SIZE", "65536")
os.environ.setdefault("SHIP_WEIGHTS", str(ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from algorithm.tiled_inference import plan_tiles  # noqa: E402
from backend import config, db  # noqa: E402
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


def _fake_detection(index: int) -> dict:
    """构造一条 v0 里的模型框，仅用于草稿初始化测试。"""
    return {"id": index, "polygon": [[1, 1], [30, 1], [30, 12], [1, 12]], "class_id": 1,
            "class_name": "ship", "confidence": 0.9, "source_tile_id": 1,
            "review_state": "pending", "origin": "model"}


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

    def test_project_images_list(self):
        """工作台需要项目影像列表；缺这个端点时前端只能用内置演示影像顶替。"""
        data = _image_bytes((120, 90))
        state = self._upload(data, "list.png")
        body = self.client.get("/projects/harbor-a/images", headers=self.headers).json()
        ids = [item["id"] for item in body["items"]]
        self.assertEqual(body["total"], len(ids))
        self.assertIn(state["image_id"], ids)
        item = next(i for i in body["items"] if i["id"] == state["image_id"])
        self.assertEqual(item["file_name"], "list.png")
        self.assertEqual(item["file_url"], f"/files/{state['image_id']}")
        self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())

        login = self.client.post("/auth/login", json={
            "username": "analyst", "password": PASSWORD, "project_id": "harbor-a"}).json()
        denied = self.client.get("/projects/harbor-b/images",
                                 headers={"Authorization": f"Bearer {login['token']}"})
        self.assertEqual(denied.status_code, 403)

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

    def test_project_tasks_list(self):
        """刷新或重新进入后要能找回已有任务：列表带影像摘要，可按影像过滤。"""
        self._wait_idle()
        state = self._upload(_image_bytes((160, 120)), "list-task.png")
        created = self.client.post("/projects/harbor-a/tasks", json={
            "image_id": state["image_id"],
            "parameters": {"tiling": True, "tile_size": 128, "stride": 64, "imgsz": 128},
        }, headers=self.headers)
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]

        body = self.client.get("/projects/harbor-a/tasks?limit=50", headers=self.headers).json()
        ids = [item["id"] for item in body["items"]]
        self.assertEqual(body["total"], len(ids))
        self.assertIn(task_id, ids)
        item = next(i for i in body["items"] if i["id"] == task_id)
        self.assertEqual(item["image"]["file_name"], "list-task.png")
        self.assertEqual(item["image"]["width"], 160)
        self.assertIn("status", item)

        filtered = self.client.get(f"/projects/harbor-a/tasks?image_id={state['image_id']}",
                                   headers=self.headers).json()
        self.assertEqual([i["id"] for i in filtered["items"]], [task_id])

        login = self.client.post("/auth/login", json={
            "username": "analyst", "password": PASSWORD, "project_id": "harbor-a"}).json()
        denied = self.client.get("/projects/harbor-b/tasks",
                                 headers={"Authorization": f"Bearer {login['token']}"})
        self.assertEqual(denied.status_code, 403)

    def _wait_idle(self, timeout: float = 180) -> None:
        """等队列清空，避免上一个用例遗留的任务触发并发上限。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.client.get("/health", headers=self.headers).json().get("active_tasks", 0) == 0:
                return
            time.sleep(0.2)

    # --- 任务（真实推理） -------------------------------------------------
    @unittest.skipUnless(HAS_WEIGHTS, "缺少本地权重 best.pt，跳过真实推理用例")
    def test_task_lifecycle_end_to_end(self):
        self._wait_idle()
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

        # 只改几何、不传状态 → 服务端应自动标记为"已修改"
        moved = self.client.patch(
            f"/tasks/{task_id}/draft/detections/{added.json()['id']}",
            json={"polygon": [[12, 12], [62, 12], [62, 42], [12, 42]]}, headers=self.headers)
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["review_state"], "modified")

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


    # --- 本轮优化：限流、缓存、维护 ---
    def test_chunk_larger_than_chunk_size_rejected(self):
        created = self.client.post("/projects/harbor-a/uploads",
                                   json={"file_name": "big.png", "size": 200},
                                   headers=self.headers).json()
        oversize = b"x" * (config.CHUNK_SIZE + 1)
        response = self.client.put(f"/uploads/{created['id']}/chunks/0", content=oversize,
                                   headers={**self.headers,
                                            "Content-Type": "application/octet-stream"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_ARGUMENT")

    def test_task_quota_returns_429(self):
        self._wait_idle()
        state = self._upload(_image_bytes((640, 480)), "quota.png")
        params = {"tiling": True, "tile_size": 128, "stride": 64, "imgsz": 128}
        first = self.client.post("/projects/harbor-a/tasks",
                                 json={"image_id": state["image_id"], "parameters": params},
                                 headers=self.headers)
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post("/projects/harbor-a/tasks",
                                  json={"image_id": state["image_id"], "parameters": params},
                                  headers=self.headers)
        self.assertEqual(second.status_code, 429, second.text)
        self.assertEqual(second.json()["error"]["code"], "RATE_LIMITED")
        self.client.post(f"/tasks/{first.json()['id']}/cancel", headers=self.headers)

    def test_preview_etag_and_304(self):
        state = self._upload(_image_bytes((64, 48)), "etag.png")
        first = self.client.get(f"/previews/{state['image_id']}", headers=self.headers)
        self.assertEqual(first.status_code, 200)
        etag = first.headers.get("etag")
        self.assertTrue(etag)
        second = self.client.get(f"/previews/{state['image_id']}",
                                 headers={**self.headers, "If-None-Match": etag})
        self.assertEqual(second.status_code, 304)

    def test_request_id_header_and_error_details(self):
        ok = self.client.get("/health")
        self.assertTrue(ok.headers.get("x-request-id", "").startswith("req_"))
        denied = self.client.get("/projects")
        self.assertEqual(denied.status_code, 401)
        self.assertTrue(denied.json()["error"]["details"].get("request_id"))

    def test_maintenance_purges_expired_uploads_and_stale_results(self):
        from backend import maintenance

        expired = self.client.post("/projects/harbor-a/uploads",
                                   json={"file_name": "expired.png", "size": 10},
                                   headers=self.headers).json()
        db.execute("UPDATE uploads SET expires_at=? WHERE id=?",
                   ("2000-01-01T00:00:00+00:00", expired["id"]))
        self.assertGreaterEqual(maintenance.purge_expired_uploads(), 1)
        self.assertEqual(db.one("SELECT status FROM uploads WHERE id=?", (expired["id"],))["status"],
                         "expired")

        # 造一个"重试过"的任务：attempt=2，磁盘上还留着 a1 的旧结果
        task_id = "tsk_prune_probe"
        current = config.RESULT_DIR / f"{task_id}_a2.json"
        stale = config.RESULT_DIR / f"{task_id}_a1.json"
        config.RESULT_DIR.mkdir(parents=True, exist_ok=True)
        current.write_text("{}", encoding="utf-8")
        stale.write_text("{}", encoding="utf-8")
        db.execute(
            "INSERT OR REPLACE INTO tasks(id,project_id,image_id,created_by,status,attempt,"
            "tiles_total,tiles_done,temp_count,parameters,cancel_requested,result_path,created_at)"
            " VALUES(?,?,?,?,'done',2,0,0,0,'{}',0,?,?)",
            (task_id, "harbor-a", "img_probe", "usr_owner", str(current), db.now()))
        self.assertGreaterEqual(maintenance.prune_result_files(), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(current.exists())

    def test_fsck_reports_and_cleans_orphans(self):
        from backend.tools import fsck

        orphan = config.BLOB_DIR / "orphan_test.png"
        orphan.write_bytes(b"not really a png")
        report = fsck.scan()
        self.assertIn("orphan_test.png", report["orphaned"]["blobs"])
        fsck.cleanup(report)
        self.assertFalse(orphan.exists())


    # --- 统计 ---
    def test_project_stats_endpoint(self):
        body = self.client.get("/projects/harbor-a/stats", headers=self.headers)
        self.assertEqual(body.status_code, 200, body.text)
        payload = body.json()
        for key in ("range", "images", "uploads", "tasks", "review", "versions", "ai", "regions"):
            self.assertIn(key, payload)
        self.assertIn("completion_rate", payload["review"])
        self.assertIn("accept_rate", payload["ai"])
        self.assertIn("p50", payload["tasks"]["duration_ms"])

    def test_project_stats_rejects_bad_range(self):
        bad = self.client.get("/projects/harbor-a/stats?from=yesterday", headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["error"]["code"], "INVALID_ARGUMENT")

    def test_project_stats_forbidden_for_non_member(self):
        login = self.client.post("/auth/login", json={
            "username": "analyst", "password": PASSWORD, "project_id": "harbor-a"}).json()
        headers = {"Authorization": f"Bearer {login['token']}"}
        denied = self.client.get("/projects/harbor-b/stats", headers=headers)
        self.assertEqual(denied.status_code, 403)

    @unittest.skipUnless(HAS_WEIGHTS, "缺少本地权重 best.pt，跳过真实推理用例")
    def test_ai_planner_status_endpoint(self):
        response = self.client.get("/ai/planner", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("enabled", "provider", "model", "daily_limit", "used_today",
                    "remaining", "budget_exhausted"):
            self.assertIn(key, body)
        self.assertFalse(body["enabled"], "测试环境默认不启用 VLM")
        self.assertEqual(body["remaining"], body["daily_limit"])

    def test_task_stats_after_real_run(self):
        self._wait_idle()
        state = self._upload(_image_bytes(), "stats.png")
        task = self.client.post("/projects/harbor-a/tasks", json={
            "image_id": state["image_id"],
            "parameters": {"tiling": True, "tile_size": 128, "stride": 64, "imgsz": 128},
        }, headers=self.headers).json()
        deadline = time.time() + 240
        while time.time() < deadline:
            current = self.client.get(f"/tasks/{task['id']}", headers=self.headers).json()
            if current["status"] in {"done", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        self.assertEqual(current["status"], "done", current)
        stats = self.client.get(f"/tasks/{task['id']}/stats", headers=self.headers)
        self.assertEqual(stats.status_code, 200, stats.text)
        payload = stats.json()
        self.assertEqual(payload["task"]["id"], task["id"])
        self.assertGreater(payload["progress"]["tiles_total"], 0)
        self.assertIsNotNone(payload["timing"]["predict_call_ms"])
        self.assertIn("v0", payload["versions"]["labels"])

    def test_tile_plan_manifest_and_tile_fetch(self):
        state = self._upload(_image_bytes((900, 600)), "tiles.png")
        image_id = state["image_id"]

        plan = self.client.post(f"/images/{image_id}/tile-plan",
                                json={"tile_size": 256, "stride": 128}, headers=self.headers)
        self.assertEqual(plan.status_code, 200, plan.text)
        body = plan.json()
        self.assertGreater(body["tile_count"], 1)
        self.assertEqual(body["tiles"][0], {"id": 1, "x": 0, "y": 0, "width": 256, "height": 256})
        self.assertGreater(body["coverage"]["overlap_ratio"], 0)

        manifest = self.client.get(f"/images/{image_id}/tile-manifest", headers=self.headers).json()
        self.assertEqual(manifest["tile_edge"], 256)
        self.assertGreaterEqual(manifest["max_z"], 1)
        self.assertEqual(manifest["levels"][0]["cols"], 4)      # 900 / 256 → 4 列

        tile = self.client.get(f"/tiles/{image_id}/0/0/0", headers=self.headers)
        self.assertEqual(tile.status_code, 200)
        self.assertEqual(tile.headers.get("content-type"), "image/jpeg")
        etag = tile.headers.get("etag")
        self.assertTrue(etag)
        cached = self.client.get(f"/tiles/{image_id}/0/0/0",
                                 headers={**self.headers, "If-None-Match": etag})
        self.assertEqual(cached.status_code, 304)

        out_of_range = self.client.get(f"/tiles/{image_id}/0/999/0", headers=self.headers)
        self.assertEqual(out_of_range.status_code, 404)
        self.assertEqual(out_of_range.json()["error"]["code"], "TILE_NOT_FOUND")

    def test_ai_candidate_box_hint_and_events(self):
        if not HAS_WEIGHTS:
            self.skipTest("缺少本地权重，跳过真实推理用例")
        image = Image.new("RGB", (400, 300), (18, 24, 32))
        draw = ImageDraw.Draw(image)
        draw.rectangle([100, 100, 190, 150], fill=(245, 245, 235))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        state = self._upload(buffer.getvalue(), "ai.png")

        created = self.client.post(f"/images/{state['image_id']}/ai-candidates",
                                   json={"mode": "box", "box": [90, 90, 200, 160]},
                                   headers=self.headers)
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertIsNone(body["confidence"], "候选不得伪造置信度")
        self.assertIn(body["source"], {"detect", "contour"})
        self.assertGreater(body["length"], body["width"], "长边必须大于短边")
        self.assertTrue(0.0 <= body["quality"] <= 1.0)

        row = db.one("SELECT detail FROM events WHERE action='ai.propose' ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row, "ai.propose 事件应被记录")

        # 精修模式：围绕同一位置再提一次（有候选或明确 NO_CANDIDATE 都算契约可用）
        refine = self.client.post(f"/images/{state['image_id']}/ai-candidates",
                                  json={"mode": "refine",
                                        "existing_polygon": [[100, 100], [190, 100],
                                                             [190, 150], [100, 150]]},
                                  headers=self.headers)
        self.assertIn(refine.status_code, (200, 422), refine.text)
        if refine.status_code == 200:
            self.assertIsNone(refine.json()["confidence"])

    def test_ai_candidate_rejects_bad_hint(self):
        state = self._upload(_image_bytes((64, 48)), "ai-bad.png")
        missing = self.client.post(f"/images/{state['image_id']}/ai-candidates",
                                   json={"mode": "box"}, headers=self.headers)
        self.assertIn(missing.status_code, (422,), missing.text)
        tiny = self.client.post(f"/images/{state['image_id']}/ai-candidates",
                                json={"mode": "box", "box": [10, 10, 11, 11]}, headers=self.headers)
        self.assertEqual(tiny.status_code, 422)


    # --- 草稿初始化（2026-11-05 修复：jobs 先建 state 导致草稿恒为空） --------
    def _seed_v0(self, task_id: str, detections: list[dict]) -> None:
        db.execute(
            "INSERT INTO versions(task_id,label,project_id,author,created_at,readonly,summary,counts,"
            "detections) VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, "v0", "harbor-a", "model", db.now(), 1, json.dumps({}), json.dumps({}),
             json.dumps(detections, ensure_ascii=False)),
        )

    @staticmethod
    def _draft_ids(task_id: str) -> list[int]:
        rows = db.query("SELECT detection_id FROM draft_detections WHERE task_id=? ORDER BY detection_id",
                        (task_id,))
        return [row["detection_id"] for row in rows]

    def test_draft_created_from_v0(self):
        from backend import api
        task_id = f"tsk_{uuid.uuid4().hex[:8]}"
        self._seed_v0(task_id, [_fake_detection(1), _fake_detection(2)])
        api._ensure_draft(task_id)
        self.assertEqual(self._draft_ids(task_id), [1, 2])
        state = db.one("SELECT base_version FROM draft_state WHERE task_id=?", (task_id,))
        self.assertEqual(state["base_version"], "v0")

    def test_draft_backfilled_when_state_has_no_rows(self):
        """旧缺陷会留下"有 state、无 detection"的草稿；再次读取必须补齐。"""
        from backend import api
        task_id = f"tsk_{uuid.uuid4().hex[:8]}"
        self._seed_v0(task_id, [_fake_detection(1)])
        db.execute("INSERT INTO draft_state(task_id,base_version,updated_at) VALUES(?,?,?)",
                   (task_id, "v0", db.now()))
        api._ensure_draft(task_id)
        self.assertEqual(self._draft_ids(task_id), [1])

    def test_draft_stays_empty_when_base_version_empty(self):
        """基线版本本身为空（用户 reset 到空版本）时不能凭空造框。"""
        from backend import api
        task_id = f"tsk_{uuid.uuid4().hex[:8]}"
        self._seed_v0(task_id, [])
        db.execute("INSERT INTO draft_state(task_id,base_version,updated_at) VALUES(?,?,?)",
                   (task_id, "v0", db.now()))
        api._ensure_draft(task_id)
        self.assertEqual(self._draft_ids(task_id), [])


if __name__ == "__main__":
    unittest.main()
