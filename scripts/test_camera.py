from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

import cv2

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    cap = cv2.VideoCapture(settings.camera_index)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("Camera frame capture failed.")
    out = settings.data_path / "snapshots" / "camera-test.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    print(f"Camera OK. Saved {out}")


if __name__ == "__main__":
    main()
