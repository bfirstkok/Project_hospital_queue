from django.db import migrations, models


SEVERITY_CHOICES = [
    ("RED", "แดง - วิกฤต"),
    ("PINK", "ชมพู - ฉุกเฉิน"),
    ("YELLOW", "เหลือง - เร่งด่วน"),
    ("GREEN", "เขียว - ไม่เร่งด่วน"),
    ("WHITE", "ขาว - ผู้ป่วยทั่วไป"),
]


def update_queue_priorities(apps, schema_editor):
    Queue = apps.get_model("queues", "Queue")
    priorities = {"RED": 1, "PINK": 2, "YELLOW": 3, "GREEN": 4, "WHITE": 5}
    for severity, priority in priorities.items():
        Queue.objects.filter(visit__final_severity=severity).update(priority=priority)


def restore_three_level_priorities(apps, schema_editor):
    Queue = apps.get_model("queues", "Queue")
    priorities = {"RED": 1, "YELLOW": 2, "GREEN": 3}
    for severity, priority in priorities.items():
        Queue.objects.filter(visit__final_severity=severity).update(priority=priority)


class Migration(migrations.Migration):
    dependencies = [("queues", "0016_triage_flow_statuses")]

    operations = [
        migrations.AlterField(
            model_name="visit",
            name="final_severity",
            field=models.CharField(blank=True, choices=SEVERITY_CHOICES, max_length=10, null=True),
        ),
        migrations.AlterField(
            model_name="triageresult",
            name="ai_severity",
            field=models.CharField(blank=True, choices=SEVERITY_CHOICES, max_length=10, null=True),
        ),
        migrations.AlterField(
            model_name="triageresult",
            name="nurse_severity",
            field=models.CharField(blank=True, choices=SEVERITY_CHOICES, max_length=10, null=True),
        ),
        migrations.AlterField(
            model_name="queue",
            name="priority",
            field=models.IntegerField(default=5),
        ),
        migrations.RunPython(update_queue_priorities, restore_three_level_priorities),
    ]
