"""Garbage hotspots: finds the places that keep getting complaints and ranks them.

How it works
- Reports that lie within 200 m of each other are grouped with density-based clustering
  (DBSCAN with a grid index, so it stays fast with thousands of reports). A place needs at
  least 3 reports to count as a hotspot.
- Each hotspot gets a score. Recent reports count more: a report loses half its weight every
  30 days. A report that arrived after the spot had already been cleaned counts double,
  because it means people are dumping there again and again.
- For every hotspot it also measures how quickly garbage comes back after a cleaning, and
  estimates when it is likely to come back next.
"""
import math
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

import auth
from database import get_db

RADIUS_METRES = 200
MIN_REPORTS = 3
HALF_LIFE_DAYS = 30
REPEAT_WEIGHT = 2.0
RECENT_DAYS = 30
MAX_NAME_LOOKUPS = 8
EARTH_METRES = 6371000

router = APIRouter(prefix="/api/staff")
_name_lock = threading.Lock()
_last_lookup = [0.0]


def parse_time(value):
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def distance_metres(lat1, lon1, lat2, lon2):
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_METRES * math.asin(math.sqrt(min(1.0, a)))


def cluster(points, radius=RADIUS_METRES, min_points=MIN_REPORTS):
    """DBSCAN over (lat, lon) points. Returns a list of groups, each a list of point indexes."""
    if not points:
        return []
    lat_step = radius / 111320.0
    widest = max(abs(lat) for lat, _ in points)
    lon_step = radius / (111320.0 * max(math.cos(math.radians(widest)), 0.01))
    grid = {}
    cells = []
    for i, (lat, lon) in enumerate(points):
        key = (math.floor(lat / lat_step), math.floor(lon / lon_step))
        cells.append(key)
        grid.setdefault(key, []).append(i)

    def neighbours(i):
        lat, lon = points[i]
        cy, cx = cells[i]
        found = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for j in grid.get((cy + dy, cx + dx), ()):
                    if distance_metres(lat, lon, points[j][0], points[j][1]) <= radius:
                        found.append(j)
        return found

    labels = [None] * len(points)
    next_label = 0
    for i in range(len(points)):
        if labels[i] is not None:
            continue
        near = neighbours(i)
        if len(near) < min_points:
            labels[i] = -1
            continue
        labels[i] = next_label
        queue = [j for j in near if j != i]
        while queue:
            j = queue.pop()
            if labels[j] == -1:
                labels[j] = next_label
            if labels[j] is not None:
                continue
            labels[j] = next_label
            more = neighbours(j)
            if len(more) >= min_points:
                queue.extend(more)
        next_label += 1

    groups = {}
    for i, label in enumerate(labels):
        if label is not None and label >= 0:
            groups.setdefault(label, []).append(i)
    return list(groups.values())


def level_for(score, repeats):
    if score >= 6 or repeats >= 3:
        return "high"
    if score >= 3 or repeats >= 1:
        return "medium"
    return "low"


def analyse(reports, now, window_days):
    """reports: dicts with id, user_id, lat, lon, address, status, created (datetime), cleaned (datetime or None)."""
    since = now - timedelta(days=window_days)
    reports = sorted((r for r in reports if r["created"] and r["created"] >= since), key=lambda r: r["created"])
    for r in reports:
        age_days = max(0.0, (now - r["created"]).total_seconds() / 86400)
        r["weight"] = 0.5 ** (age_days / HALF_LIFE_DAYS)
        r["repeat"] = False

    groups = cluster([(r["lat"], r["lon"]) for r in reports])
    hotspots = []
    for members in groups:
        items = [reports[i] for i in members]
        items.sort(key=lambda r: r["created"])
        cleanings = sorted(r["cleaned"] for r in items if r["cleaned"])
        gaps = []
        for r in items:
            before = [c for c in cleanings if c < r["created"]]
            if before:
                r["repeat"] = True
                gaps.append((r["created"] - before[-1]).total_seconds() / 86400)
        for r in items:
            if r["repeat"]:
                r["weight"] *= REPEAT_WEIGHT
        repeats = sum(1 for r in items if r["repeat"])
        score = sum(r["weight"] for r in items)
        lat = sum(r["lat"] for r in items) / len(items)
        lon = sum(r["lon"] for r in items) / len(items)
        radius = max(distance_metres(lat, lon, r["lat"], r["lon"]) for r in items)
        open_reports = sum(1 for r in items if r["status"] in ("pending", "assigned"))
        avg_return = sum(gaps) / len(gaps) if gaps else None
        last_cleaned = cleanings[-1] if cleanings else None
        next_expected = None
        recently_cleaned = last_cleaned is not None and last_cleaned >= now - timedelta(days=RECENT_DAYS)
        if avg_return is not None and repeats >= 2 and open_reports == 0 and recently_cleaned:
            next_expected = last_cleaned + timedelta(days=avg_return)
        hotspots.append({
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "radius_m": round(max(radius, 30)),
            "reports": len(items),
            "recent_reports": sum(1 for r in items if r["created"] >= now - timedelta(days=RECENT_DAYS)),
            "open_reports": open_reports,
            "people": len({r["user_id"] for r in items}),
            "repeats": repeats,
            "avg_return_days": round(avg_return, 1) if avg_return is not None else None,
            "score": round(score, 2),
            "level": level_for(score, repeats),
            "first_report": items[0]["created"].isoformat(),
            "last_report": items[-1]["created"].isoformat(),
            "last_cleaned": last_cleaned.isoformat() if last_cleaned else None,
            "next_expected": next_expected.isoformat() if next_expected else None,
            "report_ids": [r["id"] for r in reversed(items)][:20],
            "sample_address": items[-1]["address"],
            "name": None,
        })
    hotspots.sort(key=lambda h: h["score"], reverse=True)
    for rank, h in enumerate(hotspots, start=1):
        h["rank"] = rank

    top = max((r["weight"] for r in reports), default=1.0)
    heat = [[round(r["lat"], 6), round(r["lon"], 6), round(r["weight"] / top, 3)] for r in reports]
    return {"window_days": window_days, "total_reports": len(reports), "heat": heat, "hotspots": hotspots}


def _ensure_name_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS area_names (cell TEXT PRIMARY KEY, name TEXT NOT NULL)")
    conn.commit()


def _lookup_area(lat, lon):
    """Neighbourhood name from OpenStreetMap, at most one request per second as its rules require."""
    import httpx

    with _name_lock:
        wait = 1.1 - (time.time() - _last_lookup[0])
        if wait > 0:
            time.sleep(wait)
        _last_lookup[0] = time.time()
        try:
            res = httpx.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16, "addressdetails": 1},
                headers={"User-Agent": "CleanCityApp/0.1 (student project)", "Accept-Language": "en"},
                timeout=6,
            )
            res.raise_for_status()
            parts = res.json().get("address", {})
        except Exception as exc:
            print("Area name lookup failed:", exc, flush=True)
            return None
    for key in ("neighbourhood", "suburb", "quarter", "residential", "hamlet", "village",
                "city_district", "town", "road", "city"):
        if parts.get(key):
            return parts[key]
    return None


def name_hotspots(hotspots):
    conn = get_db()
    try:
        _ensure_name_table(conn)
        lookups = 0
        for h in hotspots:
            cell = f"{h['latitude']:.3f},{h['longitude']:.3f}"
            row = conn.execute("SELECT name FROM area_names WHERE cell = ?", (cell,)).fetchone()
            if row:
                h["name"] = row["name"]
                continue
            if lookups < MAX_NAME_LOOKUPS:
                lookups += 1
                name = _lookup_area(h["latitude"], h["longitude"])
                if name:
                    h["name"] = name
                    conn.execute(
                        "INSERT INTO area_names (cell, name) VALUES (?, ?) ON CONFLICT (cell) DO NOTHING",
                        (cell, name),
                    )
                    conn.commit()
                    continue
            first = (h["sample_address"] or "").split(",")[0].strip()
            h["name"] = f"Near {first}" if first else f"Area at {h['latitude']:.4f}, {h['longitude']:.4f}"
    finally:
        conn.close()


def load_reports():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, user_id, latitude, longitude, address, status, created_at, cleaned_at"
            " FROM complaints WHERE status != 'rejected'"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "address": r["address"],
            "status": r["status"],
            "created": parse_time(r["created_at"]),
            "cleaned": parse_time(r["cleaned_at"]),
        }
        for r in rows
    ]


def build(window_days=90):
    window_days = max(7, min(int(window_days), 365))
    result = analyse(load_reports(), auth.now_utc(), window_days)
    name_hotspots(result["hotspots"])
    for h in result["hotspots"]:
        h.pop("sample_address", None)
    return result


@router.get("/hotspots")
def get_hotspots(days: int = 90, user=Depends(auth.require_staff)):
    return build(days)


if __name__ == "__main__":
    data = build(365)
    print(f"Reports analysed: {data['total_reports']} | hotspots found: {len(data['hotspots'])}")
    for h in data["hotspots"]:
        back = f", comes back after ~{h['avg_return_days']} days" if h["avg_return_days"] is not None else ""
        print(
            f"  #{h['rank']} {h['name']} [{h['level'].upper()}] score {h['score']}: "
            f"{h['reports']} reports from {h['people']} people, {h['repeats']} after cleaning{back}"
        )
