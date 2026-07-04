import logging
from uuid import uuid4

import httpx

from app.config import get_settings
from app.models.door import Door

logger = logging.getLogger(__name__)


class Esp32Client:
    def __init__(self, timeout_sec: float = 2.0) -> None:
        self.timeout_sec = timeout_sec

    async def unlock(
        self,
        door: Door,
        duration_ms: int,
        source: str,
        student_id: int | None,
        full_name: str | None = None,
        student_code: str | None = None,
    ) -> bool:
        settings = get_settings()
        payload = {
            "command_id": str(uuid4()),
            "duration_ms": duration_ms,
            "source": source,
            "student_id": student_id,
            "full_name": full_name or "",
            "student_code": student_code or "",
            "employee_code": student_code or "",
            "door_id": door.door_id,
            "signature": settings.esp32_shared_secret,
        }
        headers = {"X-API-Key": settings.esp32_shared_secret}
        url = f"{door.esp32_base_url.rstrip('/')}/unlock"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning("ESP32 unlock failed for %s url=%s status=%s body=%s", door.door_id, url, exc.response.status_code, exc.response.text[:200])
            return False
        except httpx.RequestError as exc:
            logger.warning("ESP32 unlock failed for %s url=%s error=%s", door.door_id, url, repr(exc))
            return False

    async def notify(
        self,
        door: Door,
        status: str,
        reason: str,
        full_name: str | None = None,
        student_code: str | None = None,
    ) -> bool:
        settings = get_settings()
        payload = {
            "status": status,
            "reason": reason,
            "full_name": full_name or "",
            "student_code": student_code or "",
            "employee_code": student_code or "",
            "signature": settings.esp32_shared_secret,
        }
        headers = {"X-API-Key": settings.esp32_shared_secret}
        url = f"{door.esp32_base_url.rstrip('/')}/notify"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("ESP32 notify failed for %s url=%s error=%s", door.door_id, url, repr(exc))
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
