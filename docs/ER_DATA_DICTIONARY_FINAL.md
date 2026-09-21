# ER Diagram และ Data Dictionary ฉบับ Final

เอกสารนี้อ้างอิงโครงสร้างโมเดลที่ใช้งานจริงหลังการปรับฐานข้อมูล, Audit Trail และ PostgreSQL native ENUM ถึง migration `queues.0032`, `patients.0012` และ `opd.0008`

> หลักการออกแบบหลัก: **Patient = บุคคล**, **Visit = การมารับบริการแต่ละครั้ง** และข้อมูลการรักษา/คัดกรอง/คิว/IoT/ผลตรวจต้องอ้างอิง Visit เพื่อไม่ให้ข้อมูลคนละ encounter ปะปนกัน

## 1. ER Diagram

```mermaid
erDiagram
    AUTH_USER ||--o| STAFF_PROFILE : has
    AUTH_USER ||--o{ STAFF_DUTY : attends
    AUTH_USER ||--o{ SHIFT_SCHEDULE : scheduled
    AUTH_USER ||--o{ ACCOUNT_STATUS_LOG : status_history
    AUTH_USER ||--o{ VISIT_WORKFLOW_LOG : acts
    AUTH_USER ||--o{ NURSE_CARE_ASSIGNMENT : nurse
    AUTH_USER ||--o{ CRITICAL_ALERT : acknowledges
    AUTH_USER ||--o{ VISIT_ASSESSMENT : examines

    PATIENT ||--o{ VISIT : receives
    PATIENT ||--o{ APPOINTMENT : has
    PATIENT ||--o{ PATIENT_ACCESS_TOKEN : owns
    PATIENT ||--o| PATIENT_PIN : authenticates

    VISIT ||--o| VITAL_SIGN : current_vitals
    VISIT ||--o| TRIAGE_RESULT : triage
    VISIT ||--o| QUEUE : queue_state
    VISIT ||--o| VISIT_ASSESSMENT : opd_assessment
    VISIT ||--o{ VISIT_WORKFLOW_LOG : audit
    VISIT ||--o{ NURSE_CARE_ASSIGNMENT : care_history
    VISIT ||--o{ DEVICE_ASSIGNMENT : device_history
    VISIT ||--o{ TELEMETRY_LOG : telemetry
    VISIT ||--o{ CRITICAL_ALERT : alerts

    DEVICE ||--o{ DEVICE_ASSIGNMENT : pairing_history
    DEVICE ||--o{ TELEMETRY_LOG : produces

    PATIENT {
      bigint id PK
      varchar hn UK
      varchar national_id UK
      varchar first_name
      varchar last_name
    }
    VISIT {
      bigint id PK
      bigint patient_id FK
      uuid tracking_token UK
      triage_severity_enum final_severity
      datetime registered_at
    }
    VITAL_SIGN {
      bigint id PK
      bigint visit_id FK_UK
      int rr
      int pr
      int sys_bp
      int dia_bp
      float bt
      int o2sat
    }
    TRIAGE_RESULT {
      bigint id PK
      bigint visit_id FK_UK
      triage_severity_enum ai_severity
      triage_severity_enum nurse_severity
      float confidence
    }
    QUEUE {
      bigint id PK
      bigint visit_id FK_UK
      queue_status_enum status
      int priority
      int exam_room
    }
    VISIT_ASSESSMENT {
      bigint id PK
      bigint visit_id FK_UK
      bigint examiner_id FK
      text diagnosis
      text treatment
      opd_urgency_enum opd_urgency
    }
    NURSE_CARE_ASSIGNMENT {
      bigint id PK
      bigint visit_id FK
      bigint nurse_id FK
      bigint assigned_by_id FK
      bool is_active
      datetime assigned_at
      datetime ended_at
    }
    DEVICE {
      bigint id PK
      varchar device_id UK
      varchar api_key
      bool is_active
      datetime last_seen
    }
    DEVICE_ASSIGNMENT {
      bigint id PK
      bigint device_id FK
      bigint visit_id FK
      bool is_active
      datetime paired_at
      datetime unpaired_at
    }
    TELEMETRY_LOG {
      bigint id PK
      bigint visit_id FK
      bigint device_id FK
      datetime ts
      int bpm
      int o2sat
      float bt
      int rr
    }
    CRITICAL_ALERT {
      bigint id PK
      bigint visit_id FK
      varchar alert_type
      critical_alert_status_enum status
      bigint acknowledged_by_id FK
      datetime acknowledged_at
    }
    VISIT_WORKFLOW_LOG {
      bigint id PK
      bigint visit_id FK
      bigint actor_id FK
      varchar event_type
      varchar actor_name
      varchar actor_role
      json details
      datetime created_at
    }
    STAFF_PROFILE {
      bigint id PK
      bigint user_id FK_UK
      varchar role
      varchar photo
    }
    STAFF_DUTY {
      bigint id PK
      bigint user_id FK
      date duty_date
      bool is_present
      bool is_available
    }
    SHIFT_SCHEDULE {
      bigint id PK
      bigint user_id FK
      bigint created_by_id FK
      date shift_date
      time start_time
      time end_time
      shift_schedule_status_enum status
    }
    ACCOUNT_STATUS_LOG {
      bigint id PK
      bigint user_id FK
      bigint actor_id FK
      varchar action
      text reason
      datetime created_at
    }
    APPOINTMENT {
      bigint id PK
      bigint patient_id FK
      date date
      time time
      appointment_status_enum status
    }
    PATIENT_ACCESS_TOKEN {
      bigint id PK
      bigint patient_id FK
      varchar token_hash UK
      datetime expires_at
    }
    PATIENT_PIN {
      bigint id PK
      bigint patient_id FK_UK
      varchar pin_hash
      int failed_attempts
    }

```

## 2. ความสัมพันธ์หลัก

| ความสัมพันธ์ | Cardinality | ความหมาย |
|---|---|---|
| Patient → Visit | 1:N | ผู้ป่วยหนึ่งคนมารับบริการได้หลายครั้ง |
| Visit → VitalSign | 1:0..1 | ค่าหลัก/ค่าล่าสุดของ Visit |
| Visit → TriageResult | 1:0..1 | ผลประเมิน AI และผลยืนยันจากพยาบาล |
| Visit → Queue | 1:0..1 | สถานะคิวของ encounter |
| Visit → VisitAssessment | 1:0..1 | ผลตรวจ OPD ของแพทย์ในครั้งนั้น |
| Visit → TelemetryLog | 1:N | ประวัติข้อมูลจาก wearable ตามเวลา |
| Visit → CriticalAlert | 1:N | สัญญาณเตือนวิกฤตที่เกิดขึ้น |
| Visit → NurseCareAssignment | 1:N | ประวัติการมอบหมาย/ส่งต่อพยาบาล |
| Visit → VisitWorkflowLog | 1:N | Audit Trail ของการกระทำใน Visit |
| Device → DeviceAssignment | 1:N | ประวัติการผูกอุปกรณ์ |
| Visit → DeviceAssignment | 1:N | ประวัติอุปกรณ์ที่เคยผูกกับ Visit |
| User → StaffProfile | 1:0..1 | Role ของบุคลากร |
| User → StaffDuty | 1:N | สถานะเข้าเวรจริงรายวัน |
| User → ShiftSchedule | 1:N | ตารางเวรที่วางแผน |
| User → AccountStatusLog | 1:N | ประวัติ Suspend/Activate |

---

# 2.1 PostgreSQL Native ENUM ที่บังคับใช้ในฐานข้อมูล

ฟิลด์ workflow ที่มีค่าจำกัดและค่อนข้างคงที่ถูกเปลี่ยนจาก VARCHAR เป็น PostgreSQL native ENUM เพื่อป้องกันค่าพิมพ์ผิดจาก SQL โดยตรง และยังใช้ Django TextChoices เป็นชุดค่าฝั่ง application

| ENUM type | ใช้กับ Field | ค่าที่อนุญาต |
|---|---|---|
| appointment_status_enum | patients_appointment.status | SCHEDULED, ATTENDED, MISSED, CANCELLED |
| triage_severity_enum | queues_visit.final_severity, queues_triageresult.ai_severity, queues_triageresult.nurse_severity, queues_criticalalert.severity | RED, PINK, YELLOW, GREEN, WHITE |
| queue_status_enum | queues_queue.status | WAITING_VITALS, WAITING_CONFIRMATION, WAITING_QUEUE, WAITING, CALLED, MONITORING, OBSERVATION_MONITORING, REASSESSMENT_REQUIRED, EMERGENCY_TRANSFER, OPD_DONE, FOLLOWUP, DISCHARGED, CANCELLED |
| critical_alert_status_enum | queues_criticalalert.status | NEW, ACKNOWLEDGED, IN_REVIEW, ESCALATED, RESOLVED, FALSE_ALARM |
| shift_schedule_status_enum | queues_shiftschedule.status | SCHEDULED, LEAVE, CANCELLED |
| opd_urgency_enum | opd_visitassessment.opd_urgency | RED, YELLOW, NORMAL |

> หมายเหตุ: SQLite ที่ใช้ทดสอบในเครื่องจะ fallback เป็น VARCHAR เพื่อให้ test suite ทำงานได้เหมือนเดิม ส่วน production PostgreSQL จะใช้ ENUM จริง

# 3. Data Dictionary

## 3.1 patients_patient — Patient

ข้อมูลบุคคลหลักของผู้ป่วย ไม่ใช่ข้อมูลของการรักษาแต่ละครั้ง

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | รหัสผู้ป่วยภายใน |
| first_name | varchar(100) | NOT NULL | ชื่อ |
| last_name | varchar(100) | NOT NULL | นามสกุล |
| national_id | varchar(13) | UNIQUE | เลขบัตรประชาชน |
| gender | varchar(10) | NOT NULL | M/F/O/UNKNOWN |
| birth_date | date | NULL | วันเกิด |
| nationality | varchar(60) | NOT NULL | สัญชาติ |
| age | positive int | NULL | อายุปีที่ cache ไว้ |
| phone | varchar(20) | NOT NULL | โทรศัพท์ |
| email | email | NULL | อีเมล |
| hn | varchar(6) | UNIQUE, INDEX | Hospital Number |
| address_area | varchar(20) | NOT NULL | โซนที่อยู่ |
| address | text | NOT NULL | ที่อยู่ |
| blood_type | varchar(10) | NOT NULL | กรุ๊ปเลือด |
| primary_doctor_id | bigint | FK auth_user, NULL | แพทย์หลักของผู้ป่วย ถ้ามี |
| responsible_nurse_id | bigint | FK auth_user, NULL | ข้อมูลพยาบาลระดับ Patient ถ้ามี; การรับผิดชอบเชิง workflow ต่อ Visit ใช้ NurseCareAssignment |
| chronic_diseases | text | NOT NULL | โรคประจำตัว |
| allergies | text | NOT NULL | ประวัติแพ้ |
| medications | text | NOT NULL | ยาประจำ |
| height_cm | decimal(5,2) | NULL | ส่วนสูง |
| weight_kg | decimal(5,2) | NULL | น้ำหนัก |
| bp_sys | positive int | NULL | ความดันตัวบนระดับข้อมูลผู้ป่วย ถ้ามี |
| bp_dia | positive int | NULL | ความดันตัวล่างระดับข้อมูลผู้ป่วย ถ้ามี |
| emergency_name | varchar(120) | NOT NULL | ผู้ติดต่อฉุกเฉิน |
| emergency_relationship | varchar(20) | NOT NULL | ความสัมพันธ์ |
| emergency_phone | varchar(20) | NOT NULL | โทรศัพท์ฉุกเฉิน |
| note | text | NOT NULL | หมายเหตุ |
| province | varchar(100) | NOT NULL | จังหวัด |
| district | varchar(100) | NOT NULL | อำเภอ |
| subdistrict | varchar(100) | NOT NULL | ตำบล |
| postal_code | varchar(5) | NOT NULL | รหัสไปรษณีย์ |

## 3.2 queues_visit — Visit

ศูนย์กลางของ Patient Data Flow หนึ่งแถวแทนการมารับบริการหนึ่งครั้ง

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Visit ID |
| patient_id | bigint | FK Patient | ผู้ป่วย |
| tracking_token | UUID | UNIQUE, INDEX | Token สำหรับติดตาม Visit |
| registered_at | datetime | NOT NULL | เวลาเริ่มรับบริการ |
| triaged_at | datetime | NULL | เวลาประเมินคัดกรอง |
| confirmed_at | datetime | NULL | เวลาพยาบาลยืนยันผล |
| called_at | datetime | NULL | เวลาเรียกเข้าห้องตรวจ |
| final_severity | triage_severity_enum | NULL | RED/PINK/YELLOW/GREEN/WHITE |
| note | text | NULL | อาการ/หมายเหตุของ Visit |
| lat | decimal(9,6) | NULL | Latitude |
| lng | decimal(9,6) | NULL | Longitude |
| location_updated_at | datetime | NULL | เวลาอัปเดตตำแหน่ง |

## 3.3 queues_vitalsign — VitalSign

Snapshot ของสัญญาณชีพหลัก/ค่าล่าสุดใน Visit

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | รหัสรายการ |
| visit_id | bigint | FK + UNIQUE | Visit |
| rr | int | NULL | Respiratory Rate |
| pr | int | NULL | Pulse Rate |
| sys_bp | int | NULL | Systolic BP |
| dia_bp | int | NULL | Diastolic BP |
| bt | float | NULL | Body Temperature |
| o2sat | int | NULL | Oxygen Saturation |
| pain_score | positive small int | NULL | Pain score |
| urgent_symptoms | JSON | NOT NULL | อาการเร่งด่วน |
| risk_flags | JSON | NOT NULL | ปัจจัยเสี่ยง |
| updated_at | datetime | NOT NULL | เวลาอัปเดตล่าสุด |

## 3.4 queues_triageresult — TriageResult

ผลแนะนำจาก AI และผลยืนยันจากพยาบาล

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | รหัสผลคัดกรอง |
| visit_id | bigint | FK + UNIQUE | Visit |
| ai_severity | triage_severity_enum | NULL | ระดับที่ AI แนะนำ |
| nurse_severity | triage_severity_enum | NULL | ระดับที่พยาบาลยืนยัน |
| model_name | varchar(50) | NULL | ชื่อโมเดล |
| confidence | float | NULL | Confidence |
| ai_reason | text | NOT NULL | เหตุผลของ AI/Rule |
| nurse_note | text | NOT NULL | หมายเหตุพยาบาล |
| lifesaving_intervention | boolean | NULL | ต้องช่วยชีวิตทันทีหรือไม่ |
| high_risk_condition | boolean | NULL | High-risk condition |
| altered_mental_status | boolean | NULL | ระดับความรู้สึกตัวผิดปกติ |
| mental_status | varchar(20) | NULL | ALERT/VERBAL/PAIN/UNRESPONSIVE |
| severe_distress | boolean | NULL | Severe distress |
| expected_resources | varchar(10) | NULL | 0/1/2_PLUS |
| created_at | datetime | NOT NULL | เวลาสร้างผล |
| lat | decimal(9,6) | NULL | ตำแหน่งตอนคัดกรอง |
| lng | decimal(9,6) | NULL | ตำแหน่งตอนคัดกรอง |
| location_updated_at | datetime | NULL | เวลาอัปเดตตำแหน่ง |

## 3.5 queues_queue — Queue

สถานะการเดินทางของ Visit ในระบบคิว

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | รหัสคิว |
| visit_id | bigint | FK + UNIQUE | Visit |
| status | queue_status_enum | NOT NULL | สถานะ workflow |
| priority | int | NOT NULL | Priority |
| exam_room | positive small int | NULL | ห้องตรวจ |
| manual_sequence | positive int | NULL | เลขคิวที่กำหนดเอง |
| is_expedited | boolean | INDEX | ลัดลำดับภายใน severity เดิม |
| created_at | datetime | NOT NULL | เวลาสร้างคิว |

สถานะหลัก: WAITING_VITALS, WAITING_CONFIRMATION, WAITING_QUEUE, CALLED, OBSERVATION_MONITORING, REASSESSMENT_REQUIRED, MONITORING, EMERGENCY_TRANSFER, OPD_DONE, FOLLOWUP, DISCHARGED, CANCELLED

## 3.6 opd_visitassessment — VisitAssessment

**โมเดล Assessment หลักและเพียงตัวเดียวที่ใช้งานเชิงคลินิก** ผูกกับ Visit เพื่อให้ผลตรวจแต่ละครั้งไม่ปะปนกัน

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | รหัสผลตรวจ |
| visit_id | bigint | FK + UNIQUE | Visit |
| examiner_id | bigint | FK auth_user, NULL, PROTECT | แพทย์ผู้ตรวจ |
| created_at | datetime | NOT NULL | เวลาสร้าง |
| updated_at | datetime | NOT NULL | เวลาแก้ไขล่าสุด |
| known_copd_asthma | boolean | NOT NULL | COPD/Asthma |
| pain_score | positive small int | NULL | Pain score |
| bt | decimal(4,1) | NULL | อุณหภูมิ |
| sys_bp | positive small int | NULL | Systolic BP |
| dia_bp | positive small int | NULL | Diastolic BP |
| fbs | positive small int | NULL | FBS |
| lab_k | decimal(4,2) | NULL | Potassium |
| lab_mg | decimal(4,2) | NULL | Magnesium |
| lab_hct | decimal(5,2) | NULL | Hematocrit |
| anxious_family | boolean | NOT NULL | ครอบครัวกังวล |
| non_toxic_bite | boolean | NOT NULL | Non-toxic bite |
| very_fatigue | boolean | NOT NULL | อ่อนเพลียมาก |
| blood_receive | boolean | NOT NULL | รับเลือด |
| monk | boolean | NOT NULL | พระภิกษุ |
| age | positive small int | NULL | อายุที่ใช้ใน OPD rule |
| child_under_5 | boolean | NOT NULL | เด็ก < 5 ปี |
| pregnant | boolean | NOT NULL | ตั้งครรภ์ |
| ga_weeks | positive small int | NULL | อายุครรภ์สัปดาห์ |
| epilepsy | boolean | NOT NULL | Epilepsy |
| pulmonary_tb_mplus | boolean | NOT NULL | Pulmonary TB M+ |
| low_immunity | boolean | NOT NULL | ภูมิคุ้มกันต่ำ |
| low_immunity_detail | varchar(255) | NOT NULL | รายละเอียด |
| chief_complaint | text | NOT NULL | Chief complaint |
| diagnosis | text | NOT NULL | Diagnosis |
| treatment | text | NOT NULL | Treatment |
| next_appointment_at | datetime | NULL | วันนัด |
| next_appointment_note | varchar(255) | NOT NULL | หมายเหตุนัด |
| followup_visit_id | bigint | FK Visit, NULL | Visit ที่สร้างเป็น follow-up |
| opd_urgency | opd_urgency_enum | NOT NULL | RED/YELLOW/NORMAL |
| opd_reason | text | NOT NULL | เหตุผล urgency |

## 3.7 queues_device — Device

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | รหัสอุปกรณ์ภายใน |
| device_id | varchar(50) | UNIQUE | รหัสอุปกรณ์ |
| api_key | varchar(100) | NOT NULL | API key |
| is_active | boolean | NOT NULL | เปิดใช้งาน |
| last_seen | datetime | NULL | ติดต่อระบบล่าสุด |

## 3.8 queues_deviceassignment — DeviceAssignment

เก็บประวัติการ Pair/Unpair ไม่เขียนทับประวัติเดิม

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Assignment ID |
| device_id | bigint | FK Device | อุปกรณ์ |
| visit_id | bigint | FK Visit | Visit |
| paired_at | datetime | NOT NULL | เวลา Pair |
| unpaired_at | datetime | NULL | เวลา Unpair |
| is_active | boolean | NOT NULL | Pair ปัจจุบันหรือไม่ |

Constraint: Device หนึ่งตัวมี active assignment ได้หนึ่งรายการ และ Visit หนึ่งรายการมี active device ได้หนึ่งตัว

## 3.9 queues_telemetrylog — TelemetryLog

Canonical time-series ของ wearable/IoT

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Telemetry ID |
| visit_id | bigint | FK Visit | Visit |
| device_id | bigint | FK Device, NULL | อุปกรณ์ต้นทาง |
| ts | datetime | NOT NULL | เวลา measurement |
| bpm | int | NULL | Heart rate |
| o2sat | int | NULL | SpO2 |
| bt | float | NULL | Temperature |
| rr | int | NULL | Respiratory rate |
| sys_bp | int | NULL | Systolic BP |
| dia_bp | int | NULL | Diastolic BP |
| lat | decimal(9,6) | NULL | Latitude |
| lng | decimal(9,6) | NULL | Longitude |
| created_at | datetime | NOT NULL | เวลา record เข้า DB |

## 3.10 queues_criticalalert — CriticalAlert

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Alert ID |
| visit_id | bigint | FK Visit | Visit |
| alert_type | varchar(24) | NOT NULL | LOW_O2, LOW_BP, HIGH_RR, HIGH_HEART_RATE, LOW_HEART_RATE, HIGH_TEMPERATURE |
| severity | triage_severity_enum | NOT NULL | Severity ของ Alert |
| message | varchar(255) | NOT NULL | ข้อความ |
| value | float | NULL | ค่าที่ทำให้ Trigger |
| threshold | varchar(50) | NOT NULL | Threshold |
| source | varchar(32) | NOT NULL | แหล่งข้อมูล |
| status | critical_alert_status_enum | NOT NULL | NEW/ACKNOWLEDGED/IN_REVIEW/ESCALATED/RESOLVED/FALSE_ALARM |
| created_at | datetime | NOT NULL | เวลาสร้าง |
| acknowledged_at | datetime | NULL | เวลารับทราบ |
| acknowledged_by_id | bigint | FK auth_user, NULL | ผู้รับทราบ |

## 3.11 queues_nursecareassignment — NurseCareAssignment

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Assignment ID |
| nurse_id | bigint | FK auth_user, PROTECT | พยาบาลผู้รับผิดชอบ |
| visit_id | bigint | FK Visit | Visit |
| assigned_by_id | bigint | FK auth_user, NULL | ผู้สั่งมอบหมาย |
| assigned_at | datetime | NOT NULL | เวลาเริ่ม |
| ended_at | datetime | NULL | เวลาสิ้นสุด |
| is_active | boolean | NOT NULL | Assignment ปัจจุบัน |

Constraint: แต่ละ Visit มี active nurse ได้เพียงหนึ่งคนในเวลาเดียวกัน แต่มีหลาย row เพื่อเก็บประวัติการส่งต่อ

## 3.12 queues_visitworkflowlog — VisitWorkflowLog

Audit Trail หลักของ workflow ผู้ป่วย

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Log ID |
| visit_id | bigint | FK Visit | Visit |
| event_type | varchar(32) | INDEX | ประเภทเหตุการณ์ |
| actor_id | bigint | FK auth_user, NULL | ผู้กระทำ; NULL = ระบบ |
| actor_name | varchar(180) | NOT NULL | Snapshot ชื่อผู้กระทำ |
| actor_role | varchar(120) | NOT NULL | Snapshot Role |
| description | text | NOT NULL | รายละเอียด |
| details | JSON | NOT NULL | metadata ของ Event |
| created_at | datetime | INDEX | เวลาเกิดเหตุการณ์ |

Event ปัจจุบัน: VITALS_RECORDED, TRIAGE_CONFIRMED, QUEUE_EXPEDITED, QUEUE_RESTORED, QUEUE_NUMBER_CHANGED, QUEUE_CALLED, QUEUE_TRANSFERRED, NURSE_ASSIGNED, NURSE_REASSIGNED, NURSE_ASSIGNMENT_ENDED, CRITICAL_ALERT_CREATED, CRITICAL_ALERT_ACKNOWLEDGED, DOCTOR_ASSESSMENT

## 3.13 queues_staffprofile — StaffProfile

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Profile ID |
| user_id | bigint | FK + UNIQUE auth_user, PROTECT | บัญชีผู้ใช้ |
| role | varchar(24) | NOT NULL | DOCTOR/NURSE/NURSE_ASSISTANT/EMERGENCY/STAFF/QUEUE_OPERATOR/BIOMEDICAL |
| photo | file path | NOT NULL/blank | รูปบุคลากร |

## 3.14 queues_staffduty — StaffDuty

สถานะปฏิบัติงานจริง ไม่ใช่แผนเวร

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Duty ID |
| user_id | bigint | FK auth_user | บุคลากร |
| duty_date | date | NOT NULL | วันที่ |
| is_present | boolean | NOT NULL | เข้าเวรจริง |
| is_available | boolean | NOT NULL | พร้อมรับเคส |
| checked_in_at | datetime | NOT NULL | เวลาเข้า |
| checked_out_at | datetime | NULL | เวลาออก |
| last_seen_at | datetime | NULL | Active ล่าสุด |

Constraint: user + duty_date ต้องไม่ซ้ำ

## 3.15 queues_shiftschedule — ShiftSchedule

ตารางเวรที่วางแผนไว้

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Schedule ID |
| user_id | bigint | FK auth_user | บุคลากร |
| shift_date | date | NOT NULL | วันที่เวร |
| start_time | time | NOT NULL | เริ่ม |
| end_time | time | NOT NULL | สิ้นสุด |
| status | shift_schedule_status_enum | NOT NULL | SCHEDULED/LEAVE/CANCELLED |
| note | varchar(200) | NOT NULL | หมายเหตุ/ห้องตรวจ |
| created_by_id | bigint | FK auth_user, NULL | ผู้จัดเวร |
| created_at | datetime | NOT NULL | เวลาสร้าง |
| updated_at | datetime | NOT NULL | เวลาแก้ไข |

Constraint: user + shift_date + start_time ต้องไม่ซ้ำ

## 3.16 accounts_accountstatuslog — AccountStatusLog

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Log ID |
| user_id | bigint | FK auth_user, PROTECT | บัญชีที่ถูกเปลี่ยนสถานะ |
| action | varchar(16) | INDEX | SUSPEND/ACTIVATE |
| reason | text | NOT NULL | เหตุผล |
| actor_id | bigint | FK auth_user, NULL | ผู้ดำเนินการ |
| created_at | datetime | INDEX | เวลา |

สถานะปัจจุบันของบัญชีใช้ `auth_user.is_active`; ตารางนี้เก็บประวัติ ไม่ใช้แทน current state

## 3.17 patients_appointment — Appointment

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Appointment ID |
| patient_id | bigint | FK Patient | ผู้ป่วย |
| date | date | NOT NULL | วันที่นัด |
| time | time | NULL | เวลา |
| status | appointment_status_enum | NOT NULL | SCHEDULED/ATTENDED/MISSED/CANCELLED |
| note | varchar(255) | NOT NULL | หมายเหตุ |
| attended_at | datetime | NULL | เวลามาตามนัด |
| created_at | datetime | NOT NULL | เวลาสร้าง |
| updated_at | datetime | NOT NULL | เวลาแก้ไข |

## 3.18 patients_patientaccesstoken — PatientAccessToken

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | Token row ID |
| patient_id | bigint | FK Patient | ผู้ป่วย |
| token_hash | varchar(64) | UNIQUE, INDEX | Hash ของ bearer token |
| expires_at | datetime | INDEX | หมดอายุ |
| created_at | datetime | NOT NULL | สร้าง |
| last_used_at | datetime | NULL | ใช้ล่าสุด |

## 3.19 patients_patientpin — PatientPin

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | PIN row ID |
| patient_id | bigint | FK + UNIQUE | ผู้ป่วย |
| pin_hash | varchar(128) | NOT NULL | Password hash ของ PIN |
| failed_attempts | positive small int | NOT NULL | จำนวนครั้งผิด |
| locked_until | datetime | NULL | Lock จนถึง |
| lockout_level | positive small int | NOT NULL | ระดับ Lock |
| updated_at | datetime | NOT NULL | อัปเดตล่าสุด |

## 3.20 patients_otpchallenge — OtpChallenge

| Field | Type | Key / Null | ความหมาย |
|---|---|---|---|
| id | BigAutoField | PK | OTP ID |
| national_id | varchar(13) | INDEX | เลขบัตรที่ใช้ตรวจสอบ |
| channel | varchar(10) | NOT NULL | email/phone |
| purpose | varchar(24) | NOT NULL | PIN_RESET |
| code_hash | varchar(128) | NOT NULL | Hash OTP |
| expires_at | datetime | INDEX | หมดอายุ |
| consumed_at | datetime | NULL | ถูกใช้เมื่อไร |
| attempts | positive small int | NOT NULL | จำนวนครั้งลอง |
| created_at | datetime | NOT NULL | เวลาสร้าง |

---

# 4. ตารางที่เลิกเป็น Active Model

## patients_assessment_legacy_archive

เดิมคือ `Patient.Assessment` ผูก assessment กับ Patient โดยตรง มีเพียง detail/assessor/time ซึ่งทับซ้อนกับ clinical assessment และไม่แยก encounter ชัดเจน จึงถอดออกจาก Active Django Model และเก็บ physical table เดิมเป็น legacy archive เพื่อไม่ Hard Delete ประวัติ

**ตัวที่ใช้จริงแทน:** `opd_visitassessment` ซึ่งผูกกับ `Visit`

## queues_iotvital_legacy_archive

เดิมเก็บข้อมูล IoT ซ้ำกับ TelemetryLog จึงถอดออกจาก Active Model และเก็บเป็น legacy archive

**ตัวที่ใช้จริงแทน:** `queues_telemetrylog` สำหรับ time-series และ `queues_vitalsign` สำหรับ latest snapshot

---

# 5. ตารางสำหรับ System Test

`system_test_testscenariorun` เป็น registry ของข้อมูลจำลองสำหรับทดสอบระบบ ไม่ใช่ข้อมูล clinical หลัก จึงไม่ควรรวมอยู่กลาง ER สำหรับการพรีเซนต์ Patient Data Flow

Fields หลัก: scenario, patient_id, visit_id, device_id, created_by_id, created_at

---

# 6. ประโยคใช้ตอบกรรมการ

> โครงสร้างฐานข้อมูลออกแบบตาม Data Flow ของผู้ป่วย โดย Patient เป็นข้อมูลบุคคล และเมื่อเข้ารับบริการแต่ละครั้งจะสร้าง Visit เป็นศูนย์กลางของ encounter จากนั้น VitalSign, TriageResult, Queue, TelemetryLog, CriticalAlert และ VisitAssessment จะอ้างอิง Visit เดียวกัน ทำให้ข้อมูลแต่ละครั้งไม่ปะปน และ VisitWorkflowLog ใช้ตรวจสอบย้อนหลังว่าใครทำอะไร เมื่อไร

เมื่อถามเรื่อง Assessment:

> ระบบใช้ VisitAssessment เป็นผลตรวจ OPD หลักเพียงโมเดลเดียว เพราะผลตรวจต้องเป็นของการมารับบริการแต่ละครั้ง ไม่ใช่ของตัว Patient แบบรวมทุกครั้ง ส่วน Patient.Assessment เดิมถูก retire และเก็บตารางเดิมเป็น legacy archive เพื่อไม่ลบประวัติ

เมื่อถาม ShiftSchedule กับ StaffDuty:

> ShiftSchedule คือแผนเวร ส่วน StaffDuty คือสถานะการปฏิบัติงานจริง เช่น check-in, check-out และ availability

เมื่อถาม AI:

> AI เก็บคำแนะนำใน TriageResult.ai_severity แต่ผลสุดท้ายที่ใช้กับ workflow คือ Visit.final_severity หลังบุคลากรยืนยัน จึงเป็น decision support ไม่ใช่การตัดสินใจแทนบุคลากร
