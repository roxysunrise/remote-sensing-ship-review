"""前端静态服务 + `/api/v1` 反向代理：一条命令把前后端放成同源。

为什么需要它：
    `frontend/config.js` 默认 `baseUrl: ''`（同源语义），跨端口直连还要处理 CORS。
    这个脚本把静态页面和后端 API 挂在同一个 origin 下，前端零配置、无需 CORS。

用法：
    python scripts/serve.py --frontend frontend --port 8765 --backend http://127.0.0.1:8000

功能边界：
    - 只代理 `/api/v1` 开头的路径。注意前端的 `frontend/api/*.js` 也以 `/api/` 开头，
      所以不能按 `/api/` 粗匹配，否则页面脚本会被当成接口请求（实测 404）。
    - 转发方法、认证头、条件请求头与状态码（含 304），二进制分片上传与文件下载原样透传；
    - 不缓存、不改写响应体；后端不可达时返回 502 而不是挂住页面。
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import socketserver
import sys
import urllib.parse
from pathlib import Path

PROXY_PREFIXES = ("/api/v1/", "/api/v1")
FORWARD_HEADERS = ("Authorization", "Content-Type", "Accept", "If-None-Match", "Range",
                   "X-Request-Id", "Origin", "Referer")
# 逐跳头与长度头不能直接复制：长度由本层重新计算
SKIP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer",
                "content-length", "content-encoding", "proxy-authenticate",
                "proxy-authorization"}


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    backend_host: tuple[str, int] = ("127.0.0.1", 8000)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[serve] %s %s\n" % (self.address_string(), fmt % args))

    # --- 代理 ------------------------------------------------------------
    def _should_proxy(self) -> bool:
        return any(self.path.startswith(prefix) for prefix in PROXY_PREFIXES)

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {}
        for key in FORWARD_HEADERS:
            value = self.headers.get(key)
            if value:
                headers[key] = value
        connection = http.client.HTTPConnection(*self.backend_host, timeout=300)
        try:
            connection.request(method, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except OSError as exc:
            self.send_error(502, f"backend unreachable: {exc}")
            return
        finally:
            connection.close()

        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in SKIP_HEADERS:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD" and payload:
            self.wfile.write(payload)

    # --- 方法分发 --------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("GET")
        super().do_GET()

    def do_HEAD(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("HEAD")
        super().do_HEAD()

    def do_POST(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("POST")
        self.send_error(405, "只支持静态文件读取")

    def do_PUT(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("PUT")
        self.send_error(405, "只支持静态文件读取")

    def do_PATCH(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("PATCH")
        self.send_error(405, "只支持静态文件读取")

    def do_DELETE(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("DELETE")
        self.send_error(405, "只支持静态文件读取")

    def do_OPTIONS(self):  # noqa: N802
        if self._should_proxy():
            return self._proxy("OPTIONS")
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="前端静态服务 + /api 反代（同源联调）")
    parser.add_argument("--frontend", required=True, help="frontend 目录")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    root = Path(args.frontend).resolve()
    if not (root / "login.html").is_file():
        print(f"前端目录不对，找不到 login.html：{root}", file=sys.stderr)
        return 2
    parsed = urllib.parse.urlsplit(args.backend)
    Handler.backend_host = (parsed.hostname or "127.0.0.1", parsed.port or 80)

    def handler(*a, **kw):
        return Handler(*a, directory=str(root), **kw)

    with Server(("127.0.0.1", args.port), handler) as httpd:
        print(f"页面 http://127.0.0.1:{args.port}/login.html", flush=True)
        print(f"接口 {args.backend}/api/v1 → 同源 /api/v1（已反代，无需 CORS）", flush=True)
        print("Ctrl+C 停止", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
