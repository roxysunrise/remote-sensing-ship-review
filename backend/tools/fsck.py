"""数据库与文件对账（fsck）。

用途：验证"数据库记录 ↔ 私有文件"是否一致，对应文档里要求的
「文件写入与数据库不能假定共用事务，需要存在性校验、清理和补偿」。

用法：
    python -m backend.tools.fsck                # 只报告
    python -m backend.tools.fsck --delete       # 删除孤立文件/失效记录
    python -m backend.tools.fsck --json         # 机器可读输出

判定：
- 缺失：数据库有记录但文件不存在 —— 需要恢复或重新上传，**不自动删记录**；
- 孤立：文件存在但数据库无引用 —— 可安全删除（`--delete` 时执行）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config, db, storage  # noqa: E402


def scan() -> dict:
    db.init_db(seed=False)
    images = db.query("SELECT id, stored_name FROM images")
    tasks = db.query("SELECT id, attempt, result_path FROM tasks WHERE result_path IS NOT NULL")
    uploads = db.query("SELECT id FROM uploads")

    known_blobs = {row["stored_name"]: row["id"] for row in images}
    known_results = {Path(row["result_path"]).name: row["id"] for row in tasks if row["result_path"]}
    known_chunks = {row["id"] for row in uploads}

    missing_blobs = [iid for name, iid in known_blobs.items()
                     if not storage.blob_path(name).is_file()]
    missing_previews = [row["id"] for row in images
                        if not storage.preview_path(row["id"]).is_file()]
    missing_results = [tid for name, tid in known_results.items()
                       if not (config.RESULT_DIR / name).is_file()]

    orphan_blobs = [p.name for p in config.BLOB_DIR.glob("*")
                    if p.is_file() and p.name not in known_blobs]
    orphan_previews = [p.stem for p in config.PREVIEW_DIR.glob("*")
                       if p.is_file() and p.stem not in {row["id"] for row in images}]
    orphan_results = [p.name for p in config.RESULT_DIR.glob("*.json")
                      if p.name not in known_results]
    orphan_chunks = [p.name for p in config.CHUNK_DIR.glob("*")
                     if p.is_dir() and p.name not in known_chunks]

    return {
        "missing": {"blobs": missing_blobs, "previews": missing_previews,
                    "results": missing_results},
        "orphaned": {"blobs": orphan_blobs, "previews": orphan_previews,
                     "results": orphan_results, "chunks": orphan_chunks},
    }


def cleanup(report: dict) -> dict:
    removed = {"blobs": 0, "previews": 0, "results": 0, "chunks": 0}
    for name in report["orphaned"]["blobs"]:
        (config.BLOB_DIR / name).unlink(missing_ok=True)
        removed["blobs"] += 1
    for stem in report["orphaned"]["previews"]:
        (config.PREVIEW_DIR / f"{stem}.jpg").unlink(missing_ok=True)
        removed["previews"] += 1
    for name in report["orphaned"]["results"]:
        (config.RESULT_DIR / name).unlink(missing_ok=True)
        removed["results"] += 1
    for name in report["orphaned"]["chunks"]:
        import shutil
        shutil.rmtree(config.CHUNK_DIR / name, ignore_errors=True)
        removed["chunks"] += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="删除孤立文件（不动数据库记录）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = scan()
    problems = sum(len(v) for section in report.values() for v in section.values())
    if args.delete:
        report["removed"] = cleanup(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== 缺失（数据库有记录、文件不在）==")
        for kind, items in report["missing"].items():
            print(f"  {kind}: {len(items)}", items[:5] if items else "")
        print("== 孤立（文件在、数据库无引用）==")
        for kind, items in report["orphaned"].items():
            print(f"  {kind}: {len(items)}", items[:5] if items else "")
        if args.delete:
            print("== 已清理 ==", report.get("removed"))
        print(f"\n合计问题：{problems}")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
