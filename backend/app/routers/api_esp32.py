from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.student import Student
from app.security import verify_api_key
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.door_service import get_settings_for_door, update_heartbeat
from app.services.nfc_service import consume_enrollment, identify_card

router = APIRouter(prefix="/api/device", tags=["esp32"])


class NfcScan(BaseModel):
    uid: str


class ButtonEvent(BaseModel):
    event: str = "button_pressed"


class DoorStatus(BaseModel):
    status: str
    locked: bool | None = None


def require_device_key(x_api_key: str | None = Header(default=None)) -> None:
    if not verify_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="invalid api key")


def client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/{door_id}/heartbeat")
def heartbeat(door_id: str, request: Request, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    door = update_heartbeat(db, door_id, client_host(request))
    return {"ok": True, "door_id": door.door_id, "status": door.status, "esp32_base_url": door.esp32_base_url}


@router.get("/{door_id}/config")
def device_config(door_id: str, request: Request, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    update_heartbeat(db, door_id, client_host(request))
    setting = get_settings_for_door(db, door_id)
    return {
        "door_id": door_id,
        "access_mode": setting.access_mode,
        "physical_button_enabled": setting.physical_button_enabled,
        "button_mode": setting.button_mode,
        "unlock_duration_ms": setting.unlock_duration_ms,
        "allow_offline_master_card": setting.allow_offline_master_card,
    }


@router.post("/{door_id}/nfc-scan")
async def nfc_scan(door_id: str, payload: NfcScan, request: Request, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    update_heartbeat(db, door_id, client_host(request))
    enrolled = consume_enrollment(db, door_id, payload.uid)
    if enrolled:
        student = db.get(Student, enrolled.student_id)
        student_payload = {"id": student.id, "full_name": student.full_name, "student_code": student.student_code, "employee_code": student.student_code} if student else None
        return {
            "allowed": False,
            "reason": "card_enrolled",
            "student_id": enrolled.student_id,
            "student": student_payload,
            "user": student_payload,
            "should_unlock": False,
        }
    student, uid_hash = identify_card(db, payload.uid)
    return await evaluate_access(db, AccessEvent(door_id=door_id, method="nfc", student_id=student.id if student else None, nfc_uid_hash=uid_hash), dispatch_unlock=False)


@router.post("/{door_id}/button-event")
async def button_event(door_id: str, payload: ButtonEvent, request: Request, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    update_heartbeat(db, door_id, client_host(request))
    return await evaluate_access(db, AccessEvent(door_id=door_id, method="physical_button", reason=payload.event), dispatch_unlock=False)


@router.post("/{door_id}/door-status")
def door_status(door_id: str, payload: DoorStatus, request: Request, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    door = update_heartbeat(db, door_id, client_host(request))
    door.status = payload.status
    db.commit()
    return {"ok": True}
