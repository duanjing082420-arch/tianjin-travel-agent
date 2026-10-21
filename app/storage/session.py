# -*- coding: utf-8 -*-
"""
会话与行程版本管理。

两个核心概念：
    会话（session）—— 一次规划的完整上下文，包含原始需求和所有对话
    版本（version） —— 行程的快照。每改一次就存一个新版本，可以回退

为什么要存版本而不是直接覆盖？
    用户说"第二天轻松点"，改完可能觉得还不如上一版。
    有版本快照就能一键回退，这也是原项目"维护会话上下文与行程版本"的意思。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from .db import get_conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_session(request: dict, itinerary: dict) -> str:
    """开一个新会话，顺带存下第 1 版行程。"""
    sid = uuid.uuid4().hex[:12]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, request_json, current_version)"
            " VALUES (?,?,?,1)",
            (sid, _now(), json.dumps(request, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO itinerary_versions (session_id, version, itinerary_json,"
            " change_note, created_at) VALUES (?,1,?,?,?)",
            (sid, json.dumps(itinerary, ensure_ascii=False), "初始生成", _now()),
        )
    return sid


def get_session(sid: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        cur = conn.execute(
            "SELECT itinerary_json FROM itinerary_versions WHERE session_id=? AND version=?",
            (sid, row["current_version"]),
        ).fetchone()
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "request": json.loads(row["request_json"]),
        "current_version": row["current_version"],
        "itinerary": json.loads(cur["itinerary_json"]) if cur else None,
    }


def save_version(sid: str, itinerary: dict, note: str) -> int:
    """存一个新版本，返回版本号。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM itinerary_versions WHERE session_id=?",
            (sid,),
        ).fetchone()
        new_v = int(row["v"]) + 1
        conn.execute(
            "INSERT INTO itinerary_versions (session_id, version, itinerary_json,"
            " change_note, created_at) VALUES (?,?,?,?,?)",
            (sid, new_v, json.dumps(itinerary, ensure_ascii=False), note, _now()),
        )
        conn.execute(
            "UPDATE sessions SET current_version=? WHERE id=?", (new_v, sid)
        )
    return new_v


def list_versions(sid: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT version, change_note, created_at FROM itinerary_versions"
            " WHERE session_id=? ORDER BY version",
            (sid,),
        ).fetchall()
    return [dict(r) for r in rows]


def rollback(sid: str, version: int) -> dict | None:
    """回退到某一版（不删历史，只是把当前指针挪回去）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT itinerary_json FROM itinerary_versions WHERE session_id=? AND version=?",
            (sid, version),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE sessions SET current_version=? WHERE id=?", (version, sid)
        )
    return json.loads(row["itinerary_json"])


def add_message(sid: str, role: str, content: str, intent: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, intent, created_at)"
            " VALUES (?,?,?,?,?)",
            (sid, role, content, intent, _now()),
        )


def get_messages(sid: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, intent, created_at FROM messages"
            " WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
