# -*- coding: utf-8 -*-
"""
地理计算：没有高德/百度 key 也能算距离和通勤时间。

真实项目会调地图 API 拿路线，但那需要 key 且要联网限频。
这里用经纬度直算（Haversine 公式）+ 经验速度换算成时间，
对"判断景点之间远不远、会不会跨区折返"这个目的已经够用了，
而且离线、零成本、结果可复现 —— 面试时说清楚这个取舍反而是加分项。
"""
from __future__ import annotations

import math

# 行政区 → 大片区。判断是否"跨区折返"的关键依据。
ZONE_MAP = {
    "和平区": "市中心", "南开区": "市中心", "河北区": "市中心",
    "河西区": "市中心", "红桥区": "市中心", "河东区": "市中心",
    "滨海新区": "滨海", "东丽区": "东部", "西青区": "西南",
    "津南区": "西南", "北辰区": "北部", "蓟州区": "蓟州",
    "全市": "市中心",
}

# 各交通方式的经验时速（km/h），已包含等车、找路、停车等现实损耗
SPEED = {
    "walk": 4.5,
    "bike": 12.0,
    "metro": 22.0,   # 含进出站和换乘等待
    "taxi": 28.0,    # 市区含红灯
    "car": 30.0,
}

# 跨片区时速度打折系数（越远的路实际越快，但接驳耗时更多）
CROSS_ZONE_PENALTY = {
    ("市中心", "市中心"): 1.0,
    ("市中心", "滨海"): 0.75,
    ("市中心", "东部"): 0.9,
    ("市中心", "西南"): 0.85,
    ("市中心", "蓟州"): 0.7,
}


def zone_of(district: str) -> str:
    return ZONE_MAP.get(str(district).strip(), "市中心")


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """球面距离：把经纬度当成地球表面上的两个点算直线距离。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(
    a: dict, b: dict, mode: str = "metro", same_zone_speed: bool = True
) -> int:
    """两点之间的通勤分钟数（向上取整，含跨区惩罚）。"""
    km = haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
    za, zb = zone_of(a.get("district", "")), zone_of(b.get("district", ""))
    speed = SPEED.get(mode, SPEED["metro"])

    if za != zb:
        factor = CROSS_ZONE_PENALTY.get((za, zb), CROSS_ZONE_PENALTY.get((zb, za), 0.8))
        speed *= factor
        # 跨片区的固定接驳成本（进出城、换乘、找站）
        km += 3.0

    minutes = km / speed * 60
    if mode in ("metro", "taxi", "car"):
        minutes += 8  # 起步固定耗时：叫车/进站/等车
    return max(5, int(math.ceil(minutes)))


def is_far(a: dict, b: dict, threshold_km: float = 25.0) -> bool:
    return haversine_km(a["lat"], a["lng"], b["lat"], b["lng"]) > threshold_km
