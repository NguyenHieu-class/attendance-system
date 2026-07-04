from dataclasses import asdict

from sqlalchemy.orm import Session

from app.models.access_log import AccessLog
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.anti_spoofing_service import LivenessResult, anti_spoofing_service
from app.services.door_service import get_settings_for_door, notify_door
from app.services.face_service import DetectedFace, face_service


def _log_face_denied(
    db: Session,
    door_id: str,
    reason: str,
    confidence: float | None = None,
    liveness_result: LivenessResult | None = None,
) -> None:
    db.add(
        AccessLog(
            student_id=None,
            door_id=door_id,
            method="face",
            result="denied",
            reason=reason,
            confidence=confidence,
            liveness_score=liveness_result.score if liveness_result else None,
            spoof_result=liveness_result.label if liveness_result else None,
        )
    )
    db.commit()


def process_face_access(db: Session, frame, door_id: str, dispatch_unlock: bool = True) -> dict:
    setting = get_settings_for_door(db, door_id)
    faces = face_service.detect_faces(frame)
    if not faces:
        return {"allowed": False, "reason": "no_face"}

    face = max(faces, key=lambda item: (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]))
    liveness_result = None
    if setting.liveness_enabled:
        anti_spoofing_service.configure(
            anti_spoofing_service.model_path,
            setting.liveness_threshold,
            setting.liveness_fail_closed,
        )
        liveness_result = anti_spoofing_service.check(frame, face.bbox)
        if not liveness_result.is_live:
            _log_face_denied(db, door_id, liveness_result.reason, None, liveness_result)
            return {
                "allowed": False,
                "reason": liveness_result.reason,
                "liveness_score": liveness_result.score,
                "spoof_result": liveness_result.label,
            }

    embedding = face.embedding.astype(float).tolist()
    student, confidence = face_service.match_embedding(db, embedding)
    if not student or confidence < setting.face_threshold:
        _log_face_denied(db, door_id, "unknown_face", confidence, liveness_result)
        return {
            "allowed": False,
            "reason": "unknown_face",
            "confidence": confidence,
            "liveness_score": liveness_result.score if liveness_result else None,
            "spoof_result": liveness_result.label if liveness_result else None,
        }

    access = _run_policy(
        db,
        door_id,
        student.id,
        confidence,
        liveness_result,
        dispatch_unlock,
    )
    access["confidence"] = confidence
    access["liveness_score"] = liveness_result.score if liveness_result else None
    access["spoof_result"] = liveness_result.label if liveness_result else None
    access["reason"] = "face_recognized" if access.get("allowed") else access.get("reason")
    return access


async def process_face_access_async(db: Session, frame, door_id: str, dispatch_unlock: bool = True) -> dict:
    setting = get_settings_for_door(db, door_id)
    faces = face_service.detect_faces(frame)
    if not faces:
        return {"allowed": False, "reason": "no_face", "detections": []}

    if setting.liveness_enabled:
        anti_spoofing_service.configure(
            anti_spoofing_service.model_path,
            setting.liveness_threshold,
            setting.liveness_fail_closed,
        )

    detections: list[DetectedFace] = []
    first_live_match = None
    first_liveness = None
    first_spoof = None
    for face in faces:
        liveness_result = None
        if setting.liveness_enabled:
            liveness_result = anti_spoofing_service.check(frame, face.bbox)
            if not liveness_result.is_live:
                x1, y1, x2, y2 = [int(v) for v in face.bbox]
                detections.append(DetectedFace(None, "spoof", liveness_result.score, liveness_result.label, (x1, y1, x2, y2)))
                if first_spoof is None:
                    first_spoof = liveness_result
                continue
        student, confidence = face_service.match_embedding(db, face.embedding.astype(float).tolist())
        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        if student and confidence >= setting.face_threshold:
            detections.append(DetectedFace(student.id, student.full_name, confidence, "matched", (x1, y1, x2, y2)))
            if first_live_match is None:
                first_live_match = (student, confidence)
                first_liveness = liveness_result
        else:
            detections.append(DetectedFace(None, "unauthorized", max(confidence, 0.0), "unauthorized", (x1, y1, x2, y2)))

    if first_live_match:
        student, confidence = first_live_match
        access = await evaluate_access(
            db,
            AccessEvent(
                door_id=door_id,
                method="face",
                student_id=student.id,
                confidence=confidence,
                liveness_score=first_liveness.score if first_liveness else None,
                spoof_result=first_liveness.label if first_liveness else None,
            ),
            dispatch_unlock=dispatch_unlock,
        )
    else:
        if first_spoof:
            _log_face_denied(db, door_id, first_spoof.reason, None, first_spoof)
            if dispatch_unlock:
                await notify_door(db, door_id, "denied", first_spoof.reason)
            access = {
                "allowed": False,
                "reason": first_spoof.reason,
                "liveness_score": first_spoof.score,
                "spoof_result": first_spoof.label,
            }
        else:
            access = {"allowed": False, "reason": "no_authorized_live_face"}

    return {"allowed": access.get("allowed", False), "reason": access.get("reason"), "access": access, "detections": [asdict(face) for face in detections]}


def _run_policy(
    db: Session,
    door_id: str,
    student_id: int,
    confidence: float,
    liveness_result: LivenessResult | None,
    dispatch_unlock: bool,
) -> dict:
    import asyncio

    return asyncio.run(
        evaluate_access(
            db,
            AccessEvent(
                door_id=door_id,
                method="face",
                student_id=student_id,
                confidence=confidence,
                liveness_score=liveness_result.score if liveness_result else None,
                spoof_result=liveness_result.label if liveness_result else None,
            ),
            dispatch_unlock=dispatch_unlock,
        )
    )
