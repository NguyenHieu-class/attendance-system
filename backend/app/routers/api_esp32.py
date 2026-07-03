from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
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


@router.post("/{door_id}/heartbeat")
def heartbeat(door_id: str, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    door = update_heartbeat(db, door_id)
    return {"ok": True, "door_id": door.door_id, "status": door.status}


@router.get("/{door_id}/config")
def device_config(door_id: str, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
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
async def nfc_scan(door_id: str, payload: NfcScan, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    enrolled = consume_enrollment(db, door_id, payload.uid)
    if enrolled:
        return {"allowed": False, "reason": "card_enrolled", "user_id": enrolled.user_id, "should_unlock": False}
    user, uid_hash = identify_card(db, payload.uid)
    return await evaluate_access(db, AccessEvent(door_id=door_id, method="nfc", user_id=user.id if user else None, nfc_uid_hash=uid_hash), dispatch_unlock=False)


@router.post("/{door_id}/button-event")
async def button_event(door_id: str, payload: ButtonEvent, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    return await evaluate_access(db, AccessEvent(door_id=door_id, method="physical_button", reason=payload.event), dispatch_unlock=False)


@router.post("/{door_id}/door-status")
def door_status(door_id: str, payload: DoorStatus, db: Session = Depends(get_db), _: None = Depends(require_device_key)) -> dict:
    door = update_heartbeat(db, door_id)
    door.status = payload.status
    db.commit()
    return {"ok": True}
