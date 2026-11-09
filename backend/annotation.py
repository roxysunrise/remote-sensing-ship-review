"""Human annotation state independent from the detector's task state."""
from __future__ import annotations

import json
from functools import wraps

from . import config, db, errors


def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with db.serialized():
            return fn(*args, **kwargs)
    return wrapped


def atomic(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with db.atomic_write():
            return fn(*args, **kwargs)
    return wrapped


def get(task_id):
    return db.one("SELECT * FROM annotation_tasks WHERE task_id=?", (task_id,))


def guard(task_id, state, body):
    row = get(task_id)
    if row is None:
        return None
    if row["status"] != "active" or row["assignee"] != state["user"]["id"]:
        raise errors.conflict("ANNOTATION_READ_ONLY", "只有当前标注者在进行中可以修改草稿")
    if type(body.get("revision")) is not int or body["revision"] != row["revision"]:
        raise errors.conflict("STALE_DRAFT", "草稿版本已变化，请重新打开任务")
    return row


def changed(row, payload):
    if row:
        revision = row["revision"] + 1
        db.execute("UPDATE annotation_tasks SET revision=? WHERE task_id=?", (revision, row["task_id"]))
        payload["annotation_revision"] = revision
    return payload


def listing(project_id, user_id, owner):
    rows = db.query("""
        SELECT a.*, i.file_name, i.width, i.height, t.final_count,
        (SELECT COUNT(*) FROM draft_detections d WHERE d.task_id=a.task_id
          AND d.confidence < 0.45 AND d.review_state='pending') AS low_pending
        FROM annotation_tasks a JOIN tasks t ON t.id=a.task_id JOIN images i ON i.id=t.image_id
        WHERE a.project_id=? AND (? OR a.status='available' OR a.assignee=?)
        ORDER BY a.published_at,a.task_id
    """, (project_id, owner, user_id))
    current = db.one("SELECT task_id FROM annotation_tasks WHERE assignee=? AND status IN ('claimed','active')",
                     (user_id,))
    return {"items": [dict(row) for row in rows], "current_task_id": current["task_id"] if current else None}


def transition(task, state, role, action, body, draft, snapshot):
    task_id = task["id"]
    user = state["user"]["id"]
    owner = role == config.USER_ROLE_OWNER
    with db.tx() as conn:
        # Acquire the SQLite write lock before re-reading state, including across processes.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM annotation_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            if not owner or action not in {"publish", "assign"}:
                raise errors.not_found("ANNOTATION_NOT_FOUND", "标注任务尚未发布")
            if task["status"] != "done":
                raise errors.conflict("RESULT_NOT_READY", "检测完成后才能发布")
            status, assignee, version, reason, target = "available", None, None, None, None
            conn.execute("INSERT INTO annotation_tasks(task_id,project_id,status,published_at) VALUES(?,?,?,?)",
                         (task_id, task["project_id"], status, db.now()))
        else:
            status, assignee, version, reason, target = (row[k] for k in
                                                         ("status", "assignee", "version_id", "reason", "target_id"))
        manager_actions = {"publish", "assign", "accept", "return", "resolve_release"}
        if action in manager_actions and not owner:
            raise errors.forbidden("只有项目负责人可以执行此操作")
        if action not in manager_actions and owner:
            raise errors.forbidden("此操作仅限标注者")
        if action in {"claim", "start"}:
            occupied = conn.execute("SELECT task_id FROM annotation_tasks WHERE assignee=? AND status IN ('claimed','active')",
                                    (user,)).fetchone()
            if occupied and occupied["task_id"] != task_id:
                raise errors.conflict("ANNOTATION_BUSY", "请先提交或暂停当前任务")
        if action == "publish" and status == "available":
            pass
        elif action == "assign" and status in {"available", "assigned", "paused", "returned"}:
            assignee = body.get("assignee")
            member = conn.execute("SELECT m.role FROM members m JOIN users u ON u.id=m.user_id "
                                  "WHERE m.project_id=? AND m.user_id=? AND u.status='active'",
                                  (task["project_id"], assignee)).fetchone()
            if not member or member["role"] != config.USER_ROLE_ANALYST:
                raise errors.invalid("INVALID_ASSIGNEE", "请选择项目中的有效标注者")
            status = "assigned"
        elif action == "claim" and status == "available":
            status, assignee = "claimed", user
        elif action == "start" and assignee == user and status in {"assigned", "claimed", "paused", "returned"}:
            status = "active"
        elif action == "release" and assignee == user and status == "claimed":
            status, assignee = "available", None
        elif action == "pause" and assignee == user and status == "active":
            status = "paused"
        elif action == "request_release" and assignee == user and status in {"active", "paused"}:
            reason = str(body.get("reason") or "").strip()
            if not reason or len(reason) > 500:
                raise errors.invalid("INVALID_REASON", "请填写不超过 500 字的交还原因")
            status = "release_requested"
        elif action == "resolve_release" and status == "release_requested":
            status, assignee, reason, target = "available", None, None, None
        elif action == "submit" and assignee == user and status == "active":
            if type(body.get("revision")) is not int or body["revision"] != row["revision"]:
                raise errors.conflict("STALE_DRAFT", "草稿版本已变化，请重新打开任务")
            detections = draft(task_id)
            if any(det["review_state"] == "pending" for det in detections):
                raise errors.conflict("ANNOTATION_PENDING", "仍有未处理的目标")
            version = snapshot(conn, task, state, detections)
            status, reason, target = "submitted", None, None
        elif action == "accept" and status == "submitted":
            status = "signed"
        elif action == "return" and status == "submitted":
            reason = str(body.get("reason") or "").strip()
            if not reason or len(reason) > 500:
                raise errors.invalid("INVALID_REASON", "请填写不超过 500 字的退回原因")
            target = body.get("target_id")
            if target is not None:
                if type(target) is not int or not any(det["id"] == target for det in draft(task_id)):
                    raise errors.invalid("INVALID_DETECTION", "问题框不属于本任务")
                conn.execute("UPDATE draft_detections SET review_state='pending' WHERE task_id=? AND detection_id=?",
                             (task_id, target))
            status = "returned"
        else:
            raise errors.conflict("ANNOTATION_STATE", "任务状态已变化或任务不属于你，请刷新")
        conn.execute("""UPDATE annotation_tasks SET status=?,assignee=?,version_id=?,reason=?,target_id=?,
                     revision=revision+1 WHERE task_id=?""", (status, assignee, version, reason, target, task_id))
        conn.execute("INSERT INTO events(project_id,user_id,action,target,detail,created_at) VALUES(?,?,?,?,?,?)",
                     (task["project_id"], user, "annotation." + action, task_id,
                      json.dumps({"status": status, "assignee": assignee, "version": version,
                                  "reason": reason, "target_id": target}, ensure_ascii=False), db.now()))
    return dict(get(task_id))
