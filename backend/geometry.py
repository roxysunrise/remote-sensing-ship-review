"""几何、复核排序与区域统计。

权威几何一律复用 `algorithm/`（`canonical_polygon` / `polygon_iou`），
后端不再写第二套；前端 `frontend/api/geom.js` 仅用于显示与 mock。
"""
from __future__ import annotations

import math

from algorithm.ship_detector import canonical_polygon
from algorithm.tiled_inference import polygon_iou

BOUNDARY_MARGIN = 2.0      # 距切片内边多少像素内视为"贴缝"
EDGE_MARGIN = 1.0          # 距影像边缘多少像素内视为"贴边"
LOW_CONFIDENCE = 0.5       # 低于该置信度进入疑难队列
SAMPLING_CELL = 512        # 空区域抽样网格
SAMPLING_LIMIT = 12


def polygon_area(points) -> float:
    total = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        total += p[0] * q[1] - q[0] * p[1]
    return abs(total) / 2.0


def bbox(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def centroid(points) -> tuple[float, float]:
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def point_in_polygon(point, polygon) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def rect_polygon(box) -> list[list[float]]:
    """[x0,y0,x1,y1] -> 四点，规范化顺序与检测结果一致。"""
    x0, y0, x1, y1 = box
    return canonical_polygon([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def normalize_polygon(points) -> list[list[float]]:
    return canonical_polygon([[float(x), float(y)] for x, y in points])


def iou(a, b) -> float:
    return polygon_iou(a, b)


# --- 疑难复核 ---------------------------------------------------------------

def review_items(detections, *, image_width, image_height, tiles=None, merge_iou=0.1,
                 low_confidence=LOW_CONFIDENCE) -> list[dict]:
    """按"值得先看"排序。分数不是正确概率，只是排序依据。"""
    tiles = {t["id"]: t for t in (tiles or [])}
    active = [d for d in detections if d["review_state"] != "deleted"]
    items: dict[int, dict] = {}

    def push(det, code, message, weight, **extra):
        entry = items.setdefault(det["id"], {"detection_id": det["id"], "score": 0, "reasons": []})
        entry["score"] += int(round(weight))
        reason = {"code": code, "message": message}
        reason.update({k: v for k, v in extra.items() if v is not None})
        entry["reasons"].append(reason)

    for det in active:
        polygon = det["polygon"]
        x0, y0, x1, y1 = bbox(polygon)

        confidence = det.get("confidence")
        if confidence is not None and confidence < low_confidence:
            push(det, "LOW_CONFIDENCE",
                 f"模型置信度 {confidence:.2f} 低于 {low_confidence:.2f}", 10 + (low_confidence - confidence) * 100)

        if (x0 <= EDGE_MARGIN or y0 <= EDGE_MARGIN
                or x1 >= image_width - EDGE_MARGIN or y1 >= image_height - EDGE_MARGIN):
            push(det, "IMAGE_EDGE", "目标贴近影像边缘，可能被截断", 30)

        tile = tiles.get(det.get("source_tile_id"))
        if tile:
            tx0, ty0 = tile["x"], tile["y"]
            tx1, ty1 = tx0 + tile["width"], ty0 + tile["height"]
            near = []
            if tx0 > 0 and abs(x0 - tx0) <= BOUNDARY_MARGIN:
                near.append("左")
            if ty0 > 0 and abs(y0 - ty0) <= BOUNDARY_MARGIN:
                near.append("上")
            if tx1 < image_width and abs(x1 - tx1) <= BOUNDARY_MARGIN:
                near.append("右")
            if ty1 < image_height and abs(y1 - ty1) <= BOUNDARY_MARGIN:
                near.append("下")
            if near:
                push(det, "TILE_BOUNDARY", f"目标压在切片{''.join(near)}边界上，可能被切成碎片", 25)

    # 疑似重复：重叠但低于合并阈值，说明 NMS 没合并掉
    for i, first in enumerate(active):
        for second in active[i + 1:]:
            value = iou(first["polygon"], second["polygon"])
            if 0 < value <= merge_iou:
                push(first, "DUPLICATE", f"与目标 {second['id']} 重叠度过低未合并（IoU {value:.2f}）",
                     20 + value * 100, partner_id=second["id"], iou=round(value, 4))
                push(second, "DUPLICATE", f"与目标 {first['id']} 重叠度过低未合并（IoU {value:.2f}）",
                     20 + value * 100, partner_id=first["id"], iou=round(value, 4))

    ordered = sorted(items.values(), key=lambda item: (-item["score"], item["detection_id"]))
    for item in ordered:
        item["reasons"].sort(key=lambda r: r["code"])
    return ordered


def sampling_cells(detections, *, image_width, image_height,
                   cell=SAMPLING_CELL, limit=SAMPLING_LIMIT) -> list[dict]:
    """没有检测目标的网格段落，供人工抽查漏检。"""
    columns = math.ceil(image_width / cell)
    rows = math.ceil(image_height / cell)
    hits = [[0] * columns for _ in range(rows)]
    for det in detections:
        if det["review_state"] == "deleted":
            continue
        cx, cy = centroid(det["polygon"])
        column = min(columns - 1, max(0, int(cx // cell)))
        row = min(rows - 1, max(0, int(cy // cell)))
        hits[row][column] += 1

    empty = []
    for row in range(rows):
        for column in range(columns):
            if hits[row][column] == 0:
                x = column * cell
                y = row * cell
                empty.append({"x": x, "y": y,
                              "width": min(cell, image_width - x),
                              "height": min(cell, image_height - y)})
    empty.sort(key=lambda c: (c["y"], c["x"]))
    return empty[:limit]


# --- 区域统计 ---------------------------------------------------------------

def region_counts(detections, polygon) -> dict:
    """中心点落入区域规则；重叠区域不直接相加（调用方分别统计）。"""
    model = effective = deleted = added = 0
    for det in detections:
        if not point_in_polygon(centroid(det["polygon"]), polygon):
            continue
        if det["review_state"] == "deleted":
            deleted += 1
            continue
        effective += 1
        if det["origin"] == "model" and det["review_state"] != "modified":
            model += 1
        if det["origin"] in {"manual", "ai"}:
            added += 1
    return {
        "model": model,
        "effective": effective,
        "deleted": deleted,
        "added": added,
        "delta": effective - model,
    }
