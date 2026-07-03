from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nfc_card import NfcCard
from app.models.user import User
from app.security import hash_nfc_uid

_pending_enroll: dict[str, int] = {}


def start_enrollment(door_id: str, user_id: int) -> None:
    _pending_enroll[door_id] = user_id


def consume_enrollment(db: Session, door_id: str, raw_uid: str) -> NfcCard | None:
    user_id = _pending_enroll.pop(door_id, None)
    if not user_id:
        return None
    uid_hash = hash_nfc_uid(raw_uid)
    card = db.scalar(select(NfcCard).where(NfcCard.card_uid_hash == uid_hash))
    if card:
        card.user_id = user_id
        card.active = True
    else:
        card = NfcCard(user_id=user_id, card_uid_hash=uid_hash, card_label="ESP32 enrolled", active=True)
        db.add(card)
    db.commit()
    db.refresh(card)
    return card


def identify_card(db: Session, raw_uid: str) -> tuple[User | None, str]:
    uid_hash = hash_nfc_uid(raw_uid)
    card = db.scalar(select(NfcCard).where(NfcCard.card_uid_hash == uid_hash, NfcCard.active.is_(True)))
    if not card:
        return None, uid_hash
    user = db.get(User, card.user_id)
    if not user or user.status != "active":
        return None, uid_hash
    return user, uid_hash
