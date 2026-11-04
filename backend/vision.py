"""`/vision/*` 推理服务：把前端检测工作台接到真实模型上。

契约见 `frontend/api/DETECT_API.md`。关键约定：
- 请求带的是**下采样后的 JPEG data URL**，服务端必须按 `frame.width / image.width`
  把多边形还原到原始媒体像素；
- `DetectResponse.detections[]` 用 `label/score/polygon/source`（不是 v0.2 的
  `class_name/confidence/...`），这一层负责两种 schema 的转换；
- AI 候选的 `confidence` 必须为 null，只给 `quality`。
"""
from __future__ import annotations

import math
import secrets
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Body
from PIL import Image

from algorithm.errors import DetectionError

from . import config, detector, errors, imaging

router = APIRouter(prefix="/vision", tags=["vision"])

_sessions: dict[str, dict] = {}


def _align(value: int, low: int = 32, high: int = 2048) -> int:
    return max(low, min(high, int(math.ceil(value / 32.0) * 32)))


def _request_image(payload: dict) -> Image.Image:
    image_bytes = imaging.decode_data_url(((payload or {}).get("image") or {}).get("data_url"))
    return imaging.load_request_image(image_bytes)


def _scale_back(polygon, source_width: int, source_height: int, frame: dict) -> list[list[float]]:
    """payload 像素 → 原始媒体像素。"""
    width = int(frame.get("width") or source_width)
    height = int(frame.get("height") or source_height)
    sx = width / source_width if source_width else 1.0
    sy = height / source_height if source_height else 1.0
    return [[round(x * sx, 3), round(y * sy, 3)] for x, y in polygon]


@router.get("/capabilities")
def capabilities() -> dict:
    return detector.service.capabilities()


@router.post("/sessions")
def create_session(body: dict = Body(default_factory=dict)) -> dict:
    session_id = f"ses_{secrets.token_hex(4)}"
    _sessions[session_id] = {
        "kind": body.get("kind", "image"),
        "name": body.get("name"),
        "width": body.get("width"),
        "height": body.get("height"),
        "created_at": time.time(),
    }
    return {"session_id": session_id, "media_id": f"med_{secrets.token_hex(4)}",
            "kind": _sessions[session_id]["kind"]}


@router.post("/detect")
def detect(payload: dict = Body(default_factory=dict)) -> dict:
    request_id = f"req_{secrets.token_hex(4)}"
    frame = payload.get("frame") or {}
    options = payload.get("options") or {}

    image = _request_image(payload)
    started = time.perf_counter()
    try:
        params = {
            "tiling": False,
            "conf": float(options.get("conf", 0.25)),
            "iou": float(options.get("iou", 0.7)),
            # 不再二次下采样：imgsz 对齐到当前帧的最大边（上限 2048）
            "imgsz": _align(max(image.width, image.height)),
            "max_det": int(options.get("max_det", 200)),
        }
        result = detector.service.predict_image(image, params)
    finally:
        image.close()

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return _finish_detect(result, frame, request_id, elapsed_ms)


def _finish_detect(result: dict, frame: dict, request_id: str, elapsed_ms: float) -> dict:
    width = int(result["image"]["width"])
    height = int(result["image"]["height"])
    target_width = int(frame.get("width") or width)
    target_height = int(frame.get("height") or height)
    sx = target_width / width if width else 1.0
    sy = target_height / height if height else 1.0
    detections = []
    for index, det in enumerate(result["detections"], 1):
        detections.append({
            "id": f"d{index}",
            "label": det.get("class_name", "ship"),
            "score": round(float(det["confidence"]), 4) if det.get("confidence") is not None else None,
            "polygon": [[round(x * sx, 3), round(y * sy, 3)] for x, y in det["polygon"]],
            "source": "model",
        })
    model = result.get("model") or {}
    return {
        "request_id": request_id,
        "frame": {"index": int(frame.get("index") or 0), "time_ms": int(frame.get("time_ms") or 0),
                  "width": target_width, "height": target_height},
        "model": {"name": "yolov8l-obb", "version": result.get("schema_version", "ship-detection/0.1"),
                  "device": model.get("device_name") or model.get("device")},
        "elapsed_ms": elapsed_ms,
        "count": len(detections),
        "detections": detections,
    }


@router.post("/segment")
def segment(payload: dict = Body(default_factory=dict)) -> dict:
    """点选/框选 → 候选旋转框。

    首选在 ROI 上跑真实检测模型（漏检目标多半是"整图缩太小"，局部高分辨率能救回来）；
    模型也没命中时退化为经典轮廓方案，并如实标记来源。
    """
    request_id = f"req_{secrets.token_hex(4)}"
    frame = payload.get("frame") or {}
    roi = payload.get("roi")
    point = payload.get("point")

    image = _request_image(payload)
    started = time.perf_counter()
    try:
        width, height = image.size
        target_width = int(frame.get("width") or width)
        target_height = int(frame.get("height") or height)
        sx = width / target_width if target_width else 1.0
        sy = height / target_height if target_height else 1.0

        if roi:
            x0, y0, x1, y1 = [float(v) for v in roi]
            box = (min(x0, x1) * sx, min(y0, y1) * sy, max(x0, x1) * sx, max(y0, y1) * sy)
        elif point:
            px, py = float(point[0]) * sx, float(point[1]) * sy
            radius = 48.0
            box = (px - radius, py - radius, px + radius, py + radius)
        else:
            raise errors.invalid("INVALID_REGION", "需要 roi 或 point")

        pad = 8.0
        left = max(0, int(math.floor(box[0] - pad)))
        top = max(0, int(math.floor(box[1] - pad)))
        right = min(width, int(math.ceil(box[2] + pad)))
        bottom = min(height, int(math.ceil(box[3] + pad)))
        if right - left < 8 or bottom - top < 8:
            raise errors.invalid("INVALID_REGION", "选区太小，请重新框选")

        crop = image.crop((left, top, right, bottom))
        try:
            candidates = _roi_candidates(crop, left, top, sx, sy)
        finally:
            crop.close()

        if not candidates:
            raise errors.invalid("NO_CANDIDATE", "该区域没有可用候选，请改用手动标注")

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "request_id": request_id,
            "frame": {"index": int(frame.get("index") or 0), "time_ms": int(frame.get("time_ms") or 0),
                      "width": target_width, "height": target_height},
            "model": {"name": "roi-oriented-proposal", "version": "0.1"},
            "elapsed_ms": elapsed_ms,
            "roi": roi,
            "candidates": [
                {"id": f"c{index}", "label": "ship", "quality": item["quality"],
                 "confidence": None, "polygon": item["polygon"], "reason": item["reason"]}
                for index, item in enumerate(candidates, 1)
            ],
        }
    finally:
        image.close()


def _roi_candidates(crop: Image.Image, left: int, top: int, sx: float, sy: float) -> list[dict]:
    """先在 ROI 上跑真实模型，没命中再退化为经典轮廓。"""
    candidates: list[dict] = []
    params = {"tiling": False, "conf": 0.15, "iou": 0.7,
              "imgsz": _align(max(crop.size), low=32, high=1024), "max_det": 10}
    result = detector.service.predict_image(crop, params)
    for det in sorted(result["detections"], key=lambda d: -float(d["confidence"]))[:2]:
        polygon = [[(x + left) / sx, (y + top) / sy] for x, y in det["polygon"]]
        candidates.append({"polygon": _round_polygon(polygon),
                           "quality": round(float(det["confidence"]), 4),
                           "reason": "局部高分辨率检测命中"})
    if not candidates:
        fallback = _contour_candidate(crop, left, top, sx, sy)
        if fallback:
            candidates.append(fallback)
    return candidates


def _round_polygon(points) -> list[list[float]]:
    return [[round(float(x), 3), round(float(y), 3)] for x, y in points]


def _contour_candidate(crop: Image.Image, left: int, top: int, sx: float, sy: float) -> dict | None:
    """经典兜底：Otsu 阈值 + 最大轮廓外接旋转矩形。不是语义分割。"""
    import cv2

    gray = np.asarray(crop.convert("L"))
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    area = max(cv2.contourArea(c) for c in contours)
    if area < 40:
        return None
    contour = max(contours, key=cv2.contourArea)
    (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
    box = cv2.boxPoints(((cx, cy), (w, h), angle))
    polygon = [[(px + left) / sx, (py + top) / sy] for px, py in box]
    fill = area / max(1.0, w * h)
    return {"polygon": _round_polygon(polygon),
            "quality": round(min(0.75, 0.35 + 0.4 * fill), 4),
            "reason": "经典阈值轮廓兜底（非语义分割）"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """同步接口没有可取消的后台作业；保留端点以便前端统一调用。"""
    return {"cancelled": True, "job_id": job_id, "note": "同步检测在请求内完成，无队列作业"}
