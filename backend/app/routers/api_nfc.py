from fastapi import APIRouter

router = APIRouter(prefix="/api/nfc", tags=["nfc"])


@router.get("/health")
def health() -> dict:
    return {"ok": True}
