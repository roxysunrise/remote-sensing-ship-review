"""SQLite 持久化层。

单进程、单连接 + 可重入锁：本项目规模（双人课程项目）下足够，
并发写入靠锁串行化，读多写少。所有时间戳是带时区的 ISO 8601。
"""
from __future__ import annotations

import contextlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import config

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()
_atomic = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0, last_seen TEXT);

CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS members(
  project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY(project_id, user_id));

CREATE TABLE IF NOT EXISTS uploads(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT NOT NULL,
  file_name TEXT NOT NULL, chunk_size INTEGER NOT NULL, chunks_total INTEGER,
  chunks_received INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, stage TEXT NOT NULL,
  pct INTEGER NOT NULL DEFAULT 0, size INTEGER, sha256 TEXT, image_id TEXT, error TEXT,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS images(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, upload_id TEXT, file_name TEXT NOT NULL,
  stored_name TEXT NOT NULL, sha256 TEXT NOT NULL, width INTEGER, height INTEGER, mode TEXT,
  bytes INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, image_id TEXT NOT NULL, created_by TEXT NOT NULL,
  status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
  tiles_total INTEGER NOT NULL DEFAULT 0, tiles_done INTEGER NOT NULL DEFAULT 0,
  temp_count INTEGER NOT NULL DEFAULT 0, final_count INTEGER,
  parameters TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT,
  result_path TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);

CREATE TABLE IF NOT EXISTS annotation_tasks(
  task_id TEXT PRIMARY KEY REFERENCES tasks(id), project_id TEXT NOT NULL,
  status TEXT NOT NULL, assignee TEXT, revision INTEGER NOT NULL DEFAULT 0,
  published_at TEXT NOT NULL, version_id TEXT, reason TEXT, target_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS annotation_one_current ON annotation_tasks(assignee)
  WHERE status IN ('claimed','active');
CREATE TABLE IF NOT EXISTS versions(
  task_id TEXT NOT NULL, label TEXT NOT NULL, project_id TEXT NOT NULL, author TEXT NOT NULL,
  created_at TEXT NOT NULL, readonly INTEGER NOT NULL DEFAULT 0, summary TEXT, counts TEXT,
  detections TEXT NOT NULL, PRIMARY KEY(task_id, label));

CREATE TABLE IF NOT EXISTS draft_state(
  task_id TEXT PRIMARY KEY, base_version TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS draft_detections(
  task_id TEXT NOT NULL, detection_id INTEGER NOT NULL, polygon TEXT NOT NULL,
  class_id INTEGER, class_name TEXT, confidence REAL, source_tile_id INTEGER,
  review_state TEXT NOT NULL, origin TEXT NOT NULL, note TEXT, ai_candidate_id TEXT,
  PRIMARY KEY(task_id, detection_id));

CREATE TABLE IF NOT EXISTS regions(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, project_id TEXT NOT NULL, name TEXT,
  polygon TEXT NOT NULL, rule TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS exports(
  id TEXT PRIMARY KEY, version_id TEXT NOT NULL, task_id TEXT NOT NULL, project_id TEXT NOT NULL,
  status TEXT NOT NULL, formats TEXT NOT NULL, files TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS candidates(
  id TEXT PRIMARY KEY, image_id TEXT NOT NULL, project_id TEXT NOT NULL, user_id TEXT NOT NULL,
  status TEXT NOT NULL, polygon TEXT, source TEXT, width REAL, length REAL, quality REAL,
  reason TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, user_id TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, created_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_members_user ON members(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_images_project ON images(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_uploads_project ON uploads(project_id, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def after_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def query(sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
    with _lock:
        return connection().execute(sql, args).fetchall()


def one(sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
    with _lock:
        return connection().execute(sql, args).fetchone()


def execute(sql: str, args: tuple | list = ()) -> int:
    with _lock:
        conn = connection()
        cur = conn.execute(sql, args)
        if not getattr(_atomic, 'active', False):
            conn.commit()
        return cur.rowcount


@contextlib.contextmanager
def serialized():
    # Draft mutation and workflow transitions share the same process-wide writer lock.
    with _lock:
        yield


@contextlib.contextmanager
def atomic_write():
    """Apply a guarded draft mutation and its revision as one SQLite write."""
    with _lock:
        conn = connection()
        conn.execute("BEGIN IMMEDIATE")
        _atomic.active = True
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            _atomic.active = False


@contextlib.contextmanager
def tx():
    """短事务：调用方在事务内只做数据库操作，不要在里面跑推理。"""
    with _lock:
        conn = connection()
        if getattr(_atomic, 'active', False):
            yield conn
            return
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def log_event(action: str, *, project_id: str | None = None, user_id: str | None = None,
              target: str | None = None, detail: dict | None = None) -> None:
    import json
    execute(
        "INSERT INTO events(project_id,user_id,action,target,detail,created_at) VALUES(?,?,?,?,?,?)",
        (project_id, user_id, action, target, json.dumps(detail or {}, ensure_ascii=False), now()),
    )


def init_db(seed: bool | None = None) -> None:
    """建表并（默认）写入演示数据。重复调用是幂等的。"""
    from .security import hash_password

    seed = config.SEED_ON_START if seed is None else seed
    connection()
    if not seed:
        return
    if one("SELECT id FROM users LIMIT 1"):
        return

    created = now()
    users = [
        ("usr_owner", "roxysunrise", "算法负责人", 1),
        ("usr_analyst", "analyst", "影像分析员", 0),
    ]
    projects = [("harbor-a", "PRJ-A-0271", "港口巡查 A 区"), ("harbor-b", "PRJ-B-0272", "港口巡查 B 区")]
    with tx() as conn:
        for uid, username, name, admin in users:
            conn.execute(
                "INSERT INTO users(id,username,password_hash,display_name,is_admin,status,created_at)"
                " VALUES(?,?,?,?,?,'active',?)",
                (uid, username, hash_password(config.SEED_PASSWORD), name, admin, created),
            )
        for pid, code, name in projects:
            conn.execute("INSERT INTO projects(id,code,name,created_at) VALUES(?,?,?,?)",
                         (pid, code, name, created))
        conn.execute("INSERT INTO members(project_id,user_id,role) VALUES(?,?,?)",
                     ("harbor-a", "usr_owner", config.USER_ROLE_OWNER))
        conn.execute("INSERT INTO members(project_id,user_id,role) VALUES(?,?,?)",
                     ("harbor-b", "usr_owner", config.USER_ROLE_OWNER))
        conn.execute("INSERT INTO members(project_id,user_id,role) VALUES(?,?,?)",
                     ("harbor-a", "usr_analyst", config.USER_ROLE_ANALYST))
