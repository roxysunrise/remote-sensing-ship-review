"""Real-model tiled smoke checks. Composites validate flow, NOT detection accuracy."""
import argparse
import json
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import cv2
import numpy as np
from algorithm.ship_detector import ShipDetector, save_result, sha256
from algorithm.tiled_inference import polygon_iou


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=Path("datasets/smoke"))
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output", type=Path, default=Path("outputs/tiled-smoke"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    composite = args.output / "composite.png"
    with Image.new("RGB", (2305, 1537), (30, 40, 50)) as canvas:
        with Image.open(args.samples / "dense.png") as dense:
            canvas.paste(dense, (600, 300))
        canvas.save(composite)
    # This blank image exceeds the OLD 16,777,216 pixel limit.
    large_blank = args.output / "large-blank.png"
    with Image.new("RGB", (4097, 4097), (0, 0, 0)) as canvas:
        canvas.save(large_blank)
    detector = ShipDetector(args.weights, args.device)
    report = {"date": "2026-10-29", "python": platform.python_version(),
              "opencv": cv2.__version__, "numpy": np.__version__, "model": detector.metadata,
              "evidence_scope": "Real model, source-derived composite and synthetic blank; not independent accuracy evaluation",
              "samples": {}}
    original = detector.predict(args.samples / "dense.png")
    same = detector.predict_large(args.samples / "dense.png", merge_iou=1)
    assert same["tiling"]["tile_count"] == 1
    assert same["count"] == original["count"]
    equivalent_ious = [polygon_iou(a["polygon"], b["polygon"]) for a, b in
                       zip(sorted(original["detections"], key=lambda d: -d["confidence"]), same["detections"])]
    assert equivalent_ious and min(equivalent_ious) > .999
    report["single_tile_equivalence"] = {"count": same["count"], "minimum_polygon_iou": min(equivalent_ious),
                                         "note": "merge_iou=1 disables extra suppression"}
    gt = []
    for line in (args.samples / "dense.txt").read_text(encoding="utf-8-sig").splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[8] == "ship":
            gt.append((np.asarray(list(map(float, fields[:8]))).reshape(4, 2) + [600, 300]).tolist())
    for name, image, options in (
        ("composite", composite, {}),
        ("empty", args.samples / "empty.png", {"tile_size": 640, "stride": 320, "imgsz": 640}),
        ("nonsquare", args.samples / "dense_nonsquare.jpg", {"tile_size": 640, "stride": 320, "imgsz": 640}),
        ("large_blank", large_blank, {"tile_size": 2048, "stride": 2048, "imgsz": 320}),
    ):
        result = detector.predict_large(image, **options)
        save_result(result, image, args.output / name)
        assert result["count"] == len(result["detections"])
        assert result["tiling"]["candidate_count"] - result["tiling"]["suppressed_count"] == result["count"]
        for d in result["detections"]:
            assert np.isfinite(d["polygon"]).all()
            assert np.asarray(d["polygon"]).shape == (4, 2)
        for i, d in enumerate(result["detections"]):
            for e in result["detections"][i + 1:]:
                if d["class_id"] == e["class_id"]:
                    assert polygon_iou(d["polygon"], e["polygon"]) <= result["parameters"]["merge_iou"] + 1e-6
        record = {"size": [result["image"]["width"], result["image"]["height"]],
                  "image_sha256": result["image"]["sha256"], "count": result["count"],
                  "tile_count": result["tiling"]["tile_count"],
                  "candidates": result["tiling"]["candidate_count"],
                  "suppressed": result["tiling"]["suppressed_count"],
                  "parameters": result["parameters"], "timing_ms": result["timing_ms"],
                  "warnings": result["warnings"]}
        if name == "composite":
            matches = sum(any(polygon_iou(d["polygon"], g) >= .5 for g in gt) for d in result["detections"])
            record["predictions_with_translated_gt_iou_ge_0_5"] = matches
            assert matches >= 5, "Original-image offset/orientation check failed"
            assert result["tiling"]["suppressed_count"] > 0, "Cross-tile duplicate scenario not exercised"
            full = detector.predict(image)
            save_result(full, image, args.output / "composite-full")
            record["full_image_count"] = full["count"]
            record["annotated_ship_count"] = len(gt)
            record["full_predictions_with_translated_gt_iou_ge_0_5"] = sum(
                any(polygon_iou(d["polygon"], g) >= .5 for g in gt) for d in full["detections"])
            record["full_image_predict_ms"] = full["timing_ms"]["predict_call"]
        if name == "large_blank":
            assert result["count"] == 0 and result["tiling"]["tile_count"] == 9
        if name == "nonsquare":
            assert result["count"] > 0
        report["samples"][name] = record
    report["source_sha256"] = {str(p).replace("\\", "/"): sha256(p) for p in
                                (Path("algorithm/ship_detector.py"), Path("algorithm/tiled_inference.py"),
                                 Path("algorithm/errors.py"), Path("scripts/verify_tiled_smoke.py"))}
    report["status"] = "PASS"
    (args.output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
