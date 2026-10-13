"""Adds or removes demo reports so the hotspot map has something to show on a laptop.

    python demo_data.py add       add about 26 demo reports around Bengaluru
    python demo_data.py remove    delete every demo report again

Demo reports belong to the account demo-data@cleancity.local and say "[demo data]" in their note.
This script only works on the local database file, never on the live online database.
"""
import random
import sys
from datetime import datetime, timedelta, timezone

from database import USE_POSTGRES, get_db, init_db

DEMO_CONTACT = "demo-data@cleancity.local"

# (area, latitude, longitude, how many reports, days ago of the first one, days between reports, cleaned?)
SCENARIOS = [
    ("Koramangala 5th Block", 12.93520, 77.62450, 9, 58, 6, True),
    ("Jayanagar 4th Block", 12.92500, 77.58380, 6, 45, 8, True),
    ("BTM Layout 2nd Stage", 12.91660, 77.61010, 4, 20, 5, False),
    ("Indiranagar 100 Feet Road", 12.97190, 77.64120, 3, 85, 3, True),
    ("Whitefield", 12.96980, 77.75000, 1, 10, 0, False),
    ("Malleshwaram", 13.00350, 77.57090, 1, 30, 0, True),
    ("HSR Layout", 12.91160, 77.64730, 2, 15, 9, False),
]


def demo_user_id(conn, create):
    row = conn.execute("SELECT id FROM users WHERE contact = ?", (DEMO_CONTACT,)).fetchone()
    if row or not create:
        return row["id"] if row else None
    conn.execute(
        "INSERT INTO users (contact, contact_type, role) VALUES (?, 'email', 'citizen')", (DEMO_CONTACT,)
    )
    return conn.execute("SELECT id FROM users WHERE contact = ?", (DEMO_CONTACT,)).fetchone()["id"]


def remove(conn):
    uid = demo_user_id(conn, create=False)
    if uid is None:
        return 0
    count = conn.execute("SELECT COUNT(*) FROM complaints WHERE user_id = ?", (uid,)).fetchone()[0]
    conn.execute("DELETE FROM complaints WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    return count


def add(conn):
    remove(conn)
    uid = demo_user_id(conn, create=True)
    photo = conn.execute("SELECT photo_path FROM complaints ORDER BY id DESC LIMIT 1").fetchone()
    photo_path = photo["photo_path"] if photo else "demo.jpg"
    rng = random.Random(42)
    now = datetime.now(timezone.utc)
    added = 0
    for area, lat, lon, count, first_days_ago, gap_days, cleaned in SCENARIOS:
        for n in range(count):
            created = now - timedelta(days=first_days_ago - n * gap_days, hours=rng.randint(0, 10))
            is_last = n == count - 1
            done = cleaned or not is_last
            cleaned_at = created + timedelta(hours=rng.randint(10, 30)) if done and gap_days else None
            if cleaned and not gap_days:
                cleaned_at = created + timedelta(hours=20)
            if cleaned_at and cleaned_at > now:
                cleaned_at = None
            conn.execute(
                "INSERT INTO complaints (user_id, photo_path, latitude, longitude, address, description, status,"
                " ai_score, created_at, cleaned_at, assigned_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    photo_path,
                    lat + rng.uniform(-0.0005, 0.0005),
                    lon + rng.uniform(-0.0005, 0.0005),
                    f"{area}, Bengaluru, Karnataka, India",
                    "[demo data]",
                    "cleaned" if cleaned_at else "pending",
                    1.0,
                    created.isoformat(),
                    cleaned_at.isoformat() if cleaned_at else None,
                    "Demo team" if cleaned_at else None,
                ),
            )
            added += 1
    conn.commit()
    return added


if __name__ == "__main__":
    if USE_POSTGRES:
        print("This script only runs on the local database, not the live one.")
        sys.exit(1)
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in ("add", "remove"):
        print("Use: python demo_data.py add   or   python demo_data.py remove")
        sys.exit(1)
    init_db()
    conn = get_db()
    try:
        if action == "add":
            print(f"Added {add(conn)} demo reports.")
        else:
            print(f"Removed {remove(conn)} demo reports.")
    finally:
        conn.close()
