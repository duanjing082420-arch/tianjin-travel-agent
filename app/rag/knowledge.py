# -*- coding: utf-8 -*-
"""
知识语料读取层。

一条语料长这样（data/tianjin_knowledge.jsonl 里的每一行）：
    {"id":"spot-wudadao","type":"spot","name":"五大道","district":"和平区",
     "lat":39.1156,"lng":117.2012,"tags":[...],"open_hours":"...",
     "stay_min":150,"ticket":"街区免费","budget":"low","text":"..."}

设计要点：
    - type 分 spot / food / tip 三类，检索时可以只查某一类
    - lat/lng 是后面"路线优化"算距离用的，没有它就没法判断跨区折返
    - text 是给模型看的自然语言描述，也是被向量化的主体
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import settings

KNOWLEDGE_PATH: Path = settings.data_dir / "tianjin_knowledge.jsonl"


def load_knowledge(path: Path | None = None) -> list[dict]:
    """读语料，返回字典列表。

    默认扫 data/ 目录下所有 .jsonl —— 按类型拆文件更好维护
    （景点美食一个文件、酒店一个文件），加新语料只要丢个新文件进去。
    """
    files = [path] if path else sorted(settings.data_dir.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"没找到任何 .jsonl 语料：{settings.data_dir}")

    records = []
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_document(rec: dict) -> str:
    """把一条结构化语料拼成"待向量化文本"。

    为什么不只向量化 text？
        因为"五大道"这个名字、"和平区"这个位置、"拍照"这些标签，
        都是强信号。拼进去后，用户搜"和平区哪里能拍照"也能命中。
    """
    parts = [
        f"名称：{rec.get('name', '')}",
        f"类型：{rec.get('type', '')}",
        f"所在区：{rec.get('district', '')}",
        f"标签：{'、'.join(rec.get('tags', []))}",
    ]
    if rec.get("open_hours"):
        parts.append(f"开放时间：{rec['open_hours']}")
    if rec.get("ticket"):
        parts.append(f"门票：{rec['ticket']}")
    if rec.get("stay_min"):
        parts.append(f"建议游玩：{rec['stay_min']} 分钟")
    parts.append(f"介绍：{rec.get('text', '')}")
    return "\n".join(parts)


# 别名表：模型（和用户）爱用简称，"意风区""天博""煎饼果子"这种叫法很常见。
# 直接判成幻觉太浪费 —— 先归一到标准名，再决定要不要报错。
ALIASES = {
    "意风区": "意大利风情区", "意式风情区": "意大利风情区", "意大利风情区": "意大利风情区",
    "天津之眼": "天津之眼摩天轮", "摩天轮": "天津之眼摩天轮",
    "古文化街": "古文化街（津门故里）", "津门故里": "古文化街（津门故里）",
    "天博": "天津博物馆", "市博物馆": "天津博物馆",
    "海洋博物馆": "国家海洋博物馆", "海博": "国家海洋博物馆",
    "滨海图书馆": "滨海新区图书馆", "滨海新区图书馆": "滨海新区图书馆",
    "航母公园": "泰达航母主题公园", "航母": "泰达航母主题公园",
    "周邓纪念馆": "周恩来邓颖超纪念馆", "周邓馆": "周恩来邓颖超纪念馆",
    "美术馆": "天津美术馆", "海河": "海河游船", "游船": "海河游船",
    "津湾": "津湾广场", "民园": "民园体育场", "先农": "先农大院",
    "梁启超故居": "梁启超故居（饮冰室）", "饮冰室": "梁启超故居（饮冰室）",
    "觉悟社": "觉悟社纪念馆", "平津战役": "平津战役纪念馆", "平津馆": "平津战役纪念馆",
    "大悲院": "大悲禅院", "水上": "水上公园", "天大": "天津大学",
    "黄崖关": "黄崖关长城", "杨柳青": "杨柳青古镇", "小站": "小站练兵园",
    "霍元甲": "霍元甲故居纪念馆", "欢乐谷": "天津欢乐谷", "方特": "天津方特欢乐世界",
    "东疆": "东疆湾沙滩景区", "东疆湾": "东疆湾沙滩景区", "北塘": "北塘古镇",
    "大沽口": "大沽口炮台遗址", "南市": "南市食品街", "食品街": "南市食品街",
    "滨江道": "滨江道商业街", "鼓楼商业街": "鼓楼",
    "狗不理": "狗不理包子", "耳朵眼": "耳朵眼炸糕", "十八街": "十八街麻花",
    "煎饼果子": "煎饼馃子", "煎饼": "煎饼馃子", "馃子": "煎饼馃子",
    "锅巴菜": "锅巴菜（嘎巴菜）", "嘎巴菜": "锅巴菜（嘎巴菜）",
    "麻花": "十八街麻花", "炸糕": "耳朵眼炸糕", "捞面": "天津捞面（四碟八码）",
    "火锅": "铜锅涮肉", "涮肉": "铜锅涮肉", "铜锅": "铜锅涮肉",
    "海鲜": "北塘海鲜", "老爆三": "老爆三", "罾蹦鲤鱼": "罾蹦鲤鱼",
}


def resolve_name(name: str, pool_names: list[str]) -> str | None:
    """把模型/用户叫的简称归一到知识库标准名。

    三级匹配：
      1. 完全一致
      2. 别名表
      3. 包含关系（"天津之眼" ⊂ "天津之眼摩天轮"，取最长的那个）

    返回 None 表示库里真没这个东西 —— 那才是幻觉。
    """
    name = str(name).strip()
    if not name:
        return None
    if name in pool_names:
        return name
    if name in ALIASES and ALIASES[name] in pool_names:
        return ALIASES[name]
    # 包含关系：优先取最长的匹配，避免"鼓楼"匹配到"鼓楼商业街"这种短名
    candidates = [p for p in pool_names if name in p or p in name]
    if candidates:
        return max(candidates, key=len)
    return None


def get_by_ids(ids: list[str]) -> list[dict]:
    """按 id 反查原始语料（行程生成后用来补全坐标、票价等字段）。"""
    id_set = set(ids)
    return [r for r in load_knowledge() if r["id"] in id_set]


def get_by_name(name: str) -> dict | None:
    """按名称精确/模糊查一条语料。"""
    for r in load_knowledge():
        if r.get("name") == name or name in str(r.get("name", "")):
            return r
    return None
