import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import checks
import complaints
import staff
import storage
from database import USE_POSTGRES, init_db, set_role

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

DATABASE_ERROR = None
try:
    init_db()
except Exception as exc:
    DATABASE_ERROR = f"{type(exc).__name__}: {exc}"
    print("Database setup failed:", DATABASE_ERROR, flush=True)

def register_staff_from_settings():
    """Staff are registered in advance from the STAFF_CONTACTS setting (comma-separated emails or numbers)."""
    for item in os.getenv("STAFF_CONTACTS", "").split(","):
        if not item.strip():
            continue
        try:
            contact, contact_type = auth.normalize_contact(item)
            set_role(contact, contact_type, "staff")
        except Exception as exc:
            print("Could not register staff contact", item.strip(), "-", exc, flush=True)


if DATABASE_ERROR is None:
    register_staff_from_settings()

app = FastAPI(title="Clean City")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if storage.MODE == "local":
    storage.UPLOAD_DIR.mkdir(exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=storage.UPLOAD_DIR), name="uploads")
app.include_router(complaints.router)
app.include_router(staff.router)


class OtpRequest(BaseModel):
    contact: str


class OtpVerify(BaseModel):
    contact: str
    code: str


@app.post("/api/auth/request-otp")
def request_otp(body: OtpRequest):
    return auth.request_otp(body.contact)


@app.post("/api/auth/verify-otp")
def verify_otp(body: OtpVerify):
    return auth.verify_otp(body.contact, body.code)


@app.get("/api/me")
def me(user=Depends(auth.get_current_user)):
    return {"id": user["id"], "contact": user["contact"], "role": user["role"]}


@app.get("/api/health")
def health():
    """Shows how the app is set up, to help when deploying. Contains no secrets."""
    return {
        "database": "postgres" if USE_POSTGRES else "sqlite",
        "database_error": DATABASE_ERROR,
        "photo_storage": storage.MODE,
        "email_configured": bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")),
        "model_files_mb": checks.models_status(),
    }


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/.well-known/assetlinks.json")
def asset_links():
    path = STATIC_DIR / "assetlinks.json"
    if not path.exists():
        raise HTTPException(404, "Not set up yet.")
    return FileResponse(path, media_type="application/json")


@app.get("/photos/{name}")
def photo(name: str):
    data = storage.load_from_database(name)
    if data is None:
        raise HTTPException(404, "Photo not found.")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def page(name):
    path = STATIC_DIR / name
    if not path.exists():
        raise HTTPException(404, "This page is not built yet.")
    return FileResponse(path)


@app.get("/")
def home():
    return page("login.html")


@app.get("/report")
def report_page():
    return page("report.html")


@app.get("/dashboard")
def dashboard_page():
    return page("dashboard.html")



