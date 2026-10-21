# -*- coding: utf-8 -*-
"""
第 1 步验收脚本：确认"嘴"和"耳朵"都通了。

运行：
    .venv\\Scripts\\python.exe scripts\\check_step1.py
"""
import sys, io, os

# Windows 管道输出默认 GBK，打印中文/emoji 会直接崩，这里强制 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.llm import chat, chat_json, embed, cosine

print("=" * 60)
print("第 1 步：LLM 客户端连通性验收")
print("=" * 60)
print(f"对话模型: {settings.chat_model}")
print(f"向量模型: {settings.embed_model}")
print()

# --- 1. chat：能不能说出天津相关的靠谱内容 ---
print("[1/4] chat() 对话能力")
ans = chat("用两句话说明：第一次来天津，为什么值得去五大道？", max_tokens=150)
print("  回答:", ans[:120])
assert len(ans) > 10, "chat 返回为空"
print("  通过")
print()

# --- 2. chat_json：能不能吐出可被程序解析的结构 ---
print("[2/4] chat_json() 结构化输出能力")
data = chat_json(
    "请给出天津 3 个必吃小吃，输出 JSON："
    '{"foods":[{"name":"名称","desc":"一句话描述"}]}'
)
print("  解析结果:", data)
assert isinstance(data, dict) and len(data.get("foods", [])) == 3, "JSON 结构不对"
print("  通过")
print()

# --- 3. embed：文字能不能变成向量 ---
print("[3/4] embed() 向量化能力")
vecs = embed(["五大道洋楼建筑群", "煎饼馃子早餐", "滨海新区图书馆"])
print(f"  向量条数: {len(vecs)}, 维度: {len(vecs[0])}")
assert len(vecs) == 3 and len(vecs[0]) > 100, "向量维度异常"
print("  通过")
print()

# --- 4. cosine：语义相近的句子是否得分更高 ---
print("[4/4] cosine() 语义相似度")
sim_food = cosine(vecs[1], embed(["天津特色早点"])[0])
sim_bldg = cosine(vecs[0], embed(["天津特色早点"])[0])
print(f"  '煎饼馃子早餐' vs '天津特色早点' = {sim_food:.3f}")
print(f"  '五大道洋楼建筑群' vs '天津特色早点' = {sim_bldg:.3f}")
assert sim_food > sim_bldg, "语义相似度排序反了，向量检索会失效"
print("  通过（语义检索的基础成立）")

print()
print("=" * 60)
print("第 1 步全部通过。地基打好了，下一步做 RAG 知识库。")
print("=" * 60)
