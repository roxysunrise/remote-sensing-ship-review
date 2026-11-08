"""任务运行时：队列、状态机、真实进度、取消与重试。

状态：queued → running → merging → done；异常分支 failed / cancelled。
进度来自逐片回调，`temp_count` 是合并前的临时值，`final_count` 只在 done 后出现。
一个 Worker 串行消费队列（单 GPU），重试产生新的 attempt，旧结果不覆盖新结果。
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import traceback
from pathlib import Path

from algorithm.errors import DetectionError
from algorithm.ship_detector import canonical_polygon
from algorithm.tiled_inference import plan_tiles

from . import config, db, detector, errors, storage

log = logging.getLogger(__name__)

TERMINAL = {"done", "failed", "cancelled"}


class TaskRuntime:
    def __init__(self, service: detector.DetectorService | None = None, workers: int = 1):
        self.service = service or detector.service
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._worker_count = max(1, workers)
        self._started = False
        self._lock = threading.Lock()

    # --- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._recover_interrupted()
            self._recover_missing_versions()
            for index in range(self._worker_count):
                thread = threading.Thread(target=self._loop, name=f"task-worker-{index}", daemon=True)
                thread.start()
                self._workers.append(thread)
            self._started = True

    @staticmethod
    def _recover_interrupted() -> None:
        """进程重启后，内存队列里的任务不可能继续执行，必须如实标为失败而不是永久"运行中"。"""
        db.execute("UPDATE tasks SET status='failed', error=?, finished_at=?"
                   " WHERE status IN ('queued','running','merging')",
                   ("SERVER_RESTART: 服务重启导致任务中断，请重试", db.now()))

    def _recover_missing_versions(self) -> None:
        """恢复旧 Worker 已发布结果但来不及创建的基线版本。"""
        rows = db.query(
            "SELECT t.id, t.attempt, t.result_path, t.final_count, t.image_id, t.project_id,"
            " i.sha256, i.width, i.height FROM tasks t JOIN images i"
            " ON i.id=t.image_id AND i.project_id=t.project_id"
            " WHERE t.status='done' AND t.result_path IS NOT NULL AND NOT EXISTS"
            " (SELECT 1 FROM versions v WHERE v.task_id=t.id AND v.label='v0')"
        )
        for row in rows:
            try:
                payload = _read_legacy_result(row)
            except (DetectionError, OSError, ValueError, TypeError, KeyError, RuntimeError, OverflowError):
                log.warning("任务 %s 的历史结果不可验证，跳过 v0 恢复", row["id"])
                continue
            with db.tx() as conn:
                self._ensure_v0(conn, row["id"], payload)

    def submit(self, task_id: str) -> None:
        self.start()
        self._queue.put(task_id)

    def _loop(self) -> None:
        while True:
            task_id = self._queue.get()
            try:
                self._run(task_id)
            except Exception:  # pragma: no cover - 兜底，避免 Worker 线程死掉
                traceback.print_exc()
                db.execute("UPDATE tasks SET status='failed', error=?, finished_at=? WHERE id=?",
                           ("INTERNAL_ERROR", db.now(), task_id))
            finally:
                self._queue.task_done()

    # --- 执行 -------------------------------------------------------------
    def _run(self, task_id: str) -> None:
        row = db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row is None or row["status"] in TERMINAL:
            return
        attempt = row["attempt"]
        image = db.one("SELECT * FROM images WHERE id=?", (row["image_id"],))
        if image is None:
            db.execute("UPDATE tasks SET status='failed', error=?, finished_at=? WHERE id=?",
                       ("IMAGE_NOT_FOUND", db.now(), task_id))
            return
        if self._is_cancelled(task_id):
            self._finish_cancelled(task_id)
            return

        parameters = json.loads(row["parameters"])
        image_path = storage.blob_path(image["stored_name"])

        db.execute("UPDATE tasks SET status='running', started_at=?, tiles_done=0, temp_count=0,"
                   " final_count=NULL, error=NULL WHERE id=?", (db.now(), task_id))

        tiles_total = 0
        if parameters.get("tiling", True):
            try:
                tiles = plan_tiles(image["width"], image["height"],
                                   int(parameters.get("tile_size", 1024)),
                                   int(parameters.get("stride", 512)))
                tiles_total = len(tiles)
            except DetectionError as exc:
                self._finish_failed(task_id, exc)
                return
        db.execute("UPDATE tasks SET tiles_total=? WHERE id=?", (tiles_total, task_id))

        def on_progress(tile_id: int, total: int, candidates: int) -> None:
            status = "merging" if tile_id >= total else "running"
            db.execute(
                "UPDATE tasks SET tiles_done=?, temp_count=?, tiles_total=?, status=? WHERE id=?",
                (tile_id, candidates, total, status, task_id),
            )

        def should_cancel() -> bool:
            return self._is_cancelled(task_id)

        try:
            result = self.service.predict(image_path, parameters,
                                          on_progress=on_progress if tiles_total else None,
                                          should_cancel=should_cancel if tiles_total else None)
        except errors.ApiError as exc:
            if exc.code == "CANCELLED":
                self._finish_cancelled(task_id)
            else:
                self._finish_failed(task_id, exc)
            return
        except DetectionError as exc:
            self._finish_failed(task_id, exc)
            return

        if self._is_cancelled(task_id):
            self._finish_cancelled(task_id)
            return

        target = storage.result_path(task_id, attempt)
        payload = self._attach_image_record(result, image)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 状态与 v0 同时提交，避免重启后留下可读结果却无基线版本的任务。
        with db.tx() as conn:
            updated = conn.execute(
                "UPDATE tasks SET status='done', final_count=?, temp_count=?, result_path=?,"
                " finished_at=? WHERE id=? AND attempt=?",
                (payload["count"], payload["count"], str(target), db.now(), task_id, attempt),
            )
            if not updated.rowcount:
                return
            self._ensure_v0(conn, task_id, payload)
        # 旧 attempt 的结果文件可重算，不必长期占用磁盘
        for stale in config.RESULT_DIR.glob(f"{task_id}_a*.json"):
            if not stale.name.endswith(f"_a{attempt}.json"):
                stale.unlink(missing_ok=True)
        db.log_event("task.done", project_id=row["project_id"], user_id=row["created_by"],
                     target=task_id, detail={"count": payload["count"], "attempt": attempt})

    # --- 结果与版本 -------------------------------------------------------
    @staticmethod
    def _attach_image_record(result: dict, image) -> dict:
        """把算法的 image 块与业务影像记录合并：既满足 v0.2，也满足业务 Image schema。"""
        merged = dict(result.get("image") or {})
        merged.update({
            "id": image["id"],
            "project_id": image["project_id"],
            "status": image["status"],
            "file_name": merged.get("file_name") or image["file_name"],
            "sha256": merged.get("sha256") or image["sha256"],
            "width": image["width"],
            "height": image["height"],
            "mode": image["mode"] or "RGB",
            "file_url": f"/files/{image['id']}",
            "preview_url": f"/previews/{image['id']}",
            "created_at": image["created_at"],
        })
        payload = dict(result)
        payload["image"] = merged
        return payload

    @staticmethod
    def _ensure_v0(conn: sqlite3.Connection, task_id: str, payload: dict) -> None:
        detections = payload["detections"]
        counts = {
            "total": len(detections),
            "pending": sum(1 for d in detections if d.get("review_state", "pending") == "pending"),
            "confirmed": sum(1 for d in detections if d.get("review_state") == "confirmed"),
            "modified": sum(1 for d in detections if d.get("review_state") == "modified"),
            "deleted": sum(1 for d in detections if d.get("review_state") == "deleted"),
            "added": 0,
        }
        conn.execute(
            "INSERT OR IGNORE INTO versions(task_id,label,project_id,author,created_at,readonly,"
            "summary,counts,detections) VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, "v0", payload["image"]["project_id"], "model", db.now(), 1,
             json.dumps({"added": 0, "removed": 0, "changed": 0, "confirmed": 0, "deleted": 0}),
             json.dumps(counts, ensure_ascii=False),
             json.dumps([_model_detection(d) for d in detections], ensure_ascii=False)),
        )
        # 不要在这里建 draft_state：草稿由 api._ensure_draft 懒创建，
        # 它同时负责把 v0 的框灌进 draft_detections。只建 state 会让
        # `_ensure_draft` 早退，草稿恒为空（2026-11-05 修复）。

    # --- 终态 -------------------------------------------------------------
    def _is_cancelled(self, task_id: str) -> bool:
        row = db.one("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,))
        return bool(row and row["cancel_requested"])

    def _finish_cancelled(self, task_id: str) -> None:
        db.execute("UPDATE tasks SET status='cancelled', finished_at=? WHERE id=? AND status<>'done'",
                   (db.now(), task_id))

    def _finish_failed(self, task_id: str, exc: Exception) -> None:
        code = getattr(exc, "code", "INFERENCE_FAILED")
        message = errors.sanitize(str(exc))
        db.execute("UPDATE tasks SET status='failed', error=?, finished_at=? WHERE id=?",
                   (f"{code}: {message}", db.now(), task_id))
        row = db.one("SELECT project_id, created_by FROM tasks WHERE id=?", (task_id,))
        db.log_event("task.failed", project_id=row["project_id"] if row else None,
                     user_id=row["created_by"] if row else None,
                     target=task_id, detail={"code": code, "message": message})


def _read_legacy_result(row: sqlite3.Row) -> dict:
    path = Path(row["result_path"]).resolve()
    expected = storage.result_path(row["id"], row["attempt"]).resolve()
    if path != expected or path.parent != config.RESULT_DIR.resolve():
        raise ValueError("result path")
    with path.open("rb") as source:
        limit = 32 << 20  # 上限远高于 20,000 个候选和 4,096 个切片的正常结果。
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("result size")
    payload = json.loads(data)
    if payload["schema_version"] not in {"ship-detection/0.1", "ship-detection/0.2"}:
        raise ValueError("result schema")
    image = payload["image"]
    if any(image[key] != row[column] for key, column in (
        ("id", "image_id"), ("project_id", "project_id"), ("sha256", "sha256"),
        ("width", "width"), ("height", "height"),
    )):
        raise ValueError("result image")
    detections = payload["detections"]
    count = payload["count"]
    if (not isinstance(detections, list) or type(count) is not int
            or count != row["final_count"] or count != len(detections)):
        raise ValueError("result count")
    for index, detection in enumerate(detections, start=1):
        if type(detection["id"]) is not int or detection["id"] != index:
            raise ValueError("result detection id")
        polygon = detection["polygon"]
        if not isinstance(polygon, list) or len(polygon) != 4 or any(
            not isinstance(point, list) or len(point) != 2
            or any(type(value) not in (int, float) for value in point)
            for point in polygon
        ) or canonical_polygon(polygon) != polygon:
            raise ValueError("result detection polygon")
        if (type(detection["class_id"]) is not int or not isinstance(detection["class_name"], str)
                or type(detection["confidence"]) not in (int, float)
                or not 0 <= detection["confidence"] <= 1
                or detection.get("review_state", "pending") != "pending"
                or detection.get("origin", "model") != "model"
                or (detection.get("source_tile_id") is not None and type(detection["source_tile_id"]) is not int)
                or (detection.get("note") is not None and not isinstance(detection["note"], str))):
            raise ValueError("result detection metadata")
    return payload


def _model_detection(det: dict) -> dict:
    return {
        "id": det["id"],
        "polygon": det["polygon"],
        "class_id": det.get("class_id"),
        "class_name": det.get("class_name") or "object",
        "confidence": det.get("confidence"),
        "source_tile_id": det.get("source_tile_id"),
        "review_state": det.get("review_state", "pending"),
        "origin": det.get("origin", "model"),
        "note": det.get("note"),
    }


runtime = TaskRuntime()
