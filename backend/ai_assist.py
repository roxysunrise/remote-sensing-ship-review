"""AI 辅助框：候选生成核心（补框 / 改框共用一份实现）。

之前 `vision.py` 与 `api.py` 各写了一遍「ROI 裁剪 → 模型检测 → 轮廓兜底」，
坐标与过滤规则已经开始漂移。这里合并成一处，两个入口只做坐标换算与落库。

约定：
- 所有坐标都在**传入图像自身的像素空间**里；调用方负责缩放（检测页要按 frame/image 比例还原）。
- 候选 `confidence` 恒为 None，只给 `quality`（排序用，不是概率）。
- 两条通道：先在 ROI 上按原分辨率再跑一次检测模型（对"整图缩小导致的小船漏检"有效），
  没命中再退化为经典阈值轮廓。两条都不是语义分割，`reason` 会写明来源。
"""
from __future__ import annotations

import math

import numpy as np

from . import errors, geometry

DETECT_CONF = 0.15
DETECT_IMGSZ_MAX = 1024
PAD = 8.0
POINT_RADIUS = 48.0
REFINE_EXPAND = 0.25

# 合理性门限（船体经验值，评估后可校准）
AREA_RATIO = (0.02, 0.80)
ASPECT = (1.2, 15.0)          # 1:1 的方形多码头设施而非船体；正对船头会被过滤，可手动画
MIN_AREA_PX = 40.0
MIN_HINT_PX = 6.0             # 提示框本身的最小边长，防止"点了一下"就调用模型
# refine 模式：不允许跳到隔壁船上
REFINE_CENTER_SHIFT = 0.30
REFINE_SIZE_RATIO = (0.4, 2.5)


def _align(value: int, low: int = 32, high: int = DETECT_IMGSZ_MAX) -> int:
    return max(low, min(high, int(math.ceil(value / 32.0) * 32)))


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _hint_box(image, *, roi=None, point=None, existing_polygon=None, mode="fill"):
    width, height = image.size
    if mode == "refine" or existing_polygon:
        if not existing_polygon:
            raise errors.invalid("INVALID_REGION", "精修模式需要 existing_polygon")
        x0, y0, x1, y1 = _bbox([[float(p[0]), float(p[1])] for p in existing_polygon])
        dx = (x1 - x0) * REFINE_EXPAND
        dy = (y1 - y0) * REFINE_EXPAND
        return x0 - dx, y0 - dy, x1 + dx, y1 + dy
    if roi:
        x0, y0, x1, y1 = [float(v) for v in roi]
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    if point:
        px, py = float(point[0]), float(point[1])
        return px - POINT_RADIUS, py - POINT_RADIUS, px + POINT_RADIUS, py + POINT_RADIUS
    raise errors.invalid("INVALID_ARGUMENT", "需要 point、box 或 existing_polygon 之一")


def _clamp_box(box, image, pad: float = PAD):
    width, height = image.size
    left = max(0, int(math.floor(box[0] - pad)))
    top = max(0, int(math.floor(box[1] - pad)))
    right = min(width, int(math.ceil(box[2] + pad)))
    bottom = min(height, int(math.ceil(box[3] + pad)))
    return left, top, right, bottom


def _detect_candidates(service, crop, left: int, top: int, conf: float) -> list[dict]:
    """在 ROI 上按原分辨率再跑一次检测模型。"""
    params = {"tiling": False, "conf": conf, "iou": 0.7,
              "imgsz": _align(max(crop.size)), "max_det": 10}
    result = service.predict_image(crop, params)
    out = []
    for det in sorted(result["detections"], key=lambda d: -float(d["confidence"]))[:2]:
        polygon = [[x + left, y + top] for x, y in det["polygon"]]
        out.append({
            "polygon": geometry.normalize_polygon(polygon),
            "label": det.get("class_name") or "object",
            "class_id": det.get("class_id"),
            "quality": round(float(det["confidence"]), 4),
            "source": "detect",
            "reason": "局部高分辨率检测命中",
        })
    return out


def _contour_candidate(crop, left: int, top: int) -> dict | None:
    """经典兜底：Otsu 阈值 + 最大轮廓外接旋转矩形。不是语义分割。"""
    import cv2

    gray = np.asarray(crop.convert("L"))
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area < MIN_AREA_PX:
        return None
    (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
    box = cv2.boxPoints(((cx, cy), (w, h), angle))
    polygon = [[px + left, py + top] for px, py in box]
    fill = area / max(1.0, w * h)
    return {
        "polygon": geometry.normalize_polygon(polygon),
        "label": "object",
        "class_id": None,
        "quality": round(min(0.75, 0.35 + 0.4 * fill), 4),
        "source": "contour",
        "reason": "经典阈值轮廓兜底（非语义分割）",
    }


def _sane(candidate: dict, crop_box, mode: str, existing_polygon) -> bool:
    polygon = candidate["polygon"]
    area = geometry.polygon_area(polygon)
    if area < MIN_AREA_PX:
        return False
    left, top, right, bottom = crop_box
    ratio = area / max(1.0, (right - left) * (bottom - top))
    if not AREA_RATIO[0] <= ratio <= AREA_RATIO[1]:
        return False

    sides = [math.dist(polygon[i], polygon[(i + 1) % 4]) for i in range(4)]
    # 注意：sides[0] 与 sides[2] 是同一组对边，取两者的 min/max 会恒等于 1；
    # 长宽必须从**四条边**里取最大/最小。
    length, width = max(sides), min(sides)
    aspect = length / max(width, 1e-6)
    if not ASPECT[0] <= aspect <= ASPECT[1]:
        return False

    if mode == "refine" and existing_polygon:
        old = [[float(p[0]), float(p[1])] for p in existing_polygon]
        old_sides = [math.dist(old[i], old[(i + 1) % 4]) for i in range(4)]
        old_length = max(old_sides)
        old_center = geometry.centroid(old)
        new_center = geometry.centroid(polygon)
        if math.dist(old_center, new_center) > REFINE_CENTER_SHIFT * max(old_length, 1e-6):
            return False
        ratio_len = length / max(old_length, 1e-6)
        if not REFINE_SIZE_RATIO[0] <= ratio_len <= REFINE_SIZE_RATIO[1]:
            return False
    return True


def propose(service, image, *, roi=None, point=None, mode: str = "fill",
            existing_polygon=None, pad: float = PAD, keep: int = 2,
            conf: float = DETECT_CONF, gate=None) -> list[dict]:
    """生成 1–2 个候选（原图坐标）。没有可用候选时返回空列表。

    `gate(crop)` 是可选的大模型判定钩子（见 `ai_planner`）：
    返回 "no" 表示"这块区域没有目标"，直接短路返回空；返回 "yes" 或 None 继续走几何通道。
    """
    box = _hint_box(image, roi=roi, point=point,
                    existing_polygon=existing_polygon, mode=mode)
    if box[2] - box[0] < MIN_HINT_PX or box[3] - box[1] < MIN_HINT_PX:
        raise errors.invalid("INVALID_REGION", "选区太小，请重新框选")
    crop_box = _clamp_box(box, image, pad)
    left, top, right, bottom = crop_box
    if right - left < 8 or bottom - top < 8:
        raise errors.invalid("INVALID_REGION", "选区太小，请重新框选")

    crop = image.crop(crop_box)
    try:
        if gate is not None and gate(crop) == "no":
            return []
        candidates = _detect_candidates(service, crop, left, top, conf)
        if not candidates:
            fallback = _contour_candidate(crop, left, top)
            if fallback:
                candidates.append(fallback)
    finally:
        crop.close()

    kept = [c for c in candidates if _sane(c, crop_box, mode, existing_polygon)]
    kept.sort(key=lambda c: -c["quality"])
    return kept[:keep]
