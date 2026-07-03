from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attendance_log import AttendanceLog


def record_attendance(
    db: Session,
    user_id: int,
    method: str,
    confidence: float | None = None,
    nfc_uid_hash: str | None = None,
    door_id: str | None = None,
) -> AttendanceLog | None:
    start = datetime.combine(datetime.now().date(), time.min).replace(tzinfo=timezone.utc)
    logs = db.scalars(
        select(AttendanceLog)
        .where(AttendanceLog.user_id == user_id, AttendanceLog.created_at >= start)
        .order_by(AttendanceLog.created_at.asc())
    ).all()
    types = {log.event_type for log in logs}
    if "check_in" not in types:
        event_type = "check_in"
    elif "check_out" not in types:
        event_type = "check_out"
    else:
        return None
    log = AttendanceLog(
        user_id=user_id,
        method=method,
        event_type=event_type,
        confidence=confidence,
        nfc_uid_hash=nfc_uid_hash,
        door_id=door_id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
