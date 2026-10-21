# -*- coding: utf-8 -*-
"""
第 5 步验收：FastAPI 端到端（服务需先启动）

运行前：
    .venv\\Scripts\\python.exe -m uvicorn app.api.main:app --port 8000
然后：
    .venv\\Scripts\\python.exe scripts\\check_step5.py
"""
import sys, io, os, json, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests

BASE = "http://127.0.0.1:8000"
REQ = {
    "city": "天津", "days": 3, "start_date": "2026-10-24", "people": 2,
    "budget": 3000, "transport": "metro", "interests": ["美食", "拍照"],
    "with_who": "情侣", "pace": "适中",
}

print("=" * 70)
print("第 5 步：FastAPI 端到端验收")
print("=" * 70)

r = requests.get(BASE + "/health", timeout=10)
print("health:", r.json())
print()

print("[1] POST /api/plan 生成行程（约 30 秒）")
t0 = time.time()
r = requests.post(BASE + "/api/plan", json=REQ, timeout=180)
d = r.json()
sid = d["session_id"]
print(f"    {r.status_code}  耗时 {time.time()-t0:.1f}s  会话 {sid}  v{d['version']}")
print(f"    天数：{len(d['itinerary']['days'])}  预算合计：{d['itinerary']['budget_estimate']['total']} 元")
print()

print("[2] POST /api/chat 多轮对话")
for msg in ["第二天轻松一点", "晚上想吃火锅", "推荐附近酒店"]:
    t0 = time.time()
    r = requests.post(BASE + "/api/chat", json={"session_id": sid, "message": msg}, timeout=120)
    o = r.json()
    print(f"    「{msg}」 → [{o['intent']['intent']}] {o['reply'][:60]}")
    print(f"       {time.time()-t0:.1f}s  v{o['version']}  changed={o['changed']}")
print()

print("[3] GET /api/session/{sid}/versions")
vs = requests.get(BASE + f"/api/session/{sid}/versions", timeout=10).json()
for v in vs["versions"]:
    print(f"    v{v['version']}  {v['change_note'][:50]}")
print()

print("[4] POST /api/rollback 回退到 v1")
o = requests.post(BASE + "/api/rollback", json={"session_id": sid, "version": 1}, timeout=30).json()
print("    ", o["reply"])
print()

print("[5] 社区：发布 → 点赞 → 评论 → 收藏")
p = requests.post(BASE + "/api/community/posts", json={
    "author": "津门老饕", "title": "天津早餐地图",
    "content": "煎饼馃子要吃南楼的，锅巴菜去大福来，别在景区买，价格翻倍还不正宗。",
    "tags": "美食,早餐",
}, timeout=30).json()
print("    发布：", p["message"], p.get("reasons") or "")
pid = p["post_id"]
requests.post(BASE + f"/api/community/posts/{pid}/like", timeout=10)
requests.post(BASE + f"/api/community/posts/{pid}/comments",
              json={"author": "小李", "content": "收藏了，下次就去"}, timeout=10)
print("    点赞+评论：ok")
fav = requests.post(BASE + f"/api/community/posts/{pid}/favorite",
                    json={"user_id": "u1"}, timeout=10).json()
print("    收藏：", fav["message"])
cmts = requests.get(BASE + f"/api/community/posts/{pid}/comments", timeout=10).json()
print("    评论列表：", [c["content"] for c in cmts["comments"]])
print()

print("[6] 运营后台 RBAC")
r = requests.get(BASE + "/api/admin/pending", timeout=10)
print("    不带角色：", r.status_code, r.json().get("detail", ""))
r = requests.post(BASE + f"/api/admin/featured/{pid}?featured=true",
                  headers={"X-User-Role": "admin"}, timeout=10)
print("    带 admin 设推荐位：", r.status_code, r.json())
posts = requests.get(BASE + "/api/community/posts?featured=true", timeout=10).json()
print("    推荐位列表：", [x["title"] for x in posts["posts"]])
print()

print("[7] 前端页面")
r = requests.get(BASE + "/", timeout=10)
print(f"    GET / → {r.status_code}, {len(r.text)} 字节, 含 Vue: {'createApp' in r.text}")

print()
print("=" * 70)
print("第 5 步完成。下一步：README + 面试话术。")
print("=" * 70)
