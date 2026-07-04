from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.student import Student

router = APIRouter(prefix="/api/students", tags=["students"])
public_router = APIRouter(prefix="/students", tags=["students"])


class StudentCreate(BaseModel):
    student_code: str
    full_name: str
    class_name: str | None = None
    faculty: str | None = None
    major: str | None = None
    email: str | None = None
    phone: str | None = None


@router.get("")
def list_students(db: Session = Depends(get_db)) -> list[dict]:
    return _list_students(db)


@public_router.get("")
def list_students_public(db: Session = Depends(get_db)) -> list[dict]:
    return _list_students(db)


def _list_students(db: Session) -> list[dict]:
    return [
        {
            "id": student.id,
            "student_code": student.student_code,
            "full_name": student.full_name,
            "class_name": student.class_name,
            "faculty": student.faculty,
            "major": student.major,
            "email": student.email,
            "phone": student.phone,
            "status": student.status,
        }
        for student in db.scalars(select(Student).order_by(Student.id.desc())).all()
    ]


@router.post("")
def create_student(payload: StudentCreate, db: Session = Depends(get_db)) -> dict:
    return _create_student(payload, db)


@public_router.post("")
def create_student_public(payload: StudentCreate, db: Session = Depends(get_db)) -> dict:
    return _create_student(payload, db)


def _create_student(payload: StudentCreate, db: Session) -> dict:
    student = Student(**payload.model_dump(), status="active", is_active=True)
    db.add(student)
    db.commit()
    db.refresh(student)
    return {
        "id": student.id,
        "student_code": student.student_code,
        "full_name": student.full_name,
        "class_name": student.class_name,
        "faculty": student.faculty,
        "major": student.major,
        "status": student.status,
    }
