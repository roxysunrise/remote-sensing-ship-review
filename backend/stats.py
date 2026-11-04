"""业务统计聚合。

**只统计业务库里真实存在的记录**，不产生任何推测性指标：

- 运行统计（本模块）：影像/上传/任务/复核/版本/AI 采纳/区域，全部可从表里回溯；
- 算法质量（AP/Recall/边界误差）：需要独立标注集，**不在这里算**，
  由 `eval-harness` 产出报告后单独展示，避免把"检出 32 个框"当成"准确率"。

口径说明（会写进接口文档，避免误读）：
- `count`（结果里的）是模型候选数，不等于人工确认数；
- `effective` 是非删除的当前有效数；
- `accept_rate` = accepted / (accepted + rejected + preview) 之外的已处理候选；
- 耗时分位数只统计已完成且有起止时间的任务。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import db

REVIEW_STATES = ("pending", "confirmed", "modified", "deleted")
ORIGINS = ("model", "manual", "ai")


def default_range(days: int = 30) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def _percent(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _pct(values: list[float], ratio: float) -> float | None:
    """分位数（最近秩法）。数据不足时返回 None，不编造。"""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _counts(rows, key: str, keys: tuple[str, ...]) -> dict:
    result = {k: 0 for k in keys}
    for row in rows:
        value = row[key]
        if value in result:
            result[value] += 1
    return result


def project_stats(project_id: str, start: str, end: str) -> dict:
    uploads = db.query("SELECT status, created_at FROM uploads WHERE project_id=?"
                       " AND created_at BETWEEN ? AND ?", (project_id, start, end))
    images = db.query("SELECT id, status, width, height, bytes FROM images WHERE project_id=?"
                      " AND created_at BETWEEN ? AND ?", (project_id, start, end))
    tasks = db.query("SELECT * FROM tasks WHERE project_id=? AND created_at BETWEEN ? AND ?",
                     (project_id, start, end))
    task_ids = [row["id"] for row in tasks]
    placeholders = ",".join("?" * len(task_ids)) if task_ids else None

    detections = []
    versions = []
    if placeholders:
        detections = db.query(
            f"SELECT review_state, origin, confidence FROM draft_detections"
            f" WHERE task_id IN ({placeholders})", task_ids)
        versions = db.query(
            f"SELECT task_id, label, readonly FROM versions WHERE task_id IN ({placeholders})",
            task_ids)

    candidates = db.query(
        "SELECT status, source, quality FROM candidates WHERE project_id=?"
        " AND created_at BETWEEN ? AND ?", (project_id, start, end))
    regions = db.query("SELECT id FROM regions WHERE project_id=?", (project_id,))

    durations = []
    for row in tasks:
        if row["started_at"] and row["finished_at"]:
            try:
                began = datetime.fromisoformat(row["started_at"])
                ended = datetime.fromisoformat(row["finished_at"])
            except ValueError:
                continue
            durations.append((ended - began).total_seconds() * 1000)

    task_status = _counts(tasks, "status", ("queued", "running", "merging", "done", "failed", "cancelled"))
    upload_status = _counts(uploads, "status",
                            ("uploading", "verifying", "ready", "failed", "expired"))
    review = _counts(detections, "review_state", REVIEW_STATES)
    origin = _counts(detections, "origin", ORIGINS)

    accepted = sum(1 for row in candidates if row["status"] == "accepted")
    rejected = sum(1 for row in candidates if row["status"] == "cancelled")
    preview = sum(1 for row in candidates if row["status"] == "preview")
    handled = accepted + rejected

    by_source: dict[str, dict] = {}
    for row in candidates:
        bucket = by_source.setdefault(row["source"] or "unknown",
                                      {"proposed": 0, "accepted": 0, "rejected": 0})
        bucket["proposed"] += 1
        if row["status"] == "accepted":
            bucket["accepted"] += 1
        elif row["status"] == "cancelled":
            bucket["rejected"] += 1
    for bucket in by_source.values():
        bucket["accept_rate"] = _percent(bucket["accepted"], bucket["accepted"] + bucket["rejected"])

    filled = sum(1 for row in images if row["status"] == "ready")
    return {
        "range": {"from": start, "to": end},
        "images": {
            "total": len(images),
            "ready": filled,
            "pixels_total": sum(int((row["width"] or 0) * (row["height"] or 0)) for row in images),
            "bytes_total": sum(int(row["bytes"] or 0) for row in images),
        },
        "uploads": {
            **upload_status,
            "total": len(uploads),
            "success_rate": _percent(upload_status["ready"], len(uploads)),
        },
        "tasks": {
            **task_status,
            "total": len(tasks),
            "success_rate": _percent(task_status["done"], len(tasks)),
            "tiles_total": sum(int(row["tiles_total"] or 0) for row in tasks),
            "tiles_done": sum(int(row["tiles_done"] or 0) for row in tasks),
            "detections_total": sum(int(row["final_count"] or 0) for row in tasks),
            "duration_ms": {"p50": _pct(durations, 0.5), "p95": _pct(durations, 0.95),
                            "samples": len(durations)},
        },
        "review": {
            **review,
            "detections": len(detections),
            "by_origin": origin,
            "completion_rate": _percent(review["confirmed"] + review["modified"] + review["deleted"],
                                        len(detections)),
            "note": "count 是模型候选数；completion_rate 只反映复核动作，不代表整图已审核",
        },
        "versions": {
            "total": len(versions),
            "readonly": sum(1 for row in versions if row["readonly"]),
            "avg_per_task": _percent(len(versions), len(tasks)),
        },
        "ai": {
            "proposed": len(candidates),
            "accepted": accepted,
            "rejected": rejected,
            "pending": preview,
            "accept_rate": _percent(accepted, handled),
            "by_source": by_source,
            "note": "accept_rate 的分母是已处理候选（接受+取消），未处理的不计入",
        },
        "regions": {"count": len(regions)},
    }


def task_stats(task_row) -> dict:
    task_id = task_row["id"]
    detections = db.query(
        "SELECT review_state, origin, confidence FROM draft_detections WHERE task_id=?", (task_id,))
    versions = db.query("SELECT label, readonly, created_at FROM versions WHERE task_id=?"
                        " ORDER BY CAST(SUBSTR(label,2) AS INTEGER)", (task_id,))
    candidates = db.query("SELECT status, source FROM candidates WHERE project_id=?"
                          " AND image_id=?", (task_row["project_id"], task_row["image_id"]))
    review = _counts(detections, "review_state", REVIEW_STATES)
    origin = _counts(detections, "origin", ORIGINS)

    result_timing = {}
    warnings: list[str] = []
    if task_row["result_path"]:
        import json
        from pathlib import Path

        path = Path(task_row["result_path"])
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            result_timing = payload.get("timing_ms") or {}
            warnings = payload.get("warnings") or []

    duration_ms = None
    if task_row["started_at"] and task_row["finished_at"]:
        try:
            duration_ms = round((datetime.fromisoformat(task_row["finished_at"])
                                 - datetime.fromisoformat(task_row["started_at"])).total_seconds() * 1000, 1)
        except ValueError:
            duration_ms = None

    confidences = [row["confidence"] for row in detections if row["confidence"] is not None]
    return {
        "task": {
            "id": task_id, "status": task_row["status"], "attempt": task_row["attempt"],
            "image_id": task_row["image_id"], "parameters": __import__("json").loads(task_row["parameters"]),
            "cancel_requested": bool(task_row["cancel_requested"]), "error": task_row["error"],
        },
        "progress": {
            "tiles_total": task_row["tiles_total"], "tiles_done": task_row["tiles_done"],
            "temp_count": task_row["temp_count"], "final_count": task_row["final_count"],
        },
        "timing": {
            "duration_ms": duration_ms,
            "predict_call_ms": result_timing.get("predict_call"),
            "merge_ms": result_timing.get("merge"),
            "decode_ms": result_timing.get("decode"),
        },
        "detections": {
            "total": len(detections),
            "by_review_state": review,
            "by_origin": origin,
            "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        },
        "review": {
            "completion_rate": _percent(review["confirmed"] + review["modified"] + review["deleted"],
                                        len(detections)),
            "pending": review["pending"],
        },
        "versions": {
            "labels": [row["label"] for row in versions],
            "latest": versions[-1]["label"] if versions else None,
        },
        "ai": {
            "proposed": len(candidates),
            "accepted": sum(1 for row in candidates if row["status"] == "accepted"),
        },
        "warnings": warnings,
    }
