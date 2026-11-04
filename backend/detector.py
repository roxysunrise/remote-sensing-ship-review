"""算法适配层：单例模型、串行推理、参数透传。

`ShipDetector` 明确不是并发安全的，所以这里用一把运行锁把所有推理串行化：
任务 Worker 与 `/vision/*` 共用同一把锁，避免 GPU 上并发踩状态。
"""
from __future__ import annotations

import threading
from pathlib import Path

from algorithm.errors import DetectionError
from algorithm.ship_detector import ShipDetector

from . import config, errors

_tiling_keys = ("conf", "iou", "imgsz", "max_det", "tile_size", "stride", "merge_iou")


class DetectorService:
    def __init__(self, weights: Path | None = None, device: str | None = None):
        self.weights = Path(weights or config.WEIGHTS)
        self.device = device or config.DEVICE
        self._detector: ShipDetector | None = None
        self._init_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self.last_error: str | None = None

    # --- 生命周期 ---------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._detector is not None

    def load(self) -> ShipDetector:
        with self._init_lock:
            if self._detector is None:
                try:
                    self._detector = ShipDetector(self.weights, self.device)
                except DetectionError as exc:
                    self.last_error = str(exc)
                    raise errors.from_detection_error(exc) from exc
                self.last_error = None
        return self._detector

    # --- 推理 -------------------------------------------------------------
    def predict(self, image_path, parameters: dict, *,
                on_progress=None, should_cancel=None) -> dict:
        detector = self.load()
        params = {k: parameters[k] for k in _tiling_keys if k in parameters}
        tiling = bool(parameters.get("tiling", True))
        try:
            if tiling:
                # 锁下沉到单片：任务跑着的时候，即时检测仍能在片间插队
                return detector.predict_large(
                    image_path,
                    tile_size=int(params.get("tile_size", 1024)),
                    stride=int(params.get("stride", 512)),
                    merge_iou=float(params.get("merge_iou", 0.1)),
                    conf=float(params.get("conf", 0.25)),
                    iou=float(params.get("iou", 0.7)),
                    imgsz=int(params.get("imgsz", 1024)),
                    max_det=int(params.get("max_det", 1000)),
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                    tile_lock=self._run_lock,
                )
            with self._run_lock:
                return detector.predict(
                    image_path,
                    conf=float(params.get("conf", 0.25)),
                    iou=float(params.get("iou", 0.7)),
                    imgsz=int(params.get("imgsz", 1024)),
                    max_det=int(params.get("max_det", 1000)),
                )
        except DetectionError as exc:
            raise errors.from_detection_error(exc) from exc

    def predict_image(self, image, parameters: dict) -> dict:
        """内存图像推理：省掉写盘、二次解码与哈希。"""
        detector = self.load()
        params = {k: parameters[k] for k in _tiling_keys if k in parameters}
        with self._run_lock:
            try:
                return detector.predict_image(
                    image,
                    conf=float(params.get("conf", 0.25)),
                    iou=float(params.get("iou", 0.7)),
                    imgsz=int(params.get("imgsz", 1024)),
                    max_det=int(params.get("max_det", 1000)),
                )
            except DetectionError as exc:
                raise errors.from_detection_error(exc) from exc

    # --- 元信息 -----------------------------------------------------------
    def capabilities(self) -> dict:
        if self._detector is not None:
            metadata = dict(self._detector.metadata)
            model = {
                "name": metadata.get("architecture", "yolov8-obb"),
                "version": f"ship-{metadata.get('checkpoint_epoch', '?')}",
                "device": metadata.get("device_name", self.device),
                "weights": metadata.get("weights_file"),
                "weights_sha256": metadata.get("weights_sha256"),
            }
        else:
            model = {
                "name": "yolov8-obb",
                "version": "ship-0.2",
                "device": self.device,
                "weights": self.weights.name,
                "weights_sha256": None,
            }
        return {
            "service": "vision-inference",
            "version": __import__("backend").__version__,
            "modes": ["image"],
            "tasks": ["detect", "segment"],
            "models": {
                "detect": {k: model[k] for k in ("name", "version", "device")},
                "segment": {"name": "roi-oriented-proposal", "version": "0.1"},
            },
            "limits": {"max_image_edge": config.VISION_MAX_EDGE,
                       "max_pixels": config.MAX_PIXELS,
                       "max_payload_bytes": config.VISION_MAX_BYTES},
            "loaded": self.loaded,
        }


service = DetectorService()
