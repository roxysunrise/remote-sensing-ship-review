"""统一错误信封与算法错误码到 HTTP 语义的映射。

对外响应一律为 `{"error": {"code", "message", "details"}}`，与
`frontend/api/CONTRACT.md` §1 一致。算法层抛出的 `DetectionError` 在这里翻译，
并且**不会**把本机绝对路径带出接口。
"""
from __future__ import annotations

import re
from typing import Any

from algorithm.errors import DetectionError

from . import config

#: 算法/业务错误码 -> HTTP 状态码。未列出的按 400 处理。
CODE_HTTP: dict[str, int] = {
    # 输入与资源
    "INPUT_NOT_FOUND": 404,
    "INVALID_IMAGE": 400,
    "IMAGE_TOO_LARGE": 413,
    "EXIF_ORIENTATION": 422,
    "INVALID_ARGUMENT": 422,
    "TILE_LIMIT": 422,
    "CANDIDATE_LIMIT": 422,
    # 计算环境
    "WEIGHTS_NOT_FOUND": 503,
    "INVALID_DEVICE": 503,
    "MODEL_LOAD_FAILED": 503,
    "INCOMPATIBLE_MODEL": 503,
    "MISSING_SHIP_CLASS": 503,
    "OUT_OF_MEMORY": 503,
    "INFERENCE_FAILED": 500,
    "INVALID_MODEL_OUTPUT": 500,
    "ENVIRONMENT_OR_IO": 500,
    # 状态冲突
    "IMAGE_CHANGED": 409,
    "OUTPUT_CONFLICT": 409,
    "CANCELLED": 409,
}

_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/][^\s'\"<>|]*)|(?:/(?:root|home|tmp|var|Users)/[^\s'\"<>|]*)")


def sanitize(message: str) -> str:
    """去掉本机绝对路径，避免内部目录结构泄漏到前端。"""
    text = str(message)
    for known in {str(config.ROOT), str(config.VAR)}:
        text = text.replace(known, "<workspace>")
    return _ABS_PATH.sub("<path>", text)


def envelope(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": sanitize(message), "details": details or {}}}


class ApiError(Exception):
    """业务/接口层错误。"""

    def __init__(self, status: int, code: str, message: str, details: Any = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def payload(self) -> dict:
        return envelope(self.code, self.message, self.details)


def from_detection_error(exc: DetectionError) -> ApiError:
    code = getattr(exc, "code", "INTERNAL_ERROR")
    return ApiError(CODE_HTTP.get(code, 400), code, str(exc))


# 常用快捷构造
def unauthenticated(message: str = "会话失效，请重新登录") -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", message)


def forbidden(message: str = "你不是该项目的成员，访问被拒绝") -> ApiError:
    return ApiError(403, "PROJECT_FORBIDDEN", message)


def not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)


def conflict(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(409, code, message, details)


def invalid(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(422, code, message, details)


def too_large(code: str, message: str) -> ApiError:
    return ApiError(413, code, message)


def unavailable(code: str, message: str) -> ApiError:
    return ApiError(503, code, message)
