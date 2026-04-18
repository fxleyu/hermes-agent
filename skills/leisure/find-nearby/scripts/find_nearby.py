#!/usr/bin/env python3
"""使用 OpenStreetMap（Overpass + Nominatim）查找附近地点。无需 API 密钥。

用法:
    # 通过坐标查找
    python find_nearby.py --lat 36.17 --lon -115.14 --type restaurant --radius 1500

    # 通过地址/城市/邮编查找（自动地理编码）
    python find_nearby.py --near "Times Square, New York" --type cafe --radius 1000
    python find_nearby.py --near "90210" --type pharmacy

    # 多种类型
    python find_nearby.py --lat 36.17 --lon -115.14 --type restaurant --type bar

    # JSON 输出，便于程序化使用
    python find_nearby.py --near "downtown las vegas" --type restaurant --json
"""

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from typing import Any

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "HermesAgent/1.0 (find-nearby skill)"
TIMEOUT = 15


def _http_get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _http_post(url: str, data: str) -> Any:
    req = urllib.request.Request(
        url, data=data.encode(), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两个坐标之间的距离（单位：米）。"""
    R = 6_371_000
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode(query: str) -> tuple[float, float]:
    """通过 Nominatim 将地址/城市/邮编转换为坐标。"""
    params = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    results = _http_get(f"{NOMINATIM_URL}?{params}")
    if not results:
        print(f"Error: Could not geocode '{query}'. Try a more specific address.", file=sys.stderr)
        sys.exit(1)
    return float(results[0]["lat"]), float(results[0]["lon"])


def find_nearby(lat: float, lon: float, types: list[str], radius: int = 1500, limit: int = 15) -> list[dict]:
    """通过 Overpass 查询附近的设施。"""
    # 构建 Overpass QL 查询语句
    type_filters = "".join(
        f'nwr["amenity"="{t}"](around:{radius},{lat},{lon});' for t in types
    )
    query = f"[out:json][timeout:{TIMEOUT}];({type_filters});out center tags;"

    # 尝试每个 Overpass 服务器
    data = None
    for url in OVERPASS_URLS:
        try:
            data = _http_post(url, f"data={urllib.parse.quote(query)}")
            break
        except Exception:
            continue

    if not data:
        return []

    # 解析结果
    places = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        # 获取坐标（节点直接有 lat/lon，路径/关系使用 center）
        plat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
        plon = el.get("lon") or (el.get("center", {}) or {}).get("lon")
        if plat is None or plon is None:
            continue

        dist = haversine(lat, lon, plat, plon)

        place = {
            "name": name,
            "type": tags.get("amenity", ""),
            "distance_m": round(dist),
            "lat": plat,
            "lon": plon,
            "maps_url": f"https://www.google.com/maps/search/?api=1&query={plat},{plon}",
            "directions_url": f"https://www.google.com/maps/dir/?api=1&origin={lat},{lon}&destination={plat},{plon}",
        }

        # 添加有用的可选字段
        if tags.get("cuisine"):
            place["cuisine"] = tags["cuisine"]
        if tags.get("opening_hours"):
            place["hours"] = tags["opening_hours"]
        if tags.get("phone"):
            place["phone"] = tags["phone"]
        if tags.get("website"):
            place["website"] = tags["website"]
        if tags.get("addr:street"):
            addr_parts = [tags.get("addr:housenumber", ""), tags.get("addr:street", "")]
            if tags.get("addr:city"):
                addr_parts.append(tags["addr:city"])
            place["address"] = " ".join(p for p in addr_parts if p)

        places.append(place)

    # 按距离排序，限制结果数量
    places.sort(key=lambda p: p["distance_m"])
    return places[:limit]


def main():
    parser = argparse.ArgumentParser(description="Find nearby places via OpenStreetMap")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--near", type=str, help="Address, city, or zip code (geocoded automatically)")
    parser.add_argument("--type", action="append", dest="types", default=[], help="Place type (restaurant, cafe, bar, pharmacy, etc.)")
    parser.add_argument("--radius", type=int, default=1500, help="Search radius in meters (default: 1500)")
    parser.add_argument("--limit", type=int, default=15, help="Max results (default: 15)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    args = parser.parse_args()

    # 解析坐标
    if args.near:
        lat, lon = geocode(args.near)
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        print("Error: Provide --lat/--lon or --near", file=sys.stderr)
        sys.exit(1)

    if not args.types:
        args.types = ["restaurant"]

    places = find_nearby(lat, lon, args.types, args.radius, args.limit)

    if args.json_output:
        print(json.dumps({"origin": {"lat": lat, "lon": lon}, "results": places, "count": len(places)}, indent=2))
    else:
        if not places:
            print(f"No {'/'.join(args.types)} found within {args.radius}m")
            return
        print(f"Found {len(places)} places within {args.radius}m:\n")
        for i, p in enumerate(places, 1):
            dist_str = f"{p['distance_m']}m" if p["distance_m"] < 1000 else f"{p['distance_m']/1000:.1f}km"
            print(f"  {i}. {p['name']} ({p['type']}) — {dist_str}")
            if p.get("cuisine"):
                print(f"     Cuisine: {p['cuisine']}")
            if p.get("hours"):
                print(f"     Hours: {p['hours']}")
            if p.get("address"):
                print(f"     Address: {p['address']}")
            print(f"     Map: {p['maps_url']}")
            print()


if __name__ == "__main__":
    main()
