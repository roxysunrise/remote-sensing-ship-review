"""Local, single-image YOLO OBB inference. Run: python -m algorithm.ship_detector."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, UnidentifiedImageError
from .errors import DetectionError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt"
MAX_PIXELS = 16_777_216
MAX_LARGE_PIXELS = 67_108_864


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_image(path: str | Path, *, max_pixels: int = MAX_PIXELS) -> Image.Image:
    path = Path(path)
    if not path.is_file():
        raise DetectionError("INPUT_NOT_FOUND", f"Image does not exist: {path}")
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise DetectionError("INVALID_IMAGE", "Only RGB PNG/JPEG is supported")
    try:
        with Image.open(path) as source:
            if source.format not in {"PNG", "JPEG"} or source.mode != "RGB":
                raise DetectionError("INVALID_IMAGE", "Image must decode as RGB PNG/JPEG")
            if source.width * source.height > max_pixels:
                raise DetectionError("IMAGE_TOO_LARGE", f"Maximum pixels for this mode: {max_pixels}")
            if source.getexif().get(274, 1) not in {None, 1}:
                raise DetectionError("EXIF_ORIENTATION", "Normalize EXIF orientation before inference")
            source.load()
            return source.copy()
    except DetectionError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DetectionError("INVALID_IMAGE", "Image cannot be decoded") from exc


def canonical_polygon(points) -> list[list[float]]:
    """Clockwise in image coordinates; start at smallest (y, x) vertex."""
    points = [[float(x), float(y)] for x, y in points]
    if len(points) != 4 or not all(math.isfinite(v) for p in points for v in p):
        raise DetectionError("INVALID_MODEL_OUTPUT", "Expected four finite vertices")
    cx = sum(p[0] for p in points) / 4
    cy = sum(p[1] for p in points) / 4
    points.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    area2 = sum(points[i][0] * points[(i + 1) % 4][1] - points[(i + 1) % 4][0] * points[i][1] for i in range(4))
    if area2 <= 1e-6:
        raise DetectionError("INVALID_MODEL_OUTPUT", "Degenerate rotated box")
    first = min(range(4), key=lambda i: (points[i][1], points[i][0]))
    return points[first:] + points[:first]


class ShipDetector:
    """Load trusted local Ultralytics OBB weights once; reuse for multiple images.

    This object is for sequential calls, not concurrent shared use.
    """
    def __init__(self, weights: str | Path = DEFAULT_WEIGHTS, device: str = "cpu"):
        start = time.perf_counter()
        self.weights = Path(weights).resolve()
        if not self.weights.is_file() or self.weights.suffix.lower() != ".pt":
            raise DetectionError("WEIGHTS_NOT_FOUND", "A local trusted .pt file is required")
        import torch
        import ultralytics
        from ultralytics import YOLO

        if device != "cpu" and not (device.isdigit() and torch.cuda.is_available() and int(device) < torch.cuda.device_count()):
            raise DetectionError("INVALID_DEVICE", "Use cpu or an available CUDA index, e.g. 0")
        self.device = device
        self.torch = torch
        try:
            self.model = YOLO(str(self.weights))
        except Exception as exc:
            raise DetectionError("MODEL_LOAD_FAILED", "Cannot load weights with this Ultralytics environment") from exc
        if self.model.task != "obb":
            raise DetectionError("INCOMPATIBLE_MODEL", "An OBB model is required")
        # 模型是 15 类 OBB（DOTA-v1），早先只输出 ship。这里保留 ship_ids 供业务侧默认值使用，
        # 但不再强制要求模型必须含 ship 类：`classes=None` 表示输出全部类别。
        self.all_ids = sorted(int(i) for i in self.model.names)
        self.ship_ids = [int(i) for i, name in self.model.names.items() if name.lower() == "ship"]
        self.metadata = {
            "backend": "ultralytics", "task": "obb", "weights_file": self.weights.name,
            "architecture": type(self.model.model).__name__,
            "checkpoint_model": Path(str(self.model.ckpt.get("train_args", {}).get("model", "unknown"))).name,
            "checkpoint_ultralytics_version": self.model.ckpt.get("version"),
            "checkpoint_epoch": self.model.ckpt.get("epoch"),
            "weights_sha256": sha256(self.weights), "ultralytics_version": ultralytics.__version__,
            "torch_version": torch.__version__, "class_names": self.model.names,
            "device": device,
            "device_name": "CPU" if device == "cpu" else torch.cuda.get_device_name(int(device)),
        }
        self.load_ms = (time.perf_counter() - start) * 1000

    @staticmethod
    def validate_parameters(conf, iou, imgsz, max_det, classes=None, known_ids=None):
        if not all(isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v) and 0 < v <= 1 for v in (conf, iou)):
            raise DetectionError("INVALID_ARGUMENT", "conf and iou must be in (0, 1]")
        if isinstance(imgsz, bool) or not isinstance(imgsz, int) or not 32 <= imgsz <= 2048 or imgsz % 32:
            raise DetectionError("INVALID_ARGUMENT", "imgsz must be a multiple of 32 from 32 to 2048")
        if isinstance(max_det, bool) or not isinstance(max_det, int) or not 1 <= max_det <= 10000:
            raise DetectionError("INVALID_ARGUMENT", "max_det must be an integer from 1 to 10000")
        if classes is not None:
            if isinstance(classes, (str, bytes)) or not isinstance(classes, (list, tuple, set, frozenset)):
                raise DetectionError("INVALID_ARGUMENT", "classes must be a list of class ids, or null for all classes")
            ids = list(classes)
            if not ids:
                raise DetectionError("INVALID_ARGUMENT", "classes must not be empty; use null for all classes")
            for value in ids:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise DetectionError("INVALID_ARGUMENT", "classes entries must be non-negative integers")
            if known_ids is not None:
                unknown = sorted({i for i in ids if i not in set(known_ids)})
                if unknown:
                    raise DetectionError(
                        "INVALID_ARGUMENT",
                        f"classes contains ids outside the model class list: {unknown}")

    def _effective_classes(self, classes):
        """实际生效的类别列表，用于写入 parameters（None 表示全类）。"""
        if classes is not None:
            return [int(c) for c in classes]
        return list(getattr(self, "all_ids", None) or getattr(self, "ship_ids", []))

    def _infer_image(self, image: Image.Image, *, conf, iou, imgsz, max_det, classes=None):
        """Shared sequential model call; coordinates refer to this decoded PIL image.

        `classes=None` 时不向模型传类别过滤，返回全部 15 类；传列表则只返回这些类。
        """
        try:
            result = self.model.predict(source=image, device=self.device, conf=conf, iou=iou,
                                        imgsz=imgsz, max_det=max_det,
                                        classes=None if classes is None else list(classes),
                                        augment=False, save=False, verbose=False)[0]
        except self.torch.OutOfMemoryError as exc:
            raise DetectionError("OUT_OF_MEMORY", "Insufficient memory; use CPU or reduce imgsz") from exc
        except Exception as exc:
            raise DetectionError("INFERENCE_FAILED", "Model inference failed; check environment and device") from exc
        if result.obb is None or tuple(result.orig_shape) != (image.height, image.width):
            raise DetectionError("INVALID_MODEL_OUTPUT", "Missing OBB output or mismatched image dimensions")
        detections = []
        for polygon, cls, score in zip(result.obb.xyxyxyxy.cpu().tolist(),
                                       result.obb.cls.cpu().tolist(), result.obb.conf.cpu().tolist()):
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise DetectionError("INVALID_MODEL_OUTPUT", "Invalid confidence")
            detections.append({"id": len(detections) + 1, "polygon": canonical_polygon(polygon),
                               "class_id": int(cls), "class_name": result.names[int(cls)],
                               "confidence": float(score)})
        return detections, result.speed

    def predict_large(self, image_path: str | Path, *, tile_size: int = 1024,
                      stride: int = 512, merge_iou: float = 0.1, conf: float = 0.25,
                      iou: float = 0.7, imgsz: int = 1024, max_det: int = 1000,
                      classes=None, on_progress=None, should_cancel=None, tile_lock=None) -> dict:
        """Tiled inference in original pixels; see ship-detection v0.2 contract.

        `classes=None` 输出全部类别；传类别 id 列表则只输出这些类。
        `on_progress`、`should_cancel` 和 `tile_lock` 是可选集成钩子，供任务运行时做进度、
        取消与片间让锁；不传时行为与之前完全一致。
        """
        from .tiled_inference import predict_large
        return predict_large(self, image_path, tile_size=tile_size, stride=stride,
                             merge_iou=merge_iou, conf=conf, iou=iou,
                             imgsz=imgsz, max_det=max_det, classes=classes,
                             on_progress=on_progress, should_cancel=should_cancel,
                             tile_lock=tile_lock)

    def predict(self, image_path: str | Path, *, conf: float = 0.25, iou: float = 0.7,
                imgsz: int = 1024, max_det: int = 1000, classes=None) -> dict:
        """Return JSON-compatible detections in ORIGINAL image pixels."""
        self.validate_parameters(conf, iou, imgsz, max_det, classes, getattr(self, "all_ids", None))
        started = time.perf_counter()
        image = read_image(image_path)
        decoded = time.perf_counter()
        detections, backend = self._infer_image(image, conf=conf, iou=iou, imgsz=imgsz,
                                                max_det=max_det, classes=classes)
        inferred = time.perf_counter()
        warnings = []
        if len(detections) >= max_det:
            warnings.append("MAX_DET_REACHED: detections may be truncated")
        if max(image.size) > imgsz:
            warnings.append("IMAGE_DOWNSCALED: small targets may be lost; no tiling")
        return {
            "schema_version": "ship-detection/0.1", "created_at": datetime.now(timezone.utc).isoformat(),
            "image": {"file_name": Path(image_path).name, "sha256": sha256(Path(image_path)),
                      "width": image.width, "height": image.height, "mode": "RGB"},
            "coordinates": {"unit": "pixel", "origin": "top-left", "x_axis": "right", "y_axis": "down",
                            "polygon_order": "clockwise; start=min(y,x)", "space": "original_image",
                            "clipped": False},
            "model": dict(self.metadata),
            "parameters": {"conf": conf, "iou": iou, "imgsz": imgsz, "max_det": max_det,
                           "classes": self._effective_classes(classes),
                           "classes_filtered": classes is not None,
                           "half": False, "augment": False, "tiling": False},
            "timing_ms": {"model_load": self.load_ms, "decode": (decoded - started) * 1000,
                          "predict_call": (inferred - decoded) * 1000,
                          "total_before_save": (time.perf_counter() - started) * 1000,
                          "backend": backend},
            "count": len(detections), "warnings": warnings, "detections": detections,
        }


    def predict_image(self, image: Image.Image, *, conf: float = 0.25, iou: float = 0.7,
                      imgsz: int = 1024, max_det: int = 1000, classes=None) -> dict:
        """在已解码的内存图像上推理：不落盘、不做文件哈希。

        供 `/vision/detect` 这类短请求使用：调用方已经解码并校验过，重复写盘和重算 SHA256
        是纯浪费。结果结构与 `predict()` 一致，只是 `image.file_name` / `image.sha256` 为 None。
        """
        self.validate_parameters(conf, iou, imgsz, max_det, classes, getattr(self, "all_ids", None))
        if getattr(image, "mode", None) != "RGB":
            raise DetectionError("INVALID_IMAGE", "Image must decode as RGB")
        started = time.perf_counter()
        detections, backend = self._infer_image(image, conf=conf, iou=iou, imgsz=imgsz,
                                                max_det=max_det, classes=classes)
        inferred = time.perf_counter()
        warnings = []
        if len(detections) >= max_det:
            warnings.append("MAX_DET_REACHED: detections may be truncated")
        if max(image.size) > imgsz:
            warnings.append("IMAGE_DOWNSCALED: small targets may be lost; no tiling")
        return {
            "schema_version": "ship-detection/0.1", "created_at": datetime.now(timezone.utc).isoformat(),
            "image": {"file_name": None, "sha256": None,
                      "width": image.width, "height": image.height, "mode": "RGB"},
            "coordinates": {"unit": "pixel", "origin": "top-left", "x_axis": "right", "y_axis": "down",
                            "polygon_order": "clockwise; start=min(y,x)", "space": "original_image",
                            "clipped": False},
            "model": dict(self.metadata),
            "parameters": {"conf": conf, "iou": iou, "imgsz": imgsz, "max_det": max_det,
                           "classes": self._effective_classes(classes),
                           "classes_filtered": classes is not None,
                           "half": False, "augment": False, "tiling": False},
            "timing_ms": {"model_load": self.load_ms, "decode": (inferred - started) * 1000,
                          "predict_call": (inferred - started) * 1000,
                          "total_before_save": (inferred - started) * 1000, "backend": backend},
            "count": len(detections), "warnings": warnings, "detections": detections,
        }


def save_result(result: dict, image_path: str | Path, output_dir: str | Path) -> dict:
    """Draw the exact exported polygons, rather than a separate model plot."""
    start = time.perf_counter()
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    image = read_image(image_path, max_pixels=MAX_LARGE_PIXELS if result["parameters"]["tiling"] else MAX_PIXELS)
    if sha256(Path(image_path)) != result["image"]["sha256"]:
        raise DetectionError("IMAGE_CHANGED", "Input image changed after inference")
    draw = ImageDraw.Draw(image)
    for detection in result["detections"]:
        points = [tuple(p) for p in detection["polygon"]]
        draw.line(points + [points[0]], fill=(255, 50, 30), width=2)
        x, y = points[0]
        draw.text((max(0, x), max(0, y)),
                  f'{detection["id"]} {detection.get("class_name", "object")} {detection["confidence"]:.2f}',
                  fill=(255, 255, 0))
    preview_path = directory / "preview.png"
    json_path = directory / "result.json"
    if Path(image_path).resolve() == preview_path.resolve():
        raise DetectionError("OUTPUT_CONFLICT", "Preview must not overwrite the input")
    image.save(preview_path)
    result["timing_ms"]["preview_save"] = (time.perf_counter() - start) * 1000
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return {"result": str(json_path.resolve()), "preview": str(preview_path.resolve())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/inference")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--max-det", type=int, default=1000)
    parser.add_argument("--classes", default="all",
                        help='all（默认，输出全部类别）或逗号分隔的类别 id，例如 "1" 只输出船')
    parser.add_argument("--tiled", action="store_true", help="Use overlapping tiles and polygon NMS")
    parser.add_argument("--tile-size", type=int, default=1024)
    parser.add_argument("--stride", type=int, default=512)
    parser.add_argument("--merge-iou", type=float, default=0.1)
    args = parser.parse_args(argv)
    try:
        read_image(args.image, max_pixels=MAX_LARGE_PIXELS if args.tiled else MAX_PIXELS).close()
        if not args.tiled and (args.tile_size != 1024 or args.stride != 512 or args.merge_iou != 0.1):
            raise DetectionError("INVALID_ARGUMENT", "Tile options require --tiled")
        raw_classes = str(args.classes).strip().lower()
        if raw_classes in {"all", "", "none", "*"}:
            classes = None
        else:
            try:
                classes = [int(v) for v in raw_classes.replace(" ", "").split(",") if v != ""]
            except ValueError as exc:
                raise DetectionError("INVALID_ARGUMENT", "--classes must be 'all' or comma-separated ids") from exc
        ShipDetector.validate_parameters(args.conf, args.iou, args.imgsz, args.max_det, classes)
        if args.tiled:
            from .tiled_inference import validate_tiling
            validate_tiling(args.tile_size, args.stride, args.merge_iou)
        detector = ShipDetector(args.weights, args.device)
        options = dict(conf=args.conf, iou=args.iou, imgsz=args.imgsz, max_det=args.max_det,
                       classes=classes)
        if args.tiled:
            result = detector.predict_large(args.image, tile_size=args.tile_size, stride=args.stride,
                                            merge_iou=args.merge_iou, **options)
        else:
            result = detector.predict(args.image, **options)
        paths = save_result(result, args.image, args.output)
        by_class = {}
        for det in result["detections"]:
            by_class[det["class_name"]] = by_class.get(det["class_name"], 0) + 1
        print(json.dumps({"ok": True, "count": result["count"], "by_class": by_class, **paths},
                         ensure_ascii=False))
        return 0
    except DetectionError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ImportError, OSError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "ENVIRONMENT_OR_IO", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
