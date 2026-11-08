# A/B 联合验收候选基线

日期 2026-11-06（Asia/Shanghai）。验收候选代码为 `f13cc81670cdd064c370070f8332347b5eae828f`。该提交位于远端 `codex/multi-class-detect`，双方确认、合并和部署各自记录。

## 依赖和职责

| 交付 | 候选版本采用的关系 |
|---|---|
| [#2](https://github.com/roxysunrise/remote-sensing-ship-review/pull/2) → [#4](https://github.com/roxysunrise/remote-sensing-ship-review/pull/4) → [#5](https://github.com/roxysunrise/remote-sensing-ship-review/pull/5) → [#8](https://github.com/roxysunrise/remote-sensing-ship-review/pull/8) | 模块规则、单图 OBB、大图切片、业务 API，按此顺序叠加。 |
| [#6](https://github.com/roxysunrise/remote-sensing-ship-review/pull/6) → [#9](https://github.com/roxysunrise/remote-sensing-ship-review/pull/9) | B 的前端之上叠加 A 的真实接口参考接线，B 确认最终采用的前端版本。 |
| `11ebc31` → [#10](https://github.com/roxysunrise/remote-sensing-ship-review/pull/10) | `11ebc31` 集成后端 `6d660ec` 和前端接线 `8a663e4`，其上增加多类别推理和检测页着色。 |

A 负责后端、权重、存储与核心编辑组件。B 负责页面与交互集成。双方共同核对契约并执行真实业务验收。

## 从固定提交启动

在 Windows PowerShell 中执行。Python 用 3.10，权重由 A 通过双方认可的受控渠道交付。现有仓库说明记录了训练来源和校验值，B 侧下载权限与独立启动仍待验证。

```powershell
git fetch origin codex/multi-class-detect
git worktree add --detach ../ship-review-acceptance f13cc81670cdd064c370070f8332347b5eae828f
Set-Location ../ship-review-acceptance
py -3.10 -m venv .venv
$reviewPython = (Resolve-Path .venv/Scripts/python.exe).Path
& $reviewPython -m pip install -r algorithm/requirements.txt -r backend/requirements.txt pyyaml websocket-client
# 将下面路径替换为 A 交付的权重位置
$env:SHIP_WEIGHTS = (Resolve-Path 'C:/path/to/best.pt').Path
$reviewHash = (Get-FileHash -LiteralPath $env:SHIP_WEIGHTS -Algorithm SHA256).Hash
if ($reviewHash -ne '6508d583f538e858d498335c0fb6efd24c9e5116823eb4152f6d6979193cd26f') {
    throw '项目权重 SHA-256 不匹配'
}
$env:SHIP_DEVICE = 'cpu'
$env:SHIP_VAR_DIR = Join-Path (Get-Location) 'var/acceptance'
& ./scripts/start-dev.ps1 -Python $reviewPython -Weights $env:SHIP_WEIGHTS
```

使用[快速开始](../quickstart.md)中的本地演示账号，在浏览器走一遍上传、检测、复核、保存和导出。保留提交、权重哈希、影像标识及操作结果。仅本机验收，运行数据留在 Git 忽略的 `var/` 目录。

```powershell
# 解除当前终端的运行目录覆盖，让单元测试使用各自的临时数据库
Remove-Item Env:SHIP_VAR_DIR -ErrorAction SilentlyContinue
& $reviewPython -m unittest discover -s tests
& $reviewPython scripts/e2e_link_check.py --image frontend/assets/harbor.png
# HTTP 脚本先生成真实任务供浏览器读取
& $reviewPython scripts/app_flow_check.py
& $reviewPython -m backend.tools.check_contract --base-url http://127.0.0.1:8000 --spec frontend/api/openapi.yaml --vision-spec frontend/api/detect-openapi.yaml
& ./scripts/stop-dev.ps1
```

## 当前行为及待确认的约定

- 项目复核工作台通过 `SCENE.parameters` 显式传 `classes: [1]`，当前 DOTA 权重中 ID 1 为船。直接调用项目任务 API 时，省略 `parameters.classes` 则按模型全部类别推理。独立的 `detect.html` 未传 `options.classes`，默认推理全部类别。
- 首轮联调需同时验证船舶任务和多类检测。业务统计和导出依据该任务的结果及复核版本。若 B 希望项目页可配置全部类别，双方需先确认 UI 文案、统计范围以及导出口径。
- `GET /projects/{project_id}/images` 和 `GET /projects/{project_id}/tasks` 已由 A 实现，参考前端已接入。任务列表支持 `status`、`image_id` 和 `limit`，`total` 是本次返回条数。双方仍需确认这两个接口的最终 OpenAPI 定义，暂以[契约草案](../../contracts/api-project-lists.md)记录接口和样例。
- 验收门槛分别记录浏览器真实后端流程、目标类别和统计口径、大图瓦片对齐与编辑保存、独立样本精度，以及后端恢复与权限。既有联调记录可供复跑，B 实际签收仍待完成。


## 本轮验证记录

2026-11-06，Windows、Python 3.10、CPU、上文 SHA-256 对应的项目权重，独立临时运行目录。验收候选代码来自 `f13cc81`。用真实 HTTP 后端执行 `backend.tools.check_contract`，业务检查 21/21，推理检查 6/6。该脚本的业务检查集合未含两项列表接口，另运行 `python -m unittest discover -s tests -p test_backend.py -k project -v`，7 项通过（含两项列表和跨项目权限）。本轮以现有本地环境执行，B 侧新环境安装、权重交付和浏览器复跑待验收。
