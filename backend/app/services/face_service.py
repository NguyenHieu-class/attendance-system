import base64
import json
import logging
import pickle
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.face_profile import FaceProfile
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class FaceResult:
    user_id: int | None
    full_name: str | None
    confidence: float
    status: str


class FaceService:
    def __init__(self) -> None:
        self.app = None
        self.model_name = get_settings().face_model_name

    def load(self) -> None:
        try:
            from insightface.app import FaceAnalysis

            self.app = FaceAnalysis(name=self.model_name, providers=["CPUExecutionProvider"])
            self.app.prepare(ctx_id=0, det_size=(640, 640))
            logger.info("InsightFace model loaded: %s", self.model_name)
        except Exception as exc:
            self.app = None
            logger.warning("InsightFace unavailable, using mock face service: %s", exc)

    def detect_faces(self, frame) -> list:
        if self.app is None:
            return []
        return self.app.get(frame)

    def get_embedding(self, frame) -> list[float] | None:
        faces = self.detect_faces(frame)
        if not faces:
            if self.app is None and get_settings().face_allow_mock:
                return self._mock_embedding(frame)
            return None
        return faces[0].embedding.astype(float).tolist()

    def is_mock_mode(self) -> bool:
        return self.app is None and get_settings().face_allow_mock

    def is_ready(self) -> bool:
        return self.app is not None or get_settings().face_allow_mock

    def serialize_embedding(self, embedding: list[float]) -> str:
        array = np.array(embedding, dtype=np.float32)
        return "pickle:" + base64.b64encode(pickle.dumps(array)).decode("ascii")

    def deserialize_embedding(self, stored: str) -> list[float]:
        if stored.startswith("pickle:"):
            raw = base64.b64decode(stored.removeprefix("pickle:"))
            return pickle.loads(raw).astype(float).tolist()
        return json.loads(stored)

    def _mock_embedding(self, frame) -> list[float]:
        # Keeps enrollment/test flows usable on machines without InsightFace.
        resized = frame
        try:
            import cv2

            resized = cv2.resize(frame, (16, 8))
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            values = gray.astype(np.float32).flatten()
        except Exception:
            values = np.zeros(128, dtype=np.float32)
        values = values[:128]
        if values.size < 128:
            values = np.pad(values, (0, 128 - values.size))
        norm = float(np.linalg.norm(values))
        return (values / norm).astype(float).tolist() if norm else values.astype(float).tolist()

    @staticmethod
    def compare_embeddings(first: list[float], second: list[float]) -> float:
        a = np.array(first, dtype=np.float32)
        b = np.array(second, dtype=np.float32)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom else 0.0

    def recognize_face(self, db: Session, frame, threshold: float) -> FaceResult:
        embedding = self.get_embedding(frame)
        if embedding is None:
            return FaceResult(None, None, 0.0, "unknown")
        best_user: User | None = None
        best_score = 0.0
        profiles = db.scalars(select(FaceProfile)).all()
        for profile in profiles:
            user = db.get(User, profile.user_id)
            if not user or user.status != "active":
                continue
            score = self.compare_embeddings(embedding, self.deserialize_embedding(profile.embedding))
            if score > best_score:
                best_score = score
                best_user = user
        if best_user and best_score >= threshold:
            return FaceResult(best_user.id, best_user.full_name, best_score, "matched")
        return FaceResult(None, None, best_score, "unknown")


face_service = FaceService()
