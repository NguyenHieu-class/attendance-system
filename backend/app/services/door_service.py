from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.door import Door, DoorSetting
from app.security import api_key_hash, now_utc
from app.services.esp32_client import Esp32Client

_last_unlock_at: dict[str, datetime] = {}


def ensure_default_door(db: Session) -> Door:
    door = db.scalar(select(Door).where(Door.door_id == "door-01"))
    if not door:
        door = Door(
            door_id="door-01",
            door_name="Main Door",
            esp32_base_url="http://192.168.1.80",
            api_key_hash=api_key_hash("change-me"),
            status="unknown",
        )
        db.add(door)
        db.add(DoorSetting(door_id="door-01"))
        db.commit()
        db.refresh(door)
    elif not db.scalar(select(DoorSetting).where(DoorSetting.door_id == door.door_id)):
        db.add(DoorSetting(door_id=door.door_id))
        db.commit()
    return door


def get_settings_for_door(db: Session, door_id: str) -> DoorSetting:
    setting = db.scalar(select(DoorSetting).where(DoorSetting.door_id == door_id))
    if not setting:
        setting = DoorSetting(door_id=door_id)
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def update_heartbeat(db: Session, door_id: str) -> Door:
    door = db.scalar(select(Door).where(Door.door_id == door_id)) or ensure_default_door(db)
    door.status = "online"
    door.last_seen_at = now_utc()
    db.commit()
    db.refresh(door)
    return door


async def unlock_door(db: Session, door_id: str, source: str, user_id: int | None = None) -> bool:
    door = db.scalar(select(Door).where(Door.door_id == door_id))
    if not door:
        return False
    setting = get_settings_for_door(db, door_id)
    last = _last_unlock_at.get(door_id)
    now = datetime.now(timezone.utc)
    if last and now - last < timedelta(seconds=setting.anti_repeat_cooldown_sec):
        return True
    _last_unlock_at[door_id] = now
    return await Esp32Client().unlock(door, setting.unlock_duration_ms, source, user_id)
