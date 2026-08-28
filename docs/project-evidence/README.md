# สรุปหลักฐานโครงงานระบบติดตามอาการผู้ป่วยและจัดการคิวพยาบาล

อัปเดตล่าสุด: 27 สิงหาคม 2026  
Repository: https://github.com/bfirstkok/Project_hospital_queue  
Commit ที่ใช้ตรวจสอบ: `e324684e5dfaea6e35e08a1fc851ad706c17c395`

เอกสารนี้สรุปข้อมูลที่ตรวจสอบได้จริงจาก repository รายงานการฝึกโมเดล และการทดสอบบน Production VM ห้ามใช้ข้อความในหัวข้อ "ข้อมูลที่ยังขาด" เป็นข้อมูลสมมติในรายงาน

## 1. ไฟล์โครงงาน

ไฟล์สำคัญในโครงการ:

- ชุดข้อมูล: `ai_triage/data/triage_dataset.csv`
- คำอธิบายชุดข้อมูล: `ai_triage/data/DATASET_README.md`
- สคริปต์ฝึกโมเดล: `ai_triage/ml/train_dt.py`
- โมเดลที่บันทึกไว้: `ai_triage/models/triage_dt_v1.pkl`
- ผลประเมินโมเดล: `ai_triage/reports/metrics.txt`
- Confusion Matrix: `ai_triage/reports/confusion_matrix.csv`
- ชุดข้อมูลหลังทำความสะอาด: `ai_triage/reports/cleaned_dataset.csv`
- คู่มือรับข้อมูล IoT: `IOT_API_GUIDE.md`
- โปรแกรมจำลองอุปกรณ์: `scripts/simulate_iot_watch.py`
- ชุดทดสอบ: `ai_triage/tests.py`, `queues/tests.py`, `patients/tests.py`, `patients/test_patient_portal_api.py` และ `opd/tests.py`

ดาวน์โหลด repository เป็น ZIP:

https://github.com/bfirstkok/Project_hospital_queue/archive/refs/heads/main.zip

ก่อนส่ง ZIP ให้บุคคลอื่น ต้องตรวจและนำไฟล์ต่อไปนี้ออก:

- `.env` และไฟล์ที่มี secret
- รหัสผ่านฐานข้อมูล
- API key จริงของอุปกรณ์
- Private key และ SSH key
- Database dump ที่มีข้อมูลผู้ป่วย
- ภาพหน้าจอหรือไฟล์ที่เปิดเผยข้อมูลส่วนบุคคล

## 2. ชุดข้อมูลที่ใช้จริง

ชื่อชุดข้อมูล: **Emergency Service - Triage Application**  
Kaggle: https://www.kaggle.com/datasets/ilkeryildiz/emergency-service-triage-application

ข้อมูลสำคัญ:

- ไม่ใช่ชุดข้อมูล MIMIC-IV
- ไฟล์ที่ใช้ในโครงการคือ `ai_triage/data/triage_dataset.csv`
- ขนาดไฟล์ `128,674` bytes
- SHA-256: `0E2C088E358FD4CDFD0DCC2FD4C2F085AA303856E3A6DF8CEB44A69CEF8ED2DE`
- ใช้ `KTAS_expert` เป็นตัวแปรเป้าหมาย
- แปลง KTAS 1–5 เป็น RED, PINK, YELLOW, GREEN และ WHITE
- จำนวนข้อมูลหลังทำความสะอาด `1,267` แถว
- จำนวนข้อมูลยืนยันโดยพยาบาลจากระบบจริงที่นำมารวมฝึกในรอบนี้ `0` แถว

จำนวนข้อมูลแต่ละระดับ:

| ระดับ | จำนวน | สัดส่วน |
|---|---:|---:|
| RED | 26 | 2.05% |
| PINK | 220 | 17.36% |
| YELLOW | 487 | 38.44% |
| GREEN | 459 | 36.23% |
| WHITE | 75 | 5.92% |

ข้อจำกัดสำคัญคือข้อมูลไม่สมดุล โดยเฉพาะ RED มีเพียง 26 แถว และ WHITE มี 75 แถว

## 3. รายละเอียดโมเดล

แม้ชื่อไฟล์ฝึกจะเป็น `train_dt.py` แต่โมเดลจริงคือ:

`RandomForestClassifier_5Level_RuntimeFeatures_v3`

การตั้งค่าหลัก:

- `n_estimators=600`
- `max_depth=None`
- `max_features="sqrt"`
- `min_samples_leaf=2`
- `random_state=42`
- `class_weight="balanced_subsample"`

ตัวแปรเชิงตัวเลข:

- อายุ
- ระดับความปวด
- RR
- PR
- Systolic BP
- Diastolic BP
- อุณหภูมิ
- SpO2
- Altered mental status
- Mental status

ข้อความอาการสำคัญ `chief_complain` ถูกแปลงด้วย TF-IDF แบบ unigram และ bigram

ตัวแปรที่ตัดออกเพื่อป้องกัน Data Leakage ได้แก่ `KTAS_expert`, `KTAS_RN`, `Error_group`, `mistriage`, `Diagnosis in ED`, `Disposition`, `Length of stay_min` และ `KTAS duration_min`

## 4. การแบ่ง Train/Test

ใช้ `train_test_split` แบบ stratified:

- Train 80% = 1,013 แถว
- Test 20% = 254 แถว
- `test_size=0.2`
- `random_state=42`
- `stratify=y`

จำนวนข้อมูลใน Test set:

| ระดับ | จำนวน |
|---|---:|
| RED | 5 |
| PINK | 44 |
| YELLOW | 98 |
| GREEN | 92 |
| WHITE | 15 |

## 5. ผล Holdout Test

- Accuracy: `61.42%`
- Balanced Accuracy: `53.29%`
- Macro Precision: `47%`
- Macro Recall: `53%`
- Macro F1-score: `47%`
- Weighted Precision: `60%`
- Weighted Recall: `61%`
- Weighted F1-score: `60%`

ผลรายคลาส:

| ระดับ | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| RED | 31% | 80% | 44% | 5 |
| PINK | 52% | 39% | 44% | 44 |
| YELLOW | 64% | 69% | 67% | 98 |
| GREEN | 68% | 72% | 70% | 92 |
| WHITE | 20% | 7% | 10% | 15 |

Confusion Matrix โดยเรียงแถวและคอลัมน์เป็น `WHITE, GREEN, YELLOW, PINK, RED`:

```text
[[ 1,  9,  4,  1, 0],
 [ 2, 66, 19,  4, 1],
 [ 2, 18, 68, 10, 0],
 [ 0,  4, 15, 17, 8],
 [ 0,  0,  0,  1, 4]]
```

## 6. ผล Cross-validation

วิธีประเมิน:

`StratifiedKFold(n_splits=3, shuffle=True, random_state=42)`

ผลรวม Out-of-fold:

- Accuracy: `66.22%`
- Balanced Accuracy: `59.56%`
- Macro Precision: `58%`
- Macro Recall: `60%`
- Macro F1-score: `56.93%`
- Route Accuracy: `72.22%`
- ความถูกต้องเมื่อยอมให้ผิดไม่เกินหนึ่งระดับ: `94.79%`
- เป้าหมาย Exact Accuracy ≥75%: **ยังไม่ผ่าน**

ผลรายคลาส:

| ระดับ | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| RED | 44% | 77% | 56% | 26 |
| PINK | 61% | 54% | 57% | 220 |
| YELLOW | 66% | 73% | 69% | 487 |
| GREEN | 73% | 71% | 72% | 459 |
| WHITE | 44% | 23% | 30% | 75 |

Confusion Matrix โดยเรียงแถวและคอลัมน์เป็น `WHITE, GREEN, YELLOW, PINK, RED`:

```text
[[ 17,  36,  19,   3,  0],
 [ 16, 328,  98,  15,  2],
 [  4,  69, 356,  55,  3],
 [  2,  16,  64, 118, 20],
 [  0,   0,   2,   4, 20]]
```

ผลที่มีอยู่เป็นผล Out-of-fold รวม ยังไม่มีการบันทึก Accuracy, Precision, Recall และ F1 แยกเป็น Fold 1, Fold 2 และ Fold 3

## 7. ข้อสรุปด้านโมเดล

โมเดลยังไม่ผ่านเป้าหมาย Exact Accuracy 75% จึงควรอธิบายว่าเป็นระบบสนับสนุนการตัดสินใจ ไม่ใช่ระบบวินิจฉัยหรือระบบคัดกรองอัตโนมัติขั้นสุดท้าย พยาบาลต้องตรวจสอบข้อมูลและยืนยันระดับเสมอ

สิ่งที่ควรปรับปรุง:

1. เพิ่มจำนวนข้อมูล RED, PINK และ WHITE
2. เก็บข้อมูลยืนยันโดยพยาบาลจากระบบจริงอย่างมีจริยธรรมและผ่านการอนุมัติ
3. แยกชุด External Test ที่ไม่ถูกใช้ระหว่างฝึก
4. บันทึกผลแยกแต่ละ Cross-validation fold
5. ตรวจ Calibration และ Recall ของกลุ่มเร่งด่วน
6. เปรียบเทียบกับโมเดลอื่นโดยใช้ชุดแบ่งข้อมูลเดียวกัน

## 8. ผลทดสอบระบบจริง

ผล Deployment targeted suite:

- ทดสอบ `53` รายการ
- ผ่าน `53` รายการ
- ไม่ผ่าน `0` รายการ
- Database, Web และ Caddy health checks ผ่าน
- API smoke test ผ่าน

ผล Full Django test suite ที่รันบน Production VM วันที่ 27 สิงหาคม 2026:

- ทั้งหมด `68` รายการ
- ผ่าน `53` รายการ
- Error `15` รายการ
- สถานะรวม: **FAIL**

สาเหตุร่วม:

```text
Missing staticfiles manifest entry for 'css/app.css'
```

ข้อผิดพลาดนี้เกิดระหว่าง render template ด้วย staticfiles storage จึงเป็นปัญหาด้าน static manifest หรือการตั้งค่า test ไม่ใช่ assertion ของ business logic ผิด 15 จุด แต่ยังไม่ควรรายงานว่า Full test suite ผ่านทั้งหมดจนกว่าจะแก้และรันใหม่

กลุ่ม Test case ที่ได้รับผลกระทบ:

- การเพิ่มและแก้ไขวันเกิดผู้ป่วย
- หน้าลงทะเบียนของเจ้าหน้าที่
- การป้องกันการลงทะเบียนคิวซ้ำ
- การยืนยันและ Override ระดับโดยพยาบาล
- RED/PINK/YELLOW routing
- การผูกและถอดอุปกรณ์
- แบบฟอร์มประเมินสุขภาพ
- การส่งค่าจากหน้าวัดค่าไป AI และรอพยาบาลยืนยัน
- การกลับจากหน้ายืนยันไปหน้ารอวัดค่า
- Modal ข้อมูลผู้ป่วย

## 9. หลักฐานภาพหน้าจอ

ใน repository ยังไม่มีโฟลเดอร์หลักฐานภาพหน้าจอที่จัดหมวดหมู่อย่างเป็นทางการ ภาพที่ส่งผ่านแชตเป็นไฟล์ชั่วคราวและบางภาพแสดงชื่อหรือข้อมูลผู้ป่วย

ควรสร้างหลักฐานในโครงสร้างต่อไปนี้:

```text
docs/test-evidence/
  TC-001-registration/
  TC-002-vital-signs/
  TC-003-ai-recommendation/
  TC-004-nurse-confirmation/
  TC-005-queue-routing/
  TC-006-iot-telemetry/
```

แต่ละ Test case ควรมี:

- Test case ID
- วันที่ทดสอบ
- ขั้นตอนทดสอบ
- Expected result
- Actual result
- Pass/Fail
- ภาพหน้าจอที่ปกปิดข้อมูลส่วนบุคคล
- ปัญหาที่พบและวิธีแก้

## 10. อุปกรณ์ IoT

สิ่งที่ยืนยันได้จาก repository:

- รับข้อมูลด้วย HTTP POST และ JSON
- ตรวจสอบ `device_id`, `X-API-Key` และ Active pairing
- Endpoint หลัก: `/api/iot/vitals/`
- Simulator ใช้ `/api/iot/telemetry/`
- รองรับ Heart rate, SpO2 และอุณหภูมิ
- รองรับ RR เป็นค่าเสริม โดยไม่รับค่าความดันโลหิตจาก IoT
- เมื่อผูกอุปกรณ์กับ Visit แล้ว ระบบสามารถบันทึก `IoTVital`, `TelemetryLog` และ sync เข้า `VitalSign`
- เมื่ออุปกรณ์ยังไม่ถูกผูก ระบบตอบ `409`
- Credentials ไม่ถูกต้องตอบ `401` หรือ `403`
- ข้อมูลผิดปกติของผู้ป่วยกลุ่มเฝ้าระวังสามารถสร้างเหตุให้พยาบาลประเมินซ้ำ

Automated tests ที่พบครอบคลุม:

- ปฏิเสธข้อมูลจากอุปกรณ์ที่ยังไม่ผูก Visit
- ใช้ Active device assignment โดยไม่ต้องส่ง Visit ID
- ปฏิเสธการผูกอุปกรณ์ก่อนพยาบาลยืนยัน YELLOW
- YELLOW สามารถผูกอุปกรณ์ได้
- RED ต้องออกจาก OPD queue และถอดอุปกรณ์
- WHITE/GREEN ไม่สามารถผูกอุปกรณ์เฝ้าระวังได้
- ข้อมูลผิดปกติจากอุปกรณ์ของ YELLOW ต้องให้พยาบาลประเมินซ้ำ

## 11. ข้อมูล IoT ที่ยังขาด

ยังไม่พบหลักฐานต่อไปนี้ใน repository:

- รุ่นบอร์ดหรือ MCU ที่ใช้จริง
- รุ่นเซนเซอร์วัดชีพจรและ SpO2
- รุ่นเซนเซอร์วัดอุณหภูมิ
- วิธีวัด RR จริง
- Firmware ของอุปกรณ์
- Wiring diagram
- วิธีเชื่อมต่อ Wi-Fi หรือเครือข่ายในอุปกรณ์จริง
- ผลทดสอบความคลาดเคลื่อนเทียบเครื่องมือมาตรฐาน
- Latency และ Packet loss
- จำนวนรอบที่ทดสอบกับฮาร์ดแวร์จริง
- ภาพอุปกรณ์และผลการทดสอบจริง

จนกว่าจะมีข้อมูลดังกล่าว ควรใช้ข้อความว่า:

> ระบบฝั่งเซิร์ฟเวอร์และ API ได้รับการทดสอบด้วยโปรแกรมจำลองและ Automated Test แล้ว ส่วนการทดสอบร่วมกับฮาร์ดแวร์จริงอยู่ระหว่างดำเนินการ

ห้ามระบุชื่อ ESP32, MAX30102 หรือเซนเซอร์รุ่นอื่นเป็นอุปกรณ์ที่ใช้จริง หากยังไม่ได้ยืนยันจากอุปกรณ์ของโครงการ

## 12. สิ่งที่ต้องทำก่อนส่งอาจารย์

1. แก้ Full Django test suite และรันให้ผ่าน 68/68
2. บันทึกผล Cross-validation แยกแต่ละ fold
3. สร้างภาพ Confusion Matrix ที่อ่านง่าย
4. จัดเก็บ Screenshot ที่ปกปิดข้อมูลส่วนบุคคลใน `docs/test-evidence/`
5. เพิ่มข้อมูลฮาร์ดแวร์และ Firmware ที่ใช้จริง
6. ทำตารางทดสอบอุปกรณ์จริงอย่างน้อย 10–30 รอบ
7. อย่าอ้างว่าโมเดลมี Accuracy 75% เพราะผลปัจจุบันยังไม่ถึงเป้าหมาย
8. ระบุชัดว่า AI เป็น Decision Support และพยาบาลเป็นผู้ยืนยันผลสุดท้าย
