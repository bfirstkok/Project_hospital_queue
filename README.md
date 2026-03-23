# 🏥 Hospital Queue & Patient Monitoring System

ระบบติดตามอาการผู้ป่วยและจัดการคิวพยาบาล  
พัฒนาเพื่อใช้เป็นโครงงานวิศวกรรมคอมพิวเตอร์  
มหาวิทยาลัยเทคโนโลยีราชมงคลอีสาน วิทยาเขตขอนแก่น

---

## 📌 ภาพรวมโครงงาน
ระบบเว็บแอปพลิเคชันสำหรับโรงพยาบาล/คลินิก  
ช่วยลดเวลารอคอยของผู้ป่วย และเพิ่มประสิทธิภาพการจัดการคิว  
โดยใช้ **AI ช่วยประเมินระดับความรุนแรงของผู้ป่วย (Triage)**

### ระดับความรุนแรง
- 🔴 สีแดง : ฉุกเฉิน  
- 🟡 สีเหลือง : เร่งด่วน  
- 🟢 สีเขียว : ทั่วไป  

---

## ✨ ฟีเจอร์หลัก
- 👤 ระบบลงทะเบียนและจัดการข้อมูลผู้ป่วย
- ⏱️ ระบบจัดการคิวผู้ป่วยแบบเรียลไทม์
- 🧠 AI ประเมินอาการเบื้องต้น (AI Triage)
-  บันทึกเวลารอ เพื่อนำไปวิเคราะห์ Before–After

---

## 🛠️ Tech Stack
### Backend
- Python 3
- Django
- Django REST Framework

### Frontend
- HTML5 / CSS3
- Bootstrap 5
- JavaScript
- Django Template

### Database
- SQLite (Development)
- PostgreSQL (Production – Planned)

### AI / Data Mining
- Pandas / NumPy
- Scikit-learn
- Synthetic Dataset + Medical Triage Rules

---

## 📂 โครงสร้างโปรเจค (โดยย่อ)
hospital_queue/
├── accounts/ # ระบบผู้ใช้งาน
├── patients/ # ข้อมูลผู้ป่วย
├── queues/ # ระบบคิว
├── ai_triage/ # โมเดล AI ประเมินอาการ
├── config/ # Django settings
├── manage.py
├── requirements.txt
└── README.md

🔐 หมายเหตุสำคัญ

ไม่เก็บ db.sqlite3 และไฟล์ .zip ไว้ใน GitHub

ข้อมูลผู้ป่วยที่ใช้ในระบบเป็น ข้อมูลสมมติ (Test Data)

ผลการประเมินจาก AI เป็นเพียงคำแนะนำเบื้องต้น
พยาบาลเป็นผู้ตัดสินใจสุดท้าย

👨‍💻 ผู้พัฒนา

นายคณาธิปกรณ์ นามทะจัก

นายนันทวัฒน์ ร้อยเพีย

สาขาวิศวกรรมคอมพิวเตอร์
มหาวิทยาลัยเทคโนโลยีราชมงคลอีสาน วิทยาเขตขอนแก่น


# 🏥 Hospital Queue & Patient Monitoring System

**Patient Monitoring and Queue Management System**  
This project is developed as a Computer Engineering Capstone Project  
Rajamangala University of Technology Isan, Khon Kaen Campus

---

## 📌 Project Overview
This web-based application is designed for hospitals and clinics to  
**reduce patient waiting time** and **improve queue management efficiency**  
by integrating an **AI-assisted patient triage system**.

The system supports real-time queue tracking and preliminary patient
severity assessment based on symptoms and vital signs.

### Patient Severity Levels
- 🔴 Red : Emergency  
- 🟡 Yellow : Urgent  
- 🟢 Green : General  

---

## ✨ Key Features
- 👤 Patient registration and management system
- ⏱️ Real-time patient queue management
- 🧠 AI-assisted preliminary triage (AI Triage)
-  Waiting-time logging for Before–After analysis

---

## 🛠️ Technology Stack
### Backend
- Python 3
- Django Framework
- Django REST Framework

### Frontend
- HTML5 / CSS3
- Bootstrap 5
- JavaScript
- Django Template Engine

### Database
- SQLite (Development)
- PostgreSQL (Planned for Production)

### AI / Data Mining
- Pandas / NumPy
- Scikit-learn
- Synthetic Dataset with Medical Triage Rules

---

## 📂 Project Structure (Simplified)
hospital_queue/
├── accounts/ # User & authentication module
├── patients/ # Patient data management
├── queues/ # Queue management system
├── ai_triage/ # AI triage model
├── config/ # Django settings
├── manage.py
├── requirements.txt
└── README.md

🔐 Important Notes

db.sqlite3 and .zip files are excluded from the repository

All patient data used in this project are synthetic / test data

AI triage results are decision-support only
Final decisions must be made by medical professionals

👨‍💻 Developers

Mr. Kanathipkorn Namtachak

Mr. Nuntawat Roipia

Department of Computer Engineering
Rajamangala University of Technology Isan, Khon Kaen Campus
