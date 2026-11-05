"""ASGI 入口：`python -m backend.server`。

- 统一错误处理器：把 `ApiError` 与算法 `DetectionError` 翻译成契约里的错误信封；
- 请求校验失败也回同样的信封，前端只看 `error.code`；
- CORS 默认只放行本地演示端口。

启动：
    python -m backend.server --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from . import api, config, db, detector, errors, jobs, maintenance, vision  # noqa: E402

log = logging.getLogger("backend")

app = FastAPI(
    title="遥感船舶检测与复核系统 API",
    version=__import__("backend").__version__,
    description="为 frontend/ 的 29 个业务端点与 5 个推理端点提供真实实现。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "ETag", "X-Request-Id"],
)


@app.middleware("http")
async def _request_context(request: Request, call_next):
    """给每个请求一个 ID，回写响应头并带进错误信封，方便前端和日志对应。"""
    request_id = request.headers.get("x-request-id") or f"req_{secrets.token_hex(6)}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


def _with_request_id(request: Request, payload: dict) -> dict:
    request_id = getattr(request.state, "request_id", None)
    if request_id and isinstance(payload.get("error"), dict):
        details = payload["error"].get("details")
        payload["error"]["details"] = {**(details if isinstance(details, dict) else {}),
                                      "request_id": request_id}
    return payload


@app.exception_handler(errors.ApiError)
async def _api_error_handler(request: Request, exc: errors.ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=_with_request_id(request, exc.payload()))


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"loc": list(err.get("loc", [])), "msg": err.get("msg", ""),
                "type": err.get("type", "")} for err in exc.errors()]
    payload = errors.envelope("INVALID_ARGUMENT", "请求参数不合法", {"errors": details})
    return JSONResponse(status_code=422, content=_with_request_id(request, payload))


def _preload_model() -> None:
    try:
        detector.service.load()
        log.info("模型预加载完成")
    except Exception as exc:  # 预加载失败不影响服务启动，首个请求会再次尝试并返回 503
        log.warning("模型预加载失败：%s", exc)


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    db.init_db()
    # 上传校验在后台线程里做，进程重启后不会自己继续，同样要如实标记
    db.execute("UPDATE uploads SET status='failed', stage='failed', error=?"
               " WHERE status='verifying'", ("SERVER_RESTART: 服务重启导致校验中断，请重新提交",))
    summary = maintenance.run_startup_maintenance()
    if any(summary.values()):
        log.info("启动清理：%s", summary)
    jobs.runtime.start()
    if config.PRELOAD_MODEL:
        threading.Thread(target=_preload_model, name="model-preload", daemon=True).start()


app.include_router(api.router)
app.include_router(vision.router)
# 前端默认把 baseUrl + '/api/v1' 拼在前面（见 frontend/api/client.js），这里同时挂一份带前缀的路由，
# 免去前端为了联调改 apiPrefix；契约文档里的路径仍以不带前缀为准。
app.include_router(api.router, prefix="/api/v1", include_in_schema=False)
app.include_router(vision.router, prefix="/api/v1", include_in_schema=False)


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="遥感船舶检测与复核系统后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("backend.server:app", host=args.host, port=args.port, reload=args.reload,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
