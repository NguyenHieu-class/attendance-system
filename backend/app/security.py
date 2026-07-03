import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.config import get_settings
from app.database import get_db
from app.models.admin import Admin

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_nfc_uid(raw_uid: str) -> str:
    settings = get_settings()
    normalized = raw_uid.strip().upper()
    return hashlib.sha256(f"{settings.nfc_hash_salt}:{normalized}".encode()).hexdigest()


def verify_api_key(raw_key: str | None, stored_hash: str | None = None) -> bool:
    settings = get_settings()
    expected = stored_hash or hashlib.sha256(settings.esp32_shared_secret.encode()).hexdigest()
    raw_hash = hashlib.sha256((raw_key or "").encode()).hexdigest()
    return hmac.compare_digest(raw_hash, expected)


def api_key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_session(response, admin_id: int) -> None:
    settings = get_settings()
    max_age = int(timedelta(hours=12).total_seconds())
    response.set_cookie(
        settings.session_cookie_name,
        str(admin_id),
        httponly=True,
        samesite="lax",
        max_age=max_age,
    )


def clear_session(response) -> None:
    response.delete_cookie(get_settings().session_cookie_name)


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> Admin:
    cookie = request.cookies.get(get_settings().session_cookie_name)
    if not cookie or not cookie.isdigit():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    admin = db.get(Admin, int(cookie))
    if not admin or not admin.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return admin


def require_admin_page(request: Request, db: Session = Depends(get_db)) -> Admin:
    try:
        return get_current_admin(request, db)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})


def authenticate_admin(db: Session, username: str, password: str) -> Admin | None:
    admin = db.scalar(select(Admin).where(Admin.username == username, Admin.active.is_(True)))
    if admin and verify_password(password, admin.password_hash):
        return admin
    return None
