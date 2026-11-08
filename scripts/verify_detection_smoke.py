"""Real-model smoke checks; requires prepared local samples and trusted weights."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from algorithm.ship_detector import ShipDetector, save_result


def overlap(a, b):
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    intersection = cv2.intersectConvexConvex(a, b)[0]
    union = abs(cv2.contourArea(a)) + abs(cv2.contourArea(b)) - intersection
    return intersection / union if union > 0 else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    detector = ShipDetector(device=args.device)
    report = {"device": args.device, "model": detector.metadata, "samples": {}}
    for name in ("dense", "small", "empty", "dense_nonsquare"):
        suffix = ".jpg" if name == "dense_nonsquare" else ".png"
        image = Path("datasets/smoke") / (name + suffix)
        started = time.perf_counter()
        result = detector.predict(image, imgsz=640 if name == "dense_nonsquare" else 1024)
        save_result(result, image, Path("outputs/smoke") / name)
        assert result["count"] == len(result["detections"])
        assert [d["id"] for d in result["detections"]] == list(range(1, result["count"] + 1))
        width, height = result["image"]["width"], result["image"]["height"]
        # Boxes are deliberately not clipped; their centers should lie within the image.
        for detection in result["detections"]:
            points = np.asarray(detection["polygon"])
            assert np.isfinite(points).all() and points.shape == (4, 2)
            assert 0 <= points[:, 0].mean() <= width and 0 <= points[:, 1].mean() <= height
            assert detection["class_name"] == "ship" and 0 <= detection["confidence"] <= 1
        record = {"count": result["count"], "size": [width, height],
                  "parameters": result["parameters"],
                  "end_to_end_ms": (time.perf_counter() - started) * 1000,
                  "timing_ms": result["timing_ms"]}
        if name in {"dense", "small"}:
            ground_truth = []
            for line in image.with_suffix(".txt").read_text(encoding="utf-8-sig").splitlines():
                fields = line.split()
                if len(fields) >= 10 and fields[8] == "ship":
                    ground_truth.append(np.array(list(map(float, fields[:8]))).reshape(4, 2))
            overlaps = [max((overlap(d["polygon"], g) for g in ground_truth), default=0) for d in result["detections"]]
            record["annotated_ship_count"] = len(ground_truth)
            record["predictions_with_gt_iou_ge_0_5"] = sum(v >= 0.5 for v in overlaps)
        if name == "dense":
            assert result["count"] > 0
            assert record["predictions_with_gt_iou_ge_0_5"] >= 5, "Position/orientation mismatch against source annotations"
        if name == "dense_nonsquare":
            assert width != height and result["count"] > 0
            # Compare to original annotations, independently cropped by the sample script.
            cropped_gt = [g - np.array([0, height]) for g in dense_gt]
            record["predictions_with_gt_iou_ge_0_5"] = sum(any(overlap(d["polygon"], g) >= 0.5 for g in cropped_gt) for d in result["detections"])
            assert record["predictions_with_gt_iou_ge_0_5"] >= 5, "Letterbox restoration mismatch"
        if name == "dense":
            dense_gt = ground_truth
        report["samples"][name] = record
    # Repeat on the same model for a warm measurement, after the first-call setup.
    warm = detector.predict("datasets/smoke/dense.png")
    report["dense_warm_predict_ms"] = warm["timing_ms"]["predict_call"]
    report["status"] = "PASS"
    path = Path("outputs/smoke/verification.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
