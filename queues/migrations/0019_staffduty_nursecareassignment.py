import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("queues", "0018_triage_structured_decision_points"),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffDuty",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("duty_date", models.DateField(default=django.utils.timezone.localdate)),
                ("is_present", models.BooleanField(default=True)),
                ("checked_in_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("checked_out_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staff_duties", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["user__first_name", "user__username"],
                "indexes": [
                    models.Index(fields=["duty_date", "is_present"], name="queues_staf_duty_da_5d2318_idx"),
                    models.Index(fields=["last_seen_at"], name="queues_staf_last_se_5118aa_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "duty_date"), name="unique_staff_duty_per_day"),
                ],
            },
        ),
        migrations.CreateModel(
            name="NurseCareAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("assigned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_patient_care_assignments", to=settings.AUTH_USER_MODEL)),
                ("nurse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="patient_care_assignments", to=settings.AUTH_USER_MODEL)),
                ("visit", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nurse_care_assignments", to="queues.visit")),
            ],
            options={
                "ordering": ["-assigned_at"],
                "indexes": [
                    models.Index(fields=["nurse", "is_active"], name="queues_nurs_nurse_i_e9dfeb_idx"),
                    models.Index(fields=["visit", "is_active"], name="queues_nurs_visit_i_e1b7ab_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("is_active", True)), fields=("visit",), name="unique_active_nurse_per_visit"),
                ],
            },
        ),
    ]
