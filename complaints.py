import io
import math
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps

import auth
import checks
import storage
from database import get_db

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MIN_SIDE_PIXELS = 400
MAX_SIDE_PIXELS = 1600
DAILY_LIMIT = 50
SAME_SPOT_METRES = 300
OPEN_STATUSES = "('pending', 'assigned')"

router = APIRouter(prefix="/api/complaints")


def load_photo(data):
    if len(data) == 0:
        raise HTTPException(400, "The photo is empty. Please take it again.")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "The photo is too large (limit is 8 MB).")
    try:
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(400, "This file is not a valid photo.")
    if min(img.size) < MIN_SIDE_PIXELS:
        raise HTTPException(400, "The photo is too small to use. Please take a clearer one.")
    img.thumbnail((MAX_SIDE_PIXELS, MAX_SIDE_PIXELS))
    return img


def find_address(lat, lon):
    try:
        res = httpx.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
            headers={"User-Agent": "CleanCityApp/0.1 (student project)", "Accept-Language": "en"},
            timeout=8,
        )
        res.raise_for_status()
        return res.json().get("display_name")
    except Exception as exc:
        print("Address lookup failed:", exc, flush=True)
        return None


def distance_metres(lat1, lon1, lat2, lon2):
    radius = 6371000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def fill_missing_vectors(conn):
    """Older open reports were saved before scene vectors existed, so compute them once."""
    rows = conn.execute(
        "SELECT id, photo_path FROM complaints WHERE embedding IS NULL AND status IN " + OPEN_STATUSES
    ).fetchall()
    changed = False
    for row in rows:
        try:
            img = Image.open(io.BytesIO(storage.read_bytes(row["photo_path"]))).convert("RGB")
        except Exception:
            continue
        _, _, vec = checks.analyze(img)
        conn.execute(
            "UPDATE complaints SET embedding = ? WHERE id = ?", (checks.to_bytes(vec), row["id"])
        )
        changed = True
    if changed:
        conn.commit()


def to_dict(row):
    return {
        "id": row["id"],
        "photo_url": storage.photo_url(row["photo_path"]),
        "after_photo_url": storage.photo_url(row["after_photo_path"]),
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "address": row["address"],
        "description": row["description"],
        "status": row["status"],
        "staff_note": row["staff_note"],
        "created_at": row["created_at"],
        "cleaned_at": row["cleaned_at"],
    }


@router.post("")
def create_complaint(
    photo: UploadFile = File(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    description: str = Form(""),
    user=Depends(auth.get_current_user),
):
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(400, "The location is not valid. Please turn on GPS and try again.")
    img = load_photo(photo.file.read())

    conn = get_db()
    try:
        since = (auth.now_utc() - timedelta(hours=24)).isoformat()
        sent_today = conn.execute(
            "SELECT COUNT(*) FROM complaints WHERE user_id = ? AND created_at > ?",
            (user["id"], since),
        ).fetchone()[0]
        if sent_today >= DAILY_LIMIT:
            raise HTTPException(
                429, f"You have reached the limit of {DAILY_LIMIT} reports in 24 hours. Please try again later."
            )
        hashes = [
            r[0]
            for r in conn.execute(
                "SELECT photo_hash FROM complaints WHERE photo_hash IS NOT NULL AND status != 'rejected'"
            ).fetchall()
        ]
        fill_missing_vectors(conn)
        nearby = []
        open_rows = conn.execute(
            "SELECT id, latitude, longitude, embedding FROM complaints"
            " WHERE embedding IS NOT NULL AND status IN " + OPEN_STATUSES
        ).fetchall()
        for r in open_rows:
            if distance_metres(latitude, longitude, r["latitude"], r["longitude"]) <= SAME_SPOT_METRES:
                nearby.append((r["id"], checks.from_bytes(r["embedding"])))
    finally:
        conn.close()

    result = checks.check_photo(img, hashes, nearby)
    if not result["ok"]:
        raise HTTPException(400, result["reason"])

    stored = storage.save_image(img)
    address = find_address(latitude, longitude)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO complaints (user_id, photo_path, photo_hash, ai_score, fake_score, embedding,"
            " latitude, longitude, address, description, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                user["id"],
                stored,
                result["hash"],
                result["score"],
                result.get("fake_score"),
                result["embedding"],
                latitude,
                longitude,
                address,
                description.strip()[:500],
                auth.now_utc().isoformat(),
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (new_id,)).fetchone()
    finally:
        conn.close()
    return to_dict(row)


@router.get("/mine")
def my_complaints(user=Depends(auth.get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM complaints WHERE user_id = ? ORDER BY id DESC", (user["id"],)
        ).fetchall()
    finally:
        conn.close()
    return [to_dict(r) for r in rows]
