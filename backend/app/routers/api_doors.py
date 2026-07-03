from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.door import Door

router = APIRouter(prefix="/api/doors", tags=["doors"])


@router.get("")
def list_doors(db: Session = Depends(get_db)) -> list[dict]:
    return [{"door_id": door.door_id, "door_name": door.door_name, "status": door.status} for door in db.scalars(select(Door)).all()]
