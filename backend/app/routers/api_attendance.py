from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attendance_log import AttendanceLog

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.get("")
def list_attendance(db: Session = Depends(get_db)) -> list[dict]:
    logs = db.scalars(select(AttendanceLog).order_by(AttendanceLog.created_at.desc()).limit(100)).all()
    return [{"id": log.id, "student_id": log.student_id, "method": log.method, "event_type": log.event_type, "created_at": log.created_at} for log in logs]
