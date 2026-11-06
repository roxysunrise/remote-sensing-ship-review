"""多类检测对比验证。

同一张影像分别按「模型全部类别」和「仅 ship」推理，输出逐类计数，用来确认
`/vision/detect` 之类入口没有把类别过滤掉。

用法（项目根目录）：

    python -m scripts.verify_multiclass --images a.png b.jpg --device cpu
    python -m scripts.verify_multiclass --images a.png --conf 0.15 --save-dir outputs/multiclass

参数：
    --images     要验证的影像（PNG/JPEG，RGB）
    --weights    权重路径，默认 models/yolov8l-obb-dota-v1-80e/best.pt
    --device     cpu 或 CUDA 序号
    --conf       置信度阈值（默认 0.25）
    --imgsz      覆盖网络输入尺寸（默认按图最大边对齐，上限 2048，与 /vision/detect 一致）
    --save-dir   可选：为每张图保存预览与 result.json
    --json       以 JSON 打印结果（便于脚本消费）

退出码：0 正常；2 输入/环境错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algorithm.errors import DetectionError  # noqa: E402
from algorithm.ship_detector import ShipDetector, save_result  # noqa: E402


def align(value: int, low: int = 32, high: int = 2048) -> int:
    import math
    return max(low, min(high, int(math.ceil(value / 32.0) * 32)))


def count_by_class(result: dict) -> dict:
    counts: dict[str, int] = {}
    for det in result["detections"]:
        name = det.get("class_name") or "object"
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images", nargs="+", required=True)
    parser.add_argument("--weights", type=Path, default=ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--imgsz", type=int, default=0, help="0 表示按图最大边对齐（与 /vision/detect 一致）")
    parser.add_argument("--max-det", type=int, default=300)
    parser.add_argument("--save-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = []
    try:
        detector = ShipDetector(args.weights, args.device)
        for image_arg in args.images:
            path = Path(image_arg)
            from algorithm.ship_detector import read_image
            probe = read_image(path)
            try:
                width, height = probe.size
            finally:
                probe.close()
            imgsz = args.imgsz or align(max(width, height))

            all_classes = detector.predict(path, conf=args.conf, iou=args.iou,
                                           imgsz=imgsz, max_det=args.max_det, classes=None)
            ship_only = detector.predict(path, conf=args.conf, iou=args.iou,
                                         imgsz=imgsz, max_det=args.max_det, classes=detector.ship_ids or [1])
            row = {
                "image": path.name,
                "size": f"{width}x{height}",
                "imgsz": imgsz,
                "conf": args.conf,
                "all_count": all_classes["count"],
                "all_by_class": count_by_class(all_classes),
                "ship_only_count": ship_only["count"],
                "ship_ids": detector.ship_ids,
            }
            if args.save_dir:
                target = args.save_dir / path.stem
                paths = save_result(all_classes, path, target)
                row["preview"] = paths["preview"]
                row["result"] = paths["result"]
            rows.append(row)
    except DetectionError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    except (ImportError, OSError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "ENVIRONMENT_OR_IO", "message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": True, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"{'image':<22}{'size':<14}{'imgsz':<7}{'all':>5}{'ship-only':>11}  by_class (all)")
        for row in rows:
            detail = ", ".join(f"{k}={v}" for k, v in row["all_by_class"].items()) or "-"
            print(f"{row['image']:<22}{row['size']:<14}{row['imgsz']:<7}"
                  f"{row['all_count']:>5}{row['ship_only_count']:>11}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
