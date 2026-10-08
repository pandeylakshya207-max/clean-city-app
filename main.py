from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import complaints
import staff
from database import init_db

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

init_db()

app = FastAPI(title="Clean City")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
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

