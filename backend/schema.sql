-- 遥感船舶检测与人机协同复核系统 · 数据库结构
-- 生成方式：从运行库 var/ship.db 导出（SQLite 3），与 backend/db.py 的建表语句一致。
-- 使用：sqlite3 var/ship.db < backend/schema.sql
-- 说明：业务数据库是唯一写入口；文件（原图/结果/导出）落在 var/ 目录，不存二进制到库。
-- 账号口令以 PBKDF2-SHA256(120000 迭代) 摘要存储，会话令牌只存 SHA-256 摘要。

-- 共 14 张表、4 个索引

-- ========== 表结构 ==========
CREATE TABLE candidates(
  id TEXT PRIMARY KEY, image_id TEXT NOT NULL, project_id TEXT NOT NULL, user_id TEXT NOT NULL,
  status TEXT NOT NULL, polygon TEXT, source TEXT, width REAL, length REAL, quality REAL,
  reason TEXT, created_at TEXT NOT NULL);
CREATE TABLE draft_detections(
  task_id TEXT NOT NULL, detection_id INTEGER NOT NULL, polygon TEXT NOT NULL,
  class_id INTEGER, class_name TEXT, confidence REAL, source_tile_id INTEGER,
  review_state TEXT NOT NULL, origin TEXT NOT NULL, note TEXT, ai_candidate_id TEXT,
  PRIMARY KEY(task_id, detection_id));
CREATE TABLE draft_state(
  task_id TEXT PRIMARY KEY, base_version TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, user_id TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE exports(
  id TEXT PRIMARY KEY, version_id TEXT NOT NULL, task_id TEXT NOT NULL, project_id TEXT NOT NULL,
  status TEXT NOT NULL, formats TEXT NOT NULL, files TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE images(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, upload_id TEXT, file_name TEXT NOT NULL,
  stored_name TEXT NOT NULL, sha256 TEXT NOT NULL, width INTEGER, height INTEGER, mode TEXT,
  bytes INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE members(
  project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY(project_id, user_id));
CREATE TABLE projects(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE regions(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, project_id TEXT NOT NULL, name TEXT,
  polygon TEXT NOT NULL, rule TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE sessions(
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0, last_seen TEXT);
CREATE TABLE tasks(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, image_id TEXT NOT NULL, created_by TEXT NOT NULL,
  status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
  tiles_total INTEGER NOT NULL DEFAULT 0, tiles_done INTEGER NOT NULL DEFAULT 0,
  temp_count INTEGER NOT NULL DEFAULT 0, final_count INTEGER,
  parameters TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT,
  result_path TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE TABLE uploads(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT NOT NULL,
  file_name TEXT NOT NULL, chunk_size INTEGER NOT NULL, chunks_total INTEGER,
  chunks_received INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, stage TEXT NOT NULL,
  pct INTEGER NOT NULL DEFAULT 0, size INTEGER, sha256 TEXT, image_id TEXT, error TEXT,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE users(
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
CREATE TABLE versions(
  task_id TEXT NOT NULL, label TEXT NOT NULL, project_id TEXT NOT NULL, author TEXT NOT NULL,
  created_at TEXT NOT NULL, readonly INTEGER NOT NULL DEFAULT 0, summary TEXT, counts TEXT,
  detections TEXT NOT NULL, PRIMARY KEY(task_id, label));

-- ========== 索引 ==========
CREATE INDEX idx_images_project ON images(project_id, created_at);
CREATE INDEX idx_members_user ON members(user_id);
CREATE INDEX idx_tasks_project ON tasks(project_id, created_at);
CREATE INDEX idx_uploads_project ON uploads(project_id, created_at);

-- ========== 种子数据说明 ==========
-- 首次启动时 backend/db.py::init_db 会写入两个演示账号（口令来自环境变量
-- SHIP_SEED_PASSWORD，默认 demo-1234，仅存 PBKDF2 摘要）与两个演示项目：
--   users:    roxysunrise(项目负责人/管理员)  analyst(项目分析员)
--   projects: harbor-a(PRJ-A-0271)          harbor-b(PRJ-B-0272)
--   members:  roxysunrise 属于两个项目；analyst 只属于 harbor-a
-- 生产部署请设置 SHIP_SEED_PASSWORD 并置 SHIP_SEED_ON_START=0 关闭播种，
-- 改用 POST /users（管理员）或 scripts/create-user.py 受控开户。
