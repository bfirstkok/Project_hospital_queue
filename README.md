# Hospital Queue & Patient Monitoring System

Web application for patient queue management, nurse triage, AI-assisted severity assessment, IoT vital-sign monitoring, and waiting-time reporting.

The current concept focuses on clinical monitoring and queue prioritization. GPS/map tracking is not part of the core workflow.

## Project Overview

This project is a Django-based hospital queue and patient monitoring system for an educational university project. It supports patient registration, nurse triage, AI-assisted severity recommendation, queue prioritization, OPD room workflow, IoT vital-sign monitoring, and dashboard reporting.

The AI triage component predicts a suggested severity level:

```text
RED     Emergency / highest priority
YELLOW  Urgent / medium priority
GREEN   Non-urgent / lower priority
```

The AI result is decision support only. The final triage decision still requires nurse confirmation.

## Core Workflow

1. Staff registers a patient.
2. The visit starts in `WAITING_VITALS`.
3. Nurse opens OPD Triage Assessment and enters symptoms plus vital signs.
4. AI suggests RED, YELLOW, or GREEN severity after required vital signs are complete.
5. The visit moves to `WAITING_CONFIRMATION` so the nurse can confirm or override the AI result.
6. Confirmed visits move to `WAITING_QUEUE`, ordered by severity priority and confirmation time.
7. Staff calls a patient and selects OPD exam room 1, 2, or 3.
8. OPD staff complete the room assessment, including OPD urgency and follow-up information.
9. Cases can finish as `OPD_DONE`, move to `FOLLOWUP`, or be sent to `MONITORING`.
10. Monitoring pages show live vital signs, online/offline status, and clinical alerts.
11. Dashboard provides AI evaluation and waiting-time reports.

## Demo Flow

For presentation, show the clinical workflow in this order:

```text
Patient registration -> Nurse vital signs -> AI suggestion -> Rule guardrail -> Nurse confirmation -> Queue
```

The demo data includes RED, YELLOW, and GREEN cases, plus a nurse override example where the AI suggestion differs from the final nurse-confirmed triage level.

## Queue States

```text
WAITING_VITALS        Patient registered, waiting for vital signs
WAITING_CONFIRMATION  AI triage completed, waiting for nurse confirmation
WAITING_QUEUE         Confirmed and ready to be called
CALLED                Sent to an OPD exam room
MONITORING            Active post-OPD monitoring case
OPD_DONE              OPD visit completed
FOLLOWUP              Follow-up required
DISCHARGED            Monitoring case discharged
CANCELLED             Queue cancelled before completion
```

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

- RED: `O2Sat < 95`, `RR > 30`, `BP ตัวบน < 90`, `BT >= 39`
- YELLOW: `O2Sat 95-96`, `RR 21-30`, `PR/BPM >= 120`, `BT 38-38.9`
- GREEN: no RED/YELLOW trigger

The Random Forest model is attempted during AI triage, but the final AI recommendation is guarded by rule-based clinical logic in `services.py`. If the model cannot be loaded, the system falls back to the rule-based triage logic.

## OPD Urgency Logic

OPD room assessment stores a separate `VisitAssessment` and computes OPD urgency:

```text
RED    Known COPD/Asthma, pain score >= 7, FBS >= 300, K < 3.5, BT >= 39
YELLOW Monk, age >= 80, child under 5
NORMAL No OPD urgency trigger
```

If OPD urgency is RED or YELLOW, the visit severity can be upgraded during OPD assessment.

## Key Features

- Patient registration
- Public patient registration API with private tracking token
- Patient queue-status API that does not expose name, symptoms, or severity
- Auto-generated 6-digit HN
- OPD Triage Assessment
- AI result with confidence and clinical reason
- Nurse confirmation stored separately from AI prediction
- Queue ordered by severity priority and confirmation time
- Waiting-vitals and waiting-confirmation worklists
- OPD exam room selection
- OPD room queue with live refresh API
- OPD assessment and visit detail pages
- Post-OPD Monitoring Zone for active monitoring cases
- Separate waiting-monitor endpoints for pre-OPD monitoring
- IoT telemetry for BPM, SpO2, temperature, RR, and BP
- Device Pairing page
- Before-After Waiting Time Report
- CSV export
- AI Evaluation page with metrics and confusion matrix
- PostgreSQL support with SQLite fallback
- Demo data seeding command

## Main Pages

```text
/                         Login
/queues/                  Confirmed Queue
/queues/waiting-vitals/   Waiting Vitals
/queues/waiting-confirmation/ Waiting Confirmation
/queues/assessment/<id>/  OPD Triage Assessment
/queues/call/<id>/        Select OPD Exam Room
/queues/monitor/          Post-OPD Monitoring Zone
/queues/monitor/waiting/  Waiting Queue Monitor
/queues/devices/pairing/  Device Pairing
/api/iot/telemetry/       IoT Telemetry API
/queues/api/iot/telemetry/ IoT Telemetry API alias
/dashboard/               Dashboard
/dashboard/reports/waiting-time/      Waiting Time Report
/dashboard/reports/waiting-time.csv   Waiting Time CSV Export
/dashboard/ai-evaluation/             AI Evaluation
/patients/register/       Patient Registration
/api/patient/register/    Public patient registration API
/api/patient/queue/<tracking-token>/ Public patient queue status API
/opd/rooms/               OPD Room Selection
/opd/                     OPD Room Queue
/opd/api/list/            OPD Room Queue API
/opd/visit/<id>/assessment/ OPD Assessment
/opd/visit/<id>/detail/   OPD Visit Detail
```

## Tech Stack

- Python 3.13 verified locally
- Django 6.0
- Neon PostgreSQL or Supabase PostgreSQL
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
│       └── cleaned_dataset.csv
├── config/
├── dashboard/
├── opd/
├── patients/
├── queues/
├── scripts/
├── static/
│   ├── css/
│   ├── data/
│   └── images/
├── manage.py
└── requirements.txt
```

## Environment Setup

For a fresh clone on another machine, use the local SQLite fallback first. You only need Python, the requirements, and an `.env` file copied from `.env.example`.

### Quick Start

PowerShell:

```powershell
git clone <your-repo-url>
Set-Location Project_hospital_queue
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

```bash
git clone <your-repo-url>
cd Project_hospital_queue
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py runserver
```

Create `.env` from `.env.example`:

```env
SECRET_KEY=change-me
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
PATIENT_APP_ORIGINS=http://localhost:5500,http://127.0.0.1:5500,https://bfirstkok.github.io
DATABASE_URL=
DB_SSLMODE=require
```

Leave `DATABASE_URL` empty to use SQLite. This is the recommended local setup for a fresh clone.

For Neon PostgreSQL:

```env
DATABASE_URL=postgresql://<user>:<password>@<neon-host>/<database>?sslmode=require&channel_binding=require
DB_SSLMODE=require
```

For Supabase PostgreSQL:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<host>:<port>/postgres
DB_SSLMODE=require
```

Do not commit `.env`.

If you want to use PostgreSQL instead of SQLite, fill in `DATABASE_URL` and keep `DB_SSLMODE=require`.

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once in PowerShell.

## Deploy Publicly on Render

Recommended stack:

```text
Render Web Service + Neon PostgreSQL or Supabase PostgreSQL
```

Before public deployment:

1. Rotate any database password that was shared during setup.
2. Use a new production `SECRET_KEY`.
3. Keep `DEBUG=False`.
4. Do not commit `.env`.

This repository includes `render.yaml` for Render deployment.

Render environment variables:

```env
DEBUG=False
SECRET_KEY=<generated-by-render-or-your-secret>
DATABASE_URL=postgresql://<user>:<password>@<host>/<database>?sslmode=require
DB_SSLMODE=require
ALLOWED_HOSTS=.onrender.com
CSRF_TRUSTED_ORIGINS=https://*.onrender.com
PATIENT_APP_ORIGINS=https://bfirstkok.github.io
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

If using an existing PostgreSQL database that already has data, `seed_demo` and `createsuperuser` are optional.

## How to Run the Project

Create and install the virtual environment:

Windows PowerShell:

```powershell
cd "D:\code\web\Project_hospital_queue"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run database migrations:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

Start the local server:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open:

```text
http://127.0.0.1:8000/
```

If another server is already using port 8000:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Demo account:

```text
username: admin
password: Admin@12345
```

Change the demo password before production or public deployment.

## Seed Demo Data

macOS/Linux:

```bash
.venv/bin/python manage.py seed_demo
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

This creates:

- RED/YELLOW/GREEN demo patients
- waiting queues
- a monitoring case
- demo IoT devices
- telemetry logs

## AI Triage Model

The AI triage model is stored at:

```text
ai_triage/models/triage_dt_v1.pkl
```

The model predicts `RED`, `YELLOW`, or `GREEN` severity. The target label is `KTAS_expert` from the training dataset, mapped into the system severity labels:

```text
KTAS 1-2 -> RED
KTAS 3   -> YELLOW
KTAS 4-5 -> GREEN
```

The model was changed from a Decision Tree to a stronger but still understandable `RandomForestClassifier`. It is trained inside a scikit-learn `Pipeline` with a `ColumnTransformer` for numeric, categorical, and text preprocessing.

## Dataset

The model uses a public emergency triage dataset from Kaggle. The repository includes `ai_triage/data/triage_dataset.csv` for educational project use.

This dataset should not be described as real hospital data from this project or from our own hospital. Users should check the original Kaggle dataset page and license before reuse outside this educational context.

Leakage or post-triage columns are excluded from model features, including:

```text
KTAS_expert, KTAS_RN, Error_group, mistriage, Diagnosis in ED,
Disposition, Length of stay_min, KTAS duration_min
```

`KTAS_expert` is used only as the target label, not as an input feature.

## Features Used

Numeric features:

```text
group, age, patients_number_per_hour, nrs_pain, rr, pr, sys_bp, dia_bp, bt, o2sat
```

Categorical features:

```text
sex, arrival_mode, injury, mental, pain
```

Text feature:

```text
chief_complain
```

Preprocessing:

- Numeric: `SimpleImputer(strategy="median")`
- Categorical: `SimpleImputer(strategy="most_frequent")` + `OneHotEncoder(handle_unknown="ignore")`
- Text: `TfidfVectorizer(ngram_range=(1,2), min_df=2, max_features=1000)`

## Model Performance

The current Random Forest model accuracy is about `72.6%` on the held-out test split.

Previous model accuracy was about `58.4%`, so the Random Forest version improved accuracy by about `14.2 percentage points` while still avoiding data leakage columns.

Model reports are generated at:

```text
ai_triage/reports/metrics.txt
ai_triage/reports/confusion_matrix.csv
ai_triage/reports/cleaned_dataset.csv
```

## Safety Design

- AI triage is decision support only.
- The final severity must be confirmed by a nurse.
- Nurses can confirm or override the AI suggestion.
- The nurse decision is stored separately from the AI suggestion.
- Override notes can be recorded when the nurse changes the AI result.
- `services.py` keeps the rule-based guardrail active around the machine-learning model.
- If the model is unavailable, the system falls back to rule-based triage logic.

## How to Train the Model

Train or regenerate the model:

macOS/Linux:

```bash
.venv/bin/python ai_triage/ml/train_dt.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe ai_triage\ml\train_dt.py
```

Generated outputs:

- `ai_triage/models/triage_dt_v1.pkl`
- `ai_triage/reports/metrics.txt`
- `ai_triage/reports/confusion_matrix.csv`
- `ai_triage/reports/cleaned_dataset.csv`

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
- The included AI dataset is a public Kaggle emergency triage dataset for educational use, not data from this hospital project.
- `.env` contains secrets and must not be committed.
