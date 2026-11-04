"""业务 API：对应 `frontend/api/openapi.yaml` 的 29 个端点。

分工边界：本层只做业务流程、鉴权与持久化；检测与几何全部调用 `algorithm/`。
业务数据库是唯一写入口；计算产物由 Worker 写到 `var/results/`，再由本层发布为任务终态与 v0 版本。
"""
from __future__ import annotations

import csv
import io
import json
import math
import threading
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, Query
from fastapi.responses import FileResponse

from algorithm.errors import DetectionError

from . import config, db, detector, errors, geometry, imaging, security, storage

router = APIRouter()


# --------------------------------------------------------------------------
# 鉴权
# --------------------------------------------------------------------------

def current_state(authorization: str | None = Header(default=None)) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    user, project_id = security.resolve(token)
    return {"user": user, "project_id": project_id, "token": token}


State = Depends(current_state)


def _member(project_id: str, state: dict, *, owner_only: bool = False) -> str:
    role = security.member_role(project_id, state["user"]["id"])
    if owner_only and role != config.USER_ROLE_OWNER:
        raise errors.forbidden("只有项目负责人可以执行该操作")
    return role


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------

def _user_payload(user: dict, project_id: str | None) -> dict:
    project = db.one("SELECT * FROM projects WHERE id=?", (project_id,)) if project_id else None
    role = db.one("SELECT role FROM members WHERE project_id=? AND user_id=?",
                  (project_id, user["id"])) if project_id else None
    return {
        "id": user["id"], "name": user["display_name"],
        "role": role["role"] if role else config.USER_ROLE_ANALYST,
        "project": ({"id": project["id"], "name": project["name"], "code": project["code"]}
                    if project else None),
    }


def _project_payload(project_id: str) -> dict:
    project = db.one("SELECT * FROM projects WHERE id=?", (project_id,))
    if project is None:
        raise errors.not_found("PROJECT_NOT_FOUND", "项目不存在")
    members = db.query(
        "SELECT u.id AS id, u.display_name AS name, m.role AS role FROM members m"
        " JOIN users u ON u.id = m.user_id WHERE m.project_id=?"
        " ORDER BY CASE m.role WHEN ? THEN 0 ELSE 1 END, u.id",
        (project_id, config.USER_ROLE_OWNER),
    )
    return {"id": project["id"], "name": project["name"], "code": project["code"],
            "members": [dict(row) for row in members]}


def _image_payload(row) -> dict:
    return {
        "id": row["id"], "project_id": row["project_id"], "file_name": row["file_name"],
        "status": row["status"], "sha256": row["sha256"], "width": row["width"],
        "height": row["height"], "mode": row["mode"] or "RGB", "bytes": row["bytes"],
        "file_url": f"/files/{row['id']}", "preview_url": f"/previews/{row['id']}",
        "created_at": row["created_at"],
    }


def _upload_payload(row) -> dict:
    return {
        "id": row["id"], "status": row["status"], "stage": row["stage"],
        "chunk_size": row["chunk_size"], "chunks_total": row["chunks_total"],
        "chunks_received": row["chunks_received"], "pct": row["pct"],
        "image_id": row["image_id"], "error": row["error"], "expires_at": row["expires_at"],
        "file_name": row["file_name"], "size": row["size"],
    }


def _task_payload(row) -> dict:
    return {
        "id": row["id"], "project_id": row["project_id"], "image_id": row["image_id"],
        "status": row["status"], "attempt": row["attempt"], "tiles_total": row["tiles_total"],
        "tiles_done": row["tiles_done"], "temp_count": row["temp_count"],
        "final_count": row["final_count"], "parameters": json.loads(row["parameters"]),
        "cancel_requested": bool(row["cancel_requested"]), "error": row["error"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
    }


def _detection_payload(row) -> dict:
    return {
        "id": row["detection_id"], "polygon": json.loads(row["polygon"]),
        "class_id": row["class_id"], "class_name": row["class_name"],
        "confidence": row["confidence"], "source_tile_id": row["source_tile_id"],
        "review_state": row["review_state"], "origin": row["origin"], "note": row["note"],
    }


def _task_or_404(task_id: str, state: dict):
    row = db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if row is None:
        raise errors.not_found("TASK_NOT_FOUND", "任务不存在")
    _member(row["project_id"], state)
    return row


def _version_row(task_id: str, label: str):
    row = db.one("SELECT * FROM versions WHERE task_id=? AND label=?", (task_id, label))
    if row is None:
        raise errors.not_found("VERSION_NOT_FOUND", "版本不存在")
    return row


def _version_payload(row, *, with_detections: bool = False) -> dict:
    payload = {
        "id": row["label"], "task_id": row["task_id"], "label": row["label"],
        "author": row["author"], "created_at": row["created_at"],
        "readonly": bool(row["readonly"]),
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "counts": json.loads(row["counts"]) if row["counts"] else {},
    }
    if with_detections:
        payload["detections"] = json.loads(row["detections"])
    return payload


# --------------------------------------------------------------------------
# 运维与鉴权
# --------------------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    return {"status": "degraded" if detector.service.last_error else "ok", "mode": "http",
            "version": __import__("backend").__version__,
            "model_loaded": detector.service.loaded}


@router.post("/auth/login")
def login(body: dict = Body(...)) -> dict:
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    project_id = str(body.get("project_id") or "").strip()
    if not username or not password or not project_id:
        raise errors.invalid("INVALID_ARGUMENT", "用户名、密码与项目编号都是必填项")

    security.check_throttle(username)
    user = db.one("SELECT * FROM users WHERE username=?", (username,))
    if user is None or not security.verify_password(password, user["password_hash"]):
        security.note_failure(username)
        raise errors.ApiError(401, "INVALID_CREDENTIALS", "用户名或密码不正确")
    if user["status"] != "active":
        raise errors.ApiError(401, "INVALID_CREDENTIALS", "账号已停用")

    if db.one("SELECT role FROM members WHERE project_id=? AND user_id=?",
              (project_id, user["id"])) is None:
        raise errors.forbidden("你不是该项目的成员，无法进入")
    if db.one("SELECT id FROM projects WHERE id=?", (project_id,)) is None:
        raise errors.not_found("PROJECT_NOT_FOUND", "项目不存在")

    security.clear_failures(username)
    token = security.issue_token(user["id"], project_id)
    db.log_event("auth.login", project_id=project_id, user_id=user["id"])
    return {"token": token, "user": _user_payload(dict(user), project_id)}


@router.post("/auth/logout", status_code=204)
def logout(state: dict = State):
    security.revoke(state["token"])
    db.log_event("auth.logout", project_id=state["project_id"], user_id=state["user"]["id"])
    return None


@router.get("/me")
def me(state: dict = State) -> dict:
    return _user_payload(state["user"], state["project_id"])


@router.get("/projects")
def list_projects(state: dict = State) -> list[dict]:
    rows = db.query("SELECT project_id FROM members WHERE user_id=? ORDER BY project_id",
                    (state["user"]["id"],))
    return [_project_payload(row["project_id"]) for row in rows]


@router.get("/projects/{project_id}")
def get_project(project_id: str, state: dict = State) -> dict:
    _member(project_id, state)
    return _project_payload(project_id)


# --------------------------------------------------------------------------
# 上传：transport → merge → inspect → preview → ready
# --------------------------------------------------------------------------

def _process_upload(upload_id: str) -> None:
    row = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if row is None:
        return
    suffix = Path(row["file_name"]).suffix.lower()
    store = storage.UploadStore()
    try:
        db.execute("UPDATE uploads SET status='verifying', stage='merge', pct=100 WHERE id=?", (upload_id,))
        path, size, digest = store.merge(upload_id, row["chunks_total"], suffix)
        if row["size"] and size != row["size"]:
            raise errors.invalid("CHECKSUM_MISMATCH", f"文件长度不一致：收到 {size}，声明 {row['size']}")
        if row["sha256"] and digest != row["sha256"]:
            raise errors.invalid("CHECKSUM_MISMATCH", "文件校验和不一致，请重新上传")

        db.execute("UPDATE uploads SET stage='inspect' WHERE id=?", (upload_id,))
        try:
            info = imaging.describe(path)
        except DetectionError as exc:
            raise errors.from_detection_error(exc) from exc

        db.execute("UPDATE uploads SET stage='preview' WHERE id=?", (upload_id,))
        image_id = db.new_id("img")
        stored_name = f"{image_id}{suffix}"
        final_path = storage.blob_path(stored_name)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        path.replace(final_path)
        imaging.make_preview(final_path, storage.preview_path(image_id))

        db.execute(
            "INSERT INTO images(id,project_id,upload_id,file_name,stored_name,sha256,width,height,"
            "mode,bytes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'ready',?)",
            (image_id, row["project_id"], upload_id, row["file_name"], stored_name, digest,
             info["width"], info["height"], info["mode"], size, db.now()),
        )
        db.execute("UPDATE uploads SET status='ready', stage='ready', pct=100, image_id=?, sha256=?"
                   " WHERE id=?", (image_id, digest, upload_id))
        db.log_event("upload.ready", project_id=row["project_id"], user_id=row["user_id"],
                     target=upload_id,
                     detail={"image_id": image_id, "width": info["width"], "height": info["height"]})
    except errors.ApiError as exc:
        db.execute("UPDATE uploads SET status='failed', stage='failed', error=? WHERE id=?",
                   (f"{exc.code}: {exc.message}", upload_id))
    except Exception as exc:  # pragma: no cover - 兜底
        db.execute("UPDATE uploads SET status='failed', stage='failed', error=? WHERE id=?",
                   (f"INTERNAL_ERROR: {errors.sanitize(str(exc))}", upload_id))
    finally:
        store.discard(upload_id)


@router.post("/projects/{project_id}/uploads", status_code=201)
def create_upload(project_id: str, body: dict = Body(...), state: dict = State) -> dict:
    _member(project_id, state)
    file_name = str(body.get("file_name") or "").strip()
    if not file_name:
        raise errors.invalid("INVALID_ARGUMENT", "缺少 file_name")
    if Path(file_name).suffix.lower() not in storage.ALLOWED_SUFFIXES:
        raise errors.invalid("INVALID_IMAGE", "首版只支持 RGB PNG/JPEG")
    size = body.get("size")
    if size is not None and int(size) > config.MAX_UPLOAD_BYTES:
        raise errors.too_large("FILE_TOO_LARGE", "文件超过允许的大小")

    upload_id = db.new_id("upl")
    chunks_total = math.ceil(int(size) / config.CHUNK_SIZE) if size else None
    db.execute(
        "INSERT INTO uploads(id,project_id,user_id,file_name,chunk_size,chunks_total,chunks_received,"
        "status,stage,pct,size,created_at,expires_at) VALUES(?,?,?,?,?,?,0,'uploading','transport',0,?,?,?)",
        (upload_id, project_id, state["user"]["id"], file_name, config.CHUNK_SIZE, chunks_total,
         int(size) if size else None, db.now(), db.after_hours(config.UPLOAD_TTL_HOURS)),
    )
    return _upload_payload(db.one("SELECT * FROM uploads WHERE id=?", (upload_id,)))


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str, state: dict = State) -> dict:
    row = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if row is None:
        raise errors.not_found("UPLOAD_NOT_FOUND", "上传会话不存在或已过期")
    _member(row["project_id"], state)
    return _upload_payload(row)


@router.put("/uploads/{upload_id}/chunks/{index}")
def put_chunk(upload_id: str, index: int, payload: bytes = Body(...),
              authorization: str | None = Header(default=None)) -> dict:
    state = current_state(authorization)
    row = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if row is None:
        raise errors.not_found("UPLOAD_NOT_FOUND", "上传会话不存在")
    _member(row["project_id"], state)
    if row["status"] != "uploading":
        raise errors.conflict("UPLOAD_CLOSED", "该上传会话已结束")

    store = storage.UploadStore()
    if store.chunk_path(upload_id, index).exists():
        return {"upload_id": upload_id, "index": index,
                "chunks_received": row["chunks_received"], "pct": row["pct"]}
    if index != row["chunks_received"]:
        raise errors.conflict("CHUNK_OUT_OF_ORDER",
                              f"分片必须按顺序提交，当前应提交第 {row['chunks_received']} 片")
    if not payload:
        raise errors.invalid("INVALID_ARGUMENT", "分片内容为空")

    store.write_chunk(upload_id, index, payload)
    received = row["chunks_received"] + 1
    total = row["chunks_total"] or 0
    pct = min(100, int(received * 100 / total)) if total else 0
    db.execute("UPDATE uploads SET chunks_received=?, pct=? WHERE id=?", (received, pct, upload_id))
    return {"upload_id": upload_id, "index": index, "chunks_received": received, "pct": pct}


@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, body: dict = Body(default_factory=dict),
                    state: dict = State) -> dict:
    row = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if row is None:
        raise errors.not_found("UPLOAD_NOT_FOUND", "上传会话不存在")
    _member(row["project_id"], state)
    if row["status"] == "ready":
        return _upload_payload(row)
    if row["status"] != "uploading":
        raise errors.conflict("UPLOAD_CLOSED", "该上传会话已结束")
    if row["chunks_total"] and row["chunks_received"] < row["chunks_total"]:
        raise errors.conflict("UPLOAD_INCOMPLETE",
                              f"分片未收齐：{row['chunks_received']}/{row['chunks_total']}")

    sha256 = body.get("sha256")
    size = body.get("size")
    db.execute("UPDATE uploads SET status='verifying', stage='merge', sha256=?, size=COALESCE(?, size)"
               " WHERE id=?", (sha256, int(size) if size else None, upload_id))
    threading.Thread(target=_process_upload, args=(upload_id,),
                     name=f"upload-{upload_id}", daemon=True).start()
    return _upload_payload(db.one("SELECT * FROM uploads WHERE id=?", (upload_id,)))


@router.get("/images/{image_id}")
def get_image(image_id: str, state: dict = State) -> dict:
    row = db.one("SELECT * FROM images WHERE id=?", (image_id,))
    if row is None:
        raise errors.not_found("IMAGE_NOT_FOUND", "影像不存在")
    _member(row["project_id"], state)
    return _image_payload(row)


@router.get("/files/{image_id}")
def get_file(image_id: str, state: dict = State) -> FileResponse:
    row = db.one("SELECT * FROM images WHERE id=?", (image_id,))
    if row is None:
        raise errors.not_found("IMAGE_NOT_FOUND", "影像不存在")
    _member(row["project_id"], state)
    path = storage.blob_path(row["stored_name"])
    if not path.is_file():
        raise errors.not_found("IMAGE_NOT_FOUND", "影像文件缺失")
    return FileResponse(path, filename=row["file_name"])


@router.get("/previews/{image_id}")
def get_preview(image_id: str, state: dict = State) -> FileResponse:
    row = db.one("SELECT * FROM images WHERE id=?", (image_id,))
    if row is None:
        raise errors.not_found("IMAGE_NOT_FOUND", "影像不存在")
    _member(row["project_id"], state)
    path = storage.preview_path(image_id)
    if not path.is_file():
        raise errors.not_found("IMAGE_NOT_FOUND", "预览尚未生成")
    return FileResponse(path, media_type="image/jpeg")


# --------------------------------------------------------------------------
# 检测任务
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, body: dict = Body(...), state: dict = State) -> dict:
    _member(project_id, state)
    image_id = str(body.get("image_id") or "")
    image = db.one("SELECT * FROM images WHERE id=? AND project_id=?", (image_id, project_id))
    if image is None:
        raise errors.not_found("IMAGE_NOT_FOUND", "影像不存在或不属于该项目")
    if image["status"] != "ready":
        raise errors.conflict("IMAGE_NOT_READY", "影像尚未通过验证，不能创建检测任务")

    parameters = dict(config.DEFAULT_PARAMETERS)
    parameters.update({k: v for k, v in (body.get("parameters") or {}).items() if v is not None})

    task_id = db.new_id("tsk")
    db.execute(
        "INSERT INTO tasks(id,project_id,image_id,created_by,status,attempt,tiles_total,tiles_done,"
        "temp_count,parameters,cancel_requested,created_at) VALUES(?,?,?,?,'queued',1,0,0,0,?,0,?)",
        (task_id, project_id, image_id, state["user"]["id"],
         json.dumps(parameters, ensure_ascii=False), db.now()),
    )
    db.log_event("task.create", project_id=project_id, user_id=state["user"]["id"],
                 target=task_id, detail={"image_id": image_id, "parameters": parameters})
    from . import jobs
    jobs.runtime.submit(task_id)
    return _task_payload(db.one("SELECT * FROM tasks WHERE id=?", (task_id,)))


@router.get("/tasks/{task_id}")
def get_task(task_id: str, state: dict = State) -> dict:
    return _task_payload(_task_or_404(task_id, state))


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, state: dict = State) -> dict:
    row = _task_or_404(task_id, state)
    if row["status"] in {"done", "failed", "cancelled"}:
        return _task_payload(row)
    db.execute("UPDATE tasks SET cancel_requested=1 WHERE id=?", (task_id,))
    if row["status"] == "queued":
        db.execute("UPDATE tasks SET status='cancelled', finished_at=? WHERE id=? AND status='queued'",
                   (db.now(), task_id))
    db.log_event("task.cancel", project_id=row["project_id"], user_id=state["user"]["id"], target=task_id)
    return _task_payload(db.one("SELECT * FROM tasks WHERE id=?", (task_id,)))


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, state: dict = State) -> dict:
    row = _task_or_404(task_id, state)
    if row["status"] not in {"failed", "cancelled"}:
        raise errors.conflict("TASK_BUSY", "只有失败或已取消的任务可以重试")
    db.execute(
        "UPDATE tasks SET status='queued', attempt=attempt+1, tiles_done=0, temp_count=0,"
        " final_count=NULL, cancel_requested=0, error=NULL, started_at=NULL, finished_at=NULL"
        " WHERE id=?", (task_id,),
    )
    db.log_event("task.retry", project_id=row["project_id"], user_id=state["user"]["id"], target=task_id)
    from . import jobs
    jobs.runtime.submit(task_id)
    return _task_payload(db.one("SELECT * FROM tasks WHERE id=?", (task_id,)))


@router.get("/tasks/{task_id}/result")
def get_result(task_id: str, state: dict = State) -> dict:
    row = _task_or_404(task_id, state)
    if row["status"] != "done" or not row["result_path"]:
        raise errors.conflict("RESULT_NOT_READY", "任务尚未完成，最终结果不可用")
    path = Path(row["result_path"])
    if not path.is_file():
        raise errors.not_found("DETECTION_NOT_FOUND", "结果文件缺失")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 草稿
# --------------------------------------------------------------------------

def _ensure_draft(task_id: str) -> None:
    if db.one("SELECT task_id FROM draft_state WHERE task_id=?", (task_id,)):
        return
    version = db.one("SELECT detections FROM versions WHERE task_id=? AND label='v0'", (task_id,))
    if version is None:
        raise errors.conflict("RESULT_NOT_READY", "任务尚未产出可复核的结果")
    detections = json.loads(version["detections"])
    with db.tx() as conn:
        conn.execute("INSERT OR IGNORE INTO draft_state(task_id,base_version,updated_at) VALUES(?,?,?)",
                     (task_id, "v0", db.now()))
        for det in detections:
            conn.execute(
                "INSERT OR IGNORE INTO draft_detections(task_id,detection_id,polygon,class_id,"
                "class_name,confidence,source_tile_id,review_state,origin,note,ai_candidate_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (task_id, det["id"], json.dumps(det["polygon"]), det.get("class_id", 1),
                 det.get("class_name", "ship"), det.get("confidence"), det.get("source_tile_id"),
                 det.get("review_state", "pending"), det.get("origin", "model"), det.get("note")),
            )


def _draft_detections(task_id: str) -> list[dict]:
    rows = db.query("SELECT * FROM draft_detections WHERE task_id=? ORDER BY detection_id", (task_id,))
    return [_detection_payload(row) for row in rows]


@router.get("/tasks/{task_id}/draft")
def get_draft(task_id: str, state: dict = State) -> dict:
    _task_or_404(task_id, state)
    _ensure_draft(task_id)
    return {"task_id": task_id, "detections": _draft_detections(task_id)}


@router.patch("/tasks/{task_id}/draft/detections/{detection_id}")
def patch_detection(task_id: str, detection_id: int, body: dict = Body(...),
                    state: dict = State) -> dict:
    _task_or_404(task_id, state)
    _ensure_draft(task_id)
    row = db.one("SELECT * FROM draft_detections WHERE task_id=? AND detection_id=?",
                 (task_id, detection_id))
    if row is None:
        raise errors.not_found("DETECTION_NOT_FOUND", "目标不存在")

    polygon = body.get("polygon")
    review_state = body.get("review_state")
    note = body.get("note")
    if review_state is not None and review_state not in {"pending", "confirmed", "modified", "deleted"}:
        raise errors.invalid("INVALID_STATE", "未知的复核状态")
    if note is not None and len(str(note)) > 500:
        raise errors.invalid("INVALID_ARGUMENT", "备注不能超过 500 字")

    if polygon is not None:
        if len(polygon) != 4:
            raise errors.invalid("INVALID_POLYGON", "多边形必须是 4 个顶点")
        try:
            polygon = geometry.normalize_polygon(polygon)
        except DetectionError as exc:
            raise errors.from_detection_error(exc) from exc

    db.execute(
        "UPDATE draft_detections SET polygon=COALESCE(?,polygon), review_state=COALESCE(?,review_state),"
        " note=COALESCE(?,note), origin=CASE WHEN origin='model' AND ? IS NOT NULL THEN 'model'"
        " ELSE origin END WHERE task_id=? AND detection_id=?",
        (json.dumps(polygon) if polygon else None, review_state, note,
         json.dumps(polygon) if polygon else None, task_id, detection_id),
    )
    db.execute("UPDATE draft_state SET updated_at=? WHERE task_id=?", (db.now(), task_id))
    return _detection_payload(db.one(
        "SELECT * FROM draft_detections WHERE task_id=? AND detection_id=?", (task_id, detection_id)))


@router.post("/tasks/{task_id}/draft/detections", status_code=201)
def add_detection(task_id: str, body: dict = Body(...), state: dict = State) -> dict:
    _task_or_404(task_id, state)
    _ensure_draft(task_id)
    polygon = body.get("polygon")
    if not polygon or len(polygon) != 4:
        raise errors.invalid("INVALID_POLYGON", "多边形必须是 4 个顶点")
    try:
        polygon = geometry.normalize_polygon(polygon)
    except DetectionError as exc:
        raise errors.from_detection_error(exc) from exc

    origin = body.get("origin") or "manual"
    if origin not in {"manual", "ai"}:
        raise errors.invalid("INVALID_ARGUMENT", "origin 只能是 manual 或 ai")
    review_state = body.get("review_state") or "confirmed"
    if review_state not in {"pending", "confirmed", "modified", "deleted"}:
        raise errors.invalid("INVALID_STATE", "未知的复核状态")

    next_id = db.one("SELECT COALESCE(MAX(detection_id),0)+1 AS nid FROM draft_detections WHERE task_id=?",
                     (task_id,))["nid"]
    db.execute(
        "INSERT INTO draft_detections(task_id,detection_id,polygon,class_id,class_name,confidence,"
        "source_tile_id,review_state,origin,note,ai_candidate_id)"
        " VALUES(?,?,?,?,?,NULL,NULL,?,?,?,?)",
        (task_id, next_id, json.dumps(polygon), 1, "ship", review_state, origin,
         body.get("note"), body.get("candidate_id")),
    )
    db.execute("UPDATE draft_state SET updated_at=? WHERE task_id=?", (db.now(), task_id))
    return _detection_payload(db.one(
        "SELECT * FROM draft_detections WHERE task_id=? AND detection_id=?", (task_id, next_id)))


@router.post("/tasks/{task_id}/draft/reset")
def reset_draft(task_id: str, body: dict = Body(...), state: dict = State) -> dict:
    _task_or_404(task_id, state)
    label = str(body.get("version_id") or "v0")
    row = db.one("SELECT * FROM versions WHERE task_id=? AND label=?", (task_id, label))
    if row is None:
        raise errors.not_found("VERSION_NOT_FOUND", "版本不存在")
    detections = json.loads(row["detections"])
    with db.tx() as conn:
        conn.execute("DELETE FROM draft_detections WHERE task_id=?", (task_id,))
        for det in detections:
            conn.execute(
                "INSERT INTO draft_detections(task_id,detection_id,polygon,class_id,class_name,"
                "confidence,source_tile_id,review_state,origin,note,ai_candidate_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (task_id, det["id"], json.dumps(det["polygon"]), det.get("class_id", 1),
                 det.get("class_name", "ship"), det.get("confidence"), det.get("source_tile_id"),
                 det.get("review_state", "pending"), det.get("origin", "model"), det.get("note")),
            )
        conn.execute("INSERT INTO draft_state(task_id,base_version,updated_at) VALUES(?,?,?)"
                     " ON CONFLICT(task_id) DO UPDATE SET base_version=excluded.base_version,"
                     " updated_at=excluded.updated_at", (task_id, label, db.now()))
    db.log_event("draft.reset", project_id=state["project_id"], user_id=state["user"]["id"],
                 target=task_id, detail={"version": label})
    return {"task_id": task_id, "detections": _draft_detections(task_id)}


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------

def _signature(det: dict) -> tuple:
    return (det["id"], json.dumps(det["polygon"]), det["review_state"], det["origin"], det.get("note"))


def _version_counts(detections: list[dict]) -> dict:
    return {
        "total": len(detections),
        "pending": sum(1 for d in detections if d["review_state"] == "pending"),
        "confirmed": sum(1 for d in detections if d["review_state"] == "confirmed"),
        "modified": sum(1 for d in detections if d["review_state"] == "modified"),
        "deleted": sum(1 for d in detections if d["review_state"] == "deleted"),
        "added": sum(1 for d in detections if d["origin"] in {"manual", "ai"}),
    }


def _version_diff(base: list[dict], draft: list[dict]) -> dict:
    base_index = {det["id"]: det for det in base}
    draft_index = {det["id"]: det for det in draft}
    added = [i for i in draft_index if i not in base_index]
    removed = [i for i in base_index if i not in draft_index]
    changed = [
        i for i in base_index
        if i in draft_index
        and json.dumps(base_index[i]["polygon"]) != json.dumps(draft_index[i]["polygon"])
    ]
    confirmed = sum(1 for i in draft_index if i in base_index
                    and draft_index[i]["review_state"] == "confirmed"
                    and base_index[i]["review_state"] != "confirmed")
    deleted = sum(1 for i in draft_index if draft_index[i]["review_state"] == "deleted"
                  and (i not in base_index or base_index[i]["review_state"] != "deleted"))
    return {"added": len(added), "removed": len(removed), "changed": len(changed),
            "confirmed": confirmed, "deleted": deleted}


@router.get("/tasks/{task_id}/versions")
def list_versions(task_id: str, state: dict = State) -> list[dict]:
    _task_or_404(task_id, state)
    rows = db.query("SELECT * FROM versions WHERE task_id=?"
                    " ORDER BY CAST(SUBSTR(label,2) AS INTEGER)", (task_id,))
    return [_version_payload(row) for row in rows]


@router.post("/tasks/{task_id}/versions", status_code=201)
def commit_version(task_id: str, body: dict = Body(default_factory=dict),
                   state: dict = State) -> dict:
    row = _task_or_404(task_id, state)
    if row["status"] != "done":
        raise errors.conflict("RESULT_NOT_READY", "任务尚未完成，不能提交版本")
    _ensure_draft(task_id)
    draft = _draft_detections(task_id)
    latest = db.one("SELECT * FROM versions WHERE task_id=?"
                    " ORDER BY CAST(SUBSTR(label,2) AS INTEGER) DESC LIMIT 1", (task_id,))
    base = json.loads(latest["detections"]) if latest else []
    if [_signature(d) for d in draft] == [_signature(d) for d in base]:
        raise errors.conflict("NOTHING_TO_COMMIT", "草稿与当前版本一致，没有可提交的修改")

    summary = _version_diff(base, draft)
    label = f"v{int(latest['label'][1:]) + 1}" if latest else "v1"
    snapshot = [{
        "id": det["id"], "polygon": det["polygon"], "class_id": det["class_id"],
        "class_name": det["class_name"], "confidence": det["confidence"],
        "source_tile_id": det["source_tile_id"], "review_state": det["review_state"],
        "origin": det["origin"], "note": det["note"],
    } for det in draft]

    db.execute(
        "INSERT INTO versions(task_id,label,project_id,author,created_at,readonly,summary,counts,detections)"
        " VALUES(?,?,?,?,?,0,?,?,?)",
        (task_id, label, row["project_id"], state["user"]["display_name"], db.now(),
         json.dumps(summary), json.dumps(_version_counts(snapshot), ensure_ascii=False),
         json.dumps(snapshot, ensure_ascii=False)),
    )
    db.execute("UPDATE draft_state SET base_version=?, updated_at=? WHERE task_id=?",
               (label, db.now(), task_id))
    db.log_event("version.commit", project_id=row["project_id"], user_id=state["user"]["id"],
                 target=task_id, detail={"version": label, "summary": summary})
    return _version_payload(_version_row(task_id, label), with_detections=True)


@router.get("/versions/{version_id}")
def get_version(version_id: str, task_id: str = Query(...), state: dict = State) -> dict:
    _task_or_404(task_id, state)
    return _version_payload(_version_row(task_id, version_id), with_detections=True)


# --------------------------------------------------------------------------
# 疑难复核与区域
# --------------------------------------------------------------------------

def _task_result_payload(row) -> dict:
    path = Path(row["result_path"] or "")
    if not path.is_file():
        raise errors.conflict("RESULT_NOT_READY", "阈值结果文件缺失，请重试任务")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/tasks/{task_id}/draft/review-queue")
def review_queue(task_id: str, state: dict = State) -> dict:
    row = _task_or_404(task_id, state)
    if row["status"] != "done":
        raise errors.conflict("RESULT_NOT_READY", "任务尚未完成，暂不能生成复核队列")
    _ensure_draft(task_id)
    result = _task_result_payload(row)
    parameters = json.loads(row["parameters"])
    draft = _draft_detections(task_id)
    items = geometry.review_items(
        draft,
        image_width=int(result["image"]["width"]),
        image_height=int(result["image"]["height"]),
        tiles=(result.get("tiling") or {}).get("tiles"),
        merge_iou=float(parameters.get("merge_iou", 0.1)),
    )
    sampling = geometry.sampling_cells(draft, image_width=int(result["image"]["width"]),
                                       image_height=int(result["image"]["height"]))
    return {"items": items[:200], "sampling": sampling}


@router.post("/tasks/{task_id}/draft/regions")
def create_region(task_id: str, body: dict = Body(...), state: dict = State) -> dict:
    row = _task_or_404(task_id, state)
    polygon = body.get("polygon") or []
    if len(polygon) < 3:
        raise errors.invalid("INVALID_REGION", "区域至少需要 3 个顶点")
    try:
        points = [[float(p[0]), float(p[1])] for p in polygon]
    except (TypeError, ValueError, IndexError) as exc:
        raise errors.invalid("INVALID_REGION", "区域坐标格式不正确") from exc
    if not all(math.isfinite(v) for point in points for v in point):
        raise errors.invalid("INVALID_REGION", "区域坐标必须是有限数值")
    if geometry.polygon_area(points) <= 1:
        raise errors.invalid("INVALID_REGION", "区域面积过小")

    if row["status"] != "done":
        raise errors.conflict("RESULT_NOT_READY", "任务尚未完成，暂不能统计区域")
    _ensure_draft(task_id)
    draft = _draft_detections(task_id)
    region_id = db.new_id("rgn")
    name = str(body.get("name") or f"区域 {region_id[-4:]}")
    db.execute("INSERT INTO regions(id,task_id,project_id,name,polygon,rule,created_at)"
               " VALUES(?,?,?,?,?,?,?)",
               (region_id, task_id, row["project_id"], name, json.dumps(points),
                "centre_point_inside", db.now()))
    return {
        "id": region_id, "task_id": task_id, "name": name, "polygon": points,
        "rule": "centre_point_inside",
        "counts": geometry.region_counts(draft, points),
        "area_px2": int(round(geometry.polygon_area(points))),
    }


@router.get("/tasks/{task_id}/draft/regions")
def list_regions(task_id: str, state: dict = State) -> list[dict]:
    row = _task_or_404(task_id, state)
    _ensure_draft(task_id)
    draft = _draft_detections(task_id)
    rows = db.query("SELECT * FROM regions WHERE task_id=? ORDER BY created_at", (task_id,))
    payload = []
    for item in rows:
        points = json.loads(item["polygon"])
        payload.append({
            "id": item["id"], "task_id": task_id, "name": item["name"], "polygon": points,
            "rule": item["rule"], "counts": geometry.region_counts(draft, points),
            "area_px2": int(round(geometry.polygon_area(points))),
        })
    return payload


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------

CSV_HEADER = ["id", "x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3",
              "confidence", "class_name", "source_tile_id", "review_state", "origin", "note"]


def _generate_export(export_id: str) -> None:
    row = db.one("SELECT * FROM exports WHERE id=?", (export_id,))
    if row is None:
        return
    version = db.one("SELECT * FROM versions WHERE task_id=? AND label=?",
                     (row["task_id"], row["version_id"]))
    task = db.one("SELECT * FROM tasks WHERE id=?", (row["task_id"],))
    image = db.one("SELECT * FROM images WHERE id=?", (task["image_id"],)) if task else None
    formats = json.loads(row["formats"])
    directory = storage.export_dir(export_id)
    directory.mkdir(parents=True, exist_ok=True)
    detections = json.loads(version["detections"]) if version else []
    files: list[dict] = []

    for fmt in formats:
        entry = {"format": fmt, "name": None, "url": None, "bytes": None, "error": None}
        try:
            if fmt == "csv":
                name = f"{row['version_id']}-detections.csv"
                buffer = io.StringIO()
                writer = csv.writer(buffer)
                writer.writerow(CSV_HEADER)
                for det in detections:
                    flat = [coord for point in det["polygon"] for coord in point]
                    writer.writerow([det["id"], *flat, det["confidence"], det["class_name"],
                                     det["source_tile_id"], det["review_state"], det["origin"],
                                     det.get("note") or ""])
                (directory / name).write_text("\ufeff" + buffer.getvalue(), encoding="utf-8")
            elif fmt == "json":
                name = f"{row['version_id']}-snapshot.json"
                (directory / name).write_text(json.dumps({
                    "task_id": row["task_id"], "version": row["version_id"],
                    "image": image["file_name"] if image else None,
                    "detections": detections,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            elif fmt == "png":
                name = f"{row['version_id']}-annotated.png"
                _render_annotated(image, detections, directory / name)
            else:
                raise ValueError(f"不支持的格式 {fmt}")
            entry["name"] = name
            entry["bytes"] = (directory / name).stat().st_size
            entry["url"] = f"/exports/{export_id}/files/{name}"
        except Exception as exc:  # 单个格式失败不影响其他格式
            entry["error"] = errors.sanitize(str(exc))
        files.append(entry)

    status = "done" if any(f["url"] for f in files) else "failed"
    db.execute("UPDATE exports SET status=?, files=?, updated_at=? WHERE id=?",
               (status, json.dumps(files, ensure_ascii=False), db.now(), export_id))


def _render_annotated(image_row, detections, target: Path) -> None:
    from PIL import ImageDraw

    if image_row is None:
        raise ValueError("缺少影像记录")
    source = storage.preview_path(image_row["id"])
    if not source.is_file():
        source = storage.blob_path(image_row["stored_name"])
    with imaging.read_image_safe(source) as image:
        draw = ImageDraw.Draw(image)
        sx = image.width / max(1, image_row["width"])
        sy = image.height / max(1, image_row["height"])
        for det in detections:
            if det["review_state"] == "deleted":
                continue
            points = [(x * sx, y * sy) for x, y in det["polygon"]]
            draw.line(points + [points[0]], fill=(255, 60, 40), width=2)
            draw.text((points[0][0], points[0][1]), str(det["id"]), fill=(255, 255, 0))
        image.save(target, format="PNG")


def _export_payload(row) -> dict:
    return {"id": row["id"], "version_id": row["version_id"], "task_id": row["task_id"],
            "status": row["status"],
            "files": json.loads(row["files"]) if row["files"] else []}


@router.post("/versions/{version_id}/exports", status_code=202)
def create_export(version_id: str, task_id: str = Query(...),
                  body: dict = Body(default_factory=dict), state: dict = State) -> dict:
    task = _task_or_404(task_id, state)
    _version_row(task_id, version_id)
    formats = body.get("formats") or ["csv", "json", "png"]
    if not isinstance(formats, list) or any(f not in {"csv", "json", "png"} for f in formats):
        raise errors.invalid("INVALID_ARGUMENT", "formats 只能是 csv / json / png")

    export_id = db.new_id("exp")
    db.execute("INSERT INTO exports(id,version_id,task_id,project_id,status,formats,files,"
               "created_at,updated_at) VALUES(?,?,?,?,'running',?,NULL,?,?)",
               (export_id, version_id, task_id, task["project_id"],
                json.dumps(formats), db.now(), db.now()))
    threading.Thread(target=_generate_export, args=(export_id,),
                     name=f"export-{export_id}", daemon=True).start()
    db.log_event("export.create", project_id=task["project_id"], user_id=state["user"]["id"],
                 target=export_id, detail={"version": version_id, "formats": formats})
    return _export_payload(db.one("SELECT * FROM exports WHERE id=?", (export_id,)))


@router.get("/exports/{export_id}")
def get_export(export_id: str, state: dict = State) -> dict:
    row = db.one("SELECT * FROM exports WHERE id=?", (export_id,))
    if row is None:
        raise errors.not_found("EXPORT_NOT_FOUND", "导出记录不存在")
    _member(row["project_id"], state)
    return _export_payload(row)


@router.get("/exports/{export_id}/files/{name}")
def get_export_file(export_id: str, name: str, state: dict = State) -> FileResponse:
    row = db.one("SELECT * FROM exports WHERE id=?", (export_id,))
    if row is None:
        raise errors.not_found("EXPORT_NOT_FOUND", "导出记录不存在")
    _member(row["project_id"], state)
    safe_name = Path(name).name
    path = storage.export_dir(export_id) / safe_name
    if not path.is_file():
        raise errors.not_found("EXPORT_NOT_FOUND", "导出文件不存在或已过期")
    return FileResponse(path, filename=safe_name)


# --------------------------------------------------------------------------
# AI 辅助候选
# --------------------------------------------------------------------------

def _candidate_polygon(image_row, body: dict) -> dict:
    """点选/框选 → 候选旋转框。

    先在 ROI 上跑真实检测模型（局部高分辨率能救回整图缩得太小的漏检目标）；
    模型没命中时退化为经典阈值轮廓，并在 reason 里如实说明来源。
    """
    import cv2
    import numpy as np
    from PIL import Image

    path = storage.blob_path(image_row["stored_name"])
    with Image.open(path) as source:
        image = source.convert("RGB")
    try:
        width, height = image.size
        mode = body.get("mode") or ("box" if body.get("box") else "point")
        if mode == "box" and body.get("box"):
            x0, y0, x1, y1 = [float(v) for v in body["box"]]
        elif body.get("point"):
            px, py = float(body["point"][0]), float(body["point"][1])
            x0, y0, x1, y1 = px - 48, py - 48, px + 48, py + 48
        else:
            raise errors.invalid("INVALID_REGION", "需要 point 或 box")

        pad = 8.0
        left = max(0, int(min(x0, x1) - pad))
        top = max(0, int(min(y0, y1) - pad))
        right = min(width, int(max(x0, x1) + pad))
        bottom = min(height, int(max(y0, y1) + pad))
        if right - left < 8 or bottom - top < 8:
            raise errors.invalid("INVALID_REGION", "选区太小，请重新框选")

        crop = image.crop((left, top, right, bottom))
        temp = config.TMP_DIR / f"candidate_{db.new_id('tmp')}.png"
        crop.save(temp)
        candidates: list[dict] = []
        try:
            result = detector.service.predict(temp, {
                "tiling": False, "conf": 0.15, "iou": 0.7,
                "imgsz": max(32, min(1024, int(math.ceil(max(crop.size) / 32.0) * 32))),
                "max_det": 10,
            })
            for det in sorted(result["detections"], key=lambda d: -float(d["confidence"]))[:2]:
                candidates.append({
                    "polygon": geometry.normalize_polygon(
                        [[x + left, y + top] for x, y in det["polygon"]]),
                    "quality": round(float(det["confidence"]), 4),
                    "reason": "局部高分辨率检测命中",
                })
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

        if not candidates:
            gray = np.asarray(crop.convert("L"))
            _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                contour = max(contours, key=cv2.contourArea)
                area = cv2.contourArea(contour)
                if area >= 40:
                    (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
                    box = cv2.boxPoints(((cx, cy), (w, h), angle))
                    polygon = [[px + left, py + top] for px, py in box]
                    fill = area / max(1.0, w * h)
                    candidates.append({
                        "polygon": geometry.normalize_polygon(polygon),
                        "quality": round(min(0.75, 0.35 + 0.4 * fill), 4),
                        "reason": "经典阈值轮廓兜底（非语义分割）",
                    })
        if not candidates:
            raise errors.invalid("NO_CANDIDATE", "该区域没有可用候选，请改用手动标注")
        return candidates[0]
    finally:
        image.close()


@router.post("/images/{image_id}/ai-candidates")
def create_candidate(image_id: str, body: dict = Body(...), state: dict = State) -> dict:
    image = db.one("SELECT * FROM images WHERE id=?", (image_id,))
    if image is None:
        raise errors.not_found("IMAGE_NOT_FOUND", "影像不存在")
    _member(image["project_id"], state)
    if image["status"] != "ready":
        raise errors.conflict("IMAGE_NOT_READY", "影像尚未通过验证")

    proposal = _candidate_polygon(image, body)
    polygon = proposal["polygon"]
    sides = [math.dist(polygon[i], polygon[(i + 1) % 4]) for i in range(4)]
    candidate_id = db.new_id("cand")
    db.execute("INSERT INTO candidates(id,image_id,project_id,user_id,status,polygon,source,width,"
               "length,quality,reason,created_at) VALUES(?,?,?,?,'preview',?,?,?,?,?,?,?)",
               (candidate_id, image_id, image["project_id"], state["user"]["id"],
                json.dumps(polygon), "prompt_segmentation", min(sides[0], sides[2]),
                max(sides[0], sides[2]), proposal["quality"], proposal["reason"], db.now()))
    return {"id": candidate_id, "image_id": image_id, "status": "preview", "polygon": polygon,
            "source": "prompt_segmentation", "confidence": None,
            "width": round(min(sides[0], sides[2]), 2), "length": round(max(sides[0], sides[2]), 2),
            "quality": proposal["quality"], "reason": proposal["reason"]}


@router.post("/ai-candidates/{candidate_id}/accept", status_code=201)
def accept_candidate(candidate_id: str, body: dict = Body(default_factory=dict),
                     state: dict = State) -> dict:
    candidate = db.one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if candidate is None:
        raise errors.not_found("CANDIDATE_NOT_FOUND", "候选不存在")
    _member(candidate["project_id"], state)
    if candidate["status"] != "preview":
        raise errors.conflict("CANDIDATE_CLOSED", "该候选已被处理")

    task_id = body.get("task_id")
    if task_id:
        task = _task_or_404(str(task_id), state)
        if task["image_id"] != candidate["image_id"]:
            raise errors.invalid("INVALID_ARGUMENT", "候选与任务不属于同一影像")
    else:
        task = db.one("SELECT * FROM tasks WHERE image_id=? AND status='done'"
                      " ORDER BY created_at DESC LIMIT 1", (candidate["image_id"],))
        if task is None:
            raise errors.conflict("NO_TASK", "该影像还没有已完成的任务，无法写入候选")

    _ensure_draft(task["id"])
    next_id = db.one("SELECT COALESCE(MAX(detection_id),0)+1 AS nid FROM draft_detections WHERE task_id=?",
                     (task["id"],))["nid"]
    db.execute(
        "INSERT INTO draft_detections(task_id,detection_id,polygon,class_id,class_name,confidence,"
        "source_tile_id,review_state,origin,note,ai_candidate_id) VALUES(?,?,?,?,?,NULL,NULL,?,?,?,?)",
        (task["id"], next_id, candidate["polygon"], 1, "ship", "confirmed", "ai",
         f"AI 候选 {candidate_id}", candidate_id),
    )
    db.execute("UPDATE candidates SET status='accepted' WHERE id=?", (candidate_id,))
    db.execute("UPDATE draft_state SET updated_at=? WHERE task_id=?", (db.now(), task["id"]))
    db.log_event("candidate.accept", project_id=candidate["project_id"], user_id=state["user"]["id"],
                 target=candidate_id, detail={"task_id": task["id"], "detection_id": next_id})
    return _detection_payload(db.one(
        "SELECT * FROM draft_detections WHERE task_id=? AND detection_id=?", (task["id"], next_id)))


@router.post("/ai-candidates/{candidate_id}/cancel")
def cancel_candidate(candidate_id: str, state: dict = State) -> dict:
    candidate = db.one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if candidate is None:
        raise errors.not_found("CANDIDATE_NOT_FOUND", "候选不存在")
    _member(candidate["project_id"], state)
    if candidate["status"] != "preview":
        raise errors.conflict("CANDIDATE_CLOSED", "该候选已被处理")
    db.execute("UPDATE candidates SET status='cancelled' WHERE id=?", (candidate_id,))
    return {"id": candidate_id, "status": "cancelled", "polygon": json.loads(candidate["polygon"])}

