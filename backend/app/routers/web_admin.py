import csv
import io
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.access_log import AccessLog
from app.models.admin import Admin
from app.models.attendance_log import AttendanceLog
from app.models.door import Door, DoorSetting
from app.models.face_profile import FaceProfile
from app.models.nfc_card import NfcCard
from app.models.nfc_enrollment import NfcEnrollment
from app.models.student import Student
from app.security import authenticate_admin, clear_session, create_session, get_current_admin, require_admin_page
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.attendance_service import day_bounds, get_daily_attendance_summary
from app.services.camera_service import camera_service
from app.services.door_service import ensure_default_door, get_settings_for_door
from app.services.face_service import face_service
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
    today, _ = day_bounds(datetime.now())
    data = {
        "request": request,
        "admin": admin,
        "active_students": db.scalar(select(func.count()).select_from(Student).where(Student.status == "active")) or 0,
        "attendance_today": db.scalar(
            select(func.count(func.distinct(AttendanceLog.student_id))).where(
                AttendanceLog.created_at >= today,
                AttendanceLog.event_type == "check_in",
                AttendanceLog.student_id.is_not(None),
            )
        )
        or 0,
        "access_today": db.scalar(select(func.count()).select_from(AccessLog).where(AccessLog.created_at >= today, AccessLog.result == "allowed")) or 0,
        "denied_today": db.scalar(select(func.count()).select_from(AccessLog).where(AccessLog.created_at >= today, AccessLog.result == "denied")) or 0,
        "doors": db.scalars(select(Door)).all(),
    }
    return templates(request).TemplateResponse("dashboard.html", data)


@router.get("/admin/students")
def students(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    rows = db.scalars(select(Student).order_by(Student.id.desc())).all()
    return templates(request).TemplateResponse("students/list.html", {"request": request, "admin": admin, "students": rows})


@router.get("/admin/students/new")
def new_student(request: Request, admin: Admin = Depends(require_admin_page)):
    return templates(request).TemplateResponse("students/form.html", {"request": request, "admin": admin, "student": None})


@router.post("/admin/students")
def create_student(
    student_code: str = Form(...),
    full_name: str = Form(...),
    faculty: str = Form(""),
    class_name: str = Form(""),
    major: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    db.add(Student(student_code=student_code, full_name=full_name, faculty=faculty or None, class_name=class_name or None, major=major or None, email=email or None, phone=phone or None))
    db.commit()
    return RedirectResponse("/admin/students", status_code=303)


@router.get("/admin/students/{student_id}")
def edit_student_page(student_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404)
    return templates(request).TemplateResponse("students/form.html", {"request": request, "admin": admin, "student": student})


@router.post("/admin/students/{student_id}/edit")
def edit_student(student_id: int, student_code: str = Form(...), full_name: str = Form(...), faculty: str = Form(""), class_name: str = Form(""), major: str = Form(""), email: str = Form(""), phone: str = Form(""), status: str = Form("active"), db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404)
    student.student_code = student_code
    student.full_name = full_name
    student.faculty = faculty or None
    student.class_name = class_name or None
    student.major = major or None
    student.email = email or None
    student.phone = phone or None
    student.status = status
    student.is_active = status == "active"
    db.commit()
    return RedirectResponse("/admin/students", status_code=303)


@router.post("/admin/students/{student_id}/disable")
def disable_student(student_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    student = db.get(Student, student_id)
    if student:
        student.status = "inactive"
        student.is_active = False
        db.commit()
    return RedirectResponse("/admin/students", status_code=303)


@router.post("/admin/students/{student_id}/delete")
def delete_student(student_id: int, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    student = db.get(Student, student_id)
    if student:
        has_logs = db.scalar(select(func.count()).select_from(AttendanceLog).where(AttendanceLog.student_id == student_id)) or db.scalar(select(func.count()).select_from(AccessLog).where(AccessLog.student_id == student_id))
        if has_logs:
            student.status = "inactive"
            student.is_active = False
        else:
            db.delete(student)
        db.commit()
    return RedirectResponse("/admin/students", status_code=303)


@router.get("/admin/students/{student_id}/faces")
def faces_stub(student_id: int, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404)
    cards = db.scalars(select(NfcCard).where(NfcCard.student_id == student_id).order_by(NfcCard.created_at.desc())).all()
    face_profiles = db.scalars(select(FaceProfile).where(FaceProfile.student_id == student_id).order_by(FaceProfile.created_at.desc())).all()
    face_profile_rows = [{"profile": profile, "dimension": face_service.embedding_dimension(profile.embedding)} for profile in face_profiles]
    pending = db.scalar(select(NfcEnrollment).where(NfcEnrollment.student_id == student_id, NfcEnrollment.active.is_(True)).order_by(NfcEnrollment.created_at.desc()))
    return templates(request).TemplateResponse("students/faces.html", {"request": request, "admin": admin, "student": student, "cards": cards, "face_profile_rows": face_profile_rows, "pending": pending, "message": request.query_params.get("message")})


@router.post("/admin/students/{student_id}/faces/enroll")
async def face_enroll(
    student_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404)
    saved = 0
    failed = 0
    face_dir = get_settings().data_path / "faces" / str(student_id)
    face_dir.mkdir(parents=True, exist_ok=True)
    for upload in files[:5]:
        content = await upload.read()
        frame = _decode_image(content)
        if frame is None:
            failed += 1
            continue
        embedding = face_service.get_embedding(frame)
        if embedding is None:
            failed += 1
            continue
        suffix = Path(upload.filename or "face.jpg").suffix.lower() or ".jpg"
        if suffix not in {".jpg", ".jpeg", ".png"}:
            suffix = ".jpg"
        image_path = face_dir / f"{uuid4().hex}{suffix}"
        image_path.write_bytes(content)
        db.add(
            FaceProfile(
                student_id=student_id,
                embedding=face_service.serialize_embedding(embedding),
                image_path=str(image_path),
                model_name=f"{face_service.model_name}{'-mock' if face_service.is_mock_mode() else ''}",
                quality_score=0.50 if face_service.is_mock_mode() else 1.0,
            )
        )
        saved += 1
    db.commit()
    message = f"face_saved_{saved}_failed_{failed}"
    return RedirectResponse(f"/admin/students/{student_id}/faces?message={message}", status_code=303)


@router.post("/admin/students/{student_id}/faces/capture")
def face_capture(
    student_id: int,
    pose: str = Form("front"),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404)
    if not camera_service.status()["running"]:
        camera_service.start()
    frame = camera_service.get_frame_copy()
    if frame is None:
        return RedirectResponse(f"/admin/students/{student_id}/faces?message=face_camera_not_ready", status_code=303)
    embedding = face_service.get_embedding(frame)
    if embedding is None:
        return RedirectResponse(f"/admin/students/{student_id}/faces?message=face_not_detected", status_code=303)

    import cv2

    safe_pose = "".join(ch for ch in pose.lower() if ch.isalnum() or ch in ("-", "_"))[:32] or "face"
    face_dir = get_settings().data_path / "faces" / str(student_id)
    face_dir.mkdir(parents=True, exist_ok=True)
    image_path = face_dir / f"{safe_pose}-{uuid4().hex}.jpg"
    cv2.imwrite(str(image_path), frame)
    db.add(
        FaceProfile(
            student_id=student_id,
            embedding=face_service.serialize_embedding(embedding),
            image_path=str(image_path),
            model_name=f"{face_service.model_name}{'-mock' if face_service.is_mock_mode() else ''}",
            quality_score=0.50 if face_service.is_mock_mode() else 1.0,
        )
    )
    db.commit()
    return RedirectResponse(f"/admin/students/{student_id}/faces?message=face_captured_{safe_pose}", status_code=303)


@router.post("/admin/students/{student_id}/faces/{profile_id}/delete")
def delete_face_profile(
    student_id: int,
    profile_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    profile = db.get(FaceProfile, profile_id)
    if profile and profile.student_id == student_id:
        db.delete(profile)
        db.commit()
    return RedirectResponse(f"/admin/students/{student_id}/faces?message=face_deleted", status_code=303)


def _decode_image(content: bytes):
    try:
        import cv2
        import numpy as np

        return cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


@router.post("/admin/students/{student_id}/nfc/enroll")
def nfc_enroll(student_id: int, door_id: str = Form("door-01"), db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(404)
    start_enrollment(db, door_id, student_id)
    return RedirectResponse(f"/admin/students/{student_id}/faces?message=nfc_waiting", status_code=303)


@router.post("/admin/students/{student_id}/nfc/{card_id}/delete")
def delete_nfc_card(
    student_id: int,
    card_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    card = db.get(NfcCard, card_id)
    if card and card.student_id == student_id:
        db.delete(card)
        db.commit()
    return RedirectResponse(f"/admin/students/{student_id}/faces?message=nfc_deleted", status_code=303)


@router.get("/admin/attendance")
def attendance(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    logs = db.scalars(select(AttendanceLog).order_by(AttendanceLog.created_at.desc()).limit(200)).all()
    log_rows = []
    for log in logs:
        student = db.get(Student, log.student_id) if log.student_id else None
        log_rows.append(
            {
                "student_code": student.student_code if student else "",
                "full_name": student.full_name if student else "Khong xac dinh",
                "method": log.method,
                "event_type": log.event_type,
                "created_at": log.created_at,
            }
        )
    summary = get_daily_attendance_summary(db)
    return templates(request).TemplateResponse(
        "attendance/list.html",
        {
            "request": request,
            "admin": admin,
            "log_rows": log_rows,
            "people_inside": summary["people_inside"],
            "people_out": summary["people_out"],
            "attended_count": summary["attended_count"],
            "attendance_rows": summary["rows"],
        },
    )


@router.get("/admin/access-logs")
def access_logs(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    logs = db.scalars(select(AccessLog).order_by(AccessLog.created_at.desc()).limit(200)).all()
    return templates(request).TemplateResponse("logs/access.html", {"request": request, "admin": admin, "logs": logs})


@router.get("/admin/doors")
def doors(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    ensure_default_door(db)
    doors = db.scalars(select(Door)).all()
    return templates(request).TemplateResponse("doors/list.html", {"request": request, "admin": admin, "doors": doors})


@router.get("/admin/camera")
def camera_page(request: Request, admin: Admin = Depends(require_admin_page)):
    return templates(request).TemplateResponse("camera.html", {"request": request, "admin": admin, "camera": camera_service.status()})


@router.get("/admin/detection")
def detection_page(request: Request, admin: Admin = Depends(require_admin_page)):
    camera_service.start(door_id="door-01", recognition_enabled=True)
    camera_service.enable_recognition(True, door_id="door-01")
    return templates(request).TemplateResponse("detection.html", {"request": request, "admin": admin, "camera": camera_service.status()})


@router.post("/admin/camera/start")
def camera_start(door_id: str = Form("door-01"), recognize: str | None = Form(None), admin: Admin = Depends(require_admin_page)):
    camera_service.start(door_id=door_id, recognition_enabled=recognize == "on")
    camera_service.enable_recognition(recognize == "on", door_id=door_id)
    return RedirectResponse("/admin/camera", status_code=303)


@router.post("/admin/camera/recognition")
def camera_recognition(door_id: str = Form("door-01"), enabled: str | None = Form(None), admin: Admin = Depends(require_admin_page)):
    camera_service.enable_recognition(enabled == "on", door_id=door_id)
    return RedirectResponse("/admin/camera", status_code=303)


@router.post("/admin/camera/stop")
def camera_stop(admin: Admin = Depends(require_admin_page)):
    camera_service.stop()
    return RedirectResponse("/admin/camera", status_code=303)


@router.get("/admin/doors/{door_id}/settings")
def door_settings(door_id: str, request: Request, db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    setting = get_settings_for_door(db, door_id)
    return templates(request).TemplateResponse("doors/settings.html", {"request": request, "admin": admin, "setting": setting})


@router.post("/admin/doors/{door_id}/settings")
def save_door_settings(
    door_id: str,
    access_mode: str = Form(...),
    unlock_duration_ms: int = Form(...),
    face_threshold: float = Form(...),
    physical_button_enabled: str | None = Form(None),
    liveness_enabled: str | None = Form(None),
    liveness_threshold: float = Form(0.80),
    liveness_fail_closed: str | None = Form(None),
    db: Session = Depends(get_db),
    admin: Admin = Depends(require_admin_page),
):
    setting = get_settings_for_door(db, door_id)
    setting.access_mode = access_mode
    setting.unlock_duration_ms = unlock_duration_ms
    setting.face_threshold = face_threshold
    setting.physical_button_enabled = physical_button_enabled == "on"
    setting.liveness_enabled = liveness_enabled == "on"
    setting.liveness_threshold = liveness_threshold
    setting.liveness_fail_closed = liveness_fail_closed == "on"
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
    return csv_response("attendance.csv", rows, ["id", "student_id", "method", "event_type", "created_at"])


@router.get("/admin/export/access_logs.csv")
def export_access_logs(db: Session = Depends(get_db), admin: Admin = Depends(require_admin_page)):
    rows = db.scalars(select(AccessLog).order_by(AccessLog.created_at.desc())).all()
    return csv_response("access_logs.csv", rows, ["id", "student_id", "door_id", "method", "result", "reason", "confidence", "liveness_score", "spoof_result", "created_at"])
