AfriGaint-backend
=================

Small Django backend for the AfriGaint project (API and admin).

Prerequisites
-------------
- Python 3.10+
- pip
- (Optional) virtualenv or venv

Quick start (Windows PowerShell)
--------------------------------
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Using the included SQLite database
---------------------------------
This repository includes `db.sqlite3` for local development. If you want a fresh DB:
```powershell
del db.sqlite3
python manage.py migrate
```

Running tests
-------------
Run Django tests with:
```powershell
python manage.py test
```
If `pytest` is available you can run `pytest` instead.

Environment and settings
------------------------
- Primary settings: `backend/settings.py`.
- For a lightweight SQLite dump there's `backend/settings_sqlite_dump.py`.
Set any secrets or service credentials using environment variables or a local `.env` loaded by your preferred method.

Deployment notes
----------------
- There's a `render.yaml` included for reference to Render deployments.
- For production, use a WSGI/ASGI server (e.g. `gunicorn` or `uvicorn`), configure static files, and secure secret keys.

Helpful files
-------------
- `manage.py` — development CLI.
- `requirements.txt` — Python dependencies.
- `backend/` — Django project and apps.


