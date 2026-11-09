"""Local SAM 2 box prompting for the project annotation workbench."""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np

from . import ai_planner, config, errors, geometry

WEIGHTS = Path(os.environ["SHIP_SAM2_WEIGHTS"]).resolve() if os.environ.get("SHIP_SAM2_WEIGHTS") else None
DEVICE = os.environ.get("SHIP_SAM2_DEVICE", config.DEVICE)
_LOCK = threading.Lock()
_model = None
_phase = "idle"


def local_vlm():
    url = urlsplit(config.VLM_BASE_URL)
    return ai_planner.enabled() and url.scheme in {"http", "https"} and url.hostname in {
        "localhost", "127.0.0.1", "::1"
    }


def status():
    weights_present = bool(WEIGHTS and WEIGHTS.is_file())
    return {
        "sam2": _phase if weights_present else "unavailable",
        "available": weights_present and _phase != "unavailable",
        "vlm": "local_configured" if local_vlm() else "not_configured",
        "external": False,
    }


def _load():
    global _model, _phase
    if not WEIGHTS or not WEIGHTS.is_file():
        raise errors.conflict("SAM2_UNAVAILABLE", "本机 SAM 2 权重未配置")
    if _model is None:
        _phase = "loading"
        try:
            from ultralytics import SAM
            _model = SAM(str(WEIGHTS))
        except Exception as exc:
            _phase = "unavailable"
            raise errors.conflict("SAM2_UNAVAILABLE", "本机 SAM 2 加载失败，请联系主管") from exc
    return _model


def probe():
    global _phase
    with _LOCK:
        _load()
        _phase = "ready"
    return status()


def propose(image, box, gate=None):
    global _phase
    try:
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError
        x0, y0, x1, y1 = map(float, box)
        if not all(math.isfinite(v) for v in (x0,y0,x1,y1)):
            raise ValueError
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height
                and x1 - x0 >= 5 and y1 - y0 >= 5):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise errors.invalid("INVALID_BOX", "请在影像内圈出一个有效粗框") from exc
    pad = max(12, int(max(x1 - x0, y1 - y0) * .2))
    left, top = max(0, math.floor(x0)-pad), max(0, math.floor(y0)-pad)
    right, bottom = min(image.width, math.ceil(x1)+pad), min(image.height, math.ceil(y1)+pad)
    with image.crop((left, top, right, bottom)) as crop:
        crop_size = crop.size
        crop.thumbnail((1024, 1024))
        sx, sy = crop.width / crop_size[0], crop.height / crop_size[1]
        prompt = [(x0-left)*sx, (y0-top)*sy, (x1-left)*sx, (y1-top)*sy]
        with _LOCK:
            model = _load()
            _phase = "loading" if _phase != "ready" else "ready"
            try:
                result = model.predict(crop, bboxes=[prompt], device=DEVICE, verbose=False)[0]
            except Exception as exc:
                _model_reset()
                raise errors.conflict("SAM2_UNAVAILABLE", "本机 SAM 2 推理失败，请先手调或请主管重测") from exc
            _phase = "ready"
        contours = result.masks.xy if result.masks is not None else []
        if not contours:
            raise errors.invalid("NO_CANDIDATE", "该粗框内没有可用轮廓，请调整粗框或手动标注")
        points = np.asarray(contours[0], dtype=np.float32)
        if len(points) < 3 or abs(cv2.contourArea(points)) < 4:
            raise errors.invalid("NO_CANDIDATE", "分割轮廓过小，请调整粗框")
        points[:, 0] = points[:, 0] / sx + left
        points[:, 1] = points[:, 1] / sy + top
        rectangle = cv2.boxPoints(cv2.minAreaRect(points))
        rectangle[:, 0] = rectangle[:, 0].clip(0, image.width)
        rectangle[:, 1] = rectangle[:, 1].clip(0, image.height)
        polygon = geometry.normalize_polygon(rectangle.tolist())
        verdict = None
        if gate is not None:
            bounds = (math.floor(points[:,0].min()), math.floor(points[:,1].min()),
                      math.ceil(points[:,0].max())+1, math.ceil(points[:,1].max())+1)
            with image.crop(bounds) as candidate_image:
                candidate_image.thumbnail((512,512))
                verdict = gate(candidate_image)
        if verdict == "no":
            raise errors.invalid("NO_CANDIDATE", "本机 VLM 判断该候选不是船，请调整粗框或手动标注")
        quality = float(result.boxes.conf[0]) if result.boxes is not None and len(result.boxes) else None
        return {"polygon": polygon, "source": "sam2", "quality": quality,
                "reason": "SAM 2 轮廓拟合" + ("，本机 VLM 判断为船" if verdict == "yes" else "，待人工确认")}


def _model_reset():
    global _model, _phase
    _model = None
    _phase = "unavailable"
