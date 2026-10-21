# -*- coding: utf-8 -*-
"""
FastAPI 接口层：把规划能力和社区能力对外暴露，并托管前端页面。

接口分组：
    /api/plan        生成行程
    /api/chat        多轮对话调整
    /api/session*    会话与版本
    /api/spots       知识库检索（给前端做景点浏览）
    /api/community*  社区：发布/点赞/评论/收藏
    /api/admin*      运营后台（需要管理员角色）

启动：
    .venv\\Scripts\\python.exe -m uvicorn app.api.main:app --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import community
from ..chat.service import chat_turn, rollback_turn, start_session
from ..config import settings
from ..graph.trip_graph import plan_trip
from ..rag.knowledge import load_knowledge
from ..rag.retriever import retrieve
from ..storage import session as store

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"

app = FastAPI(
    title="天津 AI 旅行规划平台",
    description="LangGraph 行程编排 + RAG 本地知识增强 + 多轮对话调整",
    version="0.4.0",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ------------------------- 请求体定义 -------------------------

class PlanRequest(BaseModel):
    city: str = "天津"
    days: int = 3
    start_date: str = ""
    people: int = 2
    budget: int = 3000
    transport: str = "metro"
    interests: list[str] = Field(default_factory=list)
    with_who: str = ""
    pace: str = "适中"


class ChatRequest(BaseModel):
    session_id: str
    message: str


class RollbackRequest(BaseModel):
    session_id: str
    version: int


class PostIn(BaseModel):
    author: str
    title: str
    content: str
    tags: str = ""


class CommentIn(BaseModel):
    author: str
    content: str


class FavoriteIn(BaseModel):
    user_id: str


class ReviewIn(BaseModel):
    post_id: int
    approve: bool
    featured: bool = False


# ------------------------- 权限守卫 -------------------------

def require_admin(role: str | None) -> None:
    """RBAC 守卫：管理员接口必须带 X-User-Role: admin。

    简化实现（真实项目换成 JWT + 角色表），但"先判权限再干活"这个动作是对的。
    """
    if (role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


# ------------------------- 行程规划 -------------------------

@app.post("/api/plan")
def api_plan(req: PlanRequest):
    """生成首版行程，返回会话 id（后续对话都带着它）。"""
    try:
        return start_session(req.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"行程生成失败：{e}")


@app.post("/api/plan/sync")
def api_plan_sync(req: PlanRequest):
    """不落库的快速生成（调试和压测用）。"""
    return plan_trip(req.model_dump())


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """多轮对话调整：只改受影响的那一天，秒级返回。"""
    try:
        return chat_turn(req.session_id, req.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"对话处理失败：{e}")


@app.get("/api/session/{sid}")
def api_session(sid: str):
    sess = store.get_session(sid)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sess


@app.get("/api/session/{sid}/versions")
def api_versions(sid: str):
    return {"versions": store.list_versions(sid)}


@app.post("/api/rollback")
def api_rollback(req: RollbackRequest):
    out = rollback_turn(req.session_id, req.version)
    if "error" in out:
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@app.get("/api/messages/{sid}")
def api_messages(sid: str, limit: int = 20):
    return {"messages": store.get_messages(sid, limit)}


# ------------------------- 知识库浏览 -------------------------

@app.get("/api/spots")
def api_spots(q: str = "", type: str = "spot", district: str = "", top_k: int = 12):
    """给前端做景点/美食/酒店浏览页用。"""
    if q:
        hits = retrieve(q, top_k=top_k, types=[type] if type else None,
                        district=district or None)
        return {"items": [h["meta"] for h in hits]}
    items = [r for r in load_knowledge() if (not type or r.get("type") == type)]
    if district:
        items = [r for r in items if district in str(r.get("district", ""))]
    return {"items": items[:50]}


# ------------------------- 社区 -------------------------

@app.get("/api/community/posts")
def api_posts(tag: str = "", sort: str = "new", featured: bool = False):
    return {"posts": community.list_posts(tag=tag, sort=sort, only_featured=featured)}


@app.post("/api/community/posts")
def api_create_post(body: PostIn):
    out = community.create_post(body.author, body.title, body.content, body.tags)
    if out.get("error"):
        raise HTTPException(status_code=403, detail=out["error"])
    return out


@app.post("/api/community/posts/{pid}/like")
def api_like(pid: int):
    community.like_post(pid)
    return {"ok": True}


@app.post("/api/community/posts/{pid}/favorite")
def api_favorite(pid: int, body: FavoriteIn):
    return {"ok": True, "message": community.favorite_post(body.user_id, pid)}


@app.get("/api/community/posts/{pid}/comments")
def api_comments(pid: int):
    return {"comments": community.list_comments(pid)}


@app.post("/api/community/posts/{pid}/comments")
def api_comment(pid: int, body: CommentIn):
    out = community.comment_post(pid, body.author, body.content)
    if out.get("error"):
        raise HTTPException(status_code=403, detail=out["error"])
    return out


# ------------------------- 运营后台（RBAC）-------------------------

@app.get("/api/admin/pending")
def api_pending(x_user_role: str | None = Header(default=None)):
    require_admin(x_user_role)
    return {"posts": community.pending_posts()}


@app.post("/api/admin/review")
def api_review(body: ReviewIn, x_user_role: str | None = Header(default=None)):
    require_admin(x_user_role)
    community.review_post(body.post_id, body.approve)
    if body.approve and body.featured:
        community.set_featured(body.post_id, True)
    return {"ok": True, "post_id": body.post_id,
            "status": "approved" if body.approve else "rejected"}


@app.post("/api/admin/featured/{pid}")
def api_featured(pid: int, featured: bool = True,
                 x_user_role: str | None = Header(default=None)):
    require_admin(x_user_role)
    community.set_featured(pid, featured)
    return {"ok": True}


@app.post("/api/admin/ban/{user_id}")
def api_ban(user_id: str, reason: str = "", days: int = 7,
            x_user_role: str | None = Header(default=None)):
    require_admin(x_user_role)
    community.ban_user(user_id, reason, days)
    return {"ok": True, "user_id": user_id, "days": days}


# ------------------------- 前端托管 -------------------------

@app.get("/")
def index():
    idx = WEB_DIR / "index.html"
    if not idx.exists():
        return {"message": "前端未构建，把 index.html 放到 web/ 目录", "docs": "/docs"}
    return FileResponse(idx)


@app.get("/health")
def health():
    return {"status": "ok", "model": settings.chat_model, "embed": settings.embed_model}
