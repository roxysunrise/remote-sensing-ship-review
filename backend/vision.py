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

from fastapi import APIRouter, Body
from PIL import Image

from . import ai_assist, config, detector, errors, imaging

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
    """点选 / 框选 / 精修 → 候选旋转框。

    核心逻辑在 `ai_assist.propose`（补框与改框共用一份实现）：
    先在 ROI 上按原分辨率再跑一次检测模型，没命中才退化为经典轮廓。
    """
    request_id = f"req_{secrets.token_hex(4)}"
    frame = payload.get("frame") or {}
    roi = payload.get("roi")
    point = payload.get("point")
    existing = payload.get("existing_polygon")
    mode = str(payload.get("mode") or ("refine" if existing else "box" if roi else "point"))

    image = _request_image(payload)
    started = time.perf_counter()
    try:
        width, height = image.size
        target_width = int(frame.get("width") or width)
        target_height = int(frame.get("height") or height)
        sx = width / target_width if target_width else 1.0
        sy = height / target_height if target_height else 1.0

        # 原图坐标 → 负载坐标（候选再按 1/sx 还原）
        roi_payload = [roi[0] * sx, roi[1] * sy, roi[2] * sx, roi[3] * sy] if roi else None
        point_payload = [point[0] * sx, point[1] * sy] if point else None
        existing_payload = ([[float(p[0]) * sx, float(p[1]) * sy] for p in existing]
                            if existing else None)

        candidates = ai_assist.propose(detector.service, image, roi=roi_payload,
                                       point=point_payload, mode=mode,
                                       existing_polygon=existing_payload)
        if not candidates:
            raise errors.invalid("NO_CANDIDATE", "该区域没有可用候选，请改用手动标注")

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "request_id": request_id,
            "frame": {"index": int(frame.get("index") or 0), "time_ms": int(frame.get("time_ms") or 0),
                      "width": target_width, "height": target_height},
            "model": {"name": "roi-oriented-proposal", "version": "0.2"},
            "elapsed_ms": elapsed_ms,
            "roi": roi,
            "mode": mode,
            "candidates": [
                {"id": f"c{index}", "label": "ship", "quality": item["quality"],
                 "confidence": None, "source": item["source"],
                 "polygon": [[round(x / sx, 3), round(y / sy, 3)] for x, y in item["polygon"]],
                 "reason": item["reason"]}
                for index, item in enumerate(candidates, 1)
            ],
        }
    finally:
        image.close()


def _round_polygon(points) -> list[list[float]]:
    return [[round(float(x), 3), round(float(y), 3)] for x, y in points]


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """同步接口没有可取消的后台作业；保留端点以便前端统一调用。"""
    return {"cancelled": True, "job_id": job_id, "note": "同步检测在请求内完成，无队列作业"}
