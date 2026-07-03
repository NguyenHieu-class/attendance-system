# Attendance Door Access System

FastAPI + SQLite + ESP32-S3 MVP for local attendance, NFC access, face-recognition integration, and door unlock control.

## Architecture

Raspberry Pi 5 runs the FastAPI backend, WebUI, SQLite database, USB camera pipeline, and access policy engine. ESP32-S3 runs the door controller, reads NFC/button events, exposes `/unlock`, and reports events back to the Pi over LAN HTTP.

## Raspberry Pi Setup

```bash
cd attendance-system/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python ../scripts/init_db.py
python ../scripts/create_admin.py
./run.sh
```

Open `http://RASPBERRY_PI_IP:8000/login` from another device on the same LAN.

For real face recognition, install InsightFace and ONNX Runtime on the Pi, then keep `FACE_MODEL_NAME=buffalo_s` or switch to another InsightFace model. The backend runs with a mock fallback when InsightFace is unavailable.

## MVP Features

- Admin login/logout.
- Dashboard with user, attendance, allowed, denied, and door status counts.
- User create/edit/disable/delete-or-soft-delete.
- Door settings for access mode, unlock duration, face threshold, and physical button.
- ESP32 heartbeat, config, NFC scan, button event, and door status APIs.
- Door unlock command from backend to ESP32.
- Access logs for allowed/denied decisions.
- CSV exports for attendance and access logs.
- Face service interface with InsightFace-ready implementation and mock fallback.

## ESP32 Flash

Edit `esp32-door-controller/src/main.cpp`:

- `WIFI_SSID`
- `WIFI_PASSWORD`
- `PI_BASE_URL`
- `DOOR_ID`
- `DEVICE_API_KEY`
- `SERVO_PIN`, `BUTTON_PIN`, and NFC pins

Then flash:

```bash
cd attendance-system/esp32-door-controller
pio run -t upload
pio device monitor
```

Set `ESP32_SHARED_SECRET` in `backend/.env` to the same value as `DEVICE_API_KEY`.

## Basic Workflow

1. Create an admin with `python ../scripts/create_admin.py`.
2. Login to WebUI.
3. Create users.
4. Open a user Face/NFC screen and press `Start NFC enrollment`.
5. Scan the next NFC card on ESP32.
6. Change door mode under Doors > Settings.
7. Use `Unlock Door` for admin remote unlock.

## Backup

Stop the backend and copy `backend/attendance.db` to a safe location:

```bash
cp backend/attendance.db backup-attendance-$(date +%F).db
```

## Electrical Safety Notes

Do not power a servo or electric lock directly from the ESP32 3.3V pin. Use a separate regulated supply, common ground, and a relay/MOSFET driver where appropriate. Add flyback protection for inductive locks and test the exit button carefully before mounting the lock.
