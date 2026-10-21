# -*- coding: utf-8 -*-
"""
LLM 客户端：整个项目唯一跟大模型打交道的地方。

两个能力，对应两个"器官"：
    chat()  —— 嘴/脑：让模型读提示词、产出文字（行程、意图判断、校验意见）
    embed() —— 耳朵：把一句话变成 1024 个数字（向量），用来做语义检索

为什么要封一层？
    1. 换厂商只改这一个文件（硅基流动 → DeepSeek → 本地 vLLM，业务代码不动）
    2. 统一重试和超时，某次网络抖动不会让整条链路崩掉
    3. embedding 加磁盘缓存，同一句话第二次不花钱不耗时
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """单例客户端：OpenAI SDK 直连 OpenAI 兼容接口（硅基流动就是兼容的）。"""
    global _client
    if _client is None:
        settings.validate()
        _client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=120.0,
            max_retries=0,  # 自己用 tenacity 控制，避免两层重试打架
        )
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def chat(
    prompt: str,
    system: str = "你是资深天津本地旅行规划师，回答准确、具体、不说空话。",
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """最简单的对话：一段系统提示 + 一段用户提示，返回模型说的话。"""
    resp = get_client().chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def chat_messages(messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048) -> str:
    """多轮对话版：自己传完整 messages（带历史上下文时用这个）。"""
    resp = get_client().chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str) -> dict | list:
    """从模型输出里抠出 JSON。

    模型经常不听话，外面包一层 ```json ... ```，或者前面加一句"好的，如下："。
    这里做三件事：去围栏 → 找第一个 { 或 [ 到最后一个 } 或 ] → json.loads
    """
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = min(
        [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1],
        default=-1,
    )
    if start == -1:
        raise ValueError(f"输出里找不到 JSON：{text[:200]}")
    # 从后往前找对应的收尾括号
    end = max(cleaned.rfind("}"), cleaned.rfind("]")) + 1
    return json.loads(cleaned[start:end])


def chat_json(
    prompt: str,
    system: str = "你是严谨的 JSON 生成助手，只输出 JSON，不要任何解释和 markdown 围栏。",
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> dict | list:
    """让模型返回结构化数据。

    温度特意压到 0.2：结构化输出要稳定，不要创造力。
    解析失败会自动再试一次（第二次把要求说得更狠）。
    """
    raw = chat(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
    try:
        return _extract_json(raw)
    except Exception:
        retry_prompt = (
            prompt
            + "\n\n【重要】上一次输出无法解析成 JSON。请只输出纯 JSON，"
            "不要用 ``` 围栏，不要写任何解释文字。"
        )
        raw = chat(retry_prompt, system=system, temperature=0.0, max_tokens=max_tokens)
        return _extract_json(raw)


# ------------------------- 向量化 -------------------------

def _load_cache() -> dict:
    path: Path = settings.embed_cache_path
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    settings.embed_cache_path.write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8"
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def _embed_batch(texts: list[str]) -> list[list[float]]:
    resp = get_client().embeddings.create(model=settings.embed_model, input=texts)
    return [item.embedding for item in resp.data]


def embed(texts: str | Iterable[str]) -> list[list[float]]:
    """把文字变成向量，带磁盘缓存（RAG 建库要跑几百条，缓存能省一大笔）。"""
    if isinstance(texts, str):
        texts = [texts]
    texts = list(texts)

    cache = _load_cache()
    results: list[list[float] | None] = []
    missing: list[tuple[int, str]] = []

    for i, t in enumerate(texts):
        key = hashlib.md5(t.encode("utf-8")).hexdigest()
        if key in cache:
            results.append(cache[key])
        else:
            results.append(None)
            missing.append((i, t))

    if missing:
        # 硅基流动单次最多 32 条，这里保守按 16 条一批切
        for start in range(0, len(missing), 16):
            chunk = missing[start : start + 16]
            vectors = _embed_batch([t for _, t in chunk])
            for (idx, text), vec in zip(chunk, vectors):
                cache[hashlib.md5(text.encode("utf-8")).hexdigest()] = vec
                results[idx] = vec
        _save_cache(cache)

    return [v for v in results if v is not None]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度：两个向量方向越像，值越接近 1。检索排序就靠它。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
