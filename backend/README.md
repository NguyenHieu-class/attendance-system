# Backend

Run from this folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python ../scripts/init_db.py
python ../scripts/create_admin.py
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
