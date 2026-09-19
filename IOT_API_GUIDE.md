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
  "respiratory_rate": 31
}
```

หมายเหตุ: `patient_id` เป็น optional field สำหรับตรวจซ้ำกับ active device pairing เท่านั้น หากส่งมา ระบบจะค้นจาก `HN`, `national_id`, หรือ internal patient id และต้องตรงกับผู้ป่วยของ Visit ที่อุปกรณ์ถูกผูกอยู่

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
  -d "{\"device_id\":\"WT001\",\"heart_rate\":118,\"spo2\":94,\"temperature\":38.9,\"respiratory_rate\":31}"
```

ในหน้า Device Management สามารถกดปุ่ม `copy API key` เพื่อคัดลอก API key ของแต่ละ device ได้

## Sync Behavior

- ระบบยึด active `DeviceAssignment` เป็นแหล่งจริงในการหา `Visit` และผู้ป่วยของอุปกรณ์
- ถ้า `device_id` หรือ `X-API-Key` ไม่ถูกต้อง หรือ device ไม่ active ระบบจะปฏิเสธ request
- ถ้า device ยังไม่ได้ผูกกับ Visit ระบบตอบ `409 Device is not paired to an active visit` และไม่บันทึก `TelemetryLog`
- ระบบรับ wearable data เฉพาะ Visit ที่อยู่ในสถานะ monitoring ที่อนุญาต
- เมื่อรับข้อมูลสำเร็จ ระบบสร้าง `TelemetryLog` หนึ่งรายการ แล้ว sync ค่าล่าสุดเข้า `VitalSign`
- หากส่ง `patient_id` มาด้วย ระบบใช้เพื่อตรวจความสอดคล้องกับ active pairing; ถ้าไม่พบผู้ป่วยตอบ `404` และถ้าเป็นผู้ป่วยคนละคนตอบ `409`
- หลัง sync `VitalSign` ระบบตรวจ Critical Alert และเรียก AI/guardrail evaluation ตาม workflow ปัจจุบัน

## Device-only payload update

ตอนนี้ทีม IoT ไม่จำเป็นต้องส่ง `patient_id` แล้ว ให้ส่งแค่ `device_id` พร้อม `X-API-Key` ของอุปกรณ์นั้น ระบบจะใช้ active pairing ในหน้า Device Management เพื่อหา Visit/Patient เอง

- ถ้า device ยังไม่ได้ผูกกับ Visit ระบบตอบ `409 Device is not paired to an active visit` และไม่บันทึกข้อมูล
- ถ้า device ผูกอยู่ ระบบบันทึก `TelemetryLog` และ sync เข้า `VitalSign` ของ Visit ที่ผูกกับ device นั้น
- ถ้ายังส่ง `patient_id` มาด้วย ระบบจะใช้เป็นตัวตรวจซ้ำ ถ้าไม่ตรงกับ pairing จะตอบ `409 Posted patient_id does not match active device assignment`

ระบบรับค่าจากอุปกรณ์เฉพาะชีพจร, SpO2, อุณหภูมิ และอัตราการหายใจ ไม่รับค่าความดันโลหิตจาก IoT; ค่าความดันให้พยาบาลวัดและบันทึกในหน้าประเมินสุขภาพ


## Data persistence

- `TelemetryLog` เป็นแหล่งข้อมูลหลักสำหรับประวัติค่าจาก wearable แบบ time-series และผูกกับ `Visit` และ `Device` ด้วย Foreign Key
- `VitalSign` เก็บค่าหลัก/ค่าล่าสุดของ Visit เพื่อใช้กับ workflow การคัดกรอง
- ตาราง `IoTVital` เดิมถูกถอดออกจาก active Django model เพื่อเลี่ยงการเก็บข้อมูลชุดเดียวกันซ้ำสองตาราง
- migration จะเก็บข้อมูลเดิมไว้โดยเปลี่ยนชื่อตารางเป็น `queues_iotvital_legacy_archive` เพื่อแยกออกจาก active schema โดยไม่ Hard Delete ประวัติเดิม
- response field `id` ของ `/api/iot/vitals/` ยังคงอยู่เพื่อ compatibility แต่มีค่าเดียวกับ `telemetry_log_id`
