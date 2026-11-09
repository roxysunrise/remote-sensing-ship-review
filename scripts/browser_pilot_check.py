"""Exercise the pilot prototype in a fresh local Edge profile."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import app_flow_check as browser


def main():
    browser.PORT = int(os.environ.get("SHIP_BROWSER_PORT", "19255"))
    page = Path(__file__).resolve().parents[1] / "frontend/pilot/index.html"
    with TemporaryDirectory(prefix="ship-pilot-") as temp:
        process = browser.launch(Path(temp) / "profile")
        cdp = None
        try:
            cdp = browser.CDP(browser.ws_url())
            cdp.send("Page.enable")
            cdp.send("Page.addScriptToEvaluateOnNewDocument", source=browser.COLLECT_ERRORS)
            cdp.goto(page.as_uri())

            def check(expression, label):
                if not cdp.js(expression):
                    raise AssertionError(label)
                print("[PASS]", label)

            def click(action):
                check(f"""document.querySelector('[data-action="{action}"]').click() === undefined""", action)

            def role(value):
                cdp.js(f"$('role').value='{value}'; $('role').dispatchEvent(new Event('change'))")

            click("assign:1")
            cdp.js("$('dialog-form').requestSubmit()")
            check("images[0].owner === 'lin'", "assigned to reviewer")
            click("nav:sign")
            click("open:4")
            click("return")
            cdp.js("document.querySelector('textarea').value='核对船尾'; $('dialog-form').requestSubmit()")
            check("images[3].status === 'editing' && images[3].reason === '核对船尾'", "returned with reason")
            role("review")
            check("queue().slice(0,2).every(i => i.reason)", "returned images first")
            click("open:4")
            click("zoom-in")
            check("zoom > 1", "workbench zoom")
            click("edit")
            cdp.js("document.querySelector('[name=x]').value=380; $('dialog-form').requestSubmit()")
            check("images[3].targets[0].x === 380", "edited box")
            click("undo")
            check("images[3].targets[0].x === 350", "undo edit")
            click("submit")
            check("images[3].status === 'submitted'", "resubmitted")
            role("lead")
            click("nav:sign")
            click("open:4")
            check("!document.querySelector('[data-action=edit]')", "lead view has no edit control")
            click("accept")
            click("nav:batch")
            click("delivery")
            check("dialog.textContent.includes('纳入 2 张') && dialog.textContent.includes('未纳入 4 张')", "partial delivery manifest")
            cdp.goto(page.as_uri())
            check("images[3].status === 'accepted' && window.__errors.length === 0", "refresh preserves state")
            cdp.shot("pilot-batch.png")
            cdp.js("$('role').value='review'; $('role').dispatchEvent(new Event('change'))")
            click("open:3")
            cdp.shot("pilot-workbench.png")
        finally:
            if cdp:
                cdp.close()
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
