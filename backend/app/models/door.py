from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Door(Base):
    __tablename__ = "doors"

    id: Mapped[int] = mapped_column(primary_key=True)
    door_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    door_name: Mapped[str] = mapped_column(String(255))
    esp32_base_url: Mapped[str] = mapped_column(String(255))
    api_key_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DoorSetting(Base):
    __tablename__ = "door_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    door_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    access_mode: Mapped[str] = mapped_column(String(32), default="face_or_nfc")
    face_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    nfc_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    physical_button_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    button_mode: Mapped[str] = mapped_column(String(32), default="exit_only")
    unlock_duration_ms: Mapped[int] = mapped_column(Integer, default=3000)
    face_threshold: Mapped[float] = mapped_column(Float, default=0.50)
    dual_auth_timeout_sec: Mapped[int] = mapped_column(Integer, default=3)
    anti_repeat_cooldown_sec: Mapped[int] = mapped_column(Integer, default=5)
    allow_offline_master_card: Mapped[bool] = mapped_column(Boolean, default=False)
    liveness_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    liveness_threshold: Mapped[float] = mapped_column(Float, default=0.80)
    liveness_fail_closed: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
