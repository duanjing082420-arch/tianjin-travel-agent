# -*- coding: utf-8 -*-
"""
第 4 步验收：多轮对话调整（意图识别 + 局部重规划 + 版本管理）。

运行：
    .venv\\Scripts\\python.exe scripts\\check_step4.py
"""
import sys, io, os, json, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.chat.intent import detect_intent
from app.chat.service import start_session, chat_turn
from app.storage import session as store
from app.rag.retriever import build_index

build_index()

REQUEST = {
    "city": "天津", "days": 3, "start_date": "2026-10-24",
    "people": 2, "budget": 3000, "transport": "metro",
    "interests": ["美食", "拍照"], "with_who": "情侣", "pace": "适中",
}

UTTERANCES = [
    "第二天轻松一点，别太赶",
    "晚上想吃火锅",
    "把天津之眼去掉",
    "推荐附近酒店",
    "改成打车吧",
    "预算有点超了，省着点",
]

print("=" * 70)
print("第 4 步：多轮对话调整验收")
print("=" * 70)

# --- 意图识别单独测试（不花钱，纯规则）---
print("[A] 意图识别（规则优先）")
for u in UTTERANCES:
    r = detect_intent(u, None)
    print(f"   「{u}」")
    print(f"      → intent={r['intent']}  day={r.get('day')}  keyword={r.get('keyword')!r}  [{r.get('source')}]")
print()

# --- 真实会话 ---
print("[B] 多轮对话（会真的改行程并存版本）")
sess = start_session(REQUEST)
sid = sess["session_id"]
print(f"   会话 {sid}，初始版本 v{sess['version']}")
print()

for u in UTTERANCES:
    t0 = time.time()
    out = chat_turn(sid, u)
    cost = time.time() - t0
    itin = out["itinerary"]
    changed = "已改" if out["changed"] else "未改"
    print(f"   用户：{u}")
    print(f"   意图：{out['intent']['intent']}  用时 {cost:.1f}s  {changed}  当前 v{out['version']}")
    reply = out["reply"].replace("\n", "\n        ")
    print(f"   回复：{reply}")
    print()

print("—— 版本历史 ——")
for v in store.list_versions(sid):
    print(f"   v{v['version']}  {v['change_note']}")
print()

print("—— 最终第 2 天安排 ——")
day2 = next((d for d in out["itinerary"]["days"] if d.get("day") == 2), None)
if day2:
    for it in day2.get("items", []):
        mark = "🍜" if it.get("type") == "meal" else "📍"
        print(f"   {mark} {it.get('start')}-{it.get('end')}  {it.get('name')}")
print()

print("—— 回退测试：回到 v1 ——")
rb = store.rollback(sid, 1)
d1 = next((d for d in rb["days"] if d.get("day") == 2), None)
if d1:
    print("   v1 第2天：", "、".join(i.get("name","") for i in d1.get("items", []) if i.get("type")!="meal"))
print()

print("=" * 70)
print("第 4 步完成。下一步：FastAPI 接口 + Vue3 前端。")
print("=" * 70)
