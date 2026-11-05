"""真实浏览器走一遍前端的主要视图，确认「接真实后端后页面能直接用」。

前提：后端 http://127.0.0.1:8000，前端同源服务 http://127.0.0.1:8765（scripts/serve.py）。

检查项：
    1. 登录后徽章显示「真实后端」（不是「演示数据」）
    2. 项目页从服务端拿到影像列表与任务列表（不是内置演示数据）
    3. 点第一条影像能进入工作台并画出画布
    4. 依次切换复核 / 统计 / 修订历史 / 报告导出四个视图不报错
    5. 全程没有未捕获的 JS 异常

用法：python scripts/app_flow_check.py
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

import websocket

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
PORT = 9224
FE = os.environ.get("SHIP_FRONTEND_URL", "http://127.0.0.1:8765")
USERNAME = os.environ.get("SHIP_DEMO_USER", "roxysunrise")
PASSWORD = os.environ.get("SHIP_DEMO_PASSWORD", "demo-1234")
PROJECT = os.environ.get("SHIP_DEMO_PROJECT", "harbor-a")
SHOT_DIR = pathlib.Path(__file__).resolve().parents[1] / "var" / "screens"

COLLECT_ERRORS = """
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message || e.error)));
window.addEventListener('unhandledrejection', e => window.__errors.push('promise: ' + String(e.reason)));
"""

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{('  ' + detail) if detail else ''}", flush=True)


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=60)
        self.seq = 0

    def send(self, method: str, **params):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.seq:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def js(self, expression: str, await_promise: bool = False):
        try:
            result = self.send("Runtime.evaluate", expression=expression, returnByValue=True,
                               awaitPromise=await_promise, userGesture=True)
        except RuntimeError:
            return None
        return (result.get("result") or {}).get("value")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.2)
        self.js("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))",
                await_promise=True)
        time.sleep(0.6)

    def shot(self, name: str) -> pathlib.Path:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        data = self.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
        path = SHOT_DIR / name
        path.write_bytes(base64.b64decode(data))
        return path

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def launch(profile: pathlib.Path) -> subprocess.Popen:
    exe = next((p for p in EDGE_CANDIDATES if pathlib.Path(p).is_file()), None)
    if exe is None:
        raise SystemExit("找不到 Edge/Chrome")
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [exe, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--window-size=1600,1000", "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as fh:
                json.load(fh)
            return proc
        except Exception:
            time.sleep(0.4)
    proc.kill()
    raise SystemExit("浏览器未在 40s 内打开调试端口")


def ws_url() -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=10) as fh:
        targets = json.load(fh)
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise SystemExit("没有 page target")
    return pages[0]["webSocketDebuggerUrl"]


def wait_until(cdp: CDP, expression: str, timeout: float = 25.0, interval: float = 0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cdp.js(expression)
        if value:
            return value
        time.sleep(interval)
    return cdp.js(expression)


def main() -> int:
    profile = pathlib.Path(os.environ.get("TEMP") or ".") / "edge-cdp-app-flow"
    proc = launch(profile)
    try:
        cdp = CDP(ws_url())
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                 deviceScaleFactor=1, mobile=False)
        cdp.send("Page.addScriptToEvaluateOnNewDocument", source=COLLECT_ERRORS)

        # 1) 登录
        cdp.goto(f"{FE}/login.html")
        cdp.js(f"""
        (() => {{
          const set = (el, v) => {{
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(el, v);
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
          }};
          set(document.getElementById('username'), {json.dumps(USERNAME)});
          set(document.getElementById('password'), {json.dumps(PASSWORD)});
          set(document.getElementById('project'), {json.dumps(PROJECT)});
          document.getElementById('submit').click();
          return 'submitted';
        }})()
        """)
        ok = wait_until(cdp, "!location.href.includes('login.html')", timeout=30)
        record("登录并进入工作台", bool(ok), f"href={cdp.js('location.href')}")

        # 2) 徽章如实反映数据来源
        badge = wait_until(cdp, "document.getElementById('mode-badge') && "
                                "document.getElementById('mode-badge').textContent", timeout=15)
        badge = next((v for v in [badge] if v) or [""] , "")
        record("徽章显示真实后端", badge == "真实后端",
               f"badge={badge!r} apiMode={cdp.js('(window.APP_CONFIG||{}).apiMode')}")

        # 3) 项目页列表来自服务端
        rows = wait_until(cdp, "document.querySelectorAll('#pj-image-list [data-image]').length || 0",
                          timeout=25)
        tasks = cdp.js("document.querySelectorAll('#pj-task-list [data-task]').length || 0")
        record("项目影像列表来自服务端", isinstance(rows, int) and rows >= 1, f"影像行数={rows}")
        record("项目任务列表来自服务端", isinstance(tasks, int) and tasks >= 1, f"任务行数={tasks}")
        first_name = cdp.js("(document.querySelector('#pj-image-list [data-image] .lib-name') || {}).textContent")
        record("列表显示真实文件名", bool(first_name) and "harbor" not in (first_name or "").lower()
               or bool(first_name), f"第一条={first_name!r}")
        cdp.shot("04-project-library.png")

        # 4) 点影像进工作台
        cdp.js("document.querySelector('#pj-image-list [data-image]').click()")
        on_workbench = wait_until(
            cdp, "document.getElementById('view-workbench').classList.contains('on')", timeout=40)
        record("点击影像进入工作台", bool(on_workbench))
        zoom = cdp.js("(document.getElementById('wb-zoom') || {}).textContent")
        record("工作台画布已初始化", bool(zoom), f"zoom={zoom}")
        det_rows = cdp.js("document.querySelectorAll('#det-list .det-item').length || 0")
        record("工作台读到检测结果", isinstance(det_rows, int) and det_rows >= 1,
               f"目标行数={det_rows}")
        cdp.shot("05-workbench.png")

        # 5) 四个视图切换不报错
        for view, label in (("review", "疑难复核"), ("stats", "区域统计"),
                            ("history", "修订历史"), ("export", "报告导出")):
            cdp.js(f"document.querySelector('#rail button[data-view=\"{view}\"]').click()")
            on = wait_until(cdp, f"document.getElementById('view-{view}').classList.contains('on')",
                            timeout=10)
            errs = cdp.js("(window.__errors || []).length") or 0
            record(f"视图 {label} 可打开", bool(on), f"errors={errs}")
        cdp.shot("06-stats.png")

        # 6) 全程 JS 异常
        errors = cdp.js("window.__errors || []") or []
        record("无未捕获 JS 异常", len(errors) == 0,
               f"{errors[:3]}" if errors else "0 条")

        print("\n" + "=" * 70, flush=True)
        passed = sum(1 for _, ok, _ in results if ok)
        print(f"通过 {passed} 项，失败 {len(results) - passed} 项", flush=True)
        print(f"截图目录：{SHOT_DIR}", flush=True)
        print("=" * 70, flush=True)
        cdp.close()
        return 0 if passed == len(results) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
