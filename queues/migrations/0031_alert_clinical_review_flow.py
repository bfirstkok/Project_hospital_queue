from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def move_legacy_reassessment_to_monitoring(apps, schema_editor):
    Queue = apps.get_model("queues", "Queue")
    Queue.objects.filter(status="REASSESSMENT_REQUIRED").update(
        status="OBSERVATION_MONITORING"
    )


def resolve_legacy_non_wearable_alerts(apps, schema_editor):
    CriticalAlert = apps.get_model("queues", "CriticalAlert")
    alerts = CriticalAlert.objects.filter(
        source__in=["triage", "system_test"],
        status="ACKNOWLEDGED",
    )
    for alert in alerts.iterator():
        alert.status = "RESOLVED"
        alert.closed_at = alert.acknowledged_at or alert.created_at
        alert.resolution_note = (
            "Non-wearable alert recorded during direct clinical assessment"
        )
        alert.save(
            update_fields=["status", "closed_at", "resolution_note"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("queues", "0030_alter_visitworkflowlog_event_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            move_legacy_reassessment_to_monitoring,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="queue",
            name="status",
            field=models.CharField(
                choices=[
                    ("WAITING_VITALS", "Waiting vitals"),
                    ("WAITING_CONFIRMATION", "Waiting confirmation"),
                    ("WAITING_QUEUE", "Waiting queue"),
                    ("WAITING", "Waiting"),
                    ("CALLED", "Called"),
                    ("MONITORING", "Post-OPD monitoring"),
                    ("OBSERVATION_MONITORING", "Observation monitoring"),
                    ("EMERGENCY_TRANSFER", "Emergency transfer"),
                    ("OPD_DONE", "OPD Done"),
                    ("FOLLOWUP", "Follow-up"),
                    ("DISCHARGED", "Discharged"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="WAITING_VITALS",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="criticalalert",
            name="status",
            field=models.CharField(
                choices=[
                    ("NEW", "New"),
                    ("ACKNOWLEDGED", "Acknowledged"),
                    ("IN_REVIEW", "In clinical review"),
                    ("ESCALATED", "Escalated"),
                    ("RESOLVED", "Resolved"),
                    ("FALSE_ALARM", "False alarm"),
                ],
                default="NEW",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="review_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="review_started_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_critical_alerts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="escalated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="escalated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="escalated_critical_alerts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="closed_critical_alerts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="criticalalert",
            name="resolution_note",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(
            resolve_legacy_non_wearable_alerts,
            migrations.RunPython.noop,
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
                    ("CRITICAL_ALERT_ESCALATED", "ยกระดับสัญญาณเตือน"),
                    ("CRITICAL_ALERT_RESOLVED", "จัดการสัญญาณเตือนแล้ว"),
                    ("CRITICAL_ALERT_FALSE_ALARM", "ยืนยันสัญญาณเตือนผิดพลาด"),
                    ("DOCTOR_ASSESSMENT", "แพทย์บันทึกผลตรวจ"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
