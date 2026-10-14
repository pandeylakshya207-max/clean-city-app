"""Cleaning deadline: every report should be cleaned within 48 hours.

When a report passes its deadline it is escalated once: the senior officers in the
ESCALATION_CONTACTS setting (comma-separated emails) get one email listing the overdue reports,
and each citizen is told that their report was escalated. If ESCALATION_CONTACTS is not set,
the email goes to every staff account that has an email address.

There is no background job: the check runs at most once every 10 minutes, whenever somebody
opens the app.
"""
import os
from datetime import timedelta

import auth
from database import get_db

DEADLINE_HOURS = 48
CHECK_EVERY_MINUTES = 10
MAX_PER_RUN = 20
OPEN = ("pending", "assigned")


def parse(value):
    from hotspots import parse_time

    return parse_time(value)


def deadline_info(row):
    """Deadline facts for one report, ready to send to the app."""
    created = parse(row["reopened_at"] or row["created_at"])
    if created is None:
        return {"deadline": None, "hours_left": None, "overdue": False}
    deadline = created + timedelta(hours=DEADLINE_HOURS)
    if row["status"] in OPEN:
        left = (deadline - auth.now_utc()).total_seconds() / 3600
        return {"deadline": deadline.isoformat(), "hours_left": round(left, 1), "overdue": left < 0}
    cleaned = parse(row["cleaned_at"])
    on_time = cleaned is not None and cleaned <= deadline
    return {"deadline": deadline.isoformat(), "hours_left": None, "overdue": False, "on_time": on_time}


def _recipients(conn):
    listed = [c.strip().lower() for c in os.getenv("ESCALATION_CONTACTS", "").split(",") if "@" in c]
    if listed:
        return listed
    rows = conn.execute(
        "SELECT contact FROM users WHERE role = 'staff' AND contact_type = 'email'"
    ).fetchall()
    return [r["contact"] for r in rows]


def _due_for_check(conn):
    now = auth.now_utc()
    row = conn.execute("SELECT value FROM app_state WHERE key = 'last_escalation_check'").fetchone()
    last = parse(row["value"]) if row else None
    if last is not None and now - last < timedelta(minutes=CHECK_EVERY_MINUTES):
        return False
    if row:
        conn.execute("UPDATE app_state SET value = ? WHERE key = 'last_escalation_check'", (now.isoformat(),))
    else:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('last_escalation_check', ?)", (now.isoformat(),)
        )
    conn.commit()
    return True


def escalate_overdue(force=False):
    """Escalate reports that passed their deadline. Never raises; returns how many were escalated."""
    try:
        return _escalate(force)
    except Exception as exc:
        print("Escalation check failed:", exc, flush=True)
        return 0


def _escalate(force):
    import staff

    conn = get_db()
    try:
        if not force and not _due_for_check(conn):
            return 0
        cutoff = (auth.now_utc() - timedelta(hours=DEADLINE_HOURS)).isoformat()
        rows = conn.execute(
            "SELECT c.*, u.contact, u.contact_type FROM complaints c JOIN users u ON u.id = c.user_id"
            " WHERE c.status IN ('pending', 'assigned') AND c.escalated_at IS NULL"
            " AND COALESCE(c.reopened_at, c.created_at) < ?"
            " ORDER BY c.id LIMIT ?",
            (cutoff, MAX_PER_RUN),
        ).fetchall()
        if not rows:
            return 0
        now = auth.now_utc().isoformat()
        for r in rows:
            conn.execute("UPDATE complaints SET escalated_at = ? WHERE id = ?", (now, r["id"]))
        conn.commit()
        recipients = _recipients(conn)
    finally:
        conn.close()

    lines = []
    for r in rows:
        late = (auth.now_utc() - parse(r["reopened_at"] or r["created_at"])).total_seconds() / 3600 - DEADLINE_HOURS
        place = r["address"] or f"{r['latitude']:.5f}, {r['longitude']:.5f}"
        team = f", assigned to {r['assigned_to']}" if r["assigned_to"] else ", not assigned yet"
        lines.append(
            f"- Report #{r['id']}: {place}{team}. Overdue by {late:.0f} hours.\n"
            f"  Map: https://www.google.com/maps?q={r['latitude']},{r['longitude']}"
        )
    text = (
        f"These garbage reports were not cleaned within {DEADLINE_HOURS} hours:\n\n"
        + "\n".join(lines)
        + "\n\nPlease make sure a team is sent. You can see them in the Clean City dashboard."
    )
    subject = f"Clean City: {len(rows)} report(s) overdue"
    for to in recipients:
        try:
            staff.send_email(to, subject, text)
        except Exception as exc:
            print("Could not send escalation email to", to, "-", exc, flush=True)
    for r in rows:
        staff.notify(dict(r), "escalated")
    print(f"Escalated {len(rows)} overdue report(s) to {len(recipients)} officer(s).", flush=True)
    return len(rows)
