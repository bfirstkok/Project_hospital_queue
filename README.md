# Hospital Queue & Patient Monitoring System

Web application for patient queue management, nurse triage, AI-assisted severity assessment, IoT vital-sign monitoring, and waiting-time reporting.

The current concept focuses on clinical monitoring and queue prioritization. GPS/map tracking is not part of the core workflow.

## Project Overview

This project is a Django-based hospital queue and patient monitoring system for an educational university project. It supports patient registration, nurse triage, AI-assisted severity recommendation, queue prioritization, OPD room workflow, IoT vital-sign monitoring, and dashboard reporting.

The AI triage component predicts a suggested severity level:

```text
RED     Level 1 / resuscitation / immediate life-saving intervention
PINK    Level 2 / emergency / rapid assessment
YELLOW  Level 3 / urgent observation
GREEN   Level 4 / less urgent OPD
WHITE   Level 5 / non-urgent OPD
```

The AI result is decision support only. The final triage decision still requires nurse confirmation.

## Online and Offline Workflow

The project connects two parts of the patient journey. **Online** covers the website, APIs, cloud processing, AI recommendation, queue status, dashboard, and alerts. **Offline (on-site)** covers identity verification, real vital-sign measurement, nurse assessment, wearable assignment, and medical examination.

| Online: website and system | Offline: hospital service point |
| --- | --- |
| Patient registers in advance and receives a queue number. | Walk-in patients register with staff, while online patients verify their queue number. |
| The system stores patient, visit, symptom, and queue information. | Staff measure HR, SpO2, blood pressure, temperature, respiratory rate, and other clinical information. |
| AI analyzes the available symptoms and vital signs and produces a preliminary severity recommendation. | A nurse reviews the real patient condition together with the AI recommendation and confirms or overrides the result. |
| The dashboard shows queue order, latest vital signs, device status, and alerts. | YELLOW patients may wear a monitoring device while waiting. RED/PINK patients do not wait for device assignment. |
| The system receives wearable telemetry and raises an alert when abnormal data or a fall event is detected. | A nurse reassesses the patient after an alert. The alert is not an automatic clinical diagnosis. |
| Patients can check their current queue status through the patient portal. | A doctor calls the patient, records the examination, and completes the visit. |

Information moves between the two sides as follows:

```text
Online registration -> On-site identity and queue verification
On-site vital signs -> API / database -> AI recommendation
AI recommendation -> Nurse review and final confirmation
YELLOW wearable data -> Dashboard -> Alert -> Nurse reassessment
Queue status -> Patient portal and staff dashboard
```

Target severity flow:

```text
RED     -> Immediate emergency escalation; no normal queue and no wearable wait
PINK    -> Rapid emergency assessment; no normal OPD queue or wearable wait
YELLOW  -> Urgent/observation queue; wearable monitoring may be assigned
GREEN   -> Less urgent OPD queue
WHITE   -> Non-urgent OPD queue
```

AI and wearable alerts are decision-support tools only. A nurse remains responsible for the final triage decision. This educational prototype implements five levels adapted from five-level emergency triage guidance; thresholds are warning features used with symptoms, age, clinical context, and nurse judgment, not universal diagnoses from one value.

Reference used for the five-level workflow: [MOPH ED Triage, Department of Medical Services](https://www.dms.go.th/backend/Content/Content_File/Population_Health/Attach/25621021104459AM_44.pdf?contentId=18326). The project is an educational implementation and is not a substitute for a hospital's approved clinical protocol.

## Core Workflow

1. Staff registers a patient.
2. The visit starts in `WAITING_VITALS`.
3. Nurse opens OPD Triage Assessment and enters symptoms plus vital signs.
4. AI suggests RED, PINK, YELLOW, GREEN, or WHITE severity after required vital signs are complete.
5. The visit moves to `WAITING_CONFIRMATION` so the nurse can confirm or override the AI result.
6. The nurse-confirmed severity controls the next route:
   - RED moves to `EMERGENCY_TRANSFER` for immediate life-saving response.
   - PINK moves to `EMERGENCY_TRANSFER` for rapid emergency assessment.
   - YELLOW moves to the urgent/observation queue and is the only group eligible for wearable pairing.
   - GREEN and WHITE move to the normal `WAITING_QUEUE` and do not receive a wearable.
7. Pairing a wearable to a YELLOW visit moves it to `OBSERVATION_MONITORING`; the existing `MONITORING` state remains reserved for post-OPD monitoring.
8. Abnormal wearable data creates an alert and moves the visit to `REASSESSMENT_REQUIRED`; it does not automatically diagnose or change the nurse-confirmed severity.
9. A nurse reassesses the patient and confirms one of the five levels again.
10. Staff calls eligible waiting patients and selects OPD exam room 1, 2, or 3.
11. OPD staff complete the room assessment, including OPD urgency and follow-up information.
12. Dashboard provides monitoring, alerts, AI evaluation, and waiting-time reports.

## Demo Flow

For presentation, show the clinical workflow in this order:

```text
Patient registration -> Nurse vital signs -> AI suggestion -> Rule guardrail -> Nurse confirmation -> Queue
```

The demo data includes five-level triage cases plus a nurse override example where the AI suggestion differs from the final nurse-confirmed triage level.

## Queue States

```text
WAITING_VITALS          Patient registered, waiting for vital signs
WAITING_CONFIRMATION    AI triage completed, waiting for nurse confirmation
WAITING_QUEUE           Nurse-confirmed YELLOW/GREEN/WHITE visit ready for the next service step
OBSERVATION_MONITORING  YELLOW observation visit with an active wearable
REASSESSMENT_REQUIRED   Wearable alert detected; nurse must reassess
EMERGENCY_TRANSFER      RED/PINK visit sent to emergency care; not in the OPD queue
CALLED                  Sent to an OPD exam room
MONITORING              Active post-OPD monitoring case
OPD_DONE              OPD visit completed
FOLLOWUP              Follow-up required
DISCHARGED            Monitoring case discharged
CANCELLED             Queue cancelled before completion
```

## Severity Logic

Queue priority:

```text
RED    -> priority 1
PINK   -> priority 2
YELLOW -> priority 3
GREEN  -> priority 4
WHITE  -> priority 5
```

The queue is ordered by:

```python
priority, created_at
```

Prototype warning features used by the rule-based fallback (not universal single-value clinical decisions):

- RED: immediate life threat such as `O2Sat < 90`, extreme RR, profound hypotension, unresponsiveness, active seizure, or major bleeding.
- PINK: high-risk symptoms or dangerous vital signs requiring rapid emergency assessment.
- YELLOW: urgent/observation warning features such as moderate vital-sign abnormality, severe pain without emergency evidence, or verified special-risk groups.
- GREEN: less urgent presentation without a warning trigger.
- WHITE: non-urgent presentation distinguished by the five-class model when rule guardrails find no warning trigger.

The Random Forest model is attempted during AI triage, but the final AI recommendation is guarded by rule-based clinical logic in `services.py`. If the model cannot be loaded, the system falls back to the rule-based triage logic. These values are combined with symptoms, risk factors, age, medical context, and nurse judgment; the nurse must always confirm the final route.

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
- Staff-only patient detail modals on both waiting worklists
- OPD exam room selection
- OPD room queue with live refresh API
- OPD assessment and visit detail pages
- Post-OPD Monitoring Zone for active monitoring cases
- Separate waiting-monitor endpoints for pre-OPD monitoring
- IoT telemetry for BPM, SpO2, temperature, and RR
- Device Pairing page
- Personnel page for daily nurse attendance, online status, and wearable-patient responsibility assignment
- Before-After Waiting Time Report
- CSV export
- AI Evaluation page with metrics and confusion matrix
- PostgreSQL support with SQLite fallback
- Demo data seeding command

## Waiting Worklist Patient Details

Authenticated staff can open a patient detail modal directly from each row on:

- `/queues/waiting-vitals/`
- `/queues/waiting-confirmation/`

The waiting-vitals modal shows patient demographics, current symptoms, health
information, and the latest available vital signs without leaving the worklist.
The page remains protected by Django login, and opening the modal does not
create or modify patient records.

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
/queues/personnel/        Staff Attendance and Nurse-Patient Assignment
/queues/devices/pairing/  Device Pairing
/api/iot/telemetry/       IoT Telemetry API
/queues/api/iot/telemetry/ IoT Telemetry API alias
/dashboard/               Dashboard
/dashboard/reports/waiting-time/      Waiting Time Report
/dashboard/reports/waiting-time.csv   Waiting Time CSV Export
/dashboard/ai-evaluation/             AI Evaluation
/patients/register/       Patient Registration
/api/patient/register/    Public patient registration API
/api/patient/login/       Patient portal login API
/api/patient/me/          Bearer-authenticated patient profile API
/api/patient/queue/       Bearer-authenticated latest patient queue API
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
PATIENT_TOKEN_MAX_AGE=43200
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
PATIENT_TOKEN_MAX_AGE=43200
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

- RED/PINK/YELLOW/GREEN/WHITE demo patients
- waiting queues
- a monitoring case
- demo IoT devices
- telemetry logs

## AI Triage Model

The AI triage model is stored at:

```text
ai_triage/models/triage_dt_v1.pkl
```

The model predicts five severity labels. The target label is `KTAS_expert` from the training dataset, mapped one-to-one into the system labels:

```text
KTAS 1 -> RED
KTAS 2 -> PINK
KTAS 3 -> YELLOW
KTAS 4 -> GREEN
KTAS 5 -> WHITE
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
age, nrs_pain, rr, pr, sys_bp, dia_bp, bt, o2sat,
lifesaving_intervention, high_risk_condition,
altered_mental_status, severe_distress,
mental_status (AVPU: alert, verbal, pain, unresponsive)
```

Text feature:

```text
chief_complain
```

Structured categorical feature (available as nurse-confirmed local cases are collected):

```text
expected_resources: 0, 1, 2_PLUS
```

Preprocessing:

- Numeric: `SimpleImputer(strategy="median")`
- Text: `TfidfVectorizer(ngram_range=(1,2), min_df=2, max_features=1000)`

Only features available during production scoring are trained. Age, pain score, and chief complaint are passed from the related patient/visit instead of being silently scored as missing. Rows with a valid expert label are retained when at least one runtime numeric value or chief complaint exists; missing numeric values are imputed by the pipeline.

## Model Performance

The primary evaluation is stratified 3-fold cross-validation because RED remains a small class. Current results are approximately:

- Accuracy: `66.2%`
- Balanced accuracy: `59.6%`
- Macro F1: `56.9%`
- Route accuracy (ER / observation / OPD): `72.2%`
- Within-one-level accuracy: `94.8%`
- Recall: RED `76.9%`, PINK `53.6%`, YELLOW `73.1%`, GREEN `71.5%`, WHITE `22.7%`

Compared with the first five-level training run, balanced accuracy increased from about `44.5%` to `59.6%` and RED recall from `0%` to `76.9%`. This came from retaining valid partially measured encounters, matching training features to production inputs, and recording mental response consistently with the source dataset. The model is still not clinically validated; RED has only 26 labeled rows and WHITE has 75, so nurse confirmation and rule guardrails remain mandatory.

The next acceptance target is exact five-level accuracy `>= 75%` on unseen data. This target is not treated as achieved until a frozen test set reaches it. Accuracy is reported together with per-class recall and the confusion matrix so a majority-class score cannot hide urgent under-triage.

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

After nurses have confirmed cases that contain all structured decision points, export the local examples. The default output is ignored by Git because it contains health information:

```bash
python manage.py export_confirmed_triage
```

The export contains no name, national ID, phone number, address, device key, or API key. Keep the file local and do not share or commit it. The training script combines it with the reference dataset automatically.

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
