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


@dataclass
class DetectedFace:
    user_id: int | None
    full_name: str
    confidence: float
    status: str
    bbox: tuple[int, int, int, int]


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

    def embedding_dimension(self, stored: str) -> int:
        try:
            return len(self.deserialize_embedding(stored))
        except Exception:
            return 0

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
        if a.shape != b.shape:
            return -1.0
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom else 0.0

    def recognize_face(self, db: Session, frame, threshold: float) -> FaceResult:
        embedding = self.get_embedding(frame)
        if embedding is None:
            return FaceResult(None, None, 0.0, "unknown")
        user, score = self._match_embedding(db, embedding)
        if user and score >= threshold:
            return FaceResult(user.id, user.full_name, score, "matched")
        return FaceResult(None, None, score, "unknown")

    def recognize_faces(self, db: Session, frame, threshold: float) -> list[DetectedFace]:
        faces = self.detect_faces(frame)
        results: list[DetectedFace] = []
        for face in faces:
            embedding = face.embedding.astype(float).tolist()
            user, score = self._match_embedding(db, embedding)
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            if user and score >= threshold:
                results.append(DetectedFace(user.id, user.full_name, score, "matched", (x1, y1, x2, y2)))
            else:
                results.append(DetectedFace(None, "unauthorized", max(score, 0.0), "unauthorized", (x1, y1, x2, y2)))
        return results

    def _match_embedding(self, db: Session, embedding: list[float]) -> tuple[User | None, float]:
        best_user: User | None = None
        best_score = 0.0
        profiles = db.scalars(select(FaceProfile)).all()
        for profile in profiles:
            user = db.get(User, profile.user_id)
            if not user or user.status != "active":
                continue
            stored_embedding = self.deserialize_embedding(profile.embedding)
            score = self.compare_embeddings(embedding, stored_embedding)
            if score < 0:
                logger.warning(
                    "Skipping incompatible face profile id=%s user_id=%s dim=%s expected=%s",
                    profile.id,
                    profile.user_id,
                    len(stored_embedding),
                    len(embedding),
                )
                continue
            if score > best_score:
                best_score = score
                best_user = user
        return best_user, best_score


face_service = FaceService()
