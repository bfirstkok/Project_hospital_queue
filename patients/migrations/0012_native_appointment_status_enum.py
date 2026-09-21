from django.db import migrations

import config.db_fields


ENUM_NAME = "appointment_status_enum"
VALUES = ("SCHEDULED", "ATTENDED", "MISSED", "CANCELLED")


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    quoted_values = ", ".join(f"'{value}'" for value in VALUES)
    schema_editor.execute(
        f"""
        DO $$
        BEGIN
            CREATE TYPE {ENUM_NAME} AS ENUM ({quoted_values});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    schema_editor.execute(
        f"""
        ALTER TABLE patients_appointment
        ALTER COLUMN status TYPE {ENUM_NAME}
        USING status::text::{ENUM_NAME};
        """
    )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        """
        ALTER TABLE patients_appointment
        ALTER COLUMN status TYPE varchar(16)
        USING status::text;
        """
    )
    schema_editor.execute(f"DROP TYPE IF EXISTS {ENUM_NAME};")


class Migration(migrations.Migration):
    dependencies = [
        ("patients", "0011_patient_portal_account_and_password_recovery"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="appointment",
                    name="status",
                    field=config.db_fields.PostgresEnumField(
                        enum_type=ENUM_NAME,
                        max_length=16,
                        choices=[
                            ("SCHEDULED", "Scheduled"),
                            ("ATTENDED", "Attended"),
                            ("MISSED", "Missed"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="SCHEDULED",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
