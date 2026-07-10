# IoT Vital Signs API Guide

เอกสารนี้สำหรับทีม IoT ที่จะส่งค่าสัญญาณชีพเข้า Django ในวง Wi-Fi/LAN เดียวกัน

## Server

รัน Django ให้รับ connection จากเครื่องอื่นในวง LAN:

```bash
python manage.py runserver 0.0.0.0:8000
```

URL ที่ทีม IoT ต้องยิง:

```text
http://<IP-เครื่องที่รัน Django>:8000/api/iot/vitals/
```

ตัวอย่าง IP ที่ตรวจพบจากเครื่องนี้:

```text
http://172.24.155.96:8000/api/iot/vitals/
```

ถ้า IoT อยู่ Wi-Fi เดียวกัน ให้ใช้ IPv4 ของ Wi-Fi adapter จากคำสั่ง `ipconfig` แทน IP ด้านบนเมื่อจำเป็น

## ALLOWED_HOSTS

ในโหมด dev (`DEBUG=True`) โปรเจคนี้รองรับ host พื้นฐานเหล่านี้อัตโนมัติ:

```text
127.0.0.1, localhost, 0.0.0.0, <IP หลักของเครื่อง>
```

ถ้าต้องการกำหนด IP เอง ให้เพิ่มในไฟล์ `.env`:

```env
ALLOWED_HOSTS=127.0.0.1,localhost,192.168.x.x
```

## Request

Method: `POST`

Headers:

```text
Content-Type: application/json
X-API-Key: <API KEY ของ device>
```

Required fields:

```text
device_id, heart_rate, spo2, temperature
```

Body ตัวอย่าง:

```json
{
  "device_id": "WT001",
  "heart_rate": 118,
  "spo2": 94,
  "temperature": 38.9,
  "respiratory_rate": 31,
  "blood_pressure_sys": 90,
  "blood_pressure_dia": 60
}
```

หมายเหตุ: `patient_id` ต้องตรงกับผู้ป่วยที่มีอยู่ในระบบ โดยระบบจะค้นจาก `HN`, `national_id`, หรือ internal patient id

## Responses

สำเร็จ `200`:

```json
{
  "success": true,
  "message": "Vital signs received successfully"
}
```

ไม่มี `X-API-Key` `401`:

```json
{
  "success": false,
  "message": "Missing X-API-Key"
}
```

API Key ไม่ตรงกับ `device_id` `403`:

```json
{
  "success": false,
  "message": "Invalid device credentials"
}
```

Device ไม่ active `403`:

```json
{
  "success": false,
  "message": "Device is not active"
}
```

ข้อมูลจำเป็นไม่ครบ `400`:

```json
{
  "success": false,
  "message": "Missing required fields"
}
```

ไม่พบผู้ป่วย `404`:

```json
{
  "success": false,
  "message": "Patient not found"
}
```

## curl Example

ตัวอย่างสำหรับ device `WT001` โดยใช้ IP เครื่องจริง ไม่ใช่ `127.0.0.1`:

```bash
curl -X POST http://172.24.155.96:8000/api/iot/vitals/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY_OF_WT001>" \
  -d "{\"device_id\":\"WT001\",\"heart_rate\":118,\"spo2\":94,\"temperature\":38.9,\"respiratory_rate\":31,\"blood_pressure_sys\":90,\"blood_pressure_dia\":60}"
```

ในหน้า Device Management สามารถกดปุ่ม `copy API key` เพื่อคัดลอก API key ของแต่ละ device ได้

## Sync Behavior

- ถ้า `device_id` และ `X-API-Key` ถูกต้อง และ device active ระบบจะตรวจ `patient_id`
- ถ้าไม่พบ `patient_id` ระบบตอบ `404 Patient not found` และไม่บันทึก `IoTVital`
- ถ้าพบ `patient_id` ระบบบันทึก `IoTVital`
- ถ้าผู้ป่วยมี visit ล่าสุดที่ยังไม่จบ ระบบ sync ค่าเข้า `VitalSign` ของ visit นั้น และเรียก AI/guardrail evaluation ต่อ
- ถ้าผู้ป่วยไม่มี visit ที่ยังไม่จบ ระบบยังบันทึก `IoTVital` ไว้ แต่ไม่ sync เข้า `VitalSign`

## Device-only payload update

ตอนนี้ทีม IoT ไม่จำเป็นต้องส่ง `patient_id` แล้ว ให้ส่งแค่ `device_id` พร้อม `X-API-Key` ของอุปกรณ์นั้น ระบบจะใช้ active pairing ในหน้า Device Management เพื่อหา Visit/Patient เอง

- ถ้า device ยังไม่ได้ผูกกับ Visit ระบบตอบ `409 Device is not paired to an active visit` และไม่บันทึกข้อมูล
- ถ้า device ผูกอยู่ ระบบบันทึก `IoTVital`, `TelemetryLog` และ sync เข้า `VitalSign` ของ Visit ที่ผูกกับ device นั้น
- ถ้ายังส่ง `patient_id` มาด้วย ระบบจะใช้เป็นตัวตรวจซ้ำ ถ้าไม่ตรงกับ pairing จะตอบ `409 Posted patient_id does not match active device assignment`
