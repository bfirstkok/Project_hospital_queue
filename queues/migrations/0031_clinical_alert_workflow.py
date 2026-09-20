from django.db import migrations, models


def migrate_legacy_alert_flow(apps, schema_editor):
    Queue = apps.get_model("queues", "Queue")
    CriticalAlert = apps.get_model("queues", "CriticalAlert")

    # Legacy sensor alerts used to push YELLOW patients back into queue reassessment.
    # The new workflow keeps queue state independent and tracks review on CriticalAlert.
    Queue.objects.filter(status="REASSESSMENT_REQUIRED").update(
        status="OBSERVATION_MONITORING"
    )

    # Historical non-wearable alerts were auto-acknowledged only to suppress them.
    # Mark them closed so they do not reappear as active clinical alerts.
    CriticalAlert.objects.filter(status="ACKNOWLEDGED").exclude(
        source__startswith="iot"
    ).exclude(source="system_test_iot").update(status="RESOLVED")


class Migration(migrations.Migration):

    dependencies = [
        ("queues", "0030_alter_visitworkflowlog_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="criticalalert",
            name="status",
            field=models.CharField(
                choices=[
                    ("NEW", "New"),
                    ("ACKNOWLEDGED", "Acknowledged"),
                    ("IN_REVIEW", "Clinical review"),
                    ("ESCALATED", "Escalated"),
                    ("RESOLVED", "Resolved"),
                    ("FALSE_ALARM", "False alarm"),
                ],
                default="NEW",
                max_length=16,
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
                    ("DOCTOR_ASSESSMENT", "แพทย์บันทึกผลตรวจ"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.RunPython(migrate_legacy_alert_flow, migrations.RunPython.noop),
    ]
