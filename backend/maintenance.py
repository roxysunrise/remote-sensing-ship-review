"""启动/周期性维护：清理过期上传、旧尝试产物与残留临时文件。

原则：只删除**可重建**的东西（分片、临时文件、被新尝试取代的旧结果）。
业务记录一律保留，过期上传标记为 `expired` 而不是直接删行，便于追溯。
"""
from __future__ import annotations

import time

from . import config, db, storage


def purge_expired_uploads() -> int:
    rows = db.query(
        "SELECT id FROM uploads WHERE status IN ('uploading','verifying','failed') AND expires_at < ?",
        (db.now(),),
    )
    store = storage.UploadStore()
    for row in rows:
        store.discard(row["id"])
        db.execute("UPDATE uploads SET status='expired', stage='expired', error=?"
                   " WHERE id=?", ("EXPIRED: 上传会话已过期", row["id"]))
    return len(rows)


def prune_result_files() -> int:
    """每个任务只保留当前 attempt 的结果文件，其余删除（可重算，不算业务数据）。"""
    removed = 0
    for task in db.query("SELECT id, attempt FROM tasks WHERE result_path IS NOT NULL"):
        keep = f"_a{task['attempt']}.json"
        for path in config.RESULT_DIR.glob(f"{task['id']}_a*.json"):
            if not path.name.endswith(keep):
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def purge_temp_files(max_age_hours: int = 6) -> int:
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in config.TMP_DIR.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def run_startup_maintenance() -> dict:
    summary = {
        "expired_uploads": purge_expired_uploads(),
        "pruned_results": prune_result_files(),
        "temp_files": purge_temp_files(),
    }
    return summary
