# -*- coding: utf-8 -*-
"""
社区与运营：攻略发布、机审、点赞、评论、收藏、禁言、推荐位。

内容安全是社区功能的底线，这里做两层：
    机审（机器）—— 敏感词、联系方式引流、短文本水帖，命中就转人工
    人审（复审）—— 机审放行的也要能拉出来复核，status 字段就是为它留的

权限用 RBAC 简化实现：需要管理员权限的接口检查请求头 X-User-Role。
真实项目会换成 JWT + 角色表，这里把"权限判断"这个动作做出来就够了。
"""
from __future__ import annotations

import re
from datetime import datetime

from .storage.db import get_conn

SENSITIVE_WORDS = [
    "赌博", "代开发票", "私彩", "违禁", "刷单", "加微信",
    "兼职日入", "赌场", "色情", "办证",
]
AD_PATTERN = re.compile(r"(微信[:：]?\s*\w{5,}|QQ[:：]?\s*\d{5,}|1[3-9]\d{9}|二维码)")
MIN_CONTENT_LEN = 20


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def machine_review(title: str, content: str) -> tuple[str, list[str]]:
    """机审：返回 (状态, 命中原因列表)。

    approved 直接上架，pending 转人工复审，rejected 直接拒。
    """
    reasons = []
    text = f"{title}{content}"

    for w in SENSITIVE_WORDS:
        if w in text:
            reasons.append(f"命中敏感词「{w}」")
    if AD_PATTERN.search(text):
        reasons.append("疑似联系方式引流")
    if len(content) < MIN_CONTENT_LEN:
        reasons.append(f"正文不足 {MIN_CONTENT_LEN} 字，疑似水帖")

    if any("敏感词" in r or "引流" in r for r in reasons):
        return "rejected", reasons
    if reasons:
        return "pending", reasons
    return "approved", reasons


def is_banned(user_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT until FROM banned_users WHERE user_id=?", (user_id,)
        ).fetchone()
    if not row:
        return False
    until = row["until"] or ""
    if until and until > _now():
        return True
    return False   # 到期自动解封


def ban_user(user_id: str, reason: str = "", days: int = 7) -> None:
    from datetime import timedelta

    until = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, reason, until, created_at)"
            " VALUES (?,?,?,?)",
            (user_id, reason, until, _now()),
        )


def create_post(author: str, title: str, content: str, tags: str = "") -> dict:
    if is_banned(author):
        return {"error": "账号已被禁言，无法发布", "post_id": None}

    status, reasons = machine_review(title, content)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO community_posts (author, title, content, tags, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (author, title, content, tags, status, _now()),
        )
        pid = cur.lastrowid
    return {
        "post_id": pid,
        "status": status,
        "reasons": reasons,
        "message": {
            "approved": "发布成功",
            "pending": "已提交，进入人工复审队列",
            "rejected": "未通过机审，已拦截",
        }[status],
    }


def list_posts(tag: str = "", sort: str = "new", only_featured: bool = False) -> list[dict]:
    sql = "SELECT * FROM community_posts WHERE status='approved'"
    args: list = []
    if only_featured:
        sql += " AND featured=1"
    if tag:
        sql += " AND tags LIKE ?"
        args.append(f"%{tag}%")
    sql += {
        "hot": " ORDER BY (likes*3 + favorites*2) DESC, id DESC",
        "new": " ORDER BY id DESC",
    }.get(sort, " ORDER BY id DESC")
    sql += " LIMIT 50"

    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def pending_posts() -> list[dict]:
    """人工复审队列。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM community_posts WHERE status='pending' ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def review_post(post_id: int, approve: bool) -> bool:
    with get_conn() as conn:
        conn.execute(
            "UPDATE community_posts SET status=? WHERE id=?",
            ("approved" if approve else "rejected", post_id),
        )
    return True


def like_post(post_id: int, delta: int = 1) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE community_posts SET likes=MAX(0, likes+?) WHERE id=?", (delta, post_id)
        )


def favorite_post(user_id: str, post_id: int) -> str:
    with get_conn() as conn:
        exist = conn.execute(
            "SELECT id FROM favorites WHERE user_id=? AND post_id=?", (user_id, post_id)
        ).fetchone()
        if exist:
            conn.execute("DELETE FROM favorites WHERE id=?", (exist["id"],))
            conn.execute(
                "UPDATE community_posts SET favorites=MAX(0,favorites-1) WHERE id=?",
                (post_id,),
            )
            return "已取消收藏"
        conn.execute(
            "INSERT INTO favorites (user_id, post_id, created_at) VALUES (?,?,?)",
            (user_id, post_id, _now()),
        )
        conn.execute(
            "UPDATE community_posts SET favorites=favorites+1 WHERE id=?", (post_id,)
        )
    return "收藏成功"


def comment_post(post_id: int, author: str, content: str) -> dict:
    if is_banned(author):
        return {"error": "账号已被禁言"}
    if not content.strip():
        return {"error": "评论内容为空"}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO comments (post_id, author, content, created_at) VALUES (?,?,?,?)",
            (post_id, author, content, _now()),
        )
    return {"ok": True}


def list_comments(post_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT author, content, created_at FROM comments WHERE post_id=? ORDER BY id",
            (post_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_featured(post_id: int, featured: bool) -> None:
    """推荐位：运营手动置顶，权限由 API 层的 RBAC 守卫。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE community_posts SET featured=? WHERE id=?",
            (1 if featured else 0, post_id),
        )
