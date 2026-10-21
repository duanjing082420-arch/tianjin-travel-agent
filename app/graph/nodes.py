# -*- coding: utf-8 -*-
"""
状态图的节点实现。五个节点，对应五件事：

    retrieve  检索   —— 按需求去知识库里捞材料
    draft     起草   —— 让模型照着材料写行程
    validate  校验   —— 用规则挑毛病（防幻觉、防闭馆、防超时）
    optimize  优化   —— 按地理位置重排顺序、填交通时间
    finalize  定稿   —— 汇总成前端要的结构

为什么要单独一个 validate 节点，而不是让模型自己检查？
    模型自查靠不住：它会给自己找理由。用 Python 规则校验是确定性的，
    尤其"景点名字是不是我们库里的"这一条，能直接掐掉大半幻觉。
"""
from __future__ import annotations

from datetime import date, timedelta

from ..llm import chat_json
from ..rag.knowledge import resolve_name
from ..rag.retriever import retrieve, format_context
from ..tools.route_tools import optimize_route, route_summary, travel_minutes
from ..tools.geo import zone_of

MAX_REVISION = 2          # 最多返工 2 轮，再多说明提示词有问题，别烧钱
DAY_START_MIN = 9 * 60    # 每天 09:00 出发
DAY_END_MIN = 21 * 60     # 每天 21:00 收工
MAX_ACTIVE_MIN = 10 * 60  # 每天有效活动时长上限（含交通）

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ------------------------- 工具函数 -------------------------

def _to_min(hhmm: str) -> int:
    """'09:30' → 570 分钟"""
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def _to_hhmm(minutes: int) -> str:
    minutes = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_date(s: str | None) -> date:
    if s:
        try:
            y, m, d = str(s).split("-")
            return date(int(y), int(m), int(d))
        except Exception:
            pass
    return date.today() + timedelta(days=7)


def _dates(start: date, days: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]


# ------------------------- 节点 1：检索 -------------------------

def retrieve_node(state: dict) -> dict:
    """按用户需求多路检索，攒出一份候选材料池。"""
    req = state["request"]
    interests = req.get("interests") or []
    days = int(req.get("days", 3))
    people = req.get("people", 2)
    transport = req.get("transport", "metro")
    with_who = req.get("with_who", "")

    # 查询词：一条通用 + 每个兴趣一条 + 人群一条
    queries = ["天津必去核心景点"]
    queries += [f"天津{i}" for i in interests]
    if with_who:
        queries.append(f"适合{with_who}的天津景点")
    if transport == "walk":
        queries.append("天津市中心步行可达的景点")

    # --- 景点多路检索去重 ---
    seen: dict[str, dict] = {}
    for q in queries:
        for hit in retrieve(q, top_k=8, types=["spot"]):
            seen.setdefault(hit["id"], hit)
    # 保证头部经典景点不会被兴趣词挤掉
    for hit in retrieve("天津地标 五大道 意风区 古文化街", top_k=6, types=["spot"]):
        seen.setdefault(hit["id"], hit)

    spot_pool = [h["meta"] for h in seen.values()]

    # --- 餐饮检索 ---
    food_seen: dict[str, dict] = {}
    food_queries = ["天津特色美食 必吃"] + [f"天津{i}餐厅" for i in interests if i in ("美食", "小吃", "海鲜")]
    for q in food_queries:
        for hit in retrieve(q, top_k=8, types=["food"]):
            food_seen.setdefault(hit["id"], hit)
    food_pool = [h["meta"] for h in food_seen.values()]

    # --- 交通与避坑贴士 ---
    tip_seen: dict[str, dict] = {}
    for q in ["天津交通 地铁 出行提示", "天津旅游 避坑 省钱", "天津景点 闭馆 预约", "天津区域分布 跨区耗时"]:
        for hit in retrieve(q, top_k=3, types=["tip"]):
            tip_seen.setdefault(hit["id"], hit)
    tip_pool = [h["meta"] for h in tip_seen.values()]

    # --- 拼成给模型看的参考资料 ---
    spot_ctx = format_context(
        [{"id": s["id"], "meta": s} for s in spot_pool[:16]], max_chars=2000
    )
    food_ctx = format_context(
        [{"id": f["id"], "meta": f} for f in food_pool[:12]], max_chars=1100
    )
    tip_ctx = "\n".join(f"- {t['name']}：{t['text']}" for t in tip_pool[:5])

    knowledge = (
        "【可选景点】（只能从这里选）\n" + spot_ctx
        + "\n\n【可选餐饮】（只能从这里选）\n" + food_ctx
        + "\n\n【交通与避坑提醒】\n" + tip_ctx
    )

    return {
        "queries": queries,
        "spot_pool": spot_pool,
        "food_pool": food_pool,
        "tip_pool": tip_pool,
        "knowledge": knowledge,
        "revision": state.get("revision", 0),
        "issues": [],
    }


# ------------------------- 节点 2：起草 -------------------------

_DRAFT_SCHEMA = """{
  "days": [
    {
      "day": 1,
      "date": "2026-10-24",
      "weekday": "周六",
      "theme": "海河与老城：从租界走到津门故里",
      "items": [
        {"type": "spot", "name": "五大道", "start": "09:00", "end": "11:30",
         "stay_min": 150, "transport": "metro", "note": "骑共享单车穿行，重点看民园体育场"},
        {"type": "meal", "name": "煎饼馃子", "start": "07:40", "end": "08:10",
         "note": "路边摊最正宗，只加鸡蛋和馃箅儿"}
      ],
      "tips": "当天步行较多，穿舒适鞋"
    }
  ],
  "budget_estimate": {"ticket": 0, "food": 0, "transport": 0, "total": 0},
  "notes": "整体节奏说明"
}"""


def draft_node(state: dict) -> dict:
    """让模型照着材料写出按天拆分的行程草稿。"""
    req = state["request"]
    start = _parse_date(req.get("start_date"))
    days = int(req.get("days", 3))
    day_list = _dates(start, days)

    day_desc = "\n".join(
        f"  第{i+1}天：{d.isoformat()}（{WEEKDAY_CN[d.weekday()]}）"
        for i, d in enumerate(day_list)
    )
    budget = req.get("budget", 3000)
    people = int(req.get("people", 2))

    prev_issues = state.get("issues") or []
    fix_hint = ""
    if prev_issues:
        fix_hint = "\n\n【上一版被发现的问题，必须修正】\n" + "\n".join(
            f"  - {i['reason']}" for i in prev_issues if i.get("level") == "error"
        )

    prompt = f"""你是天津本地资深旅行规划师。请基于用户需求和参考资料，生成一份可直接执行的行程。

【用户需求】
目的地：{req.get('city', '天津')}
行程天数：{days} 天
日期与星期：
{day_desc}
同行人数：{people} 人 {('（' + req.get('with_who') + '）') if req.get('with_who') else ''}
总预算：{budget} 元（人均约 {int(budget) // max(1, people)} 元）
交通方式：{req.get('transport', 'metro')}（walk=步行/bike=骑行/metro=地铁/taxi=打车/car=自驾）
兴趣标签：{'、'.join(req.get('interests') or ['经典打卡'])}
节奏偏好：{req.get('pace', '适中')}

{state['knowledge']}
{fix_hint}

【硬性约束，违反任何一条都要重做】
1. 景点和餐厅的名字必须原样来自上面的参考资料，严禁自己编造名称、票价、地址
2. 每天安排 3-5 个景点 + 早/中/晚三餐（餐厅从餐饮列表里选，不要重复太多）
3. 每天 09:00 出发、21:00 前结束，有效活动（游览+交通）不超过 10 小时
4. 相邻景点尽量在同一片区（参考资料里的"所在区"），不要一天里来回跨区
5. 周一闭馆的景点绝对不能排在周一（天津博物馆、周邓纪念馆、静园、海洋博物馆等）
6. 每个景点的 stay_min 参考参考资料的"建议游玩时长"，不要凭空捏造
7. 交通方式字段（transport）用 walk/bike/metro/taxi/car 之一

【输出格式】只输出 JSON，结构与下面一致，不要任何解释文字：
{_DRAFT_SCHEMA}"""

    draft = chat_json(prompt, temperature=0.5, max_tokens=3500)
    if isinstance(draft, list):
        draft = {"days": draft}
    # 带问题上路说明这是返工，revision +1（用来限制返工次数，防死循环烧钱）
    revision = state.get("revision", 0) + (1 if prev_issues else 0)
    return {"draft": draft, "revision": revision}


# ------------------------- 节点 3：校验 -------------------------

def validate_node(state: dict) -> dict:
    """规则校验：掐幻觉、掐闭馆、掐超时、掐跨区折返。

    第一步先做"别名归一"：模型爱写简称（意风区、天博、煎饼果子），
    先归一到知识库标准名，再判断是不是幻觉。这样既不冤枉模型，
    也避免把"意风区"这种正确意图当成错误打回去重做。
    """
    draft = state.get("draft") or {}
    spot_pool = state.get("spot_pool", [])
    food_pool = state.get("food_pool", [])
    revision = state.get("revision", 0)

    valid_spots = {s["name"]: s for s in spot_pool}
    valid_foods = {f["name"]: f for f in food_pool}
    issues: list[dict] = []

    spot_names = list(valid_spots.keys())
    food_names = list(valid_foods.keys())

    start = _parse_date(state["request"].get("start_date"))
    days_list = _dates(start, int(state["request"].get("days", 3)))

    # 0) 别名归一
    for day in draft.get("days", []):
        for it in day.get("items", []):
            raw = str(it.get("name", "")).strip()
            pool = food_names if it.get("type") == "meal" else spot_names
            resolved = resolve_name(raw, pool)
            if resolved and resolved != raw:
                it["name"] = resolved
                issues.append(
                    {"level": "info", "reason": f"已把「{raw}」归一为标准名称「{resolved}」"}
                )

    used_names: set[str] = set()
    for day in draft.get("days", []):
        idx = int(day.get("day", 1)) - 1
        day_date = days_list[idx] if 0 <= idx < len(days_list) else start
        items = day.get("items", [])

        # 1) 幻觉检查：名字必须来自知识库
        for it in items:
            name = str(it.get("name", "")).strip()
            if it.get("type") == "meal":
                if name not in valid_foods:
                    issues.append(
                        {"level": "error", "reason": f"餐厅「{name}」不在知识库中，属于编造内容，请换成资料里的餐厅"}
                    )
            else:
                if name not in valid_spots:
                    issues.append(
                        {"level": "error", "reason": f"景点「{name}」不在知识库中，属于编造内容，请换成资料里的景点"}
                    )
                elif name in used_names:
                    issues.append({"level": "warn", "reason": f"景点「{name}」在行程中重复出现"})
                used_names.add(name)

        # 1.5) 景点数量检查：一天少于 3 个景点说明模型偷懒了
        n_spots = sum(1 for i in items if i.get("type") != "meal" and i.get("name") in valid_spots)
        if n_spots < 3:
            issues.append(
                {"level": "error", "reason": f"第{idx+1}天只安排了 {n_spots} 个景点，太单薄，请补齐到 3-5 个"}
            )

        # 2) 闭馆检查
        spots_today = [valid_spots[i["name"]] for i in items if i.get("type") != "meal" and i["name"] in valid_spots]
        weekday_cn = WEEKDAY_CN[day_date.weekday()]
        for s in spots_today:
            hours = str(s.get("open_hours", "") or "")
            if f"{weekday_cn}闭馆" in hours:
                issues.append(
                    {"level": "error", "reason": f"第{idx+1}天是{weekday_cn}，但「{s['name']}」当天闭馆（{hours}），请换景点或换日期"}
                )

        # 3) 时长检查
        total = 0
        prev_end = None
        for it in items:
            s, e = _to_min(it.get("start", "")), _to_min(it.get("end", ""))
            if s < 0 or e < 0 or e <= s:
                issues.append({"level": "warn", "reason": f"「{it.get('name','')}」的时间段填写有误"})
                continue
            total += e - s
            if prev_end is not None and s < prev_end:
                issues.append({"level": "warn", "reason": f"「{it.get('name','')}」的开始时间早于上一项结束时间，时间重叠了"})
            prev_end = e
        if total > MAX_ACTIVE_MIN:
            issues.append(
                {"level": "error", "reason": f"第{idx+1}天安排了 {total//60} 小时，超过 10 小时上限，请删掉 1-2 个景点"}
            )

        # 4) 跨区折返检查
        zones = [zone_of(s.get("district", "")) for s in spots_today]
        changes = sum(1 for i in range(1, len(zones)) if zones[i] != zones[i - 1])
        if len(set(zones)) > 2:
            issues.append(
                {"level": "error", "reason": f"第{idx+1}天跨越了 {len(set(zones))} 个片区（{'/'.join(sorted(set(zones)))}），通勤会吃掉大半天，请按片区重组"}
            )
        elif changes >= 3:
            issues.append({"level": "warn", "reason": f"第{idx+1}天在片区之间来回切换 {changes} 次，存在折返"})

        # 5) 餐饮检查
        if not any(i.get("type") == "meal" for i in items):
            issues.append({"level": "error", "reason": f"第{idx+1}天没有安排任何餐饮，必须包含早/中/晚三餐"})

    return {"issues": issues, "revision": revision}


def route_after_validate(state: dict) -> str:
    """条件边三分支：

        - 没问题                  → 直接进优化
        - 有硬伤、还能返工        → 带着问题清单回起草节点重做
        - 有硬伤、返工次数用尽    → 走规则兜底修复（不再烧 LLM）

    第三条分支是本项目刻意加的保险：不能因为模型改不好，
    就把"周一闭馆的博物馆"这种坑留给用户到门口才发现。
    """
    issues = state.get("issues", [])
    has_error = any(i.get("level") == "error" for i in issues)
    if not has_error:
        return "optimize"
    if state.get("revision", 0) < MAX_REVISION:
        return "draft"
    return "repair"


# ------------------------- 节点 3.5：规则兜底修复 -------------------------

def repair_node(state: dict) -> dict:
    """返工次数用尽仍有硬伤时，用规则硬修，保证输出能落地。

    修三类问题：
        1. 知识库里没有的景点 → 直接删掉（宁缺勿假）
        2. 当天闭馆的景点     → 换成同片区、当天开放、行程里还没用过的
        3. 一天景点少于 3 个   → 从同片区补
    """
    draft = state.get("draft") or {}
    spot_pool = {s["name"]: s for s in state.get("spot_pool", [])}
    food_names = {f["name"] for f in state.get("food_pool", [])}
    start = _parse_date(state["request"].get("start_date"))
    days_list = _dates(start, int(state["request"].get("days", 3)))

    used: set[str] = set()
    notes: list[str] = []

    for day in draft.get("days", []):
        idx = int(day.get("day", 1)) - 1
        day_date = days_list[idx] if 0 <= idx < len(days_list) else start
        weekday_cn = WEEKDAY_CN[day_date.weekday()]
        new_items = []

        for it in day.get("items", []):
            name = str(it.get("name", "")).strip()
            if it.get("type") == "meal":
                if name in food_names:
                    new_items.append(it)
                continue

            if name not in spot_pool:
                notes.append(f"第{idx+1}天：移除了知识库外的景点「{name}」")
                continue

            hours = str(spot_pool[name].get("open_hours", "") or "")
            if f"{weekday_cn}闭馆" in hours:
                zone = zone_of(spot_pool[name].get("district", ""))
                alt = None
                for cand in state.get("spot_pool", []):
                    cn = cand["name"]
                    if cn in used or cn == name:
                        continue
                    if zone_of(cand.get("district", "")) != zone:
                        continue
                    if f"{weekday_cn}闭馆" in str(cand.get("open_hours", "") or ""):
                        continue
                    alt = cand
                    break
                if alt:
                    it["name"] = alt["name"]
                    it["ticket"] = alt.get("ticket", "")
                    it["stay_min"] = alt.get("stay_min", 60)
                    notes.append(
                        f"第{idx+1}天：「{name}」{weekday_cn}闭馆，"
                        f"已替换为同片区的「{alt['name']}」"
                    )
                    name = alt["name"]
                else:
                    notes.append(f"第{idx+1}天：「{name}」{weekday_cn}闭馆且无同片区替代，已移除")
                    continue

            if name in used:
                continue
            used.add(name)
            it["name"] = name
            new_items.append(it)

        # 补足到至少 3 个景点
        n_spots = sum(1 for i in new_items if i.get("type") != "meal")
        if n_spots < 3:
            zone_count: dict[str, int] = {}
            for i in new_items:
                if i.get("type") != "meal":
                    z = zone_of(spot_pool[i["name"]].get("district", ""))
                    zone_count[z] = zone_count.get(z, 0) + 1
            main_zone = max(zone_count, key=zone_count.get) if zone_count else "市中心"
            for cand in state.get("spot_pool", []):
                if n_spots >= 3:
                    break
                cn = cand["name"]
                if cn in used or zone_of(cand.get("district", "")) != main_zone:
                    continue
                if f"{weekday_cn}闭馆" in str(cand.get("open_hours", "") or ""):
                    continue
                new_items.append(
                    {
                        "type": "spot",
                        "name": cn,
                        "stay_min": cand.get("stay_min", 60),
                        "ticket": cand.get("ticket", ""),
                        "district": cand.get("district", ""),
                        "note": "系统补充的邻近景点",
                    }
                )
                used.add(cn)
                n_spots += 1
                notes.append(f"第{idx+1}天：景点不足，已补充「{cn}」")

        day["items"] = new_items

    # 硬伤已经被规则修掉了，不该再作为 error 展示给用户，降为 info
    remaining = [
        {**i, "level": "info", "reason": i["reason"] + "（已由系统自动修复）"}
        if i.get("level") == "error"
        else i
        for i in state.get("issues", [])
    ]
    return {"draft": draft, "repair_notes": notes, "issues": remaining}


# ------------------------- 节点 4：路线优化 -------------------------

def _pick_food(pool: list[dict], slot: str, used: set[str]) -> dict | None:
    """按餐段挑一家餐厅：优先 meal 字段匹配，其次小吃，最后任意。"""
    for f in pool:
        if f.get("name") not in used and f.get("meal") == slot:
            return f
    for f in pool:
        if f.get("name") not in used and f.get("meal") == "snack":
            return f
    for f in pool:
        if f.get("name") not in used:
            return f
    return None


def optimize_node(state: dict) -> dict:
    """按地理位置重排每天的景点顺序，并把时间和三餐实打实算出来。

    模型排的顺序常常是"想到哪写到哪"：上午在和平区、中午跑去南开、
    下午又折回和平。这一节点用地理算法纠正，顺带把通勤分钟填进去。

    时间安排也在这里统一重排（而不是信模型给的时间）：
    从 09:00 出发，按 游玩时长 + 通勤时长 往后推，
    再把早/午/晚三餐插到对应的时间锚点上。
    """
    draft = state.get("draft") or {}
    spot_pool = {s["name"]: s for s in state.get("spot_pool", [])}
    food_pool = state.get("food_pool", [])
    transport = state["request"].get("transport", "metro")
    mode = transport if transport in ("walk", "bike", "metro", "taxi", "car") else "metro"

    route_info: dict = {}
    warnings: list[str] = list(state.get("repair_notes", []))
    used_food: set[str] = set()   # 跨天共享：避免三天早餐都是煎饼馃子

    for day in draft.get("days", []):
        items = day.get("items", [])
        spot_items = [
            i for i in items if i.get("type") != "meal" and i.get("name") in spot_pool
        ]
        if not spot_items:
            route_info[str(day.get("day"))] = {
                "order": [], "legs": [], "total_travel_min": 0, "zones": []
            }
            continue

        spots = [spot_pool[i["name"]] for i in spot_items]
        stay_map = {
            i["name"]: int(i.get("stay_min") or spot_pool[i["name"]].get("stay_min") or 60)
            for i in spot_items
        }
        note_map = {i.get("name"): i.get("note", "") for i in spot_items}

        order = optimize_route(spots, mode=mode)
        seq = [spots[i] for i in order]

        # 餐饮去重跨天共享：三天都吃"罾蹦鲤鱼"就不像人排的了
        draft_meals = [i for i in items if i.get("type") == "meal"]
        food_by_name = {f["name"]: f for f in food_pool}

        def choose_meal(slot: str):
            """按餐段选一家：优先用模型自己选的（尊重它的意图），不够再从库里补。"""
            for dm in draft_meals:
                nm = dm.get("name", "")
                if nm in used_food:
                    continue
                if food_by_name.get(nm, {}).get("meal") == slot:
                    used_food.add(nm)
                    return nm, dm.get("note", "")
            f = _pick_food(food_pool, slot, used_food)
            if f:
                used_food.add(f["name"])
                return f["name"], str(f.get("text", ""))[:38]
            return None

        clock = DAY_START_MIN
        new_items: list[dict] = []
        legs: list[dict] = []
        lunch_done = False

        for k, spot in enumerate(seq):
            # 到饭点先吃饭再赶路 —— 吃饭的 45 分钟必须计入时间线，
            # 否则后面的景点会跟午餐时间撞车（这是上一版真实踩到的坑）
            if not lunch_done and clock >= 11 * 60 + 30:
                picked = choose_meal("lunch")
                if picked:
                    new_items.append(
                        {
                            "type": "meal",
                            "name": picked[0],
                            "start": _to_hhmm(clock),
                            "end": _to_hhmm(clock + 45),
                            "slot": "lunch",
                            "note": picked[1],
                        }
                    )
                    clock += 45
                lunch_done = True

            if k > 0:
                t = travel_minutes(seq[k - 1], spot, mode)
                clock += t
                legs.append(
                    {
                        "from": seq[k - 1]["name"],
                        "to": spot["name"],
                        "minutes": t,
                        "mode": mode,
                        "cross_zone": zone_of(seq[k - 1].get("district", ""))
                        != zone_of(spot.get("district", "")),
                    }
                )
            stay = stay_map.get(spot["name"], 60)
            new_items.append(
                {
                    "type": "spot",
                    "name": spot["name"],
                    "start": _to_hhmm(clock),
                    "end": _to_hhmm(clock + stay),
                    "stay_min": stay,
                    "transport": mode,
                    "district": spot.get("district", ""),
                    "ticket": spot.get("ticket", ""),
                    "note": note_map.get(spot["name"], ""),
                }
            )
            clock += stay

        # 午饭兜底：如果景点排完才到饭点（比如 12 点就收工），午饭还没吃上
        if not lunch_done:
            picked = choose_meal("lunch")
            if picked:
                at = max(clock, 11 * 60 + 30)
                new_items.append(
                    {
                        "type": "meal",
                        "name": picked[0],
                        "start": _to_hhmm(at),
                        "end": _to_hhmm(at + 45),
                        "slot": "lunch",
                        "note": picked[1],
                    }
                )
            lunch_done = True

        # 早餐：出发前 45 分钟（不早于 07:30）
        picked = choose_meal("breakfast")
        if picked:
            new_items.append(
                {
                    "type": "meal",
                    "name": picked[0],
                    "start": _to_hhmm(max(7 * 60 + 30, DAY_START_MIN - 45)),
                    "end": _to_hhmm(max(7 * 60 + 30, DAY_START_MIN - 45) + 45),
                    "slot": "breakfast",
                    "note": picked[1],
                }
            )

        # 晚餐：收工后，但不早于 18:00
        picked = choose_meal("dinner")
        if picked:
            at = max(clock, 18 * 60)
            new_items.append(
                {
                    "type": "meal",
                    "name": picked[0],
                    "start": _to_hhmm(at),
                    "end": _to_hhmm(at + 60),
                    "slot": "dinner",
                    "note": picked[1],
                }
            )


        new_items.sort(key=lambda x: _to_min(x.get("start", "00:00")))
        day["items"] = new_items
        day["travel_notes"] = [
            f"{l['from']} → {l['to']}：{mode} 约 {l['minutes']} 分钟" for l in legs
        ]

        total_travel = sum(l["minutes"] for l in legs)
        zones = sorted({zone_of(s.get("district", "")) for s in seq})
        route_info[str(day.get("day"))] = {
            "order": [s["name"] for s in seq],
            "legs": legs,
            "total_travel_min": total_travel,
            "zones": zones,
        }

        if len(zones) > 1:
            warnings.append(
                f"第{day.get('day')}天跨 {'/'.join(zones)} 多个片区，"
                f"通勤约 {total_travel} 分钟，建议留出余量"
            )
        if new_items and _to_min(new_items[-1]["end"]) > DAY_END_MIN:
            warnings.append(
                f"第{day.get('day')}天排到 {new_items[-1]['end']}，偏晚，可酌情删掉最后一项"
            )

    return {"draft": draft, "route_info": route_info, "warnings": warnings}




# ------------------------- 节点 5：定稿 -------------------------

def finalize_node(state: dict) -> dict:
    """汇总成品：行程 + 路线信息 + 校验问题 + 提醒。"""
    draft = state.get("draft") or {}
    issues = state.get("issues", [])
    spot_pool = {s["name"]: s for s in state.get("spot_pool", [])}

    # 预算估算：门票取票价里的数字做粗略估计
    ticket_total = 0
    for day in draft.get("days", []):
        for it in day.get("items", []):
            if it.get("type") == "meal":
                continue
            spot = spot_pool.get(it.get("name"))
            if not spot:
                continue
            ticket_total += _extract_price(spot.get("ticket", ""))

    food_total = 0
    for day in draft.get("days", []):
        n_meals = sum(1 for i in day.get("items", []) if i.get("type") == "meal")
        food_total += n_meals * 60  # 人均每餐 60 元的经验值

    people = int(state["request"].get("people", 2))
    transport_total = sum(
        r.get("total_travel_min", 0) // 10 * 5 for r in state.get("route_info", {}).values()
    )

    itinerary = {
        "title": f"天津 {state['request'].get('days', 3)} 日行程",
        "request": state["request"],
        "days": draft.get("days", []),
        "budget_estimate": {
            "ticket": ticket_total,
            "food": food_total * people,
            "transport": transport_total,
            "total": ticket_total + food_total * people + transport_total,
        },
        "issues": issues,
        "warnings": state.get("warnings", []),
        "route_info": state.get("route_info", {}),
        "revision": state.get("revision", 0),
    }
    return {"itinerary": itinerary}


def _extract_price(ticket: str) -> int:
    """从票价文本里抠数字：'成人 70 元' → 70；'免费' → 0"""
    import re

    if not ticket:
        return 0
    if "免费" in ticket:
        return 0
    nums = re.findall(r"\d+", ticket)
    return int(nums[0]) if nums else 0
