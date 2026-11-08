"""生成大图复核验证用的本地夹具（不提交影像、权重或数据库）。

做四件事：
    1. 在 `var/blobs/` 生成一张 1820×1020、四象限纯色的测试图（非 2 的幂尺寸，
       专门覆盖最后一列/行瓦片），并生成服务端预览；
    2. 写一条 `ready` 影像、一条 `done` 任务、v0 结果与四个 OBB 检测；
    3. 清掉该影像的瓦片缓存，确保下一次请求真实生成；
    4. 打印 JSON，浏览器验证脚本据此核对颜色和坐标。

用法：
    Set SHIP_VAR_DIR to a dedicated test directory before running.
    See docs/module-controls/workbench-tiles.md for browser verification.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config, db, imaging, storage, tiles  # noqa: E402

IMAGE_ID = "img_tile_fixture"
TASK_ID = "tsk_tile_fixture"
PROJECT_ID = "harbor-a"
WIDTH, HEIGHT = 1820, 1020

QUADRANTS = {
    "tl": {"rgb": [214, 48, 49], "probe": [300, 150]},
    "tr": {"rgb": [46, 174, 92], "probe": [1200, 150]},
    "bl": {"rgb": [52, 101, 214], "probe": [300, 760]},
    "br": {"rgb": [232, 196, 39], "probe": [1200, 760]},
}
MARKERS = {
    "tl": [455, 255], "tr": [1365, 255], "bl": [455, 765], "br": [1365, 765],
}
SHIP_W, SHIP_H = 120, 60


def make_image(path: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), (20, 24, 30))
    draw = ImageDraw.Draw(image)
    half_w, half_h = WIDTH // 2, HEIGHT // 2
    draw.rectangle([0, 0, half_w - 1, half_h - 1], fill=tuple(QUADRANTS["tl"]["rgb"]))
    draw.rectangle([half_w, 0, WIDTH - 1, half_h - 1], fill=tuple(QUADRANTS["tr"]["rgb"]))
    draw.rectangle([0, half_h, half_w - 1, HEIGHT - 1], fill=tuple(QUADRANTS["bl"]["rgb"]))
    draw.rectangle([half_w, half_h, WIDTH - 1, HEIGHT - 1], fill=tuple(QUADRANTS["br"]["rgb"]))
    # 每 256 px 一条浅色细线：便于肉眼确认瓦片接缝；检测框避开这些线。
    for x in range(256, WIDTH, 256):
        draw.line([(x, 0), (x, HEIGHT)], fill=(235, 235, 235), width=1)
    for y in range(256, HEIGHT, 256):
        draw.line([(0, y), (WIDTH, y)], fill=(235, 235, 235), width=1)
    for cx, cy in MARKERS.values():
        draw.rectangle([cx - 34, cy - 18, cx + 34, cy + 18], fill=(245, 245, 245),
                       outline=(10, 10, 10), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    image.close()


def polygon_for(cx: int, cy: int) -> list[list[float]]:
    left, top = cx - SHIP_W / 2, cy - SHIP_H / 2
    right, bottom = cx + SHIP_W / 2, cy + SHIP_H / 2
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def detections() -> list[dict]:
    items = []
    for index, (key, (cx, cy)) in enumerate(MARKERS.items(), 1):
        items.append({
            "id": index,
            "polygon": polygon_for(cx, cy),
            "class_id": 1,
            "class_name": "ship",
            "confidence": round(0.95 - index * 0.03, 2),
            "source_tile_id": 1,
            "review_state": "pending",
            "origin": "model",
            "note": None,
        })
    return items


def main() -> int:
    if not os.environ.get("SHIP_VAR_DIR") or os.environ.get("SHIP_DB_PATH"):
        raise SystemExit("Set SHIP_VAR_DIR to a dedicated test directory and unset SHIP_DB_PATH")
    config.ensure_dirs()
    # 默认口令与后端 SEED_PASSWORD 一致；不打印口令。
    db.init_db(seed=True)
    stored_name = "tile-fixture.png"
    blob = storage.blob_path(stored_name)
    make_image(blob)
    sha = storage.sha256_file(blob)
    size = blob.stat().st_size
    preview = storage.preview_path(IMAGE_ID)
    imaging.make_preview(blob, preview)

    created = db.now()
    dets = detections()
    row = {"id": IMAGE_ID, "width": WIDTH, "height": HEIGHT, "stored_name": stored_name,
           "sha256": sha}
    plan = tiles.plan_inference_tiles(row, config.DEFAULT_PARAMETERS["tile_size"],
                                      config.DEFAULT_PARAMETERS["stride"])
    parameters = dict(config.DEFAULT_PARAMETERS)
    parameters["classes"] = [1]

    result = {
        "schema_version": "ship-detection/0.2",
        "created_at": created,
        "image": {"file_name": "tile-fixture.png", "sha256": sha,
                  "width": WIDTH, "height": HEIGHT, "mode": "RGB"},
        "model": {"backend": "fixture", "task": "obb", "weights_file": "fixture",
                  "architecture": "Fixture"},
        "parameters": parameters,
        "coordinates": {"unit": "pixel", "origin": "top-left", "x_axis": "right",
                        "y_axis": "down", "polygon_order": "clockwise; start=min(y,x)",
                        "space": "original_image", "clipped": False},
        "tiling": dict(plan, candidate_count=len(dets), suppressed_count=0,
                       merge_method="fixture"),
        "count": len(dets),
        "detections": [{key: value for key, value in det.items()
                        if key not in {"review_state", "origin", "note"}} for det in dets],
    }
    result_path = storage.result_path(TASK_ID, 1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    version_detections = dets
    counts = {
        "total": len(dets),
        "pending": len(dets),
        "confirmed": 0, "modified": 0, "deleted": 0, "added": 0,
    }

    with db.tx() as conn:
        conn.execute("DELETE FROM draft_detections WHERE task_id=?", (TASK_ID,))
        conn.execute("DELETE FROM draft_state WHERE task_id=?", (TASK_ID,))
        conn.execute("DELETE FROM regions WHERE task_id=?", (TASK_ID,))
        conn.execute("DELETE FROM versions WHERE task_id=?", (TASK_ID,))
        conn.execute("DELETE FROM tasks WHERE id=?", (TASK_ID,))
        conn.execute("DELETE FROM images WHERE id=?", (IMAGE_ID,))
        conn.execute(
            "INSERT INTO images(id,project_id,upload_id,file_name,stored_name,sha256,width,height,"
            "mode,bytes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (IMAGE_ID, PROJECT_ID, None, "tile-fixture.png", stored_name, sha, WIDTH, HEIGHT,
             "RGB", size, "ready", created),
        )
        conn.execute(
            "INSERT INTO tasks(id,project_id,image_id,created_by,status,attempt,tiles_total,"
            "tiles_done,temp_count,final_count,parameters,cancel_requested,error,result_path,"
            "created_at,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (TASK_ID, PROJECT_ID, IMAGE_ID, "usr_owner", "done", 1, plan["tile_count"],
             plan["tile_count"], len(dets), len(dets), json.dumps(parameters),
             0, None, str(result_path), created, created, created),
        )
        conn.execute(
            "INSERT INTO versions(task_id,label,project_id,author,created_at,readonly,summary,"
            "counts,detections) VALUES(?,?,?,?,?,?,?,?,?)",
            (TASK_ID, "v0", PROJECT_ID, "模型", created, 1,
             json.dumps({"added": 0, "removed": 0, "changed": 0, "confirmed": 0, "deleted": 0}),
             json.dumps(counts, ensure_ascii=False),
             json.dumps(version_detections, ensure_ascii=False)),
        )

    tiles.purge_tiles(IMAGE_ID)
    payload = {
        "image_id": IMAGE_ID, "task_id": TASK_ID, "project_id": PROJECT_ID,
        "width": WIDTH, "height": HEIGHT, "sha256": sha,
        "quadrants": QUADRANTS, "markers": MARKERS,
        "ship_size": [SHIP_W, SHIP_H],
        "detections": len(dets), "inference_tiles": plan["tile_count"],
        "seed_user": os.environ.get("SHIP_DEMO_USER", "roxysunrise"),
        "seed_project": PROJECT_ID,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
