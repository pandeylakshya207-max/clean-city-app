"""Photo storage. The mode is picked automatically:

- "blob":     Vercel Blob, when BLOB_READ_WRITE_TOKEN is set
- "database": inside the Postgres database, when the app runs on hosted Postgres
- "local":    the uploads folder, on a laptop
"""
import io
import os
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

from database import USE_POSTGRES, get_db

load_dotenv()

UPLOAD_DIR = Path(__file__).parent / "uploads"

if os.getenv("BLOB_READ_WRITE_TOKEN"):
    MODE = "blob"
elif USE_POSTGRES:
    MODE = "database"
else:
    MODE = "local"
USE_BLOB = MODE == "blob"


def save_image(img):
    """Save a photo as JPEG and return the reference to store with the report."""
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=85)
    data = buffer.getvalue()
    name = uuid.uuid4().hex + ".jpg"
    if MODE == "blob":
        from vercel.blob import BlobClient

        result = BlobClient().put("photos/" + name, data, access="public", content_type="image/jpeg")
        return result.url
    if MODE == "database":
        conn = get_db()
        try:
            conn.execute("INSERT INTO photos (name, data) VALUES (?, ?)", (name, data))
            conn.commit()
        finally:
            conn.close()
        return "db:" + name
    UPLOAD_DIR.mkdir(exist_ok=True)
    (UPLOAD_DIR / name).write_bytes(data)
    return name


def photo_url(stored):
    """The address a browser can load the photo from."""
    if not stored:
        return None
    if stored.startswith("http"):
        return stored
    if stored.startswith("db:"):
        return "/photos/" + stored[3:]
    return "/uploads/" + stored


def load_from_database(name):
    conn = get_db()
    try:
        row = conn.execute("SELECT data FROM photos WHERE name = ?", (name,)).fetchone()
    finally:
        conn.close()
    return bytes(row["data"]) if row else None


def read_bytes(stored):
    """The raw photo file, wherever it is stored."""
    if stored.startswith("http"):
        res = httpx.get(stored, timeout=20, follow_redirects=True)
        res.raise_for_status()
        return res.content
    if stored.startswith("db:"):
        return load_from_database(stored[3:])
    return (UPLOAD_DIR / stored).read_bytes()
