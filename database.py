"""Database access. Uses hosted Postgres when DATABASE_URL is set (for example on Vercel), otherwise a local SQLite file."""
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent / "cleancity.db"
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
USE_POSTGRES = DATABASE_URL.startswith("postgres")

EXTRA_COMPLAINT_COLUMNS = {
    "embedding": "BLOB",
    "fake_score": "REAL",
    "assigned_to": "TEXT",
    "staff_note": "TEXT",
}

SQLITE_SCHEMA = """
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
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    contact TEXT UNIQUE NOT NULL,
    contact_type TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'citizen',
    created_at TEXT NOT NULL DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS otps (
    id SERIAL PRIMARY KEY,
    contact TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS complaints (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    photo_path TEXT NOT NULL,
    photo_hash TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    address TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    ai_score DOUBLE PRECISION,
    after_photo_path TEXT,
    created_at TEXT NOT NULL DEFAULT (now()::text),
    cleaned_at TEXT,
    embedding BYTEA,
    fake_score DOUBLE PRECISION,
    assigned_to TEXT,
    staff_note TEXT
)
"""


class Row(dict):
    """A Postgres row that can be read by column name or by position, like a SQLite row."""

    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []

    def make_row(values):
        return Row(names, values)

    return make_row


class PostgresConnection:
    """Gives a Postgres connection the same small interface the app uses with SQLite."""

    def __init__(self):
        import psycopg

        self._conn = psycopg.connect(DATABASE_URL, row_factory=_row_factory, prepare_threshold=None)

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    if USE_POSTGRES:
        return PostgresConnection()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    if USE_POSTGRES:
        for statement in POSTGRES_SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
    else:
        conn.executescript(SQLITE_SCHEMA)
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
    print("Database:", "hosted Postgres" if USE_POSTGRES else f"local SQLite file ({DB_PATH.name})")
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
        print("Database ready.")
