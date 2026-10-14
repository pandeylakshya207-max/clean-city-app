import io
import math
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel

import auth
import checks
import garbage_types
import points
import sla
import storage
import wards
from database import get_db

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MIN_SIDE_PIXELS = 400
MAX_SIDE_PIXELS = 1600
DAILY_LIMIT = 50
SAME_SPOT_METRES = 300
MAX_PIN_MOVE_METRES = 250
OPEN_STATUSES = "('pending', 'assigned')"
OFFLINE_MAX_DAYS = 7
REOPEN_DAYS = 7

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


def type_of(row):
    """The garbage type of a report. Older reports get it from their stored scene vector."""
    key = row["garbage_type"]
    confidence = row["type_confidence"]
    if key is None and row["embedding"] is not None:
        key, confidence = checks.garbage_type(checks.from_bytes(row["embedding"]))
    if key is None:
        return None
    return dict(garbage_types.info(key), confidence=confidence)


def can_reopen(row):
    if row["status"] != "cleaned" or row["reopened_at"]:
        return False
    cleaned = sla.parse(row["cleaned_at"])
    return cleaned is not None and auth.now_utc() - cleaned <= timedelta(days=REOPEN_DAYS)


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
        "garbage_type": type_of(row),
        "ward": wards.ward_dict(row["latitude"], row["longitude"]),
        "escalated": row["escalated_at"] is not None,
        "clean_check": row["clean_check"],
        "reopened": row["reopened_at"] is not None,
        "can_reopen": can_reopen(row),
        **sla.deadline_info(row),
    }


def report_time(taken_at):
    """A report saved offline keeps the time the photo was taken, if it is believable."""
    now = auth.now_utc()
    if taken_at:
        t = sla.parse(taken_at)
        if t is not None and now - timedelta(days=OFFLINE_MAX_DAYS) <= t <= now + timedelta(minutes=5):
            return min(t, now)
    return now


@router.post("")
def create_complaint(
    photo: UploadFile = File(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    description: str = Form(""),
    gps_latitude: float | None = Form(None),
    gps_longitude: float | None = Form(None),
    taken_at: str = Form(""),
    user=Depends(auth.get_current_user),
):
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(400, "The location is not valid. Please turn on GPS and try again.")
    if gps_latitude is not None and gps_longitude is not None:
        if distance_metres(gps_latitude, gps_longitude, latitude, longitude) > MAX_PIN_MOVE_METRES:
            raise HTTPException(400, "The pin is too far from your GPS position. Move it within 200 m and try again.")
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
        if result.get("duplicate"):
            points.add_penalty(user["id"], points.DUPLICATE_POINTS, "Uploaded a photo that was already reported")
            raise HTTPException(400, result["reason"] + f" ({points.DUPLICATE_POINTS} Green Points)")
        raise HTTPException(400, result["reason"])

    stored = storage.save_image(img)
    address = find_address(latitude, longitude)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO complaints (user_id, photo_path, photo_hash, ai_score, fake_score, embedding,"
            " latitude, longitude, address, description, created_at, garbage_type, type_confidence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
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
                report_time(taken_at).isoformat(),
                result.get("garbage_type"),
                result.get("type_confidence"),
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
    sla.escalate_overdue()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM complaints WHERE user_id = ? ORDER BY id DESC", (user["id"],)
        ).fetchall()
    finally:
        conn.close()
    return [to_dict(r) for r in rows]


class ReopenBody(BaseModel):
    reason: str = ""


@router.post("/{complaint_id}/reopen")
def reopen(complaint_id: int, body: ReopenBody, user=Depends(auth.get_current_user)):
    """The citizen says the spot was not really cleaned. Allowed once, within 7 days of cleaning."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if row is None or row["user_id"] != user["id"]:
            raise HTTPException(404, "Report not found.")
        if not can_reopen(row):
            raise HTTPException(400, f"This report can only be reopened once, within {REOPEN_DAYS} days of cleaning.")
        reason = body.reason.strip()[:300] or "The spot is still not clean."
        conn.execute(
            "UPDATE complaints SET status = 'pending', reopened_at = ?, after_photo_path = NULL, cleaned_at = NULL,"
            " clean_check = NULL, after_similarity = NULL, after_distance_m = NULL, escalated_at = NULL,"
            " staff_note = ? WHERE id = ?",
            (auth.now_utc().isoformat(), "Reopened by the citizen: " + reason, complaint_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    finally:
        conn.close()
    return to_dict(row)
