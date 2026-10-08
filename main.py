import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import checks
import complaints
import staff
import storage
from database import USE_POSTGRES, init_db

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

DATABASE_ERROR = None
try:
    init_db()
except Exception as exc:
    DATABASE_ERROR = f"{type(exc).__name__}: {exc}"
    print("Database setup failed:", DATABASE_ERROR, flush=True)

app = FastAPI(title="Clean City")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if not storage.USE_BLOB:
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
        "photo_storage": "vercel-blob" if storage.USE_BLOB else "local-folder",
        "email_configured": bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")),
        "model_files_mb": checks.models_status(),
    }


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
