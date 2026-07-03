from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.access_policy_service import AccessEvent, evaluate_access
from app.services.door_service import get_settings_for_door

router = APIRouter(prefix="/api/face", tags=["face"])


@router.post("/recognize")
async def recognize(door_id: str = "door-01", file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    import cv2
    import numpy as np

    from app.services.face_service import face_service

    data = await file.read()
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    setting = get_settings_for_door(db, door_id)
    result = face_service.recognize_face(db, frame, setting.face_threshold)
    access = await evaluate_access(db, AccessEvent(door_id=door_id, method="face", user_id=result.user_id, confidence=result.confidence))
    return {"face": result.__dict__, "access": access}


@router.post("/enroll")
async def enroll_stub() -> dict:
    return {"ok": False, "reason": "use admin user face enrollment skeleton; camera capture is planned after MVP"}
