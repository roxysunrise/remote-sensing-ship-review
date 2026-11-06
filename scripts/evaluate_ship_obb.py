"""Compare whole-image and tiled ship detection on an annotated split."""
import argparse
import json
import math
from pathlib import Path
import sys
from time import perf_counter

import cv2
import numpy as np

from algorithm.ship_detector import (
    MAX_LARGE_PIXELS, DetectionError, ShipDetector, canonical_polygon, read_image, sha256,
)
from algorithm.tiled_inference import polygon_iou, validate_tiling


def match_detections(detections, labels, threshold):
    """Use confidence-ordered, one-to-one matching at a fixed polygon IoU."""
    matched = set()
    duplicate = 0
    for detection in sorted(detections, key=lambda item: -item["confidence"]):
        overlaps = [polygon_iou(detection["polygon"], label) for label in labels]
        eligible = [i for i, overlap in enumerate(overlaps) if overlap >= threshold]
        available = [i for i in eligible if i not in matched]
        if available:
            matched.add(max(available, key=overlaps.__getitem__))
        elif eligible:
            duplicate += 1

    return {
        "tp": len(matched),
        "fp": len(detections) - len(matched),
        "fn": len(labels) - len(matched),
        "duplicate_fp": duplicate,
    }


def metrics(counts):
    tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
    return {
        **counts,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def read_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not all(
        isinstance(manifest.get(field), str) and manifest[field].strip()
        for field in ("source", "split", "independence")
    ):
        raise ValueError("Manifest requires source, split and independence statements")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Manifest requires a nonempty samples list")
    seen = set()
    for sample in samples:
        if (not isinstance(sample, dict) or not isinstance(sample.get("image"), str)
                or not sample["image"].strip() or not isinstance(sample.get("ships"), list)):
            raise ValueError("Each sample requires an image path and an explicit ships list")
        image = (path.parent / sample["image"]).resolve()
        if image in seen:
            raise ValueError(f"Duplicate image: {sample['image']}")
        seen.add(image)
    return manifest


def validate_labels(polygons, width, height):
    labels = []
    for polygon in polygons:
        if not isinstance(polygon, list) or len(polygon) != 4:
            raise ValueError("A ship label must have four vertices")
        for point in polygon:
            if (not isinstance(point, list) or len(point) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)):
                raise ValueError("Label vertices must contain finite numeric x and y")
            if not (0 <= point[0] <= width and 0 <= point[1] <= height):
                raise ValueError("Label vertex falls outside original image coordinates")
        label = canonical_polygon(polygon)
        if not cv2.isContourConvex(np.asarray(label, dtype=np.float32)):
            raise ValueError("Label vertices must form a convex quadrilateral")
        labels.append(label)
    return labels


def evaluate(args, manifest, detector):
    options = dict(conf=args.conf, iou=args.nms_iou, imgsz=args.imgsz, max_det=args.max_det,
                   classes=detector.ship_ids)
    tiling = dict(tile_size=args.tile_size, stride=args.stride, merge_iou=args.merge_iou)
    rows = []
    image_hashes = set()
    for index, sample in enumerate(manifest["samples"]):
        path = (args.manifest.parent / sample["image"]).resolve()

        def whole():
            with read_image(path, max_pixels=MAX_LARGE_PIXELS) as image:
                return detector.predict_image(image, **options)

        def tiled():
            return detector.predict_large(path, **options, **tiling)

        if index == 0:
            # Exclude first-model setup from the per-image timings.
            whole()

        results = {}
        predictions = (("whole", whole), ("tiled", tiled))
        if index % 2:
            predictions = reversed(predictions)
        for mode, predict in predictions:
            start = perf_counter()
            result = predict()
            elapsed_ms = (perf_counter() - start) * 1000
            width, height = result["image"]["width"], result["image"]["height"]
            labels = validate_labels(sample["ships"], width, height)
            counts = match_detections(result["detections"], labels, args.match_iou)
            results[mode] = {
                **metrics(counts), "elapsed_ms": round(elapsed_ms, 3), "warnings": result["warnings"],
            }
            if mode == "tiled":
                fingerprint = result["image"]["sha256"]
                results[mode]["tile_count"] = result["tiling"]["tile_count"]
        if fingerprint in image_hashes:
            raise ValueError(f"Duplicate image content: {sample['image']}")
        image_hashes.add(fingerprint)
        rows.append({
            "image": sample["image"], "sha256": fingerprint, "width": width, "height": height,
            "ships": len(labels), **results,
        })

    summary = {}
    for mode in ("whole", "tiled"):
        totals = {key: sum(row[mode][key] for row in rows) for key in ("tp", "fp", "fn", "duplicate_fp")}
        summary[mode] = {
            **metrics(totals), "elapsed_ms": round(sum(row[mode]["elapsed_ms"] for row in rows), 3),
        }
    return {
        "dataset": {key: manifest[key] for key in ("source", "split", "independence")},
        "manifest_sha256": sha256(args.manifest),
        "model": detector.metadata,
        "parameters": {**options, **tiling, "match_iou": args.match_iou},
        "summary": summary,
        "samples": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.7)
    parser.add_argument("--match-iou", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--max-det", type=int, default=1000)
    parser.add_argument("--tile-size", type=int, default=1024)
    parser.add_argument("--stride", type=int, default=512)
    parser.add_argument("--merge-iou", type=float, default=0.1)
    args = parser.parse_args(argv)
    try:
        if not 0 < args.match_iou <= 1:
            raise ValueError("match-iou must be in (0, 1]")
        ShipDetector.validate_parameters(args.conf, args.nms_iou, args.imgsz, args.max_det)
        validate_tiling(args.tile_size, args.stride, args.merge_iou)
        manifest = read_manifest(args.manifest)
        detector = ShipDetector(args.weights, args.device)
        if not detector.ship_ids:
            raise ValueError("Weights must contain a ship class")
        report = evaluate(args, manifest, detector)
    except (DetectionError, ValueError, OSError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
