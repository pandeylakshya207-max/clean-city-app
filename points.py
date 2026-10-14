"""Green Points for citizens.

    +10  a report that passes the photo checks
     +5  more when that spot is cleaned
     -5  a report the municipal team rejects
     -3  uploading a photo that was already reported

Points are worked out from the reports themselves, so they never drift out of step. The monthly
leaderboard counts only points earned in the current month (India time) and hides most of each
email address or phone number.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

import auth
from database import get_db

REPORT_POINTS = 10
CLEANED_POINTS = 5
REJECTED_POINTS = -5
DUPLICATE_POINTS = -3
LEADERBOARD_SIZE = 10
IST = timezone(timedelta(hours=5, minutes=30))

BADGES = [
    ("first", "First report", "Sent your first verified report", "reports", 1),
    ("regular", "Regular", "5 verified reports", "reports", 5),
    ("guardian", "Street Guardian", "10 of your reports were cleaned", "cleaned", 10),
    ("hero", "City Hero", "25 of your reports were cleaned", "cleaned", 25),
]

router = APIRouter(prefix="/api/points")


def parse(value):
    from hotspots import parse_time

    return parse_time(value)


def month_start():
    now = auth.now_utc().astimezone(IST)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def mask(contact):
    if "@" in contact:
        name, domain = contact.split("@", 1)
        return name[:2] + "***@" + domain
    digits = contact.replace("+91", "")
    return "+91 " + digits[:2] + "•" * 5 + digits[-3:]


def tally(rows, penalties, since=None):
    """Points per user id. rows: complaints; penalties: penalty rows. since: only count events after it."""
    totals = {}
    stats = {}

    def add(uid, value):
        totals[uid] = totals.get(uid, 0) + value

    for r in rows:
        uid = r["user_id"]
        s = stats.setdefault(uid, {"reports": 0, "cleaned": 0, "rejected": 0})
        created = parse(r["created_at"])
        counted = since is None or (created is not None and created >= since)
        if r["status"] == "rejected":
            s["rejected"] += 1
            if counted:
                add(uid, REJECTED_POINTS)
            continue
        s["reports"] += 1
        if counted:
            add(uid, REPORT_POINTS)
        if r["status"] == "cleaned":
            s["cleaned"] += 1
            cleaned = parse(r["cleaned_at"])
            if since is None or (cleaned is not None and cleaned >= since):
                add(uid, CLEANED_POINTS)
    for p in penalties:
        when = parse(p["created_at"])
        if since is None or (when is not None and when >= since):
            add(p["user_id"], p["points"])
    for uid in stats:
        totals.setdefault(uid, 0)
    return {uid: max(0, v) for uid, v in totals.items()}, stats


def load(conn):
    rows = conn.execute(
        "SELECT c.user_id, c.status, c.created_at, c.cleaned_at FROM complaints c"
        " JOIN users u ON u.id = c.user_id WHERE u.role != 'staff'"
    ).fetchall()
    penalties = conn.execute("SELECT user_id, points, created_at FROM penalties").fetchall()
    return rows, penalties


def leaderboard(conn, limit=LEADERBOARD_SIZE):
    rows, penalties = load(conn)
    monthly, _ = tally(rows, penalties, since=month_start())
    ranked = sorted(((v, uid) for uid, v in monthly.items() if v > 0), reverse=True)
    contacts = {}
    if ranked:
        ids = [uid for _, uid in ranked[:limit]]
        marks = ",".join("?" for _ in ids)
        for r in conn.execute(f"SELECT id, contact FROM users WHERE id IN ({marks})", ids).fetchall():
            contacts[r["id"]] = r["contact"]
    board = [
        {"rank": i + 1, "name": mask(contacts.get(uid, "?")), "points": v, "user_id": uid}
        for i, (v, uid) in enumerate(ranked[:limit])
    ]
    return board, ranked


def add_penalty(user_id, points, reason):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO penalties (user_id, points, reason, created_at) VALUES (?, ?, ?, ?)",
            (user_id, points, reason, auth.now_utc().isoformat()),
        )
        conn.commit()
    except Exception as exc:
        print("Could not record penalty:", exc, flush=True)
    finally:
        conn.close()


@router.get("/me")
def my_points(user=Depends(auth.get_current_user)):
    conn = get_db()
    try:
        rows, penalties = load(conn)
        total, stats = tally(rows, penalties)
        board, ranked = leaderboard(conn)
    finally:
        conn.close()
    uid = user["id"]
    mine = stats.get(uid, {"reports": 0, "cleaned": 0, "rejected": 0})
    month_points = next((v for v, u in ranked if u == uid), 0)
    month_rank = next((i + 1 for i, (_, u) in enumerate(ranked) if u == uid), None)
    badges = [
        {"key": key, "name": name, "detail": detail, "earned": mine[field] >= need}
        for key, name, detail, field, need in BADGES
    ]
    upcoming = next((b for b in BADGES if mine[b[3]] < b[4]), None)
    next_badge = None
    if upcoming:
        next_badge = {"name": upcoming[1], "detail": upcoming[2], "have": mine[upcoming[3]], "need": upcoming[4]}
    for row in board:
        row["you"] = row.pop("user_id") == uid
    return {
        "points": total.get(uid, 0),
        "month_points": month_points,
        "month_rank": month_rank,
        "reports": mine["reports"],
        "cleaned": mine["cleaned"],
        "badges": badges,
        "next_badge": next_badge,
        "leaderboard": board,
        "rules": {
            "report": REPORT_POINTS,
            "cleaned": CLEANED_POINTS,
            "rejected": REJECTED_POINTS,
            "duplicate": DUPLICATE_POINTS,
        },
    }
