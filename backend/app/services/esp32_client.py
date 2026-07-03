import logging
from uuid import uuid4

import httpx

from app.config import get_settings
from app.models.door import Door

logger = logging.getLogger(__name__)


class Esp32Client:
    def __init__(self, timeout_sec: float = 2.0) -> None:
        self.timeout_sec = timeout_sec

    async def unlock(self, door: Door, duration_ms: int, source: str, user_id: int | None) -> bool:
        payload = {
            "command_id": str(uuid4()),
            "duration_ms": duration_ms,
            "source": source,
            "user_id": user_id,
            "door_id": door.door_id,
            "signature": "",
        }
        headers = {"X-API-Key": get_settings().esp32_shared_secret}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                response = await client.post(f"{door.esp32_base_url.rstrip('/')}/unlock", json=payload, headers=headers)
                response.raise_for_status()
            return True
        except Exception as exc:  # Hardware/network failure must not crash the backend.
            logger.warning("ESP32 unlock failed for %s: %s", door.door_id, exc)
            return False

    async def status(self, door: Door) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                response = await client.get(f"{door.esp32_base_url.rstrip('/')}/status")
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("ESP32 status failed for %s: %s", door.door_id, exc)
            return {"status": "offline", "locked": None}
