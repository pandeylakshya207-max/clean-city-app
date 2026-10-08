import os
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import auth
import checks
import storage
from complaints import load_photo
from database import get_db

FAKE_FLAG_SCORE = 0.50
OPEN = ("pending", "assigned")

router = APIRouter(prefix="/api/staff")


class AssignBody(BaseModel):
    team: str


class RejectBody(BaseModel):
    reason: str


def staff_dict(row):
    fake = row["fake_score"]
    return {
        "id": row["id"],
        "photo_url": storage.photo_url(row["photo_path"]),
        "after_photo_url": storage.photo_url(row["after_photo_path"]),
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "address": row["address"],
        "description": row["description"],
        "status": row["status"],
        "assigned_to": row["assigned_to"],
        "staff_note": row["staff_note"],
        "reporter": row["contact"],
        "garbage_score": row["ai_score"],
        "fake_score": fake,
        "possible_fake": fake is not None and fake >= FAKE_FLAG_SCORE,
        "created_at": row["created_at"],
        "cleaned_at": row["cleaned_at"],
    }


def get_complaint(conn, complaint_id):
    row = conn.execute(
        "SELECT c.*, u.contact, u.contact_type FROM complaints c"
        " JOIN users u ON u.id = c.user_id WHERE c.id = ?",
        (complaint_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Report not found.")
    return row


def send_email(to, subject, text, attachment=None):
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    if not (smtp_user and smtp_pass):
        print(f"\n[DEV MODE] Email to {to}: {subject}\n{text}\n", flush=True)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to
    msg.set_content(text)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="image", subtype="jpeg", filename="after-cleaning.jpg")
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(host, 465, timeout=20) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


def notify(info, event):
    """Tell the person who complained what happened to their report. Never raises."""
    try:
        place = info["address"] or f"{info['latitude']:.5f}, {info['longitude']:.5f}"
        number = info["id"]
        attachment = None
        if event == "cleaned":
            subject = f"Clean City: your report #{number} has been cleaned"
            text = (
                f"Good news. The garbage you reported at {place} has been cleaned.\n\n"
                "The photo taken after cleaning is attached. You can also see it in the app under My reports.\n\n"
                "Thank you for helping keep the city clean."
            )
        elif event == "assigned":
            subject = f"Clean City: a team is on the way for report #{number}"
            text = (
                f"Your report #{number} at {place} has been assigned to: {info['assigned_to']}.\n\n"
                "You will get another message with a photo once the spot is cleaned."
            )
        else:
            subject = f"Clean City: your report #{number} was not accepted"
            text = (
                f"The municipal team reviewed your report #{number} at {place} and did not accept it.\n\n"
                f"Reason: {info['staff_note']}"
            )
        if info["contact_type"] != "email":
            print(f"\n[DEV MODE] SMS to {info['contact']}: {subject}\n", flush=True)
            return
        if event == "cleaned" and info["after_photo_path"]:
            attachment = storage.read_bytes(info["after_photo_path"])
        send_email(info["contact"], subject, text, attachment)
    except Exception as exc:
        print("Could not send notification:", exc, flush=True)


@router.get("/complaints")
def list_complaints(status: str = "", user=Depends(auth.require_staff)):
    conn = get_db()
    try:
        sql = "SELECT c.*, u.contact, u.contact_type FROM complaints c JOIN users u ON u.id = c.user_id"
        params = ()
        if status:
            sql += " WHERE c.status = ?"
            params = (status,)
        rows = conn.execute(sql + " ORDER BY c.id DESC", params).fetchall()
        counts = {
            r[0]: r[1]
            for r in conn.execute("SELECT status, COUNT(*) FROM complaints GROUP BY status").fetchall()
        }
    finally:
        conn.close()
    return {"counts": counts, "complaints": [staff_dict(r) for r in rows]}


@router.post("/complaints/{complaint_id}/assign")
def assign(complaint_id: int, body: AssignBody, user=Depends(auth.require_staff)):
    team = body.team.strip()[:100]
    if not team:
        raise HTTPException(400, "Enter the name of the team or worker.")
    conn = get_db()
    try:
        row = get_complaint(conn, complaint_id)
        if row["status"] not in OPEN:
            raise HTTPException(400, "This report is already closed.")
        conn.execute(
            "UPDATE complaints SET status = 'assigned', assigned_to = ? WHERE id = ?",
            (team, complaint_id),
        )
        conn.commit()
        row = get_complaint(conn, complaint_id)
    finally:
        conn.close()
    notify(dict(row), "assigned")
    return staff_dict(row)


@router.post("/complaints/{complaint_id}/reject")
def reject(complaint_id: int, body: RejectBody, user=Depends(auth.require_staff)):
    reason = body.reason.strip()[:300]
    if not reason:
        raise HTTPException(400, "Enter the reason for rejecting this report.")
    conn = get_db()
    try:
        row = get_complaint(conn, complaint_id)
        if row["status"] not in OPEN:
            raise HTTPException(400, "This report is already closed.")
        conn.execute(
            "UPDATE complaints SET status = 'rejected', staff_note = ? WHERE id = ?",
            (reason, complaint_id),
        )
        conn.commit()
        row = get_complaint(conn, complaint_id)
    finally:
        conn.close()
    notify(dict(row), "rejected")
    return staff_dict(row)


@router.post("/complaints/{complaint_id}/clean")
def mark_cleaned(
    complaint_id: int,
    photo: UploadFile = File(...),
    confirm: bool = Form(False),
    user=Depends(auth.require_staff),
):
    img = load_photo(photo.file.read())
    conn = get_db()
    try:
        row = get_complaint(conn, complaint_id)
        if row["status"] not in OPEN:
            raise HTTPException(400, "This report is already closed.")
    finally:
        conn.close()

    if not confirm:
        score, best, _ = checks.analyze(img)
        print(f"After-photo check: garbage score {score:.2f} | looks most like: {best}", flush=True)
        if score >= checks.GARBAGE_THRESHOLD:
            raise HTTPException(409, "This photo still seems to show garbage.")

    stored = storage.save_image(img)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE complaints SET status = 'cleaned', after_photo_path = ?, cleaned_at = ? WHERE id = ?",
            (stored, auth.now_utc().isoformat(), complaint_id),
        )
        conn.commit()
        row = get_complaint(conn, complaint_id)
    finally:
        conn.close()
    notify(dict(row), "cleaned")
    return staff_dict(row)
