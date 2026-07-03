from fastapi import APIRouter

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("/health")
def health() -> dict:
    return {"ok": True}
