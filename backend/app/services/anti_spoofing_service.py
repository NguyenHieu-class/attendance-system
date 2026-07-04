import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LivenessResult:
    is_live: bool
    score: float
    label: str
    reason: str


class AntiSpoofingService:
    def __init__(self, model_path: str, threshold: float, fail_closed: bool = True) -> None:
        self.model_path = model_path
        self.threshold = threshold
        self.fail_closed = fail_closed
        self.session = None
        self.input_name: str | None = None
        self.input_size = (224, 224)

    def configure(self, model_path: str, threshold: float, fail_closed: bool) -> None:
        changed = model_path != self.model_path or threshold != self.threshold or fail_closed != self.fail_closed
        self.model_path = model_path
        self.threshold = threshold
        self.fail_closed = fail_closed
        if changed and self.session is None:
            self.load()

    def load(self) -> None:
        path = Path(self.model_path).resolve()
        if not path.exists():
            logger.warning("Anti-spoofing model not found: %s", path)
            self.session = None
            return
        try:
            import onnxruntime as ort

            self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            input_meta = self.session.get_inputs()[0]
            self.input_name = input_meta.name
            shape = input_meta.shape
            if len(shape) == 4:
                height = shape[2] if isinstance(shape[2], int) else shape[1]
                width = shape[3] if isinstance(shape[3], int) else shape[2]
                if isinstance(height, int) and isinstance(width, int):
                    self.input_size = (width, height)
            logger.info("Anti-spoofing ONNX model loaded: %s input_size=%s", path, self.input_size)
        except Exception as exc:
            self.session = None
            logger.error("Failed to load anti-spoofing model %s: %s", path, exc)

    def is_loaded(self) -> bool:
        return self.session is not None and self.input_name is not None

    def preprocess(self, frame, face_bbox) -> np.ndarray | None:
        crop = self._crop_face(frame, face_bbox)
        if crop is None:
            return None
        resized = cv2.resize(crop, self.input_size)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        normalized = rgb.astype(np.float32) / 255.0
        normalized = (normalized - 0.5) / 0.5
        chw = np.transpose(normalized, (2, 0, 1))
        return np.expand_dims(chw, axis=0).astype(np.float32)

    def check(self, frame, face_bbox) -> LivenessResult:
        if not self.is_loaded():
            if self.fail_closed:
                return LivenessResult(False, 0.0, "unknown", "anti_spoofing_model_not_loaded")
            return LivenessResult(True, 1.0, "bypassed", "model_not_loaded_bypass")

        tensor = self.preprocess(frame, face_bbox)
        if tensor is None:
            return LivenessResult(False, 0.0, "unknown", "invalid_face_crop")

        try:
            outputs = self.session.run(None, {self.input_name: tensor})
            live_score = self._extract_live_score(outputs)
        except Exception as exc:
            logger.error("Anti-spoofing inference failed: %s", exc)
            if self.fail_closed:
                return LivenessResult(False, 0.0, "unknown", "anti_spoofing_inference_failed")
            return LivenessResult(True, 1.0, "bypassed", "inference_failed_bypass")

        if live_score >= self.threshold:
            return LivenessResult(True, live_score, "live", "ok")
        return LivenessResult(False, live_score, "fake", "spoof_detected")

    def _crop_face(self, frame, face_bbox):
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in face_bbox]
        box_w = x2 - x1
        box_h = y2 - y1
        pad_x = int(box_w * 0.25)
        pad_y = int(box_h * 0.35)
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(width, x2 + pad_x)
        y2 = min(height, y2 + pad_y)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    @staticmethod
    def _extract_live_score(outputs) -> float:
        values = np.array(outputs[0]).astype(np.float32).reshape(-1)
        if values.size == 0:
            return 0.0
        if values.size == 1:
            return float(np.clip(values[0], 0.0, 1.0))
        exp = np.exp(values - np.max(values))
        probs = exp / np.sum(exp)
        return float(probs[-1])


anti_spoofing_service = AntiSpoofingService("", 0.80, True)
