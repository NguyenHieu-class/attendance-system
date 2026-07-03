from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def list_users(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {"id": user.id, "employee_code": user.employee_code, "full_name": user.full_name, "status": user.status}
        for user in db.scalars(select(User).order_by(User.id.desc())).all()
    ]
