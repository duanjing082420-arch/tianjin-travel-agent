# -*- coding: utf-8 -*-
"""
路线工具箱：这三个函数是"工具调用"层，既能被 LangGraph 节点直接调，
也能包装成 function calling 交给模型自己决定什么时候用。

    distance_matrix() 距离矩阵：任意两点之间要多久
    optimize_route()  路线优化：把一堆景点排成不走冤枉路的顺序
    check_opening()   开放时间校验：这天去会不会吃闭门羹

旅行商问题（TSP）不需要最优解 —— 一天最多 5-6 个点，
贪心最近邻 + 2-opt 局部改进就能拿到接近最优的路线，且毫秒级返回。
"""
from __future__ import annotations

import itertools
from datetime import date

from .geo import travel_minutes, zone_of, haversine_km


def distance_matrix(spots: list[dict], mode: str = "metro") -> list[list[int]]:
    """返回 n×n 的通勤分钟矩阵，matrix[i][j] 表示从 i 到 j 要多少分钟。"""
    n = len(spots)
    m = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            t = travel_minutes(spots[i], spots[j], mode)
            m[i][j] = t
            m[j][i] = t
    return m


def _route_cost(order: list[int], matrix: list[list[int]]) -> int:
    return sum(matrix[order[i]][order[i + 1]] for i in range(len(order) - 1))


def optimize_route(
    spots: list[dict],
    start_index: int | None = None,
    mode: str = "metro",
) -> list[int]:
    """给景点排序，使总通勤时间最短。

    算法：
      1. 贪心最近邻：从起点出发，每次都去最近的还没去的点
      2. 2-opt 改进：试着把路线中间某一段整个翻转，看总时间是否变短

    返回重排后的下标列表（不是景点本身，方便调用方自己取整条记录）。
    """
    n = len(spots)
    if n <= 2:
        return list(range(n))

    matrix = distance_matrix(spots, mode)

    # --- 贪心最近邻 ---
    if start_index is None:
        # 没指定起点时，选"离其他点平均最近"的点当起点（通常是片区中心）
        start_index = min(range(n), key=lambda i: sum(matrix[i]) / n)

    unvisited = set(range(n)) - {start_index}
    order = [start_index]
    cur = start_index
    while unvisited:
        nxt = min(unvisited, key=lambda j: matrix[cur][j])
        order.append(nxt)
        unvisited.discard(nxt)
        cur = nxt

    # --- 2-opt 局部改进（翻转子段）---
    improved = True
    best = order
    best_cost = _route_cost(best, matrix)
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                cand = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                cost = _route_cost(cand, matrix)
                if cost < best_cost - 1:
                    best, best_cost, improved = cand, cost, True
    return best


def check_opening(spots: list[dict], day: date) -> list[dict]:
    """检查这批景点在某天是否闭馆。

    规则来源：语料里的 open_hours 字段。
    "周一闭馆" 是最常见也最容易踩的坑 —— 天津的博物馆群几乎集体周一休息，
    自动排行程时如果不查，第二天用户到了门口才发现进不去。
    """
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day.weekday()]
    issues = []
    for s in spots:
        hours = str(s.get("open_hours", "") or "")
        if not hours:
            continue
        if f"{weekday_cn}闭馆" in hours:
            issues.append(
                {
                    "spot": s.get("name", ""),
                    "level": "error",
                    "reason": f"{weekday_cn}闭馆（开放时间：{hours}）",
                }
            )
        elif "周一上午检修" in hours and weekday_cn == "周一":
            issues.append(
                {
                    "spot": s.get("name", ""),
                    "level": "warn",
                    "reason": "周一上午检修，建议下午前往",
                }
            )
    return issues


def route_summary(spots: list[dict], order: list[int], mode: str = "metro") -> dict:
    """按给定顺序生成路线摘要：总通勤时间、总游玩时间、是否跨区。"""
    if not order:
        return {"total_travel_min": 0, "total_stay_min": 0, "zones": [], "legs": []}

    legs = []
    total_travel = 0
    for i in range(len(order) - 1):
        a, b = spots[order[i]], spots[order[i + 1]]
        t = travel_minutes(a, b, mode)
        km = haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
        total_travel += t
        legs.append(
            {
                "from": a.get("name", ""),
                "to": b.get("name", ""),
                "minutes": t,
                "km": round(km, 1),
                "mode": mode,
                "cross_zone": zone_of(a.get("district", "")) != zone_of(b.get("district", "")),
            }
        )
    total_stay = sum(spots[i].get("stay_min", 60) for i in order)
    zones = sorted({zone_of(spots[i].get("district", "")) for i in order})
    return {
        "total_travel_min": total_travel,
        "total_stay_min": total_stay,
        "zones": zones,
        "legs": legs,
    }
