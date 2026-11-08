"""命令行开户 / 改密：不改 HTTP API 也能建账号（受控开户的运维入口）。

直接写业务库，因此必须和后端用同一套环境变量（`SHIP_VAR_DIR` / `SHIP_DB_PATH`）。

用法：
    # 建一个分析员并加入 harbor-a
    python scripts/create-user.py --username analyst2 --name 影像分析员2 \
        --password 'ChangeMe-123' --project harbor-a

    # 建管理员
    python scripts/create-user.py --username admin2 --password 'ChangeMe-123' --admin

    # 只改密码
    python scripts/create-user.py --username analyst2 --password 'NewPass-456' --reset

    # 列出账号
    python scripts/create-user.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config, db, security  # noqa: E402

ROLES = (config.USER_ROLE_OWNER, config.USER_ROLE_ANALYST)


def list_users() -> int:
    db.init_db()
    rows = db.query(
        "SELECT u.id, u.username, u.display_name, u.is_admin, u.status, u.created_at,"
        " (SELECT GROUP_CONCAT(m.project_id || ':' || m.role) FROM members m WHERE m.user_id=u.id) AS projects"
        " FROM users u ORDER BY u.created_at, u.username")
    if not rows:
        print("（没有账号）")
        return 0
    print(f"{'用户名':<18}{'显示名':<14}{'管理员':<8}{'状态':<10}项目")
    for row in rows:
        print(f"{row['username']:<18}{row['display_name']:<14}"
              f"{'是' if row['is_admin'] else '否':<8}{row['status']:<10}{row['projects'] or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="命令行开户 / 改密（受控开户）")
    parser.add_argument("--username")
    parser.add_argument("--name", help="显示名称，缺省用用户名")
    parser.add_argument("--password")
    parser.add_argument("--project", help="创建后加入的项目 ID")
    parser.add_argument("--role", default=config.USER_ROLE_ANALYST, choices=ROLES)
    parser.add_argument("--admin", action="store_true", help="设为账号管理员")
    parser.add_argument("--reset", action="store_true", help="账号已存在时只重置密码")
    parser.add_argument("--list", action="store_true", help="列出账号后退出")
    args = parser.parse_args(argv)

    db.init_db(config.SEED_ON_START)
    print(f"库：{config.DB_PATH}")

    if args.list:
        return list_users()

    if not args.username or not args.password:
        parser.error("需要 --username 与 --password（或用 --list）")
    if len(args.password) < 8:
        parser.error("密码至少 8 位")

    existing = db.one("SELECT * FROM users WHERE username=?", (args.username,))
    if existing:
        if not args.reset:
            print(f"账号 {args.username} 已存在；要重置密码请加 --reset", file=sys.stderr)
            return 1
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (security.hash_password(args.password), existing["id"]))
        revoked = security.revoke_user_sessions(existing["id"])
        print(f"已重置 {args.username} 的密码，撤销 {revoked} 个会话")
        return 0

    project_id = args.project
    if project_id and db.one("SELECT id FROM projects WHERE id=?", (project_id,)) is None:
        print(f"项目不存在：{project_id}（现有：{[r['id'] for r in db.query('SELECT id FROM projects')]}）",
              file=sys.stderr)
        return 1

    user_id = db.new_id("usr")
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,display_name,is_admin,status,created_at)"
            " VALUES(?,?,?,?,?,'active',?)",
            (user_id, args.username, security.hash_password(args.password),
             args.name or args.username, 1 if args.admin else 0, db.now()))
        if project_id:
            conn.execute("INSERT INTO members(project_id,user_id,role) VALUES(?,?,?)",
                         (project_id, user_id, args.role))
    print(f"已创建 {args.username}（{user_id}）"
          + (f"，加入 {project_id} 角色 {args.role}" if project_id else "")
          + ("，管理员" if args.admin else ""))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
