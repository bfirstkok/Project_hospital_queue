from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0034_alter_visitworkflowlog_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("DOCTOR", "แพทย์"),
                    ("NURSE", "พยาบาลวิชาชีพ (คัดกรอง/เฝ้าระวัง)"),
                    ("NURSE_ASSISTANT", "ผู้ช่วยพยาบาล (วัดสัญญาณชีพ)"),
                    ("EMERGENCY", "เจ้าหน้าที่ฉุกเฉิน"),
                    ("STAFF", "เจ้าหน้าที่เวชระเบียน"),
                    ("QUEUE_OPERATOR", "เจ้าหน้าที่จัดคิว"),
                    ("BIOMEDICAL", "เจ้าหน้าที่เครื่องมือแพทย์"),
                    ("PHARMACIST", "เภสัชกร"),
                    ("CASHIER", "เจ้าหน้าที่การเงิน"),
                ],
                default="STAFF",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="visitworkflowlog",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("VITALS_RECORDED", "บันทึกสัญญาณชีพ"),
                    ("TRIAGE_CONFIRMED", "ยืนยันผลคัดกรอง"),
                    ("QUEUE_EXPEDITED", "ลัดลำดับคิว"),
                    ("QUEUE_RESTORED", "คืนลำดับคิวปกติ"),
                    ("QUEUE_NUMBER_CHANGED", "เปลี่ยนเลขคิว"),
                    ("QUEUE_CALLED", "เรียกเข้าห้องตรวจ"),
                    ("QUEUE_TRANSFERRED", "ย้ายผู้ป่วย"),
                    ("NURSE_ASSIGNED", "มอบหมายพยาบาล"),
                    ("NURSE_REASSIGNED", "เปลี่ยนพยาบาลผู้รับผิดชอบ"),
                    ("NURSE_ASSIGNMENT_ENDED", "สิ้นสุดการมอบหมายพยาบาล"),
                    ("CRITICAL_ALERT_CREATED", "สร้างสัญญาณเตือนวิกฤต"),
                    ("CRITICAL_ALERT_ACKNOWLEDGED", "รับทราบสัญญาณเตือนวิกฤต"),
                    ("CRITICAL_ALERT_REVIEW_STARTED", "เริ่มตรวจผู้ป่วยจากสัญญาณเตือน"),
                    ("CRITICAL_ALERT_ESCALATED", "ยกระดับการดูแลจากสัญญาณเตือน"),
                    ("CRITICAL_ALERT_RESOLVED", "ปิดสัญญาณเตือนและกลับไปเฝ้าระวัง"),
                    ("CRITICAL_ALERT_FALSE_ALARM", "ปิดสัญญาณเตือนเป็น false alarm"),
                    ("EMERGENCY_ACCEPTED", "เจ้าหน้าที่ฉุกเฉินรับเคส"),
                    ("EMERGENCY_DISCHARGED", "สิ้นสุดการรักษาฉุกเฉิน"),
                    ("EMERGENCY_REFERRED", "ส่งต่อผู้ป่วยฉุกเฉิน"),
                    ("DOCTOR_ASSESSMENT", "แพทย์บันทึกผลตรวจ"),
                    ("PRESCRIPTION_CREATED", "สร้าง/แก้ไขใบสั่งยา"),
                    ("PHARMACY_STATUS_CHANGED", "เปลี่ยนสถานะงานห้องยา"),
                    ("BILL_CREATED", "สร้างรายการค่าใช้จ่าย"),
                    ("PAYMENT_RECEIVED", "รับชำระเงิน"),
                    ("MEDICAL_CERTIFICATE_ISSUED", "ออกใบรับรองแพทย์"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
