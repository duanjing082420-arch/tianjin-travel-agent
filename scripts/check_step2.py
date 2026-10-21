# -*- coding: utf-8 -*-
"""
第 2 步验收脚本：RAG 知识库 + 混合检索。

运行：
    .venv\\Scripts\\python.exe scripts\\check_step2.py
"""
import sys, io, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag.knowledge import load_knowledge
from app.rag.retriever import retrieve, format_context, build_index

print("=" * 60)
print("第 2 步：RAG 知识库验收")
print("=" * 60)

records = load_knowledge()
types = {}
for r in records:
    types[r["type"]] = types.get(r["type"], 0) + 1
print(f"语料总数：{len(records)} 条  →  {types}")
print()

build_index()
print()

# --- 用例 1：专有名词，考验关键词路 ---
print("[用例1] 专名查询：'天津之眼 门票多少钱'")
for h in retrieve("天津之眼 门票多少钱", top_k=3):
    print(f"  {h['name']:<12} 总分{h['score']:.3f}  关键词{h['kw']:.2f}  向量{h['vec']:.2f}")
print()

# --- 用例 2：模糊意图，考验向量路 ---
print("[用例2] 模糊意图：'带老人孩子走得慢一点，不要太累'")
for h in retrieve("带老人孩子走得慢一点，不要太累", top_k=4):
    print(f"  {h['name']:<12} 总分{h['score']:.3f}  关键词{h['kw']:.2f}  向量{h['vec']:.2f}")
print()

# --- 用例 3：只查吃的 ---
print("[用例3] 类型过滤：'天津特色早餐' 只要 food 类")
for h in retrieve("天津特色早餐", top_k=4, types=["food"]):
    print(f"  {h['name']:<12} 总分{h['score']:.3f}")
print()

# --- 用例 4：区域过滤 ---
print("[用例4] 区域过滤：'值得去的地方' 限定滨海新区")
for h in retrieve("值得去的地方", top_k=4, district="滨海新区"):
    print(f"  {h['name']:<12} 总分{h['score']:.3f}")
print()

# --- 用例 5：拼给模型看的上下文 ---
print("[用例5] 拼装给模型的参考资料（前 400 字预览）")
ctx = format_context(retrieve("五大道 拍照 咖啡", top_k=3))
print(ctx[:400])
print("...")

print()
print("=" * 60)
print("第 2 步通过。下一步：用 LangGraph 把这些材料编成行程。")
print("=" * 60)
