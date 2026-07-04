from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.access_log import AccessLog
from app.models.student import Student
from app.services.attendance_service import record_attendance
from app.services.door_service import get_settings_for_door, notify_door, unlock_door

_pending_face: dict[tuple[str, int], datetime] = {}
_pending_nfc: dict[tuple[str, int], datetime] = {}


@dataclass
class AccessEvent:
    door_id: str
    method: str
    student_id: int | None = None
    confidence: float | None = None
    liveness_score: float | None = None
    spoof_result: str | None = None
    nfc_uid_hash: str | None = None
    reason: str | None = None


async def evaluate_access(db: Session, event: AccessEvent, dispatch_unlock: bool = True) -> dict:
    setting = get_settings_for_door(db, event.door_id)
    student = db.get(Student, event.student_id) if event.student_id else None
    allowed = False
    reason = "denied_by_policy"
    source = event.method

    if event.method == "physical_button":
        allowed = setting.physical_button_enabled and setting.button_mode != "disabled"
        reason = "exit_button" if allowed else "physical_button_disabled"
    elif event.method == "admin_remote":
        allowed = setting.access_mode != "disabled"
        reason = "admin_remote" if allowed else "door_disabled"
    elif setting.access_mode in ("admin_only", "disabled"):
        reason = setting.access_mode
    elif event.method == "face":
        if not setting.face_enabled:
            reason = "face_disabled"
        elif setting.access_mode in ("face_only", "face_or_nfc") and event.student_id:
            allowed, reason = True, "face_allowed"
        elif setting.access_mode == "face_and_nfc" and event.student_id:
            allowed, reason = _handle_dual_auth(event.door_id, event.student_id, "face", setting.dual_auth_timeout_sec)
            source = "face_and_nfc" if allowed else "face"
        else:
            reason = "face_not_allowed_in_mode"
    elif event.method == "nfc":
        if not setting.nfc_enabled:
            reason = "nfc_disabled"
        elif setting.access_mode in ("nfc_only", "face_or_nfc") and event.student_id:
            allowed, reason = True, "nfc_allowed"
        elif setting.access_mode == "face_and_nfc" and event.student_id:
            allowed, reason = _handle_dual_auth(event.door_id, event.student_id, "nfc", setting.dual_auth_timeout_sec)
            source = "face_and_nfc" if allowed else "nfc"
        else:
            reason = "nfc_not_allowed_in_mode"

    if allowed and event.student_id and event.method in ("face", "nfc"):
        record_attendance(db, event.student_id, source, event.confidence, event.nfc_uid_hash, event.door_id)

    log = AccessLog(
        student_id=event.student_id,
        door_id=event.door_id,
        method=source,
        result="allowed" if allowed else "denied",
        reason=event.reason or reason,
        confidence=event.confidence,
        liveness_score=event.liveness_score,
        spoof_result=event.spoof_result,
        nfc_uid_hash=event.nfc_uid_hash,
    )
    db.add(log)
    db.commit()

    should_unlock = bool(allowed and event.method != "physical_button")
    unlock_sent = (
        await unlock_door(
            db,
            event.door_id,
            source,
            event.student_id,
            student.full_name if student else None,
            student.student_code if student else None,
        )
        if should_unlock and dispatch_unlock
        else False
    )
    notify_sent = False
    if dispatch_unlock and not should_unlock and event.method in ("face", "admin_remote") and reason == "waiting_for_second_factor":
        notify_sent = await notify_door(
            db,
            event.door_id,
            "waiting",
            reason,
            student.full_name if student else None,
            student.student_code if student else None,
        )
    elif dispatch_unlock and not should_unlock and event.method in ("face", "admin_remote") and reason != "waiting_for_second_factor":
        notify_sent = await notify_door(
            db,
            event.door_id,
            "denied",
            reason,
            student.full_name if student else None,
            student.student_code if student else None,
        )
    student_payload = {
        "id": student.id,
        "full_name": student.full_name,
        "student_code": student.student_code,
        "employee_code": student.student_code,
        "class_name": student.class_name,
        "faculty": student.faculty,
    } if student else None
    return {
        "allowed": allowed,
        "reason": reason,
        "student_id": event.student_id,
        "student": student_payload,
        "user": student_payload,
        "should_unlock": should_unlock,
        "unlock_sent": unlock_sent,
        "notify_sent": notify_sent,
    }


def _handle_dual_auth(door_id: str, student_id: int, method: str, timeout_sec: int) -> tuple[bool, str]:
    key = (door_id, student_id)
    now = datetime.now(timezone.utc)
    expires = timedelta(seconds=timeout_sec)
    if method == "face":
        _pending_face[key] = now
        other = _pending_nfc.get(key)
    else:
        _pending_nfc[key] = now
        other = _pending_face.get(key)
    if other and now - other <= expires:
        _pending_face.pop(key, None)
        _pending_nfc.pop(key, None)
        return True, "dual_auth_allowed"
    return False, "waiting_for_second_factor"
