# -*- coding: utf-8 -*-
"""
第 3 步验收：LangGraph 行程生成全链路。

运行：
    .venv\\Scripts\\python.exe scripts\\check_step3.py
"""
import sys, io, os, json, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.graph.trip_graph import plan_trip

REQUEST = {
    "city": "天津",
    "days": 3,
    "start_date": "2026-10-24",   # 周六 → 周日 → 周一（第三天正好撞上闭馆日）
    "people": 2,
    "budget": 3000,
    "transport": "metro",
    "interests": ["美食", "拍照", "历史建筑"],
    "with_who": "情侣",
    "pace": "适中",
}

print("=" * 70)
print("第 3 步：LangGraph 行程生成链路验收")
print("=" * 70)
print("需求:", json.dumps(REQUEST, ensure_ascii=False))
print()

t0 = time.time()
itinerary = plan_trip(REQUEST)
cost = time.time() - t0

print(f"生成耗时：{cost:.1f} 秒    返工轮次：{itinerary.get('revision', 0)}")
print()

budget = itinerary.get("budget_estimate", {})
print(f"预算估算：门票 {budget.get('ticket')} + 餐饮 {budget.get('food')} "
      f"+ 交通 {budget.get('transport')} = 总计 {budget.get('total')} 元")
print()

for day in itinerary.get("days", []):
    print(f"—— 第 {day.get('day')} 天  {day.get('date')} {day.get('weekday')} ——")
    print(f"   主题：{day.get('theme','')}")
    for it in day.get("items", []):
        if it.get("type") == "meal":
            print(f"   🍜 {it.get('start')}-{it.get('end')}  {it.get('name')}")
        else:
            print(f"   📍 {it.get('start')}-{it.get('end')}  {it.get('name')}"
                  f"  [{it.get('district','')}]  停留 {it.get('stay_min')} 分  票：{it.get('ticket','')}")
    if day.get("travel_notes"):
        for n in day["travel_notes"]:
            print(f"     通勤 {n}")
    print()

print("—— 校验问题 ——")
if itinerary.get("issues"):
    for i in itinerary["issues"]:
        print(f"   [{i['level']}] {i['reason']}")
else:
    print("   无（全部通过）")
print()

print("—— 路线优化结果 ——")
for day_no, info in itinerary.get("route_info", {}).items():
    print(f"   第{day_no}天顺序：{' → '.join(info.get('order', []))}")
    print(f"        片区：{'/'.join(info.get('zones', []))}   通勤合计：{info.get('total_travel_min')} 分钟")
print()

print("—— 用户提醒 ——")
for w in itinerary.get("warnings", []) or ["无"]:
    print("   ", w)

print()
print("=" * 70)
print("第 3 步完成。下一步：多轮对话调整（意图识别 + 局部重规划）。")
print("=" * 70)
