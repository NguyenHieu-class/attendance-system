import csv
import io
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.access_log import AccessLog
from app.models.admin import Admin
from app.models.attendance_log import AttendanceLog
from app.models.door import Door, DoorSetting
from app.models.nfc_card import NfcCard
from app.models.nfc_enrollment import NfcEnrollment
from app.models.user import User
from app.security import authenticate_admin, clear_session, create_session, get_current_admin, require_admin_page
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.door_service import ensure_default_door, get_settings_for_door
from app.services.nfc_service import start_enrollment

router = APIRouter(tags=["admin"])


def templates(request: Request):
    return request.app.state.templates


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=303)


@router.get("/login")
def login_page(request: Request):
    return templates(request).TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin = authenticate_admin(db, username, password)
    if not admin:
        return templates(request).TemplateResponse("login.html", {"request": request, "error": "Sai tai khoan hoac mat khau"}, status_code=401)
    response = RedirectResponse("/admin", status_code=303)
    create_session(response, admin.id)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response


@router.get("/admin")
def dashboard(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    ensure_default_door(db)
    today = datetime.combine(datetime.now().date(), time.min).replace(tzinfo=timezone.utc)
    data = {
        "request": request,
        "admin": admin,
        "active_users": db.scalar(select(func.count()).select_from(User).where(User.status == "active")) or 0,
        "attendance_today": db.scalar(select(func.count()).select_from(AttendanceLog).where(AttendanceLog.created_at >= today)) or 0,
        "access_today": db.scalar(select(func.count()).select_from(AccessLog).where(AccessLog.created_at >= today, AccessLog.result == "allowed")) or 0,
        "denied_today": db.scalar(select(func.count()).select_from(AccessLog).where(AccessLog.created_at >= today, AccessLog.result == "denied")) or 0,
        "doors": db.scalars(select(Door)).all(),
    }
    return templates(request).TemplateResponse("dashboard.html", data)


@router.get("/admin/users")
def users(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    rows = db.scalars(select(User).order_by(User.id.desc())).all()
    return templates(request).TemplateResponse("users/list.html", {"request": request, "admin": admin, "users": rows})


@router.get("/admin/users/new")
def new_user(request: Request, admin: Admin = Depends(require_admin_page)):
    return templates(request).TemplateResponse("users/form.html", {"request": request, "admin": admin, "user": None})


@router.post("/admin/users")
def create_user(
    employee_code: str = Form(...),
    full_name: str = Form(...),
    department: str = Form(""),
    position: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    db.add(User(employee_code=employee_code, full_name=full_name, department=department or None, position=position or None, email=email or None, phone=phone or None))
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/admin/users/{user_id}")
def edit_user_page(user_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    return templates(request).TemplateResponse("users/form.html", {"request": request, "admin": admin, "user": user})


@router.post("/admin/users/{user_id}/edit")
def edit_user(user_id: int, employee_code: str = Form(...), full_name: str = Form(...), department: str = Form(""), position: str = Form(""), email: str = Form(""), phone: str = Form(""), status: str = Form("active"), db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    user.employee_code = employee_code
    user.full_name = full_name
    user.department = department or None
    user.position = position or None
    user.email = email or None
    user.phone = phone or None
    user.status = status
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/disable")
def disable_user(user_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    user = db.get(User, user_id)
    if user:
        user.status = "inactive"
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/delete")
def delete_user(user_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    user = db.get(User, user_id)
    if user:
        has_logs = db.scalar(select(func.count()).select_from(AttendanceLog).where(AttendanceLog.user_id == user_id)) or db.scalar(select(func.count()).select_from(AccessLog).where(AccessLog.user_id == user_id))
        if has_logs:
            user.status = "inactive"
        else:
            db.delete(user)
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/admin/users/{user_id}/faces")
def faces_stub(user_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    cards = db.scalars(select(NfcCard).where(NfcCard.user_id == user_id).order_by(NfcCard.created_at.desc())).all()
    pending = db.scalar(select(NfcEnrollment).where(NfcEnrollment.user_id == user_id, NfcEnrollment.active.is_(True)).order_by(NfcEnrollment.created_at.desc()))
    return templates(request).TemplateResponse("users/faces.html", {"request": request, "admin": admin, "user": user, "cards": cards, "pending": pending, "message": request.query_params.get("message")})


@router.post("/admin/users/{user_id}/faces/enroll")
def face_enroll_stub(user_id: int, admin: Admin = Depends(require_admin_page)):
    return RedirectResponse(f"/admin/users/{user_id}/faces", status_code=303)


@router.post("/admin/users/{user_id}/nfc/enroll")
def nfc_enroll(user_id: int, door_id: str = Form("door-01"), db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    start_enrollment(db, door_id, user_id)
    return RedirectResponse(f"/admin/users/{user_id}/faces?message=nfc_waiting", status_code=303)


@router.get("/admin/attendance")
def attendance(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    logs = db.scalars(select(AttendanceLog).order_by(AttendanceLog.created_at.desc()).limit(200)).all()
    return templates(request).TemplateResponse("attendance/list.html", {"request": request, "admin": admin, "logs": logs})


@router.get("/admin/access-logs")
def access_logs(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    logs = db.scalars(select(AccessLog).order_by(AccessLog.created_at.desc()).limit(200)).all()
    return templates(request).TemplateResponse("logs/access.html", {"request": request, "admin": admin, "logs": logs})


@router.get("/admin/doors")
def doors(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    ensure_default_door(db)
    doors = db.scalars(select(Door)).all()
    return templates(request).TemplateResponse("doors/list.html", {"request": request, "admin": admin, "doors": doors})


@router.get("/admin/doors/{door_id}/settings")
def door_settings(door_id: str, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    setting = get_settings_for_door(db, door_id)
    return templates(request).TemplateResponse("doors/settings.html", {"request": request, "admin": admin, "setting": setting})


@router.post("/admin/doors/{door_id}/settings")
def save_door_settings(door_id: str, access_mode: str = Form(...), unlock_duration_ms: int = Form(...), face_threshold: float = Form(...), physical_button_enabled: str | None = Form(None), db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    setting = get_settings_for_door(db, door_id)
    setting.access_mode = access_mode
    setting.unlock_duration_ms = unlock_duration_ms
    setting.face_threshold = face_threshold
    setting.physical_button_enabled = physical_button_enabled == "on"
    db.commit()
    return RedirectResponse(f"/admin/doors/{door_id}/settings", status_code=303)


@router.post("/admin/doors/{door_id}/unlock")
async def admin_unlock(door_id: str, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    await evaluate_access(db, AccessEvent(door_id=door_id, method="admin_remote"))
    return RedirectResponse("/admin/doors", status_code=303)


def csv_response(filename: str, rows: list, fields: list[str]) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in rows:
        writer.writerow([getattr(row, field) for field in fields])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/admin/export/attendance.csv")
def export_attendance(db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    rows = db.scalars(select(AttendanceLog).order_by(AttendanceLog.created_at.desc())).all()
    return csv_response("attendance.csv", rows, ["id", "user_id", "method", "event_type", "created_at"])


@router.get("/admin/export/access_logs.csv")
def export_access_logs(db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    rows = db.scalars(select(AccessLog).order_by(AccessLog.created_at.desc())).all()
    return csv_response("access_logs.csv", rows, ["id", "user_id", "door_id", "method", "result", "reason", "created_at"])
