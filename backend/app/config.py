from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_secret_key: str = "change-me"
    database_url: str = "sqlite:///./attendance.db"
    nfc_hash_salt: str = "change-me"
    esp32_shared_secret: str = "change-me"
    camera_index: int = 0
    face_model_name: str = "buffalo_s"
    face_allow_mock: bool = False
    data_dir: str = "../data"
    session_cookie_name: str = "attendance_session"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
