from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("patients", "0008_patientaccesstoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="email",
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
        migrations.CreateModel(
            name="PatientPin",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pin_hash", models.CharField(max_length=128)),
                ("failed_attempts", models.PositiveSmallIntegerField(default=0)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
                ("lockout_level", models.PositiveSmallIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("patient", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="pin", to="patients.patient")),
            ],
        ),
        migrations.CreateModel(
            name="OtpChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("national_id", models.CharField(db_index=True, max_length=13)),
                ("channel", models.CharField(choices=[("email", "Email"), ("phone", "Phone")], max_length=10)),
                ("purpose", models.CharField(choices=[("PIN_RESET", "PIN reset")], default="PIN_RESET", max_length=24)),
                ("code_hash", models.CharField(max_length=128)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [models.Index(fields=["national_id", "channel", "purpose", "created_at"], name="otp_lookup_created_idx")],
            },
        ),
    ]
