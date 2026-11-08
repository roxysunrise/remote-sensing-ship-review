"""口令、会话与登录限速。

口令使用 PBKDF2-HMAC-SHA256（标准库实现，避免自造密码学）；会话令牌只保存
SHA-256 摘要，数据库泄露也不能直接复用令牌。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from . import config, db, errors

_ITERATIONS = 120_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = _ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != _ALGO:
            return False
        expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                       bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(user_id: str, project_id: str | None) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,project_id,created_at,expires_at,revoked,last_seen)"
        " VALUES(?,?,?,?,?,0,?)",
        (_token_hash(token), user_id, project_id, db.now(), db.after_hours(config.SESSION_TTL_HOURS), db.now()),
    )
    return token


def resolve(token: str | None) -> tuple[dict, str | None]:
    """返回 (user, project_id)；无效或过期抛 401。"""
    if not token:
        raise errors.unauthenticated("缺少访问令牌")
    row = db.one("SELECT * FROM sessions WHERE token_hash=?", (_token_hash(token),))
    if row is None or row["revoked"]:
        raise errors.unauthenticated("会话已失效，请重新登录")
    if row["expires_at"] < db.now():
        db.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (row["token_hash"],))
        raise errors.unauthenticated("会话已过期，请重新登录")
    user = db.one("SELECT * FROM users WHERE id=?", (row["user_id"],))
    if user is None or user["status"] != "active":
        raise errors.unauthenticated("账号不可用")
    db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (db.now(), row["token_hash"]))
    return dict(user), row["project_id"]


def revoke(token: str) -> None:
    db.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (_token_hash(token),))


def revoke_user_sessions(user_id: str, *, keep_token: str | None = None) -> int:
    """撤销某用户全部会话。改密、停用、移出项目后调用。

    `keep_token` 用于"管理员改自己的密码"这类场景：保留当前会话，其余全部失效。
    """
    if keep_token:
        return db.execute("UPDATE sessions SET revoked=1 WHERE user_id=? AND token_hash<>?",
                          (user_id, _token_hash(keep_token)))
    return db.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))


# --- 登录失败限速（进程内，够演示用；生产应放共享存储） -------------------------

_attempts: dict[str, list[float]] = {}
_attempt_lock = threading.Lock()
_WINDOW_SECONDS = 300
_MAX_FAILURES = 10


def note_failure(key: str) -> None:
    with _attempt_lock:
        bucket = _attempts.setdefault(key, [])
        cutoff = time.time() - _WINDOW_SECONDS
        bucket[:] = [t for t in bucket if t > cutoff]
        bucket.append(time.time())


def check_throttle(key: str) -> None:
    with _attempt_lock:
        bucket = _attempts.get(key, [])
        cutoff = time.time() - _WINDOW_SECONDS
        recent = [t for t in bucket if t > cutoff]
        if len(recent) >= _MAX_FAILURES:
            raise errors.ApiError(429, "RATE_LIMITED", "登录失败次数过多，请稍后再试")


def clear_failures(key: str) -> None:
    with _attempt_lock:
        _attempts.pop(key, None)


def member_role(project_id: str, user_id: str) -> str:
    row = db.one("SELECT role FROM members WHERE project_id=? AND user_id=?", (project_id, user_id))
    if row is None:
        raise errors.forbidden()
    return row["role"]
