import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "cleancity.db"

EXTRA_COMPLAINT_COLUMNS = {
    "embedding": "BLOB",
    "fake_score": "REAL",
    "assigned_to": "TEXT",
    "staff_note": "TEXT",
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact TEXT UNIQUE NOT NULL,
        contact_type TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'citizen',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS otps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        photo_path TEXT NOT NULL,
        photo_hash TEXT,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        address TEXT,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        ai_score REAL,
        after_photo_path TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        cleaned_at TEXT
    );
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(complaints)")}
    for name, kind in EXTRA_COMPLAINT_COLUMNS.items():
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE complaints ADD COLUMN {name} {kind}")
            except sqlite3.OperationalError:
                pass
    conn.commit()
    conn.close()


def set_role(contact, contact_type, role):
    """Give an email or number a role. If it has never logged in, register it in advance."""
    conn = get_db()
    cur = conn.execute("UPDATE users SET role = ? WHERE contact = ?", (role, contact))
    if cur.rowcount == 0:
        conn.execute(
            "INSERT INTO users (contact, contact_type, role) VALUES (?, ?, ?)",
            (contact, contact_type, role),
        )
        result = "registered in advance"
    else:
        result = "existing account updated"
    conn.commit()
    conn.close()
    return result


def list_staff():
    conn = get_db()
    rows = conn.execute("SELECT contact FROM users WHERE role = 'staff' ORDER BY id").fetchall()
    conn.close()
    return [row["contact"] for row in rows]


if __name__ == "__main__":
    init_db()
    args = sys.argv[1:]
    if len(args) == 2 and args[0] in ("staff", "citizen"):
        from auth import normalize_contact

        try:
            contact, contact_type = normalize_contact(args[1])
        except Exception:
            print("That is not a valid email address or 10-digit mobile number.")
            sys.exit(1)
        result = set_role(contact, contact_type, args[0])
        print(f"{contact} is now {args[0]} ({result}).")
    elif args == ["staff"]:
        staff = list_staff()
        print("Municipal staff accounts:")
        for contact in staff:
            print("  " + contact)
        if not staff:
            print("  (none yet)")
    else:
        conn = get_db()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(complaints)")]
        conn.close()
        print("Database ready. Complaint columns:", ", ".join(cols))
