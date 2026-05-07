# Hospital Queue & Patient Monitoring System

Web application for patient queue management, nurse triage, AI-assisted severity assessment, IoT vital-sign monitoring, and waiting-time reporting.

The current concept focuses on clinical monitoring and queue prioritization. GPS/map tracking is not part of the core workflow.

## Core Workflow

1. Staff registers a patient.
2. Nurse opens OPD Triage Assessment and enters symptoms plus vital signs.
3. AI suggests RED, YELLOW, or GREEN severity.
4. Nurse confirms or overrides the AI result.
5. Queue is ordered by severity priority and registration time.
6. RED patients or patients selected by the nurse can be sent to Monitoring Zone.
7. Monitoring Zone shows live vital signs, online/offline status, and clinical alerts.
8. Dashboard provides AI evaluation and waiting-time reports.

## Severity Logic

Queue priority:

```text
RED    -> priority 1
YELLOW -> priority 2
GREEN  -> priority 3
```

The queue is ordered by:

```python
priority, created_at
```

Rule-based fallback thresholds:

- RED: `O2Sat < 95`, `RR > 30`, `Systolic BP < 90`, `BT >= 39`
- YELLOW: `O2Sat 95-96`, `RR 21-30`, `PR/BPM >= 120`, `BT 38-38.9`
- GREEN: no RED/YELLOW trigger

The Decision Tree model is used first. If the model cannot be loaded, the system falls back to the rule-based triage logic.

## Key Features

- Patient registration
- OPD Triage Assessment
- AI result with confidence and clinical reason
- Nurse confirmation stored separately from AI prediction
- Queue ordered by priority and registration time
- Monitoring Zone for active monitoring cases
- IoT telemetry for BPM, SpO2, temperature, RR, and BP
- Device Pairing page
- Before-After Waiting Time Report
- CSV export
- AI Evaluation page with metrics and confusion matrix
- Supabase PostgreSQL support with SQLite fallback
- Demo data seeding command

## Main Pages

```text
/                         Login
/queues/                  Queue
/queues/assessment/<id>/  OPD Triage Assessment
/queues/monitor/          Monitoring Zone
/queues/devices/pairing/  Device Pairing
/dashboard/               Dashboard
/dashboard/reports/waiting-time/      Waiting Time Report
/dashboard/reports/waiting-time.csv   Waiting Time CSV Export
/dashboard/ai-evaluation/             AI Evaluation
/patients/register/       Patient Registration
/opd/                     OPD List
```

## Tech Stack

- Python 3.12
- Django 6.0
- Supabase PostgreSQL
- SQLite fallback for local development
- scikit-learn
- pandas
- NumPy
- HTML/CSS/JavaScript
- Django templates

## Project Structure

```text
Project_hospital_queue/
├── accounts/
├── ai_triage/
│   ├── ml/
│   │   ├── predictor.py
│   │   └── train_dt.py
│   ├── models/
│   │   └── triage_dt_v1.pkl
│   └── reports/
│       ├── confusion_matrix.csv
│       ├── metrics.txt
│       └── synth_dataset.csv
├── config/
├── dashboard/
├── opd/
├── patients/
├── queues/
├── static/
├── manage.py
└── requirements.txt
```

## Environment Setup

Create `.env` from `.env.example`:

```env
SECRET_KEY=change-me
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
DATABASE_URL=
DB_SSLMODE=require
```

Leave `DATABASE_URL` empty to use SQLite.

For Supabase PostgreSQL:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<host>:<port>/postgres
DB_SSLMODE=require
```

Do not commit `.env`.

## Deploy Publicly on Render

Recommended stack:

```text
Render Web Service + Supabase PostgreSQL
```

Before public deployment:

1. Rotate the Supabase database password because the previous password was shared during setup.
2. Use a new production `SECRET_KEY`.
3. Keep `DEBUG=False`.
4. Do not commit `.env`.

This repository includes `render.yaml` for Render deployment.

Render environment variables:

```env
DEBUG=False
SECRET_KEY=<generated-by-render-or-your-secret>
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<host>:<port>/postgres
DB_SSLMODE=require
ALLOWED_HOSTS=.onrender.com
CSRF_TRUSTED_ORIGINS=https://*.onrender.com
```

Render build command:

```bash
pip install -r requirements.txt && python manage.py collectstatic --no-input
```

Render start command:

```bash
gunicorn config.wsgi:application
```

After the first deploy, run migrations from Render Shell:

```bash
python manage.py migrate
python manage.py seed_demo
python manage.py createsuperuser
```

If using the existing Supabase data, `seed_demo` and `createsuperuser` are optional.

## Run Locally

```powershell
cd "F:\Code\code web\Project_hospital_queue"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

If another server is already using port 8000:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Demo account:

```text
username: admin
password: Admin@12345
```

Change the demo password before production or public deployment.

## Seed Demo Data

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

This creates:

- RED/YELLOW/GREEN demo patients
- waiting queues
- a monitoring case
- demo IoT devices
- telemetry logs

## AI Training

Train or regenerate the model:

```powershell
.\.venv\Scripts\python.exe ai_triage\ml\train_dt.py
```

Generated outputs:

- `ai_triage/models/triage_dt_v1.pkl`
- `ai_triage/reports/metrics.txt`
- `ai_triage/reports/confusion_matrix.csv`
- `ai_triage/reports/synth_dataset.csv`

## Verification

Useful checks:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

## Important Notes

- Patient data is synthetic/test data.
- AI triage is decision support only.
- Final severity must be confirmed by medical staff.
- `.env` contains secrets and must not be committed.
