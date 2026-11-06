"""Bounded RGB tiled inference; full-image polygon NMS, without retraining."""
import contextlib
from datetime import datetime, timezone
import math
from numbers import Real
from pathlib import Path
import time

import cv2
import numpy as np

from .ship_detector import DetectionError, MAX_LARGE_PIXELS, canonical_polygon, read_image, sha256

MAX_TILES = 4096
MAX_CANDIDATES = 20000


def _input_stamp(path):
    """(size, mtime_ns)。正常情况下用它代替整文件重算；命中变化才回退到全量哈希。"""
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns)


def validate_tiling(tile_size, stride, merge_iou):
    if isinstance(tile_size, bool) or not isinstance(tile_size, int) or not 32 <= tile_size <= 2048:
        raise DetectionError("INVALID_ARGUMENT", "tile_size must be an integer from 32 to 2048")
    if isinstance(stride, bool) or not isinstance(stride, int) or not 1 <= stride <= tile_size:
        raise DetectionError("INVALID_ARGUMENT", "stride must be an integer from 1 to tile_size")
    if not isinstance(merge_iou, Real) or isinstance(merge_iou, bool) or not math.isfinite(merge_iou) or not 0 < merge_iou <= 1:
        raise DetectionError("INVALID_ARGUMENT", "merge_iou must be in (0, 1]")


def axis_starts(length, tile_size, stride):
    """Anchor final window at the edge. Small images have one unpadded window."""
    last = max(0, length - tile_size)
    starts = list(range(0, last + 1, stride))
    if starts[-1] != last:
        starts.append(last)
    return starts


def plan_tiles(width, height, tile_size, stride):
    validate_tiling(tile_size, stride, 0.1)
    # Check the count BEFORE allocating coordinates (e.g. a very wide image, stride=1).
    nx = math.ceil(max(0, width - tile_size) / stride) + 1
    ny = math.ceil(max(0, height - tile_size) / stride) + 1
    if nx * ny > MAX_TILES:
        raise DetectionError("TILE_LIMIT", f"At most {MAX_TILES} tiles; increase stride or tile_size")
    xs, ys = axis_starts(width, tile_size, stride), axis_starts(height, tile_size, stride)
    return [(x, y, min(x + tile_size, width), min(y + tile_size, height)) for y in ys for x in xs]


def polygon_iou(a, b):
    """Translate before float32 conversion to preserve local detail at large offsets."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    origin = a[0].copy()
    a, b = (a - origin).astype(np.float32), (b - origin).astype(np.float32)
    area_a, area_b = abs(cv2.contourArea(a)), abs(cv2.contourArea(b))
    intersection = float(cv2.intersectConvexConvex(a, b)[0])
    intersection = min(max(0.0, intersection), area_a, area_b)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def polygon_nms(detections, threshold):
    """Class-aware greedy NMS. Confidence ties preserve tile/candidate order."""
    if not detections:
        return []
    polygons = np.asarray([d["polygon"] for d in detections], dtype=np.float64)
    lower, upper = polygons.min(axis=1), polygons.max(axis=1)
    classes = np.asarray([d["class_id"] for d in detections])
    order = np.asarray(sorted(range(len(detections)), key=lambda i: (-detections[i]["confidence"], i)))
    kept = []
    while order.size:
        best = int(order[0])
        kept.append(detections[best])
        rest = order[1:]
        # AABBs only prefilter pairs; suppression ALWAYS uses actual polygon IoU.
        overlaps = (np.minimum(upper[best], upper[rest]) > np.maximum(lower[best], lower[rest])).all(axis=1)
        possible = np.flatnonzero(overlaps & (classes[rest] == classes[best]))
        suppress = np.zeros(len(rest), dtype=bool)
        for j in possible:
            suppress[j] = polygon_iou(polygons[best], polygons[rest[j]]) > threshold
        order = rest[~suppress]
    return [{**d, "id": i + 1} for i, d in enumerate(kept)]


def predict_large(detector, image_path, *, tile_size, stride, merge_iou, conf, iou, imgsz, max_det,
                  classes=None, on_progress=None, should_cancel=None, tile_lock=None):
    """Tiled inference. `on_progress(tile_id, tile_total, candidate_count)` is optional and does not
    change results; `should_cancel()` is polled only at tile boundaries, so cancellation is never
    observed inside a model call and never produces a partial result.

    `classes=None` 表示不做类别过滤（输出模型全部类别）；传 id 列表则只保留这些类。
    `tile_lock` 只包住单片的模型调用，让调用方能在片间把 GPU 让给别的请求；
    不传时行为与之前完全一致。
    """
    validate_tiling(tile_size, stride, merge_iou)
    detector.validate_parameters(conf, iou, imgsz, max_det, classes, getattr(detector, "all_ids", None))
    started = time.perf_counter()
    image = read_image(image_path, max_pixels=MAX_LARGE_PIXELS)
    try:
        fingerprint = sha256(Path(image_path))
        stamp = _input_stamp(image_path)
        tiles = plan_tiles(image.width, image.height, tile_size, stride)
        decoded = time.perf_counter()
        candidates, records, warnings = [], [], []
        for tile_id, box in enumerate(tiles, 1):
            tile_started = time.perf_counter()
            guard = tile_lock if tile_lock is not None else contextlib.nullcontext()
            try:
                with guard:
                    with image.crop(box) as crop:
                        detections, _ = detector._infer_image(crop, conf=conf, iou=iou, imgsz=imgsz,
                                                               max_det=max_det, classes=classes)
            except DetectionError as exc:
                raise DetectionError(exc.code, f"Tile {tile_id}: {exc}") from exc
            if len(candidates) + len(detections) > MAX_CANDIDATES:
                raise DetectionError("CANDIDATE_LIMIT", f"At most {MAX_CANDIDATES} candidates; no partial result returned")
            for d in detections:
                points = [[x + box[0], y + box[1]] for x, y in d["polygon"]]
                candidates.append({**d, "polygon": canonical_polygon(points), "source_tile_id": tile_id})
            if len(detections) >= max_det:
                warnings.append(f"MAX_DET_REACHED: tile {tile_id} may be truncated")
            records.append({"id": tile_id, "x": box[0], "y": box[1],
                            "width": box[2] - box[0], "height": box[3] - box[1],
                            "candidate_count": len(detections),
                            "predict_ms": (time.perf_counter() - tile_started) * 1000})
            if on_progress is not None:
                on_progress(tile_id, len(tiles), len(candidates))
            if should_cancel is not None and should_cancel():
                raise DetectionError("CANCELLED", f"Cancelled at tile boundary {tile_id}/{len(tiles)}")
        inferred = time.perf_counter()
        detections = polygon_nms(candidates, merge_iou)
        merged = time.perf_counter()
        if _input_stamp(image_path) != stamp and sha256(Path(image_path)) != fingerprint:
            raise DetectionError("IMAGE_CHANGED", "Input image changed during inference")
        if max(image.size) > tile_size:
            warnings.append("TILE_BOUNDARIES: truncated objects may remain or be missed; fragments are not stitched")
        if any(max(t["width"], t["height"]) > imgsz for t in records):
            warnings.append("TILE_DOWNSCALED: small targets may be lost")
        return {
            "schema_version": "ship-detection/0.2", "created_at": datetime.now(timezone.utc).isoformat(),
            "image": {"file_name": Path(image_path).name, "sha256": fingerprint,
                      "width": image.width, "height": image.height, "mode": "RGB"},
            "coordinates": {"unit": "pixel", "origin": "top-left", "x_axis": "right", "y_axis": "down",
                            "polygon_order": "clockwise; start=min(y,x)", "space": "original_image", "clipped": False},
            "model": dict(detector.metadata),
            "parameters": {"conf": conf, "iou": iou, "imgsz": imgsz, "max_det": max_det,
                           "classes": (list(classes) if classes is not None
                                       else list(getattr(detector, "all_ids", None) or getattr(detector, "ship_ids", []))),
                           "classes_filtered": classes is not None,
                           "half": False, "augment": False, "tiling": True,
                           "tile_size": tile_size, "stride": stride, "merge_iou": merge_iou},
            "tiling": {"tile_count": len(tiles), "candidate_count": len(candidates),
                       "suppressed_count": len(candidates) - len(detections),
                       "merge_method": "class_aware_polygon_nms", "tiles": records},
            "timing_ms": {"model_load": detector.load_ms, "decode": (decoded - started) * 1000,
                          "predict_call": (inferred - decoded) * 1000, "merge": (merged - inferred) * 1000,
                          "total_before_save": (time.perf_counter() - started) * 1000},
            "count": len(detections), "warnings": warnings, "detections": detections,
        }
    finally:
        image.close()
