from django.db import migrations, models
import django.utils.timezone


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
            name="token_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="patient",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="patient",
            name="emergency_contacts",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="patient",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="patient",
            name="email",
            field=models.EmailField(blank=True, db_index=True, max_length=254, null=True),
        ),
        migrations.AddField(
            model_name="patientaccesstoken",
            name="token_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="otpchallenge",
            name="reset_token_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, unique=True),
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
    ]
