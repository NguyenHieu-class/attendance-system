from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nfc_card import NfcCard
from app.models.nfc_enrollment import NfcEnrollment
from app.models.student import Student
from app.security import hash_nfc_uid, now_utc


def start_enrollment(db: Session, door_id: str, student_id: int) -> NfcEnrollment:
    for existing in db.scalars(select(NfcEnrollment).where(NfcEnrollment.door_id == door_id, NfcEnrollment.active.is_(True))).all():
        existing.active = False
        existing.consumed_at = now_utc()
    enrollment = NfcEnrollment(door_id=door_id, student_id=student_id, active=True)
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


def consume_enrollment(db: Session, door_id: str, raw_uid: str) -> NfcCard | None:
    enrollment = db.scalar(
        select(NfcEnrollment)
        .where(NfcEnrollment.door_id == door_id, NfcEnrollment.active.is_(True))
        .order_by(NfcEnrollment.created_at.desc())
    )
    if not enrollment:
        return None
    uid_hash = hash_nfc_uid(raw_uid)
    card = db.scalar(select(NfcCard).where(NfcCard.card_uid_hash == uid_hash))
    if card:
        card.student_id = enrollment.student_id
        card.active = True
    else:
        card = NfcCard(student_id=enrollment.student_id, card_uid_hash=uid_hash, card_label="ESP32 enrolled", active=True)
        db.add(card)
    enrollment.active = False
    enrollment.consumed_at = now_utc()
    db.commit()
    db.refresh(card)
    return card


def identify_card(db: Session, raw_uid: str) -> tuple[Student | None, str]:
    uid_hash = hash_nfc_uid(raw_uid)
    card = db.scalar(select(NfcCard).where(NfcCard.card_uid_hash == uid_hash, NfcCard.active.is_(True)))
    if not card:
        return None, uid_hash
    student = db.get(Student, card.student_id)
    if not student or student.status != "active":
        return None, uid_hash
    return student, uid_hash
