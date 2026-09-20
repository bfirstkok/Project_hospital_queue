from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("queues", "0029_alter_visitworkflowlog_event_type"),
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
                    ("DOCTOR_ASSESSMENT", "แพทย์บันทึกผลตรวจ"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
