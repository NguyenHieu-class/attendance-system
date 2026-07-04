import asyncio
import logging
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone

import cv2
import numpy as np

from app.config import get_settings
from app.database import SessionLocal
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.door_service import get_settings_for_door
from app.services.face_access_service import process_face_access_async
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
        self._detection_jpeg: bytes | None = None
        self._detections: list[dict] = []
        self._last_detection_at = 0.0
        self._last_recognize_at = 0.0
        self._recognize_interval_sec = 0.75
        self._last_access_at_by_student: dict[int, float] = {}
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

    def detection_mjpeg_frames(self):
        while True:
            frame = self.get_detection_jpeg()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.05)

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def get_detection_jpeg(self) -> bytes:
        with self._lock:
            if self._detection_jpeg and time.time() - self._last_detection_at <= 2.0:
                return self._detection_jpeg
        return self._black_jpeg()

    def get_frame_copy(self):
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

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
                    self._detection_jpeg = self._draw_cached_detections(preview, frame.shape[:2])

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
            dry_run = False
            result = asyncio.run(process_face_access_async(db, frame, self._door_id, dispatch_unlock=not dry_run))
            detections = result.get("detections", [])
            access = result.get("access")
            first_allowed = next((face for face in detections if face.get("student_id")), None)
            if first_allowed:
                self._last_access_at_by_student[first_allowed["student_id"]] = time.time()
            with self._lock:
                self._detections = detections
                self._last_detection_at = time.time() if detections else 0.0
                self._last_result = {
                    "status": "face_detected" if detections else "no_face",
                    "faces": detections,
                    "access": access,
                    "recognized_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as exc:
            logger.warning("Live face recognition failed: %s", exc)
            with self._lock:
                self._last_result = {"status": "recognition_error", "error": str(exc)}
        finally:
            db.close()

    def _can_send_access(self, student_id: int | None, cooldown_sec: int) -> bool:
        if student_id is None:
            return False
        last = self._last_access_at_by_student.get(student_id, 0.0)
        return time.time() - last >= max(cooldown_sec, 1)

    @staticmethod
    def _resize_for_preview(frame):
        height, width = frame.shape[:2]
        if width <= 800:
            return frame
        scale = 800 / float(width)
        return cv2.resize(frame, (800, int(height * scale)))

    def _draw_cached_detections(self, preview, original_shape: tuple[int, int]) -> bytes | None:
        if not self._detections or time.time() - self._last_detection_at > 2.0:
            return None

        annotated = preview.copy()
        original_h, original_w = original_shape
        preview_h, preview_w = annotated.shape[:2]
        scale_x = preview_w / float(original_w)
        scale_y = preview_h / float(original_h)

        for face in self._detections:
            x1, y1, x2, y2 = face["bbox"]
            x1 = int(x1 * scale_x)
            x2 = int(x2 * scale_x)
            y1 = int(y1 * scale_y)
            y2 = int(y2 * scale_y)
            authorized = face["status"] == "matched"
            color = (0, 220, 0) if authorized else (0, 0, 255)
            label = face["full_name"] if authorized else ("spoof" if face["full_name"] == "spoof" else "unauthorized")
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            text_y = min(y2 + 24, preview_h - 8)
            cv2.putText(annotated, label, (x1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

        ok, jpeg = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return jpeg.tobytes() if ok else None

    @staticmethod
    def _black_jpeg() -> bytes:
        black = np.zeros((480, 800, 3), dtype=np.uint8)
        ok, jpeg = cv2.imencode(".jpg", black, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return jpeg.tobytes() if ok else b""


camera_service = CameraService()
