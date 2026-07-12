from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attendance_log import AttendanceLog
from app.models.student import Student


@dataclass
class AttendanceFilters:
    start_date: date | None = None
    end_date: date | None = None
    student_code: str | None = None
    class_name: str | None = None
    faculty: str | None = None
    method: str | None = None


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
    target = (day or datetime.now()).date()
    return get_attendance_summary(db, AttendanceFilters(start_date=target, end_date=target))


def get_attendance_summary(db: Session, filters: AttendanceFilters | None = None) -> dict:
    logs = get_filtered_attendance_logs(db, filters)

    grouped: dict[tuple[int, date], list[AttendanceLog]] = {}
    for log in logs:
        if log.student_id is not None and log.created_at is not None:
            grouped.setdefault((log.student_id, log.created_at.date()), []).append(log)

    rows = []
    people_inside = 0
    people_out = 0
    for (student_id, day), student_logs in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        _, end = day_bounds(datetime.combine(day, time.min))
        row = build_attendance_day_row(db, student_id, student_logs, end)
        if not row:
            continue
        row["date"] = day
        if row["status"] == "inside":
            people_inside += 1
        elif row["status"] == "out":
            people_out += 1
        rows.append(row)

    return {
        "rows": rows,
        "attended_count": len(rows),
        "people_inside": people_inside,
        "people_out": people_out,
    }


def get_filtered_attendance_logs(db: Session, filters: AttendanceFilters | None = None) -> list[AttendanceLog]:
    filters = filters or AttendanceFilters()
    stmt = (
        select(AttendanceLog)
        .join(Student, AttendanceLog.student_id == Student.id)
        .where(AttendanceLog.student_id.is_not(None))
    )
    if filters.start_date:
        stmt = stmt.where(AttendanceLog.created_at >= datetime.combine(filters.start_date, time.min))
    if filters.end_date:
        stmt = stmt.where(AttendanceLog.created_at <= datetime.combine(filters.end_date, time.max))
    if filters.student_code:
        stmt = stmt.where(Student.student_code.ilike(f"%{filters.student_code.strip()}%"))
    if filters.class_name:
        stmt = stmt.where(Student.class_name.ilike(f"%{filters.class_name.strip()}%"))
    if filters.faculty:
        stmt = stmt.where(Student.faculty.ilike(f"%{filters.faculty.strip()}%"))
    if filters.method:
        stmt = stmt.where(AttendanceLog.method == filters.method.strip())
    return db.scalars(stmt.order_by(AttendanceLog.student_id.asc(), AttendanceLog.created_at.asc())).all()


def get_attendance_export_rows(db: Session, filters: AttendanceFilters | None = None) -> list[dict]:
    logs = get_filtered_attendance_logs(db, filters)

    grouped: dict[tuple[int, date], list[AttendanceLog]] = {}
    for log in logs:
        if log.student_id is None or log.created_at is None:
            continue
        grouped.setdefault((log.student_id, log.created_at.date()), []).append(log)

    rows = []
    index = 1
    for (student_id, day), day_logs in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        _, end = day_bounds(datetime.combine(day, time.min))
        row = build_attendance_day_row(db, student_id, day_logs, end)
        if not row:
            continue
        for session_index, session in enumerate(row["sessions"], start=1):
            rows.append(
                {
                    "stt": index,
                    "ma_sinh_vien": row["student_code"],
                    "ho_va_ten": row["full_name"],
                    "lop": row["class_name"],
                    "khoa": row["faculty"],
                    "ngay": day.isoformat(),
                    "lan_vao": session_index,
                    "gio_vao": session["check_in_at"],
                    "gio_ra": session["check_out_at"],
                    "phuong_thuc_mo_cua": session["method"],
                    "so_gio_phien": session["duration_hours"],
                    "tong_so_gio_trong_ngay": row["duration_hours"],
                    "thoi_gian_o_trong_lop": row["duration"],
                    "trang_thai": "trong_phong" if row["status"] == "inside" else "da_ra",
                }
            )
            index += 1
    return rows


def build_attendance_day_row(db: Session, student_id: int, logs: list[AttendanceLog], day_end: datetime) -> dict | None:
    sorted_logs = sorted(logs, key=lambda log: log.created_at)
    sessions: list[tuple[AttendanceLog, AttendanceLog | None]] = []
    open_check_in: AttendanceLog | None = None
    methods: set[str] = set()

    for log in sorted_logs:
        if log.event_type == "check_in":
            if open_check_in is None:
                open_check_in = log
                methods.add(log.method)
        elif log.event_type == "check_out" and open_check_in is not None:
            sessions.append((open_check_in, log))
            open_check_in = None

    if open_check_in is not None:
        sessions.append((open_check_in, None))

    if not sessions:
        return None

    first_check_in = sessions[0][0]
    last_checkout = next((checkout for _, checkout in reversed(sessions) if checkout is not None), None)
    has_open_session = sessions[-1][1] is None
    default_checkout = _align_datetime_timezone(day_end, first_check_in.created_at) if has_open_session else None
    seconds_inside = 0
    session_rows = []

    for check_in, check_out in sessions:
        effective_checkout = check_out.created_at if check_out else _align_datetime_timezone(day_end, check_in.created_at)
        session_seconds = max(0, int((effective_checkout - check_in.created_at).total_seconds()))
        seconds_inside += session_seconds
        session_rows.append(
            {
                "check_in_at": check_in.created_at,
                "check_out_at": effective_checkout,
                "method": check_in.method,
                "seconds_inside": session_seconds,
                "duration": format_duration(session_seconds),
                "duration_hours": format_hours(session_seconds),
            }
        )

    student = db.get(Student, student_id)
    return {
        "student_id": student_id,
        "student_code": student.student_code if student else "",
        "full_name": student.full_name if student else f"Student {student_id}",
        "class_name": student.class_name if student else "",
        "faculty": student.faculty if student else "",
        "check_in_at": first_check_in.created_at,
        "check_out_at": last_checkout.created_at if last_checkout and not has_open_session else None,
        "default_check_out_at": default_checkout,
        "seconds_inside": seconds_inside,
        "duration": format_duration(seconds_inside),
        "duration_hours": format_hours(seconds_inside),
        "status": "inside" if has_open_session else "out",
        "method": ", ".join(sorted(methods)),
        "session_count": len(sessions),
        "sessions": session_rows,
    }


def format_duration(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def format_hours(total_seconds: int) -> str:
    return f"{total_seconds / 3600:.2f}"


def _align_datetime_timezone(value: datetime, reference: datetime) -> datetime:
    if reference.tzinfo is None:
        return value.replace(tzinfo=None)
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value
