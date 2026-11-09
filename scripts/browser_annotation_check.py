"""Run the annotation handoff in Edge against the seeded tile fixture and HTTP API."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import app_flow_check as browser


def main():
    browser.PORT = int(os.environ.get("SHIP_BROWSER_PORT", "19256"))
    with TemporaryDirectory(prefix="ship-annotation-") as temp:
        process = browser.launch(Path(temp) / "profile")
        cdp = None
        try:
            cdp = browser.CDP(browser.ws_url())
            cdp.send("Page.enable")
            cdp.send("Page.addScriptToEvaluateOnNewDocument", source=browser.COLLECT_ERRORS)

            def check(expression, label):
                if not browser.wait_until(cdp, expression, timeout=10):
                    raise AssertionError(label)
                print("[PASS]", label, flush=True)

            def click(label):
                name = json.dumps(label)
                check(f"""(() => {{
                    const button = [...document.querySelectorAll('button')].find(b =>
                      b.getClientRects().length && !b.disabled && b.textContent.trim() === {name});
                    if (!button) return false;
                    button.click();
                    return true;
                }})()""", label)

            def login(user):
                cdp.goto(browser.FE + "/login.html")
                cdp.js(f"""(() => {{
                    const form = {json.dumps({"username": user, "password": browser.PASSWORD, "project": browser.PROJECT})};
                    for (const [id, value] of Object.entries(form)) {{
                        const input = document.getElementById(id);
                        input.value = value;
                        input.dispatchEvent(new Event('input', {{bubbles:true}}));
                    }}
                    document.getElementById('submit').click();
                }})()""")
                check("!location.pathname.endsWith('login.html') && !!state.user", "login " + user)

            def logout():
                cdp.js("document.getElementById('btn-logout').click()")
                check("location.pathname.endsWith('login.html')", "logout")

            login(browser.USERNAME)
            click("发布到大厅")
            check("document.querySelector('#annotation-content').textContent.includes('可领取')", "published")
            logout()
            login("analyst")
            click("推荐领取")
            click("开始标注")
            check("state.view === 'workbench' && state.draft.length === 4", "existing workbench with four model boxes")
            check("document.querySelector('#annotation-workbench .primary').disabled", "pending boxes block submission")
            cdp.js("state.selection=1; refreshWorkbench()")
            check("!!document.querySelector('#det-geometry')", "precision controls")
            cdp.js("""(() => {
                const form = document.getElementById('det-geometry');
                const input = form.elements.cx;
                input.value = Number(input.value) + 3;
                input.dispatchEvent(new Event('input', {bubbles:true}));
            })()""")
            check("!!state.geometryPreview", "numeric preview")
            click("应用数值")
            check("state.draft[0].review_state === 'modified'", "numeric edit saved")
            click("批量确认高置信度")
            click("确认")
            check("state.draft.every(box => box.review_state !== 'pending')", "bulk confirmation")
            click("保存并暂停")
            check("document.querySelector('#annotation-content').textContent.includes('已暂停')", "pause persisted")
            cdp.goto(browser.FE + "/index.html")
            click("我的任务")
            click("查看详情")
            click("继续标注")
            check("state.draft[0].review_state === 'modified'", "resume after full reload")
            click("提交审核")
            check("document.querySelector('#annotation-content').textContent.includes('待审核')", "submitted version")
            logout()
            login(browser.USERNAME)
            click("待审核")
            click("查看详情")
            check("state.view === 'workbench' && state.viewingVersion?.id === 'v1'", "immutable review version")
            check("document.getElementById('btn-confirm').disabled", "supervisor geometry read-only")
            click("对照模型原始框")
            check("state.viewingVersion?.id === 'v0'", "compare original")
            click("签收")
            check("state.view === 'annotation' && document.querySelector('#annotation-content').textContent.includes('当前没有符合条件')", "approval queue cleared")
            click("项目任务")
            check("document.querySelector('#annotation-content').textContent.includes('已签收')", "signed")
            check("window.__errors.length === 0", "no uncaught browser errors")
        finally:
            if cdp:
                cdp.close()
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
