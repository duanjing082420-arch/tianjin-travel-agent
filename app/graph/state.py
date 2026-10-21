# -*- coding: utf-8 -*-
"""
状态定义：整张图里流动的那一张"表"。

打个比方：图是流水线，state 就是在流水线上传递的工件。
每个节点从 state 里读自己需要的东西，干完活把结果写回 state，
下一个节点接着干。节点之间不互相调用，只跟 state 打交道 ——
这样每个节点都能单独测试、单独替换。
"""
from __future__ import annotations

from typing import Any, TypedDict


class TripState(TypedDict, total=False):
    # --- 输入：用户填的原始需求 ---
    request: dict[str, Any]      # city/days/people/budget/transport/interests/start_date
    session_id: str

    # --- 检索阶段产物 ---
    queries: list[str]           # 由需求推导出的检索词
    knowledge: str               # 拼好的参考资料文本（给模型看）
    spot_pool: list[dict]        # 候选景点（完整字段，后面算路线要用）
    food_pool: list[dict]        # 候选餐饮
    tip_pool: list[dict]         # 交通/避坑贴士

    # --- 生成阶段产物 ---
    draft: dict[str, Any]        # 模型吐出的行程草稿
    issues: list[dict]           # 校验发现的问题 [{"level","reason",...}]
    revision: int                # 已经返工过几轮（防死循环）

    # --- 输出 ---
    itinerary: dict[str, Any]    # 最终行程
    route_info: dict[str, Any]   # 路线优化结果（通勤时间、跨区情况）
    warnings: list[str]          # 给用户的提醒
