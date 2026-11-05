"""图像校验、预览与请求体解码。

校验规则一律复用 `algorithm/ship_detector.read_image`，保证"上传时接受的影像"
与"检测时接受的影像"是同一套标准，不在后端另写一套。
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from algorithm.errors import DetectionError
from algorithm.ship_detector import read_image

from . import config, errors


def describe(path: str | Path) -> dict:
    """真实解码一次，返回尺寸与模式；不通过则抛 DetectionError。"""
    image = read_image(path, max_pixels=config.MAX_PIXELS)
    try:
        return {"width": image.width, "height": image.height, "mode": image.mode,
                "format": image.format or Path(path).suffix.lstrip(".").upper()}
    finally:
        image.close()


def make_preview(source: str | Path, target: Path, max_edge: int = config.PREVIEW_MAX_EDGE) -> dict:
    """生成等比缩略图（JPEG）。返回预览尺寸。"""
    image = read_image(source, max_pixels=config.MAX_PIXELS)
    try:
        scale = min(1.0, max_edge / max(image.size))
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        preview = image.resize(size, Image.LANCZOS) if scale < 1 else image.copy()
    finally:
        image.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        preview.save(target, format="JPEG", quality=85, optimize=True)
    finally:
        preview.close()
    return {"width": size[0], "height": size[1]}


def decode_data_url(data_url: str) -> bytes:
    """解析 `data:image/jpeg;base64,...`，拒绝非图片与非 base64。"""
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise errors.invalid("INVALID_FRAME", "缺少帧数据，或格式不是 data URL")
    header, _, payload = data_url.partition(",")
    if "base64" not in header:
        raise errors.invalid("INVALID_FRAME", "只接受 base64 编码的帧数据")
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise errors.invalid("INVALID_FRAME", "帧数据无法解码") from exc
    if not raw:
        raise errors.invalid("INVALID_FRAME", "帧数据为空")
    if len(raw) > config.VISION_MAX_BYTES:
        raise errors.too_large("FRAME_TOO_LARGE", f"帧超过 {config.VISION_MAX_BYTES} 字节上限")
    return raw


def load_request_image(raw: bytes) -> Image.Image:
    """把请求体里的字节解码为 RGB 图像，规则与磁盘影像一致。"""
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG"} or source.mode != "RGB":
                raise errors.invalid("INVALID_FRAME", "帧必须是 RGB PNG/JPEG")
            if source.width * source.height > config.MAX_PIXELS:
                raise errors.too_large("FRAME_TOO_LARGE", f"帧超过 {config.MAX_PIXELS} 像素上限")
            if source.getexif().get(274, 1) not in {None, 1}:
                raise errors.invalid("INVALID_FRAME", "帧带 EXIF 方向，请先归一化")
            source.load()
            return source.copy()
    except errors.ApiError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise errors.invalid("INVALID_FRAME", "帧无法解码为图像") from exc


def to_detection_error(exc: DetectionError) -> errors.ApiError:
    return errors.from_detection_error(exc)


@contextlib.contextmanager
def read_image_safe(path: str | Path):
    """`read_image` 的上下文管理器版本，保证异常路径也会释放解码缓冲。"""
    image = read_image(path, max_pixels=config.MAX_PIXELS)
    try:
        yield image
    finally:
        image.close()
