from getpass import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.database import SessionLocal, create_all
from app.models.admin import Admin
from app.security import hash_password


def main() -> None:
    create_all()
    username = input("Admin username: ").strip()
    password = getpass("Admin password: ")
    db = SessionLocal()
    try:
        admin = db.scalar(select(Admin).where(Admin.username == username))
        if admin:
            admin.password_hash = hash_password(password)
            admin.active = True
            print("Updated existing admin.")
        else:
            db.add(Admin(username=username, password_hash=hash_password(password), role="superadmin", active=True))
            print("Created admin.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
