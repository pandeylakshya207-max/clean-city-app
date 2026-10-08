"""Photo storage. Uses Vercel Blob when it is configured (on Vercel), otherwise the local uploads folder."""
import io
import os
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

UPLOAD_DIR = Path(__file__).parent / "uploads"
USE_BLOB = bool(os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("BLOB_STORE_ID"))


def save_image(img):
    """Save a photo as JPEG. Returns what to store in the database: a file name locally, or a full URL online."""
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=85)
    data = buffer.getvalue()
    name = uuid.uuid4().hex + ".jpg"
    if USE_BLOB:
        from vercel.blob import BlobClient

        result = BlobClient().put("photos/" + name, data, access="public", content_type="image/jpeg")
        return result.url
    UPLOAD_DIR.mkdir(exist_ok=True)
    (UPLOAD_DIR / name).write_bytes(data)
    return name


def photo_url(stored):
    """The address a browser can load the photo from."""
    if not stored:
        return None
    if stored.startswith("http"):
        return stored
    return "/uploads/" + stored


def read_bytes(stored):
    """The raw photo file, wherever it is stored."""
    if stored.startswith("http"):
        res = httpx.get(stored, timeout=20, follow_redirects=True)
        res.raise_for_status()
        return res.content
    return (UPLOAD_DIR / stored).read_bytes()
