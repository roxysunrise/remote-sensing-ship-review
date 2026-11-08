# 接口补充：账号与项目成员（受控开户）

日期：2026-11-08（Asia/Shanghai）
提出方：A（`backend/` 已实现并有测试）
状态：**实现完成，待 B 方并入 `frontend/api/openapi.yaml`**
影响：新增 4 条路径 / 5 个方法，`/me` 与登录响应各加一个字段。无破坏性变更。

## 1. 为什么需要

`04-应用设计补充` §2.1 写的是"管理员创建或邀请账号、修改密码、账号可停用"，但当前后端只有
`db.py` 里 seed 的两个固定账号，**没有任何账号或成员管理端点**：

| 能力 | 改动前 |
|---|---|
| 新建账号 | 无（只能改 `SHIP_SEED_PASSWORD` 重建） |
| 修改密码 | 无 |
| 停用账号 / 撤销会话 | 表里有 `status`、`sessions.revoked`，但没有端点 |
| 项目成员增删 | 无（`GET /projects/{id}` 只读） |

因此"登录页没有注册入口"不是少一个按钮，而是**整套受控开户流程没实现**。
按设计文档的取舍，首版做受控开户（管理员建号），不做公开注册。

## 2. 权限模型

| 操作 | 谁能做 | 判定 |
|---|---|---|
| 查看/新建/修改账号 | 账号管理员 | `users.is_admin = 1` |
| 加/移项目成员 | 该项目负责人 | `members.role = 项目负责人` |

- 非管理员访问 `/users` 返回 `403 FORBIDDEN`（与项目权限的 `PROJECT_FORBIDDEN` 区分）。
- 管理员**不等于**能看所有项目影像：影像仍按项目成员关系校验（`04` §2.1 原文要求）。
- 建号时若指定 `project_id`，调用者还必须是该项目的负责人。

## 3. 端点

### 3.1 `GET /users`（管理员）

```json
{
  "total": 3,
  "items": [
    {
      "id": "usr_owner",
      "username": "roxysunrise",
      "name": "算法负责人",
      "is_admin": true,
      "status": "active",
      "created_at": "2026-11-05T11:46:28+00:00",
      "projects": [{"id": "harbor-a", "name": "港口巡查 A 区", "role": "项目负责人"}]
    }
  ]
}
```

### 3.2 `POST /users`（管理员）→ `201`

请求体：

| 字段 | 必填 | 规则 |
|---|---|---|
| `username` | 是 | 3–32 位，`[A-Za-z0-9._-]` |
| `password` | 是 | ≥ 8 位 |
| `name` | 否 | 显示名称，缺省用 `username` |
| `is_admin` | 否 | 默认 `false` |
| `project_id` | 否 | 创建后一并加入该项目（要求调用者是该项目负责人） |
| `role` | 否 | `项目负责人` / `项目分析员`，默认后者 |

响应同 §3.1 的单个 `Account` 对象。

错误：

| 码 | 场景 |
|---|---|
| `409 USERNAME_TAKEN` | 用户名已存在 |
| `422 INVALID_ARGUMENT` | 用户名格式或密码长度不合法 |
| `404 PROJECT_NOT_FOUND` | 指定的项目不存在 |
| `403 PROJECT_FORBIDDEN` | 调用者不是该项目负责人 |

### 3.3 `PATCH /users/{user_id}`（管理员）

请求体字段任意组合：`name`、`password`、`status`（`active`/`disabled`）、`is_admin`。

- 改 `password` 或设 `status=disabled` 会**撤销该账号的其他会话**，响应里带 `revoked_sessions` 数量。
- 管理员改自己的密码时保留当前会话（不会把自己踢下线）。
- 不能停用自己、不能取消自己的管理员权限 → `409 SELF_LOCKOUT`。
- `404 USER_NOT_FOUND`；`422 INVALID_ARGUMENT`（无字段 / 密码过短 / 状态值非法）。

### 3.4 `POST /projects/{project_id}/members`（项目负责人）→ `201`

```json
{"user_id": "usr_xxx", "role": "项目分析员"}
```

响应 `{"project_id","user_id","role"}`。

错误：`409 ALREADY_MEMBER`、`409 USER_DISABLED`、`404 USER_NOT_FOUND`、`403`。

### 3.5 `DELETE /projects/{project_id}/members/{user_id}`（项目负责人）→ `204`

- 同时把该账号在**本项目**的会话标记撤销（不影响他在其他项目）。
- 不能移除最后一名负责人 → `409 LAST_OWNER`。
- `404 MEMBER_NOT_FOUND`。

## 4. 已有响应的字段新增

`/me` 与 `POST /auth/login` 返回的 `user` 对象新增：

```json
{"is_admin": true}
```

前端可据此决定是否显示"账号管理"入口。**这是唯一影响既有页面的变更，且是新增字段。**

## 5. 前端接入建议（B 方决定实现方式）

1. `frontend/api/client.js` 的 http 适配器加 `listUsers` / `createUser` / `updateUser` /
   `addMember` / `removeMember`；mock 适配器可返回"离线演示不支持账号管理"。
2. 管理入口只在 `me.is_admin` 为真时显示；非管理员直接访问页面也应看到 403 提示，
   而不是空白。
3. 建议把"改密 / 停用"做成二次确认：这两个操作会即时踢掉对方会话。
4. 登录页可以补一句提示："不勾选保持登录时，换标签页需要重新登录"——
   这是 `sessionStorage` 的语义，不是缺陷。

## 6. 契约并入位置

A 侧未改动 `frontend/api/openapi.yaml`（该目录归 B）。请把 §3 的 4 条路径与
`Account` / `AccountList` / `CreateAccountRequest` / `UpdateAccountRequest` / `MemberAddRequest`
五个 schema、`UserId` 参数、以及 §4 的 `is_admin` 字段按现有风格补齐。
编号/命名若与 B 的约定不同，以 B 为准。

## 7. A 侧实现与验证

- 端点：`backend/api.py`（`list_users` / `create_user` / `update_user` / `add_member` / `remove_member`）
- 会话撤销：`backend/security.py::revoke_user_sessions`
- 运维入口：`scripts/create-user.py`（不改 API 也能建号/重置密码，`--list` 查看）
- 测试：`tests/test_backend.py` 新增 7 项（建号+入项目、参数校验、非管理员 403、
  改密撤销会话、停用阻断登录与自锁、成员增删、最后负责人保护），全套 **92 项通过**
- 真实 HTTP 冒烟见 `docs/validation/accounts-2026-11-08.md`
