import asyncio
import logging
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone

import cv2

from app.config import get_settings
from app.database import SessionLocal
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.door_service import get_settings_for_door
from app.services.face_service import face_service

logger = logging.getLogger(__name__)


class CameraService:
    def __init__(self) -> None:
        self._capture: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._recognition_enabled = False
        self._door_id = "door-01"
        self._latest_frame = None
        self._latest_jpeg: bytes | None = None
        self._last_recognize_at = 0.0
        self._recognize_interval_sec = 2.0
        self._last_result: dict = {"status": "stopped"}

    def start(self, door_id: str = "door-01", recognition_enabled: bool = False) -> None:
        self._door_id = door_id
        self._recognition_enabled = recognition_enabled
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        if self._capture:
            self._capture.release()
        self._capture = None
        self._last_result = {"status": "stopped"}

    def enable_recognition(self, enabled: bool, door_id: str = "door-01") -> None:
        self._door_id = door_id
        self._recognition_enabled = enabled
        if enabled and not self._running:
            self.start(door_id=door_id, recognition_enabled=True)

    def status(self) -> dict:
        with self._lock:
            result = dict(self._last_result)
        result.update(
            {
                "running": self._running,
                "recognition_enabled": self._recognition_enabled,
                "door_id": self._door_id,
                "camera_index": get_settings().camera_index,
            }
        )
        return result

    def mjpeg_frames(self):
        while True:
            frame = self.get_jpeg()
            if frame is None:
                time.sleep(0.2)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.05)

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def _loop(self) -> None:
        settings = get_settings()
        self._capture = cv2.VideoCapture(settings.camera_index)
        if not self._capture.isOpened():
            logger.error("USB camera could not be opened at index %s", settings.camera_index)
            self._running = False
            self._last_result = {"status": "camera_open_failed"}
            return

        while self._running:
            ok, frame = self._capture.read()
            if not ok:
                self._last_result = {"status": "frame_read_failed"}
                time.sleep(0.2)
                continue

            preview = self._resize_for_preview(frame)
            ok_jpeg, jpeg = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok_jpeg:
                with self._lock:
                    self._latest_frame = frame
                    self._latest_jpeg = jpeg.tobytes()

            if self._recognition_enabled and time.time() - self._last_recognize_at >= self._recognize_interval_sec:
                self._last_recognize_at = time.time()
                self._recognize_frame(frame)

            time.sleep(0.03)

        if self._capture:
            self._capture.release()

    def _recognize_frame(self, frame) -> None:
        db = SessionLocal()
        try:
            setting = get_settings_for_door(db, self._door_id)
            result = face_service.recognize_face(db, frame, setting.face_threshold)
            access = None
            if result.user_id:
                access = asyncio.run(
                    evaluate_access(
                        db,
                        AccessEvent(
                            door_id=self._door_id,
                            method="face",
                            user_id=result.user_id,
                            confidence=result.confidence,
                        ),
                    )
                )
            with self._lock:
                self._last_result = {
                    "status": result.status,
                    "face": asdict(result),
                    "access": access,
                    "recognized_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as exc:
            logger.warning("Live face recognition failed: %s", exc)
            with self._lock:
                self._last_result = {"status": "recognition_error", "error": str(exc)}
        finally:
            db.close()

    @staticmethod
    def _resize_for_preview(frame):
        height, width = frame.shape[:2]
        if width <= 800:
            return frame
        scale = 800 / float(width)
        return cv2.resize(frame, (800, int(height * scale)))


camera_service = CameraService()
