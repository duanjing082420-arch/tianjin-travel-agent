# -*- coding: utf-8 -*-
"""
对话服务：把"用户一句话"变成"行程的一次改动"。

一次对话的完整路径：
    用户说话 → 意图识别 → 局部改动（只动受影响的天）→ 时间重排+冲突检查
             → 存新版本 → 存消息 → 回复

只有 replan 意图才会整份重生成，其余都是局部修改 ——
这就是为什么多轮对话能做到秒级响应而不是每次等 30 秒。
"""
from __future__ import annotations

from ..graph.trip_graph import plan_trip
from ..storage import session as store
from .adjust import apply_intent
from .intent import detect_intent


def start_session(request: dict) -> dict:
    """开一次规划：跑完整图生成首版行程，落库。"""
    itinerary = plan_trip(request)
    sid = store.create_session(request, itinerary)
    store.add_message(sid, "user", json_dumps(request), "start")
    return {
        "session_id": sid,
        "version": 1,
        "itinerary": itinerary,
        "reply": f"已生成天津 {request.get('days', 3)} 日行程（第 1 版）",
    }


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def chat_turn(sid: str, text: str) -> dict:
    """处理一轮对话。"""
    sess = store.get_session(sid)
    if not sess:
        return {"error": "会话不存在", "session_id": sid}

    itinerary = sess["itinerary"]
    store.add_message(sid, "user", text)

    intent = detect_intent(text, itinerary)
    store.add_message(sid, "system", json_dumps(intent), intent.get("intent", ""))

    # --- 整份重做 ---
    if intent.get("intent") == "replan":
        new_itin = plan_trip(sess["request"])
        version = store.save_version(sid, new_itin, "用户要求重新规划")
        store.add_message(sid, "assistant", "已按需求重新生成整份行程")
        return {
            "session_id": sid,
            "intent": intent,
            "reply": "好的，我重新规划了一份（详见新版本）",
            "itinerary": new_itin,
            "version": version,
            "changed": True,
        }

    # --- 局部改动 ---
    note, changed = apply_intent(itinerary, intent)
    version = sess["current_version"]
    if changed:
        version = store.save_version(sid, itinerary, note)

    store.add_message(sid, "assistant", note)
    return {
        "session_id": sid,
        "intent": intent,
        "reply": note,
        "itinerary": itinerary,
        "version": version,
        "changed": changed,
    }


def rollback_turn(sid: str, version: int) -> dict:
    """回退到某个历史版本。"""
    itin = store.rollback(sid, version)
    if itin is None:
        return {"error": f"没有第 {version} 版"}
    return {"session_id": sid, "version": version, "itinerary": itin,
            "reply": f"已回退到第 {version} 版"}
