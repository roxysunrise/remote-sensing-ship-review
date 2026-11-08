"""端到端验证：按前端同一路径 POST /vision/detect，检查类别是否被过滤。

用法（项目根目录）：

    python -m scripts.verify_multiclass_http --dir <图片目录> [--conf 0.25]

修复前：非港口图返回 0 个框（后端固定 classes=[1]）。
修复后：默认输出模型全部类别，by_class 里能看到 tennis-court / small-vehicle / large-vehicle 等；
显式传 options.classes=[1] 时才退回只出船。
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SHIP_VAR_DIR", str(Path(os.environ.get("TEMP", "/tmp")) / "ship_e2e_var"))
os.environ.setdefault("SHIP_SEED_ON_START", "1")
os.environ.setdefault("SHIP_DEVICE", "cpu")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.server import app  # noqa: E402

MAX_EDGE = 1600  # 与 frontend/detect-config.js 的 video.maxPayloadEdge 一致


def payload_for(path: Path, conf: float) -> dict:
    with Image.open(path) as src:
        im = src.convert("RGB")
        width, height = im.size
        scale = min(1.0, MAX_EDGE / max(width, height))
        small = (im.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
                 if scale < 1 else im)
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=86)
        small_size = small.size
        if small is not im:
            small.close()
        im.close()
    return {
        "session_id": "ses_e2e",
        "kind": "image",
        "frame": {"index": 0, "time_ms": 0, "width": width, "height": height},
        "image": {"data_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
                  "width": small_size[0], "height": small_size[1]},
        "options": {"conf": conf, "max_det": 300},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", required=True, help="含待验证影像的目录（PNG/JPEG）")
    parser.add_argument("--conf", type=float, default=0.25)
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    images = sorted([p for p in directory.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]) \
        if directory.is_dir() else []
    if not images:
        print(f"[fail] {directory} 下没有 PNG/JPEG", file=sys.stderr)
        return 2

    client = TestClient(app)
    rows = []
    for path in images:
        resp = client.post("/vision/detect", json=payload_for(path, args.conf))
        if resp.status_code != 200:
            print(f"[fail] {path.name}: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
            return 1
        data = resp.json()
        first = data["detections"][0] if data["detections"] else {}
        rows.append({
            "image": path.name,
            "count": data["count"],
            "by_class": data.get("by_class"),
            "first": f'{first.get("label")}/{first.get("class_id")}' if first else "-",
            "classes": (data.get("model") or {}).get("classes"),
            "filtered": (data.get("model") or {}).get("classes_filtered"),
        })

    print(f"{'image':<24}{'count':>6}  by_class")
    for row in rows:
        detail = ", ".join(f"{k}={v}" for k, v in (row["by_class"] or {}).items()) or "-"
        print(f"{row['image']:<24}{row['count']:>6}  {detail}")
    print()
    print("默认 model.classes =", rows[0]["classes"], "| classes_filtered =", rows[0]["filtered"])

    non_ship = [r for r in rows if r["by_class"] and any(k != "ship" for k in r["by_class"])]
    print(f"出现非 ship 类别的图：{len(non_ship)}/{len(rows)}")

    # 反向验证：显式只请求 ship
    body = payload_for(images[0], args.conf)
    body["options"]["classes"] = [1]
    resp = client.post("/vision/detect", json=body)
    only_ship = resp.json()
    print(f"显式 classes=[1] 时 {images[0].name} count = {only_ship['count']}"
          f" | classes_filtered = {(only_ship.get('model') or {}).get('classes_filtered')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
