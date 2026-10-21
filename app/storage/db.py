# -*- coding: utf-8 -*-
"""
SQLite 存储层。

为什么用 SQLite 而不是 MySQL？
    原项目技术栈写了 MySQL + Redis + Celery，但你机器上没装这些服务。
    为了能当场跑起来，这里用标准库 sqlite3 降级：单文件、零安装、
    功能一个不少（会话、版本快照、消息、社区内容都能存）。
    真要上生产，把这一层换成 SQLAlchemy + MySQL 即可，业务代码不动。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    request_json    TEXT NOT NULL,
    current_version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS itinerary_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    version       INTEGER NOT NULL,
    itinerary_json TEXT NOT NULL,
    change_note   TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE(session_id, version)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    intent     TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS community_posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author     TEXT NOT NULL,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    tags       TEXT DEFAULT '',
    likes      INTEGER DEFAULT 0,
    favorites  INTEGER DEFAULT 0,
    status     TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL,
    author     TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    post_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, post_id)
);

CREATE TABLE IF NOT EXISTS banned_users (
    user_id    TEXT PRIMARY KEY,
    reason     TEXT DEFAULT '',
    until      TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# 后加的字段（老库升级用）：推荐位
MIGRATIONS = [
    "ALTER TABLE community_posts ADD COLUMN featured INTEGER DEFAULT 0",
]


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or settings.db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _migrate()


def _migrate() -> None:
    """给老库补字段。SQLite 没有 ADD COLUMN IF NOT EXISTS，只能试错。"""
    with get_conn() as conn:
        for sql in MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # 字段已存在，忽略


def reset_db() -> None:
    """清空重建（调试用）。"""
    p: Path = settings.db_path
    if p.exists():
        p.unlink()
    init_db()


init_db()
