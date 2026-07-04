from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.camera_service import camera_service
from app.services.face_access_service import process_face_access_async

router = APIRouter(prefix="/api/face", tags=["face"])


@router.post("/recognize")
async def recognize(door_id: str = "door-01", file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    import cv2
    import numpy as np

    data = await file.read()
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return await process_face_access_async(db, frame, door_id)


@router.post("/enroll")
async def enroll_stub() -> dict:
    return {"ok": False, "reason": "use admin student face enrollment page"}


@router.get("/camera/stream")
def camera_stream() -> StreamingResponse:
    if not camera_service.status()["running"]:
        camera_service.start()
    return StreamingResponse(camera_service.mjpeg_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/detection/stream")
def detection_stream(door_id: str = "door-01") -> StreamingResponse:
    camera_service.start(door_id=door_id, recognition_enabled=True)
    camera_service.enable_recognition(True, door_id=door_id)
    return StreamingResponse(camera_service.detection_mjpeg_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/camera/status")
def camera_status() -> dict:
    return camera_service.status()
