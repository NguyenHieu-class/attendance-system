from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attendance_log import AttendanceLog
from app.models.student import Student

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.get("")
def list_attendance(db: Session = Depends(get_db)) -> list[dict]:
    logs = db.scalars(select(AttendanceLog).order_by(AttendanceLog.created_at.desc()).limit(100)).all()
    rows = []
    for log in logs:
        student = db.get(Student, log.student_id) if log.student_id else None
        rows.append(
            {
                "id": log.id,
                "student_id": log.student_id,
                "student_code": student.student_code if student else None,
                "full_name": student.full_name if student else None,
                "method": log.method,
                "event_type": log.event_type,
                "created_at": log.created_at,
            }
        )
    return rows
