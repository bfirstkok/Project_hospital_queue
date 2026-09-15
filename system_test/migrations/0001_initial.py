import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("patients", "0009_patient_email_pin_otp"),
        ("queues", "0021_staffprofile_photo"),
    ]

    operations = [
        migrations.CreateModel(
            name="TestScenarioRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scenario", models.CharField(choices=[("FULL", "ครบกระบวนการและติดตามด้วยนาฬิกา"), ("WAITING", "ผู้ป่วยใหม่รอวัดสัญญาณชีพ"), ("EMERGENCY", "ผู้ป่วยฉุกเฉินส่งต่อทันที")], max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("device", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="queues.device")),
                ("patient", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="patients.patient")),
                ("visit", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="queues.visit")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
