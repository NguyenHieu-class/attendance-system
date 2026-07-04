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

For face recognition, install InsightFace and ONNX Runtime on the Pi, then keep `FACE_MODEL_NAME=buffalo_s` or switch to another InsightFace model. InsightFace is the primary engine. The mock fallback is disabled by default and only runs when `FACE_ALLOW_MOCK=true`.

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

## USB Webcam Live Recognition

Connect the USB webcam to the Raspberry Pi and set `CAMERA_INDEX` in `backend/.env`. Most single-camera setups use:

```env
CAMERA_INDEX=0
```

Test capture first:

```bash
cd attendance-system/backend
source .venv/bin/activate
python ../scripts/test_camera.py
```

Open a user detail page and choose `Face/NFC` to enroll face profiles directly from the USB camera. Capture several directions: front, left, right, look up, and look down. Then open `http://RASPBERRY_PI_IP:8000/admin/camera`, start the camera, and enable live recognition. The backend reads frames from the USB webcam, recognizes faces against enrolled `face_profiles`, applies the selected door access mode, writes logs, and sends unlock commands to ESP32 when allowed.

## Passive Liveness Detection

InsightFace identifies who a face belongs to; it does not by itself prove that the camera is seeing a real live person. This system adds a passive liveness layer before InsightFace recognition. The liveness service can run a MiniFASNet, Silent-Face-Anti-Spoofing, or compatible ONNX anti-spoofing model with ONNX Runtime.

Configure it in `backend/.env`:

```env
LIVENESS_ENABLED=true
LIVENESS_MODEL_PATH=../data/models/anti_spoofing.onnx
LIVENESS_THRESHOLD=0.80
LIVENESS_FAIL_CLOSED=true
```

Place the ONNX model at:

```text
attendance-system/data/models/anti_spoofing.onnx
```

You can also adjust per-door liveness settings in WebUI under `Doors > Settings > Liveness Detection`. Door settings take priority over `.env` threshold and fail-closed behavior.

When liveness is enabled:

- The camera first detects a face.
- The anti-spoofing model checks the face crop.
- If the result is fake, unknown, or the model is unavailable while fail-closed is enabled, face unlock is denied and an access log is written.
- Only live faces continue to InsightFace recognition.

If no ONNX model is present and `LIVENESS_FAIL_CLOSED=true`, face unlock is denied with reason `anti_spoofing_model_not_loaded`. This is intentional for real door deployments. If you need to test without a model, temporarily set `LIVENESS_FAIL_CLOSED=false` or disable liveness in Door Settings.

On Raspberry Pi, if `opencv-python` is difficult to install, use `opencv-python-headless` instead.

For real access control, keep liveness enabled, prefer `face_and_nfc` for important rooms, and avoid relying only on `face_only` until your RGB camera and anti-spoofing model have been tested in your lighting conditions.

## Backup

Stop the backend and copy `backend/attendance.db` to a safe location:

```bash
cp backend/attendance.db backup-attendance-$(date +%F).db
```

## Electrical Safety Notes

Do not power a servo or electric lock directly from the ESP32 3.3V pin. Use a separate regulated supply, common ground, and a relay/MOSFET driver where appropriate. Add flyback protection for inductive locks and test the exit button carefully before mounting the lock.
