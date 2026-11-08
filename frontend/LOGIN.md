# 登录模块

独立登录页，只对接真实后端，不包含 mock 账号。工作台内原有的登录门禁仅保留给
`config.js` 的离线 mock 演示；真实后端模式下由本页负责登录与退出。

- 入口：`login.html`
- 规格：`api/openapi.yaml` 的 `/auth/login`、`/me`、`/auth/logout`
- 默认跳转：`index.html`（支持同源 `?next=` 回跳）

## 1. 文件

| 文件 | 作用 |
|---|---|
| `login.html` | 页面结构：品牌、登录表单、错误条、后端状态 |
| `login.css` | 独立样式：港口航拍 + 纸面表单、深水 / 钴蓝 / 海水青配色 |
| `login.js` | 登录、错误映射、会话恢复、回跳校验 |
| `auth-session.js` | 登录页与工作台共用的会话存储（token + user） |
| `auth-config.js` | 登录页配置：项目候选、回跳地址、超时、记住天数 |
| `assets/harbor-login.webp` | 右侧背景：安特卫普港吊机日落，Unsplash / Wikimedia Commons，CC0 |
| `assets/harbor-panel.webp` | 旧版表单面背景，当前版本不再使用（保留文件） |

### 视觉

- 背景照片来自 Wikimedia Commons，CC0，可自由使用；照片做裁切、压暗与青绿色调遮罩，并轻微去饱和以贴合冷色 UI。
- 桌面端为「航拍图 + 巨型标题」左栏、「纸面表单」右栏；宽度 ≤1080 px 时切换为「顶部影像 + 下方表单」。
- 配色沿用主工作台的「海岸科技」取样：深水 `#0a1218`、冷雾纸面 `#f7f9f9`、
  钴蓝 `#2f5bff`（焦点 / 箭头 / 主操作）、海水青 `#19c9bc`（kicker 方块 / 数据标签）、
  青柠 `#d8ff4a`（警告 / AI）、珊瑚 `#d92d50`（错误）。
- 表单栏顶部 4 px 是钴蓝 / 青 / 青柠三色条；标题、字段和提交按钮均为方角。
- 登录表单：账号、密码、项目空间左侧各有一个线性图标；密码可显隐；提交按钮右侧带钴蓝箭头方块，悬停时右移。
- 照片加载失败时使用深水 `#0a1218` 纯色兜底，不会退回渐变或旧版青绿配色。

## 2. 流程

```
打开 login.html
  ├─ 已有 token → GET /me 校验 → 成功则跳转 index.html
  │                    ├─ 401 → 清除会话，提示「会话已过期」
  │                    └─ 网络失败 → 保留会话，允许手工重试
  └─ 无会话 → 填写账号 / 密码 / 项目编号
        └─ POST /auth/login
              ├─ 200 → 保存 token 与 user → 跳转 next 或 index.html
              ├─ 401 INVALID_CREDENTIALS → 聚焦密码框
              ├─ 403 PROJECT_FORBIDDEN → 聚焦项目框
              └─ 0 / 5xx → 显示可恢复的错误，表单保持可用
```

工作台启动时 `app.js` 读取同一份会话，调用 `GET /projects/{id}` 补齐成员列表，
再渲染项目页；若真实后端模式且无会话，直接重定向到
`login.html?next=index.html`。退出时调用 `/auth/logout`、清空会话并回到登录页。

## 3. 配置

后端地址沿用 `config.js`，避免两处不一致：

```js
window.APP_CONFIG = {
  apiMode: 'http',                 // 真实后端必须为 http
  baseUrl: '',                     // 同源部署留空；跨域填 http://127.0.0.1:8000
  apiPrefix: '/api/v1'
};
```

登录页专属配置在 `auth-config.js`：

| 字段 | 说明 |
|---|---|
| `projects` | 项目编号候选，仅作输入提示，不构成权限 |
| `appUrl` | 登录成功后的默认落点，默认 `index.html` |
| `loginUrl` | 工作台退出/失效后的回跳地址 |
| `requestTimeoutMs` | 单次请求超时，默认 15 s |
| `rememberDays` | 勾选「保持登录」的有效期，默认 7 天 |

`projects` 里的 `harbor-a` / `harbor-b` 是演示值，部署时替换为真实项目。
留空时表单仍可手工输入项目编号。

## 4. 接口与错误

请求体与响应严格按 `api/openapi.yaml`：

```json
POST /auth/login
{ "username": "roxysunrise", "password": "demo", "project_id": "harbor-a" }

200 { "token": "…", "user": { "id": "u-1", "name": "roxysunrise",
      "role": "项目负责人", "project": { "id": "harbor-a", "name": "港口巡查 A 区" } } }
```

| 状态 / code | 页面行为 |
|---|---|
| 401 `INVALID_CREDENTIALS` | 「账号或密码不正确」，聚焦并选中密码 |
| 403 `PROJECT_FORBIDDEN` | 「你不是该项目的成员」，聚焦项目编号 |
| 401 `UNAUTHENTICATED` | 清除本地会话，提示重新登录 |
| 0 `NETWORK_ERROR` / `TIMEOUT` | 显示恢复建议（不显示接口地址），表单可重试 |
| 5xx | 「服务端异常（5xx）」，不暴露堆栈 |
| 429 | 「尝试次数过多，请稍后再试」 |
| 502 `BAD_RESPONSE` | 后端返回缺少 `token` 或 `user.id` 时拒绝进入 |

错误条展示 `状态码 · 错误码`，便于对照服务端日志；`details` 不直接渲染。

## 5. 会话存储

| 场景 | 位置 | 有效期 |
|---|---|---|
| 勾选「保持登录」 | `localStorage` | 7 天（`auth-config.js` 可改） |
| 不勾选 | `sessionStorage` | 当前标签页，关闭即失效 |

键名：

- `dsh.ship.token`：与 `api/client.js` 共用，工作台请求自动带 `Authorization: Bearer`
- `dsh.ship.session`：`{ token, user, remember, savedAt, expiresAt }`
- `dsh.ship.lastUser` / `dsh.ship.lastProject`：仅用于预填，不含密码

退出、401、过期都会同时清理两个存储。密码只在登录请求体中出现，不落盘、不写日志。

## 6. 对工作台的改动

真实后端模式下工作台不再显示内嵌登录门禁：

| 文件 | 改动 |
|---|---|
| `index.html` | 在 `api/client.js` 前引入 `auth-session.js` |
| `api/client.js` | token 改为 `AuthSession.token()`（兼容 sessionStorage）；带 token 的 401 会清会话并派发 `auth:expired` |
| `app.js` | 新增 `restoreSession()`；`boot()` 恢复会话或跳登录页；`doLogout()` 清理并回登录页；监听 `auth:expired` |

`apiMode: 'mock'` 时行为不变：没有会话就显示原来的内嵌门禁，已有会话则直接进入，
便于离线演示。**真实后端部署必须把 `apiMode` 设为 `'http'`。**

## 7. 安全说明

- **开放重定向**：`next` 只接受同源路径，`//evil.com`、跨域绝对地址、
  `login.html` 自身都会回落到 `index.html`。
- **权限不由前端决定**：项目候选只是提示；跨项目访问依赖服务端逐请求返回 403。
- **XSS 风险**：token 存在 Web Storage，任何同源脚本都能读取；生产环境应配合 CSP、
  输出转义与依赖审计，或改为 HttpOnly Cookie + CSRF 防护。
- **传输**：生产必须 HTTPS；`Authorization` 头不应出现在日志或 URL 中。
- **限流**：页面只做按钮禁用，登录失败次数限制必须由服务端实现。
- **无公开注册**：页面不提供注册、短信或邮件入口，账号由管理员开户。

## 8. 验证

临时后端 `analysis/_tmp/auth_stub.py` 按 OpenAPI 实现登录、`/me`、项目与健康检查，
配合 `python -m http.server 8765` 用 Playwright 实测：

| 场景 | 结果 |
|---|---|
| 错误密码 | 401 错误条，焦点回到密码框 |
| 非成员项目 | 403 提示，焦点回到项目框 |
| 正确登录 | 跳转 `index.html`，顶栏显示用户与项目 |
| 不勾选保持登录 | 仅写 `sessionStorage`，刷新仍有效 |
| 勾选保持登录 | 写 `localStorage`，`expiresAt` 为 7 天后 |
| 已登录再开登录页 | `GET /me` 通过后自动进入工作台 |
| 伪造 token | 清除会话并提示「会话已过期」 |
| 后端停机 | 提交后显示 `NETWORK_ERROR`，表单可重试 |
| 退出 | 回到 `login.html?logout=1`，提示「已安全退出」 |
| 真实后端模式直开工作台 | 重定向 `login.html?next=index.html` |
| `?next=//evil.example/x` | 登录后回落到 `index.html` |
| 390 / 1024 / 1280 / 1440 宽 | 无横向溢出，面板完整可见 |
| 键盘 Tab | 账号 → 密码 → 显示密码 → 项目 → 保持登录 → 提交 |
| 空表单提交 | 三个字段各自报错，焦点落在账号 |

设计检查：`design-taste-frontend` 的 `lint.mjs --app` 为 0 FAIL / 0 WARN。
`inspect.mjs` 未能运行：沙箱禁止 Node 子进程启动浏览器（`spawn EPERM`），
因此改用 Playwright 截图与 DOM 度量（对比度、溢出、尺寸）。

截图：`screenshots/07-login-colorful.png`（1440×900）、
`screenshots/08-login-colorful-mobile.png`（390×844）、
`screenshots/09-login-error.png`（401 错误态）、
`screenshots/10-login-tablet.png`（551×735 @2x，窄屏竖版）。
旧版单色稿保留在 `screenshots/05-login.png`、`06-login-mobile.png`。

## 9. 已知限制

- 未实现 SSO、双因素、找回密码、刷新令牌；会话失效后需重新登录。
- 项目候选来自静态配置，没有登录前的项目发现接口（`/auth/login` 要求先给项目）。
- 登录页不做前端限流，防爆破依赖服务端。
- 工作台数据仍可能是 mock：登录是真实的，但 `apiMode: 'mock'` 时其余接口走浏览器内模拟。
- 未做多语言；文案为中文。
