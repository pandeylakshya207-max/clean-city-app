"""Public transparency page data: no login needed, and no names, emails, numbers or addresses.

Every ward gets a cleaning score from 0 to 100:
    score = 100 x (0.6 x share of reports cleaned within 48 hours + 0.4 x share of reports cleaned)
A ward needs at least 3 reports in the period to get a score and a grade (A 85+, B 70+, C 50+, D).
"""
import statistics
import threading
import time
from datetime import timedelta

from fastapi import APIRouter

import auth
import garbage_types
import points
import sla
import storage
import wards
from complaints import type_of
from database import get_db

MIN_WARD_REPORTS = 3
CACHE_SECONDS = 60
RECENT_CLEANINGS = 6

router = APIRouter(prefix="/api/public")
_cache = {}
_cache_lock = threading.Lock()


def grade(score):
    if score is None:
        return None
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 50 else "D"


def hours_between(a, b):
    a, b = sla.parse(a), sla.parse(b)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds() / 3600)


def build(days):
    since = (auth.now_utc() - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM complaints WHERE status != 'rejected' AND created_at >= ? ORDER BY id DESC", (since,)
        ).fetchall()
        board, _ = points.leaderboard(conn)
    finally:
        conn.close()

    by_ward = {}
    type_counts = {}
    open_points = []
    clean_hours = []
    on_time = 0
    verified = 0
    recent = []
    for r in rows:
        ward = wards.ward_dict(r["latitude"], r["longitude"])
        key = (ward["no"], ward["name"]) if ward else (0, "Outside BBMP wards")
        w = by_ward.setdefault(key, {"reports": 0, "cleaned": 0, "on_time": 0, "open": 0, "overdue": 0, "hours": []})
        w["reports"] += 1
        kind = type_of(r)
        if kind:
            type_counts[kind["key"]] = type_counts.get(kind["key"], 0) + 1
        info = sla.deadline_info(r)
        if r["status"] == "cleaned":
            w["cleaned"] += 1
            hours = hours_between(r["reopened_at"] or r["created_at"], r["cleaned_at"])
            if hours is not None:
                w["hours"].append(hours)
                clean_hours.append(hours)
            if info.get("on_time"):
                w["on_time"] += 1
                on_time += 1
            if r["clean_check"] == "verified":
                verified += 1
            if r["after_photo_path"] and r["clean_check"] != "flagged" and len(recent) < RECENT_CLEANINGS:
                recent.append({
                    "id": r["id"],
                    "photo_url": storage.photo_url(r["photo_path"]),
                    "after_photo_url": storage.photo_url(r["after_photo_path"]),
                    "ward": key[1],
                    "cleaned_at": r["cleaned_at"],
                    "hours": round(hours, 1) if hours is not None else None,
                    "verified": r["clean_check"] == "verified",
                    "garbage_type": kind["name"] if kind else None,
                })
        else:
            w["open"] += 1
            if info["overdue"]:
                w["overdue"] += 1
            open_points.append([round(r["latitude"], 5), round(r["longitude"], 5), 1 if info["overdue"] else 0])

    ward_list = []
    for (no, name), w in by_ward.items():
        score = None
        if no and w["reports"] >= MIN_WARD_REPORTS:
            score = round(100 * (0.6 * w["on_time"] / w["reports"] + 0.4 * w["cleaned"] / w["reports"]))
        ward_list.append({
            "no": no,
            "name": name,
            "reports": w["reports"],
            "cleaned": w["cleaned"],
            "open": w["open"],
            "overdue": w["overdue"],
            "on_time": w["on_time"],
            "median_hours": round(statistics.median(w["hours"]), 1) if w["hours"] else None,
            "score": score,
            "grade": grade(score),
        })
    ward_list.sort(key=lambda w: (w["score"] is None, -(w["score"] or 0), -w["reports"]))
    rank = 0
    for w in ward_list:
        if w["score"] is not None:
            rank += 1
            w["rank"] = rank
        else:
            w["rank"] = None

    for row in board:
        row.pop("user_id", None)
    total = len(rows)
    cleaned = sum(w["cleaned"] for w in by_ward.values())
    return {
        "days": days,
        "updated_at": auth.now_utc().isoformat(),
        "totals": {
            "reports": total,
            "cleaned": cleaned,
            "open": total - cleaned,
            "overdue": sum(w["overdue"] for w in by_ward.values()),
            "on_time_pct": round(100 * on_time / total) if total else None,
            "median_hours": round(statistics.median(clean_hours), 1) if clean_hours else None,
            "verified": verified,
            "wards_active": sum(1 for (no, _) in by_ward if no),
        },
        "deadline_hours": sla.DEADLINE_HOURS,
        "wards": ward_list,
        "types": sorted(
            ({"key": k, "name": garbage_types.info(k)["name"], "count": n} for k, n in type_counts.items()),
            key=lambda t: -t["count"],
        ),
        "open_points": open_points,
        "recent": recent,
        "leaderboard": board,
    }


@router.get("/stats")
def public_stats(days: int = 90):
    days = 30 if days <= 30 else 90 if days <= 90 else 365
    with _cache_lock:
        hit = _cache.get(days)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = build(days)
    with _cache_lock:
        _cache[days] = (time.time(), data)
    return data
