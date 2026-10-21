# -*- coding: utf-8 -*-
"""
混合检索器：关键词检索 + 向量检索，加权合并。

为什么要两路？
    - 关键词路：擅长"专有名词"。用户搜"天津之眼"，字面命中必须排第一。
      纯向量检索对这种专名反而容易飘到别的摩天轮上。
    - 向量路：擅长"模糊意图"。用户搜"带老人慢一点的地方"，
      字面上跟任何景点都不重合，只有语义相近才能捞出来。
    两路各补对方的短板，就是混合检索（hybrid search）。

关键词路为什么不装 jieba？
    中文分词要多一个依赖，而对几十条短文本，用"字符二元组（bigram）"
    覆盖率打分足够准：把查询切成两字一组，看有多少组出现在文档里。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import settings
from ..llm import cosine, embed
from .knowledge import build_document, load_knowledge

_INDEX_CACHE: list[dict] | None = None


# ------------------------- 建库 -------------------------

def build_index(force: bool = False) -> list[dict]:
    """把语料全部向量化，存到 data/vector_store.json。

    只在语料变更时需要重跑；embedding 有磁盘缓存，
    所以第二次跑几乎不花钱。
    """
    global _INDEX_CACHE
    path: Path = settings.vector_store_path

    records = load_knowledge()
    if not force and path.exists():
        store = json.loads(path.read_text(encoding="utf-8"))
        if store.get("count") == len(records):
            _INDEX_CACHE = store["items"]
            return _INDEX_CACHE

    docs = [build_document(r) for r in records]
    print(f"[RAG] 正在向量化 {len(docs)} 条语料 ...")
    vectors = embed(docs)

    items = []
    for rec, doc, vec in zip(records, docs, vectors):
        items.append(
            {"id": rec["id"], "doc": doc, "vec": vec, "meta": rec}
        )
    path.write_text(
        json.dumps({"count": len(items), "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[RAG] 建库完成，保存到 {path.name}")
    _INDEX_CACHE = items
    return items


def load_index() -> list[dict]:
    global _INDEX_CACHE
    if _INDEX_CACHE is None:
        if settings.vector_store_path.exists():
            _INDEX_CACHE = json.loads(
                settings.vector_store_path.read_text(encoding="utf-8")
            )["items"]
        else:
            _INDEX_CACHE = build_index()
    return _INDEX_CACHE


# ------------------------- 打分 -------------------------

_PUNCT = re.compile(r"[^\u4e00-\u9fa5a-zA-Z0-9]")


def _normalize(text: str) -> str:
    return _PUNCT.sub("", text.lower())


def _bigrams(text: str) -> set[str]:
    t = _normalize(text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def keyword_score(query: str, doc: str) -> float:
    """关键词覆盖度：查询切成的二字组，有多少比例出现在文档里。"""
    q_grams = _bigrams(query)
    if not q_grams:
        return 0.0
    d_grams = _bigrams(doc)
    hit = sum(1 for g in q_grams if g in d_grams)
    return hit / len(q_grams)


def _tag_boost(query: str, meta: dict) -> float:
    """标签命中加权：查询里出现"亲子""拍照"这类标签词时给额外分。"""
    q = _normalize(query)
    boost = 0.0
    for tag in meta.get("tags", []):
        if tag and tag in q:
            boost += 0.15
    name = str(meta.get("name", ""))
    if name and name in query:
        boost += 0.3  # 名字被完整点到，权重最高
    return min(boost, 0.5)


def retrieve(
    query: str,
    top_k: int | None = None,
    types: list[str] | None = None,
    district: str | None = None,
) -> list[dict]:
    """混合检索主入口。

    参数：
        query    用户的问题或兴趣描述
        top_k    返回条数
        types    只要某类：["spot"] / ["food"] / ["spot","tip"]
        district 限定行政区，例如"滨海新区"

    返回：[{"id","name","type","score","kw","vec","meta"}, ...] 按分数降序
    """
    top_k = top_k or settings.top_k
    items = load_index()
    if types:
        items = [i for i in items if i["meta"].get("type") in types]
    if district:
        items = [i for i in items if district in str(i["meta"].get("district", ""))]
    if not items:
        return []

    q_vec = embed([query])[0]
    scored = []
    for it in items:
        kw = keyword_score(query, it["doc"])
        vec = (cosine(q_vec, it["vec"]) + 1) / 2  # 从 [-1,1] 映射到 [0,1]
        base = settings.keyword_weight * kw + (1 - settings.keyword_weight) * vec
        score = base + _tag_boost(query, it["meta"])
        scored.append(
            {
                "id": it["meta"]["id"],
                "name": it["meta"].get("name", ""),
                "type": it["meta"].get("type", ""),
                "score": round(score, 4),
                "kw": round(kw, 3),
                "vec": round(vec, 3),
                "meta": it["meta"],
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def format_context(hits: list[dict], max_chars: int = 2200) -> str:
    """把检索结果拼成给模型看的"参考资料"文本块。

    max_chars 是硬约束：上下文太长模型会走神，也会更贵。
    """
    lines, total = [], 0
    for i, h in enumerate(hits, 1):
        m = h["meta"]
        block = (
            f"[{i}] {m.get('name','')}（{m.get('type','')}·{m.get('district','')}）\n"
            f"标签：{'、'.join(m.get('tags', []))}\n"
            f"开放：{m.get('open_hours','全天')}｜门票：{m.get('ticket','无')}\n"
            f"建议游玩：{m.get('stay_min','60')} 分钟\n"
            f"{m.get('text','')}"
        )
        if total + len(block) > max_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n\n".join(lines)
