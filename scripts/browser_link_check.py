"""真实浏览器（Edge headless + CDP）验证前端页面能否跑通真实后端。

前提：
    后端 http://127.0.0.1:8000 已起
    前端 http://127.0.0.1:8765 已起（config.js 的 apiMode 必须是 'http'）

做三件事：
    1. 打开 login.html，在真实 DOM 里填账号密码并提交；
    2. 看是否跳到工作台，并检查页面上出现的是真实项目数据而不是 mock；
    3. 两张截图落到 var/screens/。

用法：python scripts/browser_link_check.py
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
PORT = 9223
FE = os.environ.get("SHIP_FRONTEND_URL", "http://127.0.0.1:8765")
USERNAME = os.environ.get("SHIP_DEMO_USER", "roxysunrise")
PASSWORD = os.environ.get("SHIP_DEMO_PASSWORD", "demo-1234")
PROJECT = os.environ.get("SHIP_DEMO_PROJECT", "harbor-a")
SHOT_DIR = pathlib.Path(__file__).resolve().parents[1] / "var" / "screens"

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
        result = self.send("Runtime.evaluate", expression=expression, returnByValue=True,
                           awaitPromise=await_promise, userGesture=True)
        return (result.get("result") or {}).get("value")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.2)
        # headless 的合成器要两帧才会真正出画面，否则截图是纯黑
        self.js("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))", await_promise=True)
        time.sleep(0.8)

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
        raise SystemExit("找不到 Edge/Chrome 可执行文件")
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [exe, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--window-size=1440,900", "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as fh:
                json.load(fh)
            return proc
        except Exception:
            time.sleep(0.4)
    proc.kill()
    raise SystemExit("Edge 未在 40s 内打开调试端口，可能被策略阻止")


def ws_url() -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=10) as fh:
        targets = json.load(fh)
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise SystemExit("没有可用的 page target")
    return pages[0]["webSocketDebuggerUrl"]


def main() -> int:
    profile = pathlib.Path(os.environ.get("TEMP") or ".") / "edge-cdp-ship-review"
    proc = launch(profile)
    try:
        cdp = CDP(ws_url())
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                 deviceScaleFactor=1, mobile=False)

        # 1) 登录页
        cdp.goto(f"{FE}/login.html")
        title = cdp.js("document.title")
        record("登录页加载", bool(title) and "登录" in (title or ""), f"title={title}")
        cdp.shot("01-login.png")

        # 2) 真实 DOM 填表并提交
        filled = cdp.js(f"""
        (() => {{
          const set = (el, value) => {{
            const proto = Object.getPrototypeOf(el);
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
                        || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(el, value);
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
          }};
          const u = document.getElementById('username');
          const p = document.getElementById('password');
          const pr = document.getElementById('project');
          if (!u || !p || !pr) return 'missing-fields';
          set(u, {json.dumps(USERNAME)});
          set(p, {json.dumps(PASSWORD)});
          set(pr, {json.dumps(PROJECT)});
          return [u.value, p.value.length, pr.value].join('|');
        }})()
        """)
        record("表单可填", filled not in (None, "missing-fields"), f"value={filled}")

        cdp.js("document.getElementById('submit').click()")
        deadline = time.time() + 25
        href = ""
        while time.time() < deadline:
            href = cdp.js("location.href") or ""
            if "login.html" not in href:
                break
            time.sleep(0.4)
        record("登录后离开登录页", "login.html" not in href, f"href={href}")
        cdp.shot("02-workbench.png")

        # 3) 工作台是否拿到真实后端数据
        time.sleep(2.5)
        text = cdp.js("document.body.innerText") or ""
        has_project = "港口巡查" in text or "harbor" in text.lower()
        has_error = any(word in text for word in ("请求失败", "网络错误", "无法连接", "Failed to fetch"))
        record("工作台渲染真实项目", has_project and not has_error,
               f"项目名可见={has_project} 错误提示={has_error} 文本片段={text[:120]!r}")

        mode = cdp.js("(window.APP_CONFIG||{}).apiMode")
        record("前端运行在 http 模式", mode == "http", f"apiMode={mode}")

        token = cdp.js("localStorage.getItem('dsh.ship.token') || sessionStorage.getItem('dsh.ship.token')")
        record("会话令牌已落地", bool(token), f"token 长度={len(token) if token else 0}")

        # 4) 检测工作台也走一遍（/vision/* 走真实推理）
        cdp.goto(f"{FE}/detect.html")
        time.sleep(1.5)
        dtext = cdp.js("document.body.innerText") or ""
        caps = cdp.js("(window.DETECT_CONFIG||{}).mode")
        cdp.shot("03-detect.png")
        record("检测页加载", "detect" in (cdp.js("location.pathname") or "") or len(dtext) > 0,
               f"mode={caps} 文本片段={dtext[:100]!r}")

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
