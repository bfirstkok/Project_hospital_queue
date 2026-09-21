from django.db import migrations, models
import django.utils.timezone


def backfill_portal_fields(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    PatientAccessToken = apps.get_model("patients", "PatientAccessToken")

    for patient in Patient.objects.all().iterator():
        raw = "".join(ch for ch in str(patient.phone or "") if ch.isdigit() or ch == "+")
        if raw.startswith("+66"):
            normalized = "0" + raw[3:]
        elif raw.startswith("66") and len(raw) == 11:
            normalized = "0" + raw[2:]
        else:
            normalized = raw

        contacts = []
        if patient.emergency_name or patient.emergency_phone:
            contacts.append({
                "id": "primary",
                "name": patient.emergency_name or "",
                "relationship": patient.emergency_relationship or "",
                "phone": patient.emergency_phone or "",
            })

        Patient.objects.filter(pk=patient.pk).update(
            phone_normalized=normalized,
            emergency_contacts=contacts,
        )

    # Existing opaque tokens pre-date token_version; they belong to version 1.
    PatientAccessToken.objects.all().update(token_version=1)


class Migration(migrations.Migration):
    dependencies = [
        ("patients", "0010_retire_patient_assessment"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="username",
            field=models.CharField(blank=True, max_length=50, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="patient",
            name="password_hash",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="patient",
            name="token_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="patient",
            name="google_id",
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="patient",
            name="email_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="patient",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="patient",
            name="phone_normalized",
            field=models.CharField(blank=True, db_index=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="patient",
            name="emergency_contacts",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="patient",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="patient",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="patientaccesstoken",
            name="token_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="otpchallenge",
            name="target",
            field=models.CharField(blank=True, default="", max_length=191),
        ),
        migrations.AddField(
            model_name="otpchallenge",
            name="reset_token_hash",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="otpchallenge",
            name="reset_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="otpchallenge",
            name="purpose",
            field=models.CharField(
                choices=[("PIN_RESET", "PIN reset"), ("PASSWORD_RESET", "Password reset")],
                default="PIN_RESET",
                max_length=24,
            ),
        ),
        migrations.RunPython(backfill_portal_fields, migrations.RunPython.noop),
    ]
