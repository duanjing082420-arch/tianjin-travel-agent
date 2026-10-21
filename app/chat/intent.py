# -*- coding: utf-8 -*-
"""
意图识别：把一句自然语言，翻译成一个可执行的操作指令。

为什么规则优先、模型兜底？
    "第二天轻松一点""加个火锅""去掉五大道" 这类高频说法是有限集，
    用正则秒回、零成本、绝不出错。只有规则认不出来的复杂表述
    （比如"我妈腿脚不好，后半程能不能别爬山了"）才交给模型判断。
    这样既快又省钱 —— 一次对话如果都要问模型，多轮下来成本翻倍。

输出统一结构：
    {"intent": "relax", "day": 2, "keyword": "", "params": {...}}
"""
from __future__ import annotations

import re

from ..llm import chat_json
from ..rag.knowledge import load_knowledge, resolve_name

INTENTS = [
    "relax",        # 当天轻松一点
    "add_meal",     # 加一顿饭 / 想吃火锅
    "add_spot",     # 加个景点
    "remove_spot",  # 去掉某景点
    "replace_spot", # 把 A 换成 B
    "hotel",        # 推荐附近酒店
    "transport",    # 改交通方式
    "budget",       # 预算调整
    "replan",       # 整份重做
    "query",        # 只是问问题，不改行程
]

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "两": 2}
_DAY_PATTERNS = [
    r"第\s*([0-9一二三四五两])\s*天",
    r"day\s*([0-9一二三四五两])",
    r"([0-9一二三四五两])\s*号那天",
]

_TRANSPORT_MAP = {
    "步行": "walk", "走路": "walk", "走过去": "walk",
    "骑车": "bike", "骑行": "bike", "共享单车": "bike", "自行车": "bike",
    "地铁": "metro", "公交": "metro", "轻轨": "metro",
    "打车": "taxi", "出租车": "taxi", "滴滴": "taxi",
    "自驾": "car", "开车": "car",
}

_POOL_CACHE: dict[str, list[str]] = {}


def _pools() -> tuple[list[str], list[str], list[str]]:
    """缓存景点/餐饮/酒店的标准名列表，用来判断关键词属于哪一类。"""
    if "spot" in _POOL_CACHE:
        return _POOL_CACHE["spot"], _POOL_CACHE["food"], _POOL_CACHE["hotel"]
    spots, foods, hotels = [], [], []
    for r in load_knowledge():
        t = r.get("type")
        name = str(r.get("name", ""))
        if t == "spot":
            spots.append(name)
        elif t == "food":
            foods.append(name)
        elif t == "hotel":
            hotels.append(name)
    _POOL_CACHE["spot"], _POOL_CACHE["food"], _POOL_CACHE["hotel"] = spots, foods, hotels
    return spots, foods, hotels


def _extract_day(text: str) -> int | None:
    for pat in _DAY_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            return int(raw) if raw.isdigit() else _CN_NUM.get(raw)
    return None


def _extract_transport(text: str) -> str | None:
    for cn, mode in _TRANSPORT_MAP.items():
        if cn in text:
            return mode
    return None


def _rule_detect(text: str) -> dict | None:
    """规则匹配，命中就返回，不命中返回 None。"""
    t = text.strip()
    day = _extract_day(t)

    if re.search(r"酒店|住宿|住哪|过夜|落脚", t):
        return {"intent": "hotel", "day": day, "keyword": "", "params": {}, "source": "rule"}

    mode = _extract_transport(t)
    if mode and re.search(r"改|换|改乘|改成|都用|全部|交通", t):
        return {"intent": "transport", "day": day, "keyword": mode,
                "params": {"mode": mode}, "source": "rule"}

    if re.search(r"重新规划|重做|重新生成|换个方案|推翻", t):
        return {"intent": "replan", "day": day, "keyword": "", "params": {}, "source": "rule"}

    if re.search(r"轻松|不累|慢一点|别太赶|悠闲|少一点|太满了|太紧|累", t):
        return {"intent": "relax", "day": day, "keyword": "", "params": {}, "source": "rule"}

    if re.search(r"预算|便宜|省钱|花太多|超预算|降低费用", t):
        return {"intent": "budget", "day": day, "keyword": "", "params": {}, "source": "rule"}

    spots, foods, hotels = _pools()

    if re.search(r"去掉|删除|不想去|别去|取消|挪掉|拿掉", t):
        kw = _match_keyword(t, spots + foods)
        if kw:
            return {"intent": "remove_spot", "day": day, "keyword": kw,
                    "params": {}, "source": "rule"}

    if re.search(r"换成|替换|改成|不去.*去|改为", t):
        return {"intent": "replace_spot", "day": day, "keyword": t,
                "params": {}, "source": "rule"}

    if re.search(r"加|增加|想去|安排|来顿|吃|加上|补上|再加", t):
        kw_food = _match_keyword(t, foods)
        kw_spot = _match_keyword(t, spots)
        if kw_food and re.search(r"吃|餐|饭|顿|火锅|早点|早餐", t):
            return {"intent": "add_meal", "day": day, "keyword": kw_food,
                    "params": {}, "source": "rule"}
        if kw_spot:
            return {"intent": "add_spot", "day": day, "keyword": kw_spot,
                    "params": {}, "source": "rule"}
        if kw_food:
            return {"intent": "add_meal", "day": day, "keyword": kw_food,
                    "params": {}, "source": "rule"}
        # 说了"加"但没匹配到具体名字：交给模型从整句里提炼
        return None

    return None


def _match_keyword(text: str, names: list[str]) -> str | None:
    """在句子里找已知名字（支持简称），返回标准名。

    中文没有空格，不能像英文那样按词切分。做法是从长到短滑窗：
    "把天津之眼去掉" 里逐段试，最终能命中"天津之眼"这个简称。
    （上一版用 re.findall(2-6字) 是非重叠匹配，遇到"把天津之眼去"整段
      吞掉就永远匹配不上，这是真实踩到的坑。）
    """
    text = text.strip()
    for n in names:
        core = re.sub(r"（.*?）", "", n)
        if core in text or n in text:
            return n

    max_len = min(8, len(text))
    for length in range(max_len, 1, -1):
        for i in range(len(text) - length + 1):
            r = resolve_name(text[i : i + length], names)
            if r:
                return r
    return None


def _llm_detect(text: str, itinerary_summary: str) -> dict:
    """规则认不出来时，交给模型。"""
    prompt = f"""用户想修改自己的天津行程，请判断他的意图。

当前行程概要：
{itinerary_summary}

用户说：{text}

可选意图：
  relax        想让某天轻松一点/少安排/别太赶
  add_meal     想加一顿饭/某种吃的
  add_spot     想加某个景点
  remove_spot  想去掉某个景点
  replace_spot 想把某个景点换成另一个
  hotel        想找住的地方
  transport    想改交通方式（walk/bike/metro/taxi/car）
  budget       想省钱/调预算
  replan       想整体重新规划
  query        只是问问题，不需要改行程

输出 JSON：
{{"intent":"意图代码","day":天数数字或null,"keyword":"关键词（如景点名、食物名、交通方式）","params":{{}}}}"""
    result = chat_json(prompt, temperature=0.1, max_tokens=300)
    if isinstance(result, dict):
        result.setdefault("day", None)
        result.setdefault("keyword", "")
        result.setdefault("params", {})
        result["source"] = "llm"
        if result.get("intent") not in INTENTS:
            result["intent"] = "query"
        return result
    return {"intent": "query", "day": None, "keyword": "", "params": {}, "source": "llm"}


def detect_intent(text: str, itinerary: dict | None = None) -> dict:
    """对外入口：先规则，后模型。"""
    hit = _rule_detect(text)
    if hit:
        return hit

    summary = ""
    if itinerary:
        summary = "\n".join(
            f"第{d.get('day')}天：{d.get('theme','')} —— "
            + "、".join(i.get("name", "") for i in d.get("items", []) if i.get("type") != "meal")
            for d in itinerary.get("days", [])
        )
    return _llm_detect(text, summary)
