import hashlib
import hmac
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database import get_db

load_dotenv()

BASE_DIR = Path(__file__).parent
KEY_FILE = BASE_DIR / "secret.key"
if not KEY_FILE.exists():
    KEY_FILE.write_text(secrets.token_hex(32))
SECRET_KEY = KEY_FILE.read_text().strip()

OTP_VALID_MINUTES = 5
OTP_MAX_ATTEMPTS = 5
OTP_MAX_REQUESTS = 3
TOKEN_VALID_DAYS = 7

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def now_utc():
    return datetime.now(timezone.utc)


def normalize_contact(raw):
    value = raw.strip().lower()
    if EMAIL_RE.match(value):
        return value, "email"
    digits = re.sub(r"[\s\-()]", "", value)
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if PHONE_RE.match(digits):
        return "+91" + digits, "phone"
    raise HTTPException(400, "Enter a valid email address or 10-digit mobile number.")


def hash_code(contact, code):
    data = f"{contact}:{code}".encode()
    return hmac.new(SECRET_KEY.encode(), data, hashlib.sha256).hexdigest()


def send_otp(contact, contact_type, code):
    text = f"Your Clean City login code is {code}. It is valid for {OTP_VALID_MINUTES} minutes."
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    if contact_type == "email" and smtp_user and smtp_pass:
        msg = EmailMessage()
        msg["Subject"] = "Your Clean City login code"
        msg["From"] = smtp_user
        msg["To"] = contact
        msg.set_content(text)
        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        with smtplib.SMTP_SSL(host, 465, timeout=15) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return "sent"
    # Development mode: no email/SMS service set up yet, so show the code in the terminal.
    print(f"\n[DEV MODE] OTP for {contact}: {code}\n", flush=True)
    return "dev"


def request_otp(raw_contact):
    contact, contact_type = normalize_contact(raw_contact)
    conn = get_db()
    try:
        window_start = (now_utc() - timedelta(minutes=10)).isoformat()
        recent = conn.execute(
            "SELECT COUNT(*) FROM otps WHERE contact = ? AND created_at > ?",
            (contact, window_start),
        ).fetchone()[0]
        if recent >= OTP_MAX_REQUESTS:
            raise HTTPException(429, "Too many codes requested. Please wait 10 minutes and try again.")
        code = f"{secrets.randbelow(1000000):06d}"
        conn.execute("UPDATE otps SET used = 1 WHERE contact = ? AND used = 0", (contact,))
        conn.execute(
            "INSERT INTO otps (contact, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (
                contact,
                hash_code(contact, code),
                (now_utc() + timedelta(minutes=OTP_VALID_MINUTES)).isoformat(),
                now_utc().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        delivery = send_otp(contact, contact_type, code)
    except Exception as exc:
        print("Could not send OTP:", exc, flush=True)
        raise HTTPException(502, "Could not send the code right now. Please try again.")
    return {"contact": contact, "contact_type": contact_type, "delivery": delivery}


def verify_otp(raw_contact, code):
    contact, contact_type = normalize_contact(raw_contact)
    code = code.strip()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM otps WHERE contact = ? AND used = 0 ORDER BY id DESC LIMIT 1",
            (contact,),
        ).fetchone()
        if row is None or row["expires_at"] < now_utc().isoformat():
            raise HTTPException(400, "This code has expired. Please request a new one.")
        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            raise HTTPException(400, "Too many wrong attempts. Please request a new code.")
        if not hmac.compare_digest(row["code_hash"], hash_code(contact, code)):
            conn.execute("UPDATE otps SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            conn.commit()
            raise HTTPException(400, "Incorrect code. Please try again.")
        conn.execute("UPDATE otps SET used = 1 WHERE id = ?", (row["id"],))
        user = conn.execute("SELECT * FROM users WHERE contact = ?", (contact,)).fetchone()
        if user is None:
            conn.execute(
                "INSERT INTO users (contact, contact_type) VALUES (?, ?)",
                (contact, contact_type),
            )
            user = conn.execute("SELECT * FROM users WHERE contact = ?", (contact,)).fetchone()
        conn.commit()
    finally:
        conn.close()
    token = jwt.encode(
        {
            "sub": str(user["id"]),
            "role": user["role"],
            "exp": now_utc() + timedelta(days=TOKEN_VALID_DAYS),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    return {
        "token": token,
        "user": {"id": user["id"], "contact": user["contact"], "role": user["role"]},
    }


bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if creds is None:
        raise HTTPException(401, "Please log in.")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Your session has expired. Please log in again.")
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(payload["sub"]),)
        ).fetchone()
    finally:
        conn.close()
    if user is None:
        raise HTTPException(401, "Account not found. Please log in again.")
    return dict(user)


def require_staff(user=Depends(get_current_user)):
    if user["role"] != "staff":
        raise HTTPException(403, "Only municipal staff can do this.")
    return user
