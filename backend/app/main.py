import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import create_all
from app.routers import api_attendance, api_devices, api_doors, api_esp32, api_faces, api_nfc, api_users, web_admin
from app.config import get_settings
from app.services.anti_spoofing_service import anti_spoofing_service
from app.services.door_service import ensure_default_door
from app.services.face_service import face_service
from app.services.camera_service import camera_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Attendance Door Access System")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.state.templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def startup() -> None:
    create_all()
    face_service.load()
    settings = get_settings()
    anti_spoofing_service.configure(settings.liveness_model_path, settings.liveness_threshold, settings.liveness_fail_closed)
    if settings.liveness_enabled:
        anti_spoofing_service.load()
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ensure_default_door(db)
    finally:
        db.close()


@app.on_event("shutdown")
def shutdown() -> None:
    camera_service.stop()


app.include_router(web_admin.router)
app.include_router(api_esp32.router)
app.include_router(api_faces.router)
app.include_router(api_users.router)
app.include_router(api_users.public_router)
app.include_router(api_nfc.router)
app.include_router(api_attendance.router)
app.include_router(api_doors.router)
app.include_router(api_devices.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
