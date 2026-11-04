"""大图瓦片：显示用的金字塔 + 推理切片的预演。

两件不同的事，别混：

- **显示瓦片**（`level_tiles` / `read_tile`）：不重叠、按缩放层级切，浏览器只取视口内那几块；
  `z=0` 是原生分辨率（1:1），`z` 越大越粗，最大层级整图一块。
- **推理切片预演**（`plan_inference_tiles`）：复用 `algorithm.plan_tiles` 的重叠切片，
  让界面在跑模型之前就能画出真实网格，并与任务实际切的片一一对应。

瓦片按需生成并落盘缓存（`var/tiles/{image_id}/{z}/{x}_{y}.jpg`），不预生成整棵金字塔。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from algorithm.tiled_inference import plan_tiles

from . import config, storage

TILE_EDGE = 256
JPEG_QUALITY = 82


def tile_dir(image_id: str) -> Path:
    return config.TILE_DIR / image_id


def _level_size(width: int, height: int, z: int) -> tuple[int, int]:
    factor = 2 ** z
    return max(1, math.ceil(width / factor)), max(1, math.ceil(height / factor))


def max_level(width: int, height: int) -> int:
    """让最大层的长边只剩 1 块瓦片。"""
    longest = max(width, height)
    return max(0, math.ceil(math.log2(max(1, longest) / TILE_EDGE)))


def level_tiles(width: int, height: int, z: int, edge: int = TILE_EDGE) -> tuple[int, int]:
    level_w, level_h = _level_size(width, height, z)
    return math.ceil(level_w / edge), math.ceil(level_h / edge)


def manifest(image_row) -> dict:
    width, height = int(image_row["width"]), int(image_row["height"])
    top = max_level(width, height)
    levels = []
    for z in range(top + 1):
        level_w, level_h = _level_size(width, height, z)
        cols, rows = level_tiles(width, height, z)
        levels.append({"z": z, "width": level_w, "height": level_h,
                       "cols": cols, "rows": rows, "scale": 1 / (2 ** z)})
    return {
        "image_id": image_row["id"],
        "width": width,
        "height": height,
        "tile_edge": TILE_EDGE,
        "max_z": top,
        # z=0 为原生分辨率，z 越大越粗；前端按 scale 选层
        "levels": levels,
        "url_template": f"/tiles/{image_row['id']}/{{z}}/{{x}}/{{y}}",
    }


def read_tile(image_row, z: int, x: int, y: int, edge: int = TILE_EDGE) -> tuple[Path, bool]:
    """返回 (瓦片路径, 是否命中缓存)。越界抛 ValueError。"""
    width, height = int(image_row["width"]), int(image_row["height"])
    if z < 0 or z > max_level(width, height):
        raise ValueError("层级超出范围")
    cols, rows = level_tiles(width, height, z, edge)
    if not (0 <= x < cols and 0 <= y < rows):
        raise ValueError("瓦片坐标超出范围")

    target = tile_dir(image_row["id"]) / str(z) / f"{x}_{y}.jpg"
    if target.is_file():
        return target, True

    source = storage.blob_path(image_row["stored_name"])
    if not source.is_file():
        raise FileNotFoundError("原图缺失")

    factor = 2 ** z
    src_box = (x * edge * factor, y * edge * factor,
               min(width, (x + 1) * edge * factor), min(height, (y + 1) * edge * factor))
    level_w, level_h = _level_size(width, height, z)
    dest_size = (min(edge, level_w - x * edge), min(edge, level_h - y * edge))

    with Image.open(source) as handle:
        image = handle.convert("RGB")
    try:
        crop = image.crop(src_box)
        try:
            tile = crop.resize(dest_size, Image.LANCZOS) if crop.size != dest_size else crop.copy()
        finally:
            crop.close()
    finally:
        image.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tile.save(tmp, format="JPEG", quality=JPEG_QUALITY)
        tmp.replace(target)
    finally:
        tile.close()
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return target, False


def tile_etag(image_row, z: int, x: int, y: int) -> str:
    return f"{image_row['sha256']}-{z}-{x}-{y}-e{TILE_EDGE}"


def plan_inference_tiles(image_row, tile_size: int, stride: int) -> dict:
    """推理切片预演：不跑模型，只返回真实网格与覆盖情况。"""
    width, height = int(image_row["width"]), int(image_row["height"])
    tiles = plan_tiles(width, height, tile_size, stride)
    step = stride if stride < tile_size else tile_size
    covered = sum((right - left) * (bottom - top) for left, top, right, bottom in tiles)
    return {
        "image_id": image_row["id"],
        "image": {"width": width, "height": height},
        "parameters": {"tile_size": tile_size, "stride": stride},
        "tile_count": len(tiles),
        "tiles": [{"id": index, "x": left, "y": top,
                   "width": right - left, "height": bottom - top}
                  for index, (left, top, right, bottom) in enumerate(tiles, 1)],
        "coverage": {
            "covered_px": covered,
            "image_px": width * height,
            "overlap_ratio": round(covered / max(1, width * height) - 1, 4),
            "step": step,
        },
        "note": "仅几何预演，未运行模型；片序与任务实际切片的 id 一致",
    }


def purge_tiles(image_id: str | None = None) -> int:
    """清理瓦片缓存（可重算，不算业务数据）。"""
    import shutil

    removed = 0
    roots = [config.TILE_DIR / image_id] if image_id else list(config.TILE_DIR.glob("*"))
    for path in roots:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed
