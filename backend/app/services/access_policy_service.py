from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.access_log import AccessLog
from app.models.student import Student
from app.services.attendance_service import record_attendance
from app.services.door_service import get_settings_for_door, notify_door, unlock_door

_pending_face: dict[tuple[str, int], datetime] = {}
_pending_nfc: dict[tuple[str, int], datetime] = {}
DUAL_AUTH_TIMEOUT_SEC = 3


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
    waiting_started = False
    duplicate_same_factor = False

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
            allowed, reason, waiting_started, duplicate_same_factor = _handle_dual_auth(event.door_id, event.student_id, "face")
            source = "face_and_nfc" if allowed else "face"
        else:
            reason = "face_not_allowed_in_mode"
    elif event.method == "nfc":
        if not setting.nfc_enabled:
            reason = "nfc_disabled"
        elif setting.access_mode in ("nfc_only", "face_or_nfc") and event.student_id:
            allowed, reason = True, "nfc_allowed"
        elif setting.access_mode == "face_and_nfc" and event.student_id:
            allowed, reason, waiting_started, duplicate_same_factor = _handle_dual_auth(event.door_id, event.student_id, "nfc")
            source = "face_and_nfc" if allowed else "nfc"
        else:
            reason = "nfc_not_allowed_in_mode"

    attendance_log = None
    should_record_attendance = (
        student
        and event.method in ("face", "nfc")
        and allowed
        and (setting.access_mode != "face_and_nfc" or reason == "dual_auth_allowed")
    )
    if should_record_attendance:
        attendance_log = record_attendance(db, student.id, source, event.confidence, event.nfc_uid_hash, event.door_id)

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
    if dispatch_unlock and not should_unlock and event.method in ("face", "admin_remote") and reason == "waiting_for_second_factor" and waiting_started:
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
        "attendance_event_type": attendance_log.event_type if attendance_log else None,
        "waiting_started": waiting_started,
        "suppress_feedback": duplicate_same_factor,
    }


def _handle_dual_auth(door_id: str, student_id: int, method: str) -> tuple[bool, str, bool, bool]:
    key = (door_id, student_id)
    now = datetime.now(timezone.utc)
    expires = timedelta(seconds=DUAL_AUTH_TIMEOUT_SEC)
    _clear_expired_pending(now, expires)
    if method == "face":
        same = _pending_face.get(key)
        other = _pending_nfc.get(key)
    else:
        same = _pending_nfc.get(key)
        other = _pending_face.get(key)
    if other and now - other <= expires:
        _pending_face.pop(key, None)
        _pending_nfc.pop(key, None)
        return True, "dual_auth_allowed", False, False
    if same and now - same <= expires:
        return False, "waiting_for_second_factor", False, True
    if method == "face":
        _pending_face[key] = now
    else:
        _pending_nfc[key] = now
    return False, "waiting_for_second_factor", True, False


def _clear_expired_pending(now: datetime, expires: timedelta) -> None:
    for pending in (_pending_face, _pending_nfc):
        for key, created_at in list(pending.items()):
            if now - created_at > expires:
                pending.pop(key, None)
