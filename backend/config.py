"""运行时配置。所有值都可被环境变量覆盖，默认值面向本机演示。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VAR = Path(os.environ.get("SHIP_VAR_DIR") or (ROOT / "var")).resolve()
BLOB_DIR = VAR / "blobs"
PREVIEW_DIR = VAR / "previews"
RESULT_DIR = VAR / "results"
EXPORT_DIR = VAR / "exports"
CHUNK_DIR = VAR / "chunks"
TMP_DIR = VAR / "tmp"
DB_PATH = Path(os.environ.get("SHIP_DB_PATH") or (VAR / "ship.db")).resolve()

WEIGHTS = Path(os.environ.get("SHIP_WEIGHTS") or (ROOT / "models/yolov8l-obb-dota-v1-80e/best.pt")).resolve()
DEVICE = os.environ.get("SHIP_DEVICE", "cpu")

CHUNK_SIZE = int(os.environ.get("SHIP_CHUNK_SIZE", 1 << 20))
MAX_UPLOAD_BYTES = int(os.environ.get("SHIP_MAX_UPLOAD_BYTES", 256 << 20))
MAX_PIXELS = int(os.environ.get("SHIP_MAX_PIXELS", 67_108_864))
PREVIEW_MAX_EDGE = int(os.environ.get("SHIP_PREVIEW_MAX_EDGE", 2048))

SESSION_TTL_HOURS = int(os.environ.get("SHIP_SESSION_TTL_HOURS", 7 * 24))
UPLOAD_TTL_HOURS = int(os.environ.get("SHIP_UPLOAD_TTL_HOURS", 24))
EXPORT_TTL_HOURS = int(os.environ.get("SHIP_EXPORT_TTL_HOURS", 72))

# 队列治理：每项目同时排队/运行的任务数、全局排队长度
MAX_ACTIVE_TASKS = int(os.environ.get("SHIP_MAX_ACTIVE_TASKS", 2))
MAX_QUEUE_LENGTH = int(os.environ.get("SHIP_MAX_QUEUE_LENGTH", 20))
# 启动时后台预热模型（首次交互不用等 2.6s 加载）
PRELOAD_MODEL = os.environ.get("SHIP_PRELOAD", "0") not in {"0", "false", "False"}

SEED_PASSWORD = os.environ.get("SHIP_SEED_PASSWORD", "demo-1234")
SEED_ON_START = os.environ.get("SHIP_SEED_ON_START", "1") not in {"0", "false", "False"}

# 与 contracts/ship-detection-v0.2.md 的默认值一致
DEFAULT_PARAMETERS = {
    "conf": 0.25,
    "iou": 0.7,
    "imgsz": 1024,
    "max_det": 1000,
    "tiling": True,
    "tile_size": 1024,
    "stride": 512,
    "merge_iou": 0.1,
}

# /vision/detect 允许的最大边长（客户端下采样上限，见 frontend/api/DETECT_API.md）
VISION_MAX_EDGE = int(os.environ.get("SHIP_VISION_MAX_EDGE", 8192))
VISION_MAX_BYTES = int(os.environ.get("SHIP_VISION_MAX_BYTES", 32 << 20))

CORS_ORIGINS = [o for o in os.environ.get(
    "SHIP_CORS_ORIGINS",
    "http://127.0.0.1:8765,http://localhost:8765",
).split(",") if o]

USER_ROLE_OWNER = "项目负责人"
USER_ROLE_ANALYST = "项目分析员"


def ensure_dirs() -> None:
    for path in (VAR, BLOB_DIR, PREVIEW_DIR, RESULT_DIR, EXPORT_DIR, CHUNK_DIR, TMP_DIR):
        path.mkdir(parents=True, exist_ok=True)
