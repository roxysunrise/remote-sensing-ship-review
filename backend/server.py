"""ASGI 入口：`python -m backend.server`。

- 统一错误处理器：把 `ApiError` 与算法 `DetectionError` 翻译成契约里的错误信封；
- 请求校验失败也回同样的信封，前端只看 `error.code`；
- CORS 默认只放行本地演示端口。

启动：
    python -m backend.server --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from . import api, config, db, errors, jobs, vision  # noqa: E402

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
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(errors.ApiError)
async def _api_error_handler(_: Request, exc: errors.ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.payload())


@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"loc": list(err.get("loc", [])), "msg": err.get("msg", ""),
                "type": err.get("type", "")} for err in exc.errors()]
    return JSONResponse(status_code=422,
                        content=errors.envelope("INVALID_ARGUMENT", "请求参数不合法", {"errors": details}))


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    db.init_db()
    # 上传校验在后台线程里做，进程重启后不会自己继续，同样要如实标记
    db.execute("UPDATE uploads SET status='failed', stage='failed', error=?"
               " WHERE status='verifying'", ("SERVER_RESTART: 服务重启导致校验中断，请重新提交",))
    jobs.runtime.start()


app.include_router(api.router)
app.include_router(vision.router)


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
