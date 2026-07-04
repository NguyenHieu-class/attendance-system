from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attendance_log import AttendanceLog
from app.models.student import Student


def record_attendance(
    db: Session,
    student_id: int,
    method: str,
    confidence: float | None = None,
    nfc_uid_hash: str | None = None,
    door_id: str | None = None,
) -> AttendanceLog | None:
    now = datetime.now()
    start, end = day_bounds(now)
    latest_log = db.scalars(
        select(AttendanceLog)
        .where(AttendanceLog.student_id == student_id, AttendanceLog.created_at >= start, AttendanceLog.created_at <= end)
        .order_by(AttendanceLog.created_at.desc())
    ).first()
    event_type = "check_out" if latest_log and latest_log.event_type == "check_in" else "check_in"
    log = AttendanceLog(
        student_id=student_id,
        method=method,
        event_type=event_type,
        confidence=confidence,
        nfc_uid_hash=nfc_uid_hash,
        door_id=door_id,
        created_at=now,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def day_bounds(day: datetime | None = None) -> tuple[datetime, datetime]:
    target = (day or datetime.now()).date()
    start = datetime.combine(target, time.min)
    end = datetime.combine(target, time.max)
    return start, end


def get_daily_attendance_summary(db: Session, day: datetime | None = None) -> dict:
    start, end = day_bounds(day)
    logs = db.scalars(
        select(AttendanceLog)
        .where(AttendanceLog.created_at >= start, AttendanceLog.created_at <= end, AttendanceLog.student_id.is_not(None))
        .order_by(AttendanceLog.student_id.asc(), AttendanceLog.created_at.asc())
    ).all()

    grouped: dict[int, list[AttendanceLog]] = {}
    for log in logs:
        if log.student_id is not None:
            grouped.setdefault(log.student_id, []).append(log)

    rows = []
    people_inside = 0
    people_out = 0
    for student_id, student_logs in grouped.items():
        first_check_in = next((log for log in student_logs if log.event_type == "check_in"), None)
        if not first_check_in:
            continue
        checkout = next((log for log in student_logs if log.event_type == "check_out" and log.created_at >= first_check_in.created_at), None)
        default_checkout = _align_datetime_timezone(end, first_check_in.created_at)
        effective_checkout = checkout.created_at if checkout else default_checkout
        seconds_inside = max(0, int((effective_checkout - first_check_in.created_at).total_seconds()))
        latest_event = student_logs[-1].event_type
        if latest_event == "check_in":
            people_inside += 1
        elif latest_event == "check_out":
            people_out += 1
        student = db.get(Student, student_id)
        rows.append(
            {
                "student_id": student_id,
                "student_code": student.student_code if student else "",
                "full_name": student.full_name if student else f"Student {student_id}",
                "class_name": student.class_name if student else "",
                "faculty": student.faculty if student else "",
                "check_in_at": first_check_in.created_at,
                "check_out_at": checkout.created_at if checkout else None,
                "default_check_out_at": default_checkout if not checkout else None,
                "seconds_inside": seconds_inside,
                "duration": format_duration(seconds_inside),
                "status": "inside" if latest_event == "check_in" else "out",
            }
        )

    return {
        "rows": rows,
        "attended_count": len(rows),
        "people_inside": people_inside,
        "people_out": people_out,
    }


def format_duration(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def _align_datetime_timezone(value: datetime, reference: datetime) -> datetime:
    if reference.tzinfo is None:
        return value.replace(tzinfo=None)
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value
