from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0033_confirmedtriagecase"),
    ]

    operations = [
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
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
