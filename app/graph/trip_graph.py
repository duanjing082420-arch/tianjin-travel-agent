# -*- coding: utf-8 -*-
"""
把节点拼成一张图。

流程：
    retrieve → draft → validate ─┬─(有硬伤、还能返工)→ draft   ← 回头边是关键
                                 ├─(有硬伤、返工用尽)→ repair ─┐
                                 └─(没问题)──────────────────┤
                                                            └→ optimize → finalize

那条回头的边就是 LangGraph 相对普通顺序代码的价值：
校验不通过时自动带着问题清单回到起草节点重做，最多 2 轮。
如果用 if/else 硬写也能实现，但状态、返工次数、条件分支会散落一地，
画成图之后，整条链路一眼能看懂，也方便往里加节点。
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from . import nodes
from .state import TripState

_graph = None


def build_graph():
    g = StateGraph(TripState)
    g.add_node("retrieve", nodes.retrieve_node)
    g.add_node("draft", nodes.draft_node)
    g.add_node("validate", nodes.validate_node)
    g.add_node("repair", nodes.repair_node)
    g.add_node("optimize", nodes.optimize_node)
    g.add_node("finalize", nodes.finalize_node)

    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", "validate")
    g.add_conditional_edges(
        "validate",
        nodes.route_after_validate,
        {"draft": "draft", "repair": "repair", "optimize": "optimize"},
    )
    g.add_edge("repair", "optimize")
    g.add_edge("optimize", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def plan_trip(request: dict) -> dict:
    """对外唯一入口：丢进需求，吐出行程。

    request 示例：
        {"city":"天津","days":3,"start_date":"2026-10-26","people":2,
         "budget":3000,"transport":"metro","interests":["美食","拍照"],
         "with_who":"情侣","pace":"适中"}
    """
    result = get_graph().invoke(
        {"request": request, "revision": 0, "issues": []},
        config={"recursion_limit": 12},
    )
    return result.get("itinerary", {})
