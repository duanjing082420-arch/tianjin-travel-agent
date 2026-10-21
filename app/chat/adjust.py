# -*- coding: utf-8 -*-
"""
局部重规划：把意图翻译成对行程的"最小改动"。

核心思想 —— 千万不要每次对话都整份重生成：
    用户说"第二天轻松点"，只有第二天需要动，第一、三天原封不动。
    全量重生成要 30 秒、要花 token、还会把用户之前满意的部分改掉。
    这里的做法是：定位到具体某一天 → 只改那天的 items → 
    用同一套 optimize/validate 逻辑把时间和冲突重新算一遍。

每个 handler 返回 (改动说明, 是否真的改了)。
"""
from __future__ import annotations

from ..graph.nodes import optimize_node, validate_node
from ..rag.knowledge import load_knowledge, resolve_name
from ..rag.retriever import retrieve, format_context
from ..llm import chat

_SPOTS: list[dict] | None = None
_FOODS: list[dict] | None = None


def _pools() -> tuple[list[dict], list[dict]]:
    global _SPOTS, _FOODS
    if _SPOTS is None:
        _SPOTS, _FOODS = [], []
        for r in load_knowledge():
            if r.get("type") == "spot":
                _SPOTS.append(r)
            elif r.get("type") == "food":
                _FOODS.append(r)
    return _SPOTS, _FOODS


def _pick_day(itinerary: dict, day: int | None, prefer: str = "fullest") -> dict | None:
    """选要改的那天：没指明就按策略挑（加东西挑最空的，减东西挑最满的）。"""
    days = itinerary.get("days", [])
    if not days:
        return None
    if day:
        for d in days:
            if int(d.get("day", 0)) == int(day):
                return d
        return None

    def n_spots(d: dict) -> int:
        return sum(1 for i in d.get("items", []) if i.get("type") != "meal")

    return max(days, key=n_spots) if prefer == "fullest" else min(days, key=n_spots)


def _reflow(itinerary: dict, day_obj: dict) -> tuple[dict, dict]:
    """对某一天重排时间线 + 重算通勤，并做冲突检查。

    直接复用图里的 optimize_node 和 validate_node —— 
    保证"初始生成"和"对话调整"走的是同一套时间/冲突逻辑，
    不会出现两种方式算出来的行程格式不一致。
    """
    spots, foods = _pools()
    request = itinerary.get("request", {})
    state = {
        "draft": {"days": [day_obj]},
        "spot_pool": spots,
        "food_pool": foods,
        "request": request,
        "issues": [],
        "revision": 0,
    }
    out = optimize_node(state)
    day_obj = out["draft"]["days"][0]
    day_obj["route"] = out["route_info"].get(str(day_obj.get("day")), {})
    day_obj["warnings"] = [w for w in out["warnings"] if str(day_obj.get("day")) in w]

    v = validate_node({**state, "draft": {"days": [day_obj]}})
    day_obj["issues"] = [i for i in v.get("issues", []) if i.get("level") == "error"]
    return day_obj, {}


# ------------------------- 各意图的处理 -------------------------

def _h_relax(itinerary: dict, intent: dict) -> tuple[str, bool]:
    day = _pick_day(itinerary, intent.get("day"), prefer="fullest")
    if not day:
        return "没找到要调整的那天", False

    spots = [i for i in day.get("items", []) if i.get("type") != "meal"]
    if len(spots) > 3:
        victim = spots[-1]
        day["items"] = [i for i in day["items"] if i is not victim]
        note = f"第{day['day']}天去掉了「{victim['name']}」，节奏松一些"
    else:
        for i in day["items"]:
            if i.get("type") != "meal":
                i["stay_min"] = max(30, int(int(i.get("stay_min", 60)) * 0.75))
        note = f"第{day['day']}天各景点游玩时长缩短 25%，留出更多休息"

    _reflow(itinerary, day)
    return note, True


def _h_add_meal(itinerary: dict, intent: dict) -> tuple[str, bool]:
    kw = intent.get("keyword", "")
    spots, foods = _pools()
    target = resolve_name(kw, [f["name"] for f in foods]) if kw else None

    food = None
    if target:
        food = next(f for f in foods if f["name"] == target)
    else:
        hits = retrieve(kw or "天津特色美食", top_k=3, types=["food"])
        if hits:
            food = hits[0]["meta"]

    if not food:
        return f"没找到你说的「{kw}」，换个说法试试？比如'加顿火锅'", False

    day = _pick_day(itinerary, intent.get("day"), prefer="emptiest")
    if not day:
        return "没找到要加餐的那天", False

    day["items"].append(
        {
            "type": "meal",
            "name": food["name"],
            "note": str(food.get("text", ""))[:40],
            "stay_min": 45,
        }
    )
    _reflow(itinerary, day)
    return f"第{day['day']}天加上了「{food['name']}」", True


def _h_add_spot(itinerary: dict, intent: dict) -> tuple[str, bool]:
    kw = intent.get("keyword", "")
    spots, foods = _pools()
    target = resolve_name(kw, [s["name"] for s in spots]) if kw else None

    spot = None
    if target:
        spot = next(s for s in spots if s["name"] == target)
    else:
        hits = retrieve(kw or "天津必去景点", top_k=3, types=["spot"])
        if hits:
            spot = hits[0]["meta"]

    if not spot:
        return f"没找到你说的景点「{kw}」", False

    # 已经排过就不重复加
    for d in itinerary.get("days", []):
        if any(i.get("name") == spot["name"] for i in d.get("items", [])):
            return f"「{spot['name']}」已经在行程里了", False

    day = _pick_day(itinerary, intent.get("day"), prefer="emptiest")
    if not day:
        return "没找到要加景点的那天", False

    day["items"].append(
        {
            "type": "spot",
            "name": spot["name"],
            "stay_min": spot.get("stay_min", 60),
            "note": "按你的要求新增",
            "ticket": spot.get("ticket", ""),
            "district": spot.get("district", ""),
        }
    )
    _reflow(itinerary, day)
    return f"第{day['day']}天加上了「{spot['name']}」（{spot.get('district','')}）", True


def _h_remove_spot(itinerary: dict, intent: dict) -> tuple[str, bool]:
    kw = intent.get("keyword", "")
    if not kw:
        return "想去掉哪个？说清楚名字我才能删", False

    for day in itinerary.get("days", []):
        for it in day.get("items", []):
            if it.get("name") == kw or kw in str(it.get("name", "")):
                day["items"] = [i for i in day["items"] if i is not it]
                _reflow(itinerary, day)
                return f"第{day['day']}天去掉了「{it['name']}」", True
    return f"行程里没有「{kw}」", False


def _h_replace_spot(itinerary: dict, intent: dict) -> tuple[str, bool]:
    """'把 A 换成 B'：靠 LLM 抽取两个名字，再执行删除+新增。"""
    text = intent.get("keyword", "")
    parsed = chat_json_safe(text)
    old, new = parsed.get("old", ""), parsed.get("new", "")
    if not old or not new:
        return "没听清要把哪个换成哪个，试着说'把五大道换成古文化街'", False

    removed = False
    for day in itinerary.get("days", []):
        for it in day["items"]:
            if it.get("name") == old or old in str(it.get("name", "")):
                day["items"] = [i for i in day["items"] if i is not it]
                spots, _ = _pools()
                cand = resolve_name(new, [s["name"] for s in spots])
                if cand:
                    s = next(x for x in spots if x["name"] == cand)
                    day["items"].append(
                        {
                            "type": "spot",
                            "name": s["name"],
                            "stay_min": s.get("stay_min", 60),
                            "note": "替换后的景点",
                            "ticket": s.get("ticket", ""),
                            "district": s.get("district", ""),
                        }
                    )
                _reflow(itinerary, day)
                return f"第{day['day']}天：{old} → {cand or new}", True
    return f"行程里没有「{old}」", False


def _h_transport(itinerary: dict, intent: dict) -> tuple[str, bool]:
    mode = intent.get("params", {}).get("mode") or intent.get("keyword", "")
    if mode not in ("walk", "bike", "metro", "taxi", "car"):
        return "没识别要换成什么交通方式", False
    itinerary["request"]["transport"] = mode
    for day in itinerary.get("days", []):
        _reflow(itinerary, day)
    cn = {"walk": "步行", "bike": "骑行", "metro": "地铁", "taxi": "打车", "car": "自驾"}[mode]
    return f"全程交通方式改为{cn}，各天通勤时间已重算", True


def _h_budget(itinerary: dict, intent: dict) -> tuple[str, bool]:
    """省钱：把每天最贵的收费景点换成同片区的免费景点。"""
    changed = []
    for day in itinerary.get("days", []):
        paid = [
            i for i in day.get("items", [])
            if i.get("type") != "meal" and "免费" not in str(i.get("ticket", ""))
            and any(ch.isdigit() for ch in str(i.get("ticket", "")))
        ]
        if not paid:
            continue
        victim = paid[0]
        spots, _ = _pools()
        district = victim.get("district", "")
        alt = next(
            (
                s for s in spots
                if "免费" in str(s.get("ticket", ""))
                and s.get("district", "") == district
                and not any(i.get("name") == s["name"] for d in itinerary["days"] for i in d["items"])
            ),
            None,
        )
        day["items"] = [i for i in day["items"] if i is not victim]
        if alt:
            day["items"].append(
                {
                    "type": "spot", "name": alt["name"],
                    "stay_min": alt.get("stay_min", 60),
                    "ticket": alt.get("ticket", ""),
                    "district": alt.get("district", ""),
                    "note": "为控制预算替换的免费景点",
                }
            )
            changed.append(f"第{day['day']}天 {victim['name']} → {alt['name']}（免费）")
        else:
            changed.append(f"第{day['day']}天去掉收费景点 {victim['name']}")
        _reflow(itinerary, day)
    return "；".join(changed) if changed else "行程里没有收费景点可省", bool(changed)


def _h_hotel(itinerary: dict, intent: dict) -> tuple[str, bool]:
    """推荐住宿：按当天最后一个景点所在区域来推荐（就近住是硬道理）。"""
    day = _pick_day(itinerary, intent.get("day"), prefer="fullest")
    district = ""
    if day:
        spots = [i for i in day.get("items", []) if i.get("type") != "meal"]
        if spots:
            district = spots[-1].get("district", "")

    # 优先推荐同区域的：住得近是硬道理，别推荐 60 公里外的酒店
    query = f"{district} 住宿 酒店" if district else "市中心 住宿 酒店"
    hits = retrieve(query, top_k=3, types=["hotel"], district=district or None)
    if not hits:
        hits = retrieve("天津住宿 酒店", top_k=3, types=["hotel"])
    if not hits:
        return "暂时没有合适的住宿推荐", False

    lines = [f"按第{day['day']}天行程结束位置（{district or '市中心'}）推荐：" if day else "住宿推荐："]
    for h in hits:
        m = h["meta"]
        lines.append(
            f"  · {m['name']}（{m.get('district','')}，{m.get('price_level','')}，"
            f"约 {m.get('price','?')} 元/晚）\n    {str(m.get('text',''))[:80]}"
        )
    return "\n".join(lines), False   # 不改行程，只是回答


def _h_query(itinerary: dict, intent: dict) -> tuple[str, bool]:
    """纯提问：用 RAG 检索 + 模型回答，不动行程。"""
    from ..rag.retriever import retrieve as _r

    text = intent.get("keyword") or intent.get("raw", "")
    hits = _r(text or "天津旅游", top_k=4)
    ctx = format_context(hits, max_chars=1500)
    prompt = f"参考资料：\n{ctx}\n\n用户问题：{text}\n\n请基于资料回答，不要编造资料里没有的信息。"
    return chat(prompt, max_tokens=500), False


def chat_json_safe(text: str) -> dict:
    from ..llm import chat_json

    return chat_json(
        f"从下面这句话里，抽取'要被换掉的景点'和'想换成的景点'。"
        f"输出 JSON：{{\"old\":\"原景点名\",\"new\":\"新景点名\"}}\n\n原话：{text}",
        temperature=0.1,
        max_tokens=200,
    )


HANDLERS = {
    "relax": _h_relax,
    "add_meal": _h_add_meal,
    "add_spot": _h_add_spot,
    "remove_spot": _h_remove_spot,
    "replace_spot": _h_replace_spot,
    "transport": _h_transport,
    "budget": _h_budget,
    "hotel": _h_hotel,
    "query": _h_query,
}


def apply_intent(itinerary: dict, intent: dict) -> tuple[str, bool]:
    """统一入口：按意图分发到对应 handler。"""
    name = intent.get("intent", "query")
    if name == "replan":
        return "REPLAN", True
    handler = HANDLERS.get(name, _h_query)
    return handler(itinerary, intent)
