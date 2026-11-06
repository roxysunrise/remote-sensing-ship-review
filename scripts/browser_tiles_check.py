"""Check the real tiled workbench in Edge/Chrome against a seeded synthetic image."""
from pathlib import Path
import json
import os
import tempfile

import app_flow_check as browser
from seed_tile_fixture import IMAGE_ID, MARKERS, QUADRANTS, TASK_ID


def check(cdp, label, expression, *, promise=False):
    value = cdp.js(expression, await_promise=True) if promise else browser.wait_until(
        cdp, expression, timeout=15)
    if not value:
        raise AssertionError(label)
    print(f"[PASS] {label}", flush=True)


def open_fixture(cdp):
    selector = json.dumps(f'#pj-image-list [data-image="{IMAGE_ID}"]')
    check(cdp, "loaded project image list", f"!!document.querySelector({selector})")
    cdp.js(f"document.querySelector({selector}).click()")
    check(cdp, "opened fixture task with four original-pixel boxes",
          f"state.view === 'workbench' && state.task.id === '{TASK_ID}' && state.draft.length === 4")


def main():
    browser.PORT = int(os.environ.get("SHIP_BROWSER_PORT", "19224"))
    with tempfile.TemporaryDirectory(prefix="ship-tile-browser-") as folder:
        proc = browser.launch(Path(folder) / "profile")
        cdp = None
        try:
            cdp = browser.CDP(browser.ws_url())
            cdp.send("Page.enable")
            cdp.send("Network.enable")
            cdp.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                     deviceScaleFactor=1, mobile=False)
            cdp.send("Page.addScriptToEvaluateOnNewDocument", source=browser.COLLECT_ERRORS)
            cdp.goto(f"{browser.FE}/login.html")
            cdp.js(f"""
            (() => {{
              for (const [id, value] of Object.entries({json.dumps({
                  "username": browser.USERNAME, "password": browser.PASSWORD, "project": browser.PROJECT
              })})) {{
                const element = document.getElementById(id);
                element.value = value;
                element.dispatchEvent(new Event('input', {{bubbles:true}}));
              }}
              document.getElementById('submit').click();
            }})()
            """)
            open_fixture(cdp)
            check(cdp, "authenticated tile requests populated the visible cache",
                  "WB.tileLayer?.cache.size > 0 && WB.vp.worldW === 1820 && WB.vp.worldH === 1020")
            check(cdp, "all four image quadrants align in original image coordinates", f"""
            (() => {{
              const probes = {json.dumps(list(QUADRANTS.values()))};
              return probes.every(({{probe, rgb}}) => {{
                const [x,y] = WB.vp.w2s(...probe);
                const actual = WB.vp.ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data;
                return rgb.every((channel, i) => Math.abs(actual[i] - channel) < 24);
              }});
            }})()
            """)

            cx, cy, original_ox = cdp.js("""
            (() => {
              const rect = WB.vp.canvas.getBoundingClientRect();
              return [rect.left + rect.width / 2, rect.top + rect.height / 2, WB.vp.ox];
            })()
            """)
            cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=cx, y=cy, deltaX=0, deltaY=900)
            check(cdp, "zoomed out and loaded coarser tiles over HTTP",
                  "[...WB.tileLayer.cache.keys()].some(key => !key.startsWith('0/'))")
            zoomed_ox = cdp.js("WB.vp.ox")
            cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=cx, y=cy,
                     button="middle", buttons=4, clickCount=1)
            cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=cx+60, y=cy, buttons=4)
            cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=cx+60, y=cy,
                     button="middle", buttons=0, clickCount=1)
            check(cdp, "panned the tiled viewport", f"Math.abs(WB.vp.ox - {zoomed_ox} - 60) < 3")
            cdp.js("document.getElementById('btn-fit').click()")
            check(cdp, "fit restored original-image alignment", f"Math.abs(WB.vp.ox - {original_ox}) < 1")

            x, y = MARKERS["tl"]
            destination_x = x + 100  # crosses original x=512 tile boundary
            points = cdp.js(f"""
            [{x}, {destination_x}].map(x => {{
              const [sx, sy] = WB.vp.w2s(x, {y});
              const rect = WB.vp.canvas.getBoundingClientRect();
              return [sx + rect.left, sy + rect.top];
            }})
            """)
            start, stop = points
            cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=start[0], y=start[1],
                     button="left", buttons=1, clickCount=1)
            cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=stop[0], y=stop[1], buttons=1)
            cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=stop[0], y=stop[1],
                     button="left", buttons=0, clickCount=1)
            moved = f"Math.abs(G.fromPolygon(state.draft.find(d => d.id === 1).polygon).cx - {destination_x}) < 3"
            check(cdp, "dragged an OBB across the tile boundary", moved)
            check(cdp, "waited for the draft PATCH response", moved.replace("state.draft", "state.serverDraft"))
            check(cdp, "cross-tile edit reached the backend draft",
                  f"API.getDraft(state.task.id).then(d => Math.abs(G.fromPolygon(d.detections.find(o => o.id === 1).polygon).cx - {destination_x}) < 3)",
                  promise=True)
            cdp.js("document.getElementById('btn-save').click()")
            check(cdp, "saved the modified geometry as a version", "state.versions.length >= 2 && !state.dirty")

            # Force a fresh view with the manifest blocked. The preview should
            # still use original-image coordinates and load the saved geometry.
            cdp.send("Network.setBlockedURLs", urls=["*/tile-manifest"])
            cdp.send("Network.setCacheDisabled", cacheDisabled=True)
            cdp.goto(f"{browser.FE}/index.html?preview-fallback=1")
            check(cdp, "loaded a fresh page for fallback", "location.search === '?preview-fallback=1'")
            open_fixture(cdp)
            check(cdp, "preview fallback and saved geometry survive full reload",
                  f"!!WB.tileLayer?.preview && !WB.tileLayer.manifest && ({moved})")
            check(cdp, "no uncaught browser errors", "window.__errors.length === 0")
        finally:
            if cdp:
                cdp.close()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except TimeoutError:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
