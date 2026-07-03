from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, create_all
from app.services.door_service import ensure_default_door


def main() -> None:
    create_all()
    db = SessionLocal()
    try:
        ensure_default_door(db)
    finally:
        db.close()
    print("Database initialized.")


if __name__ == "__main__":
    main()
