from django.db import migrations

import config.db_fields


ENUM_NAME = "opd_urgency_enum"
VALUES = ("RED", "YELLOW", "NORMAL")


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    placeholders = ", ".join(["%s"] * len(VALUES))
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT DISTINCT opd_urgency::text
            FROM opd_visitassessment
            WHERE opd_urgency IS NOT NULL
              AND opd_urgency::text NOT IN ({placeholders})
            ORDER BY 1
            """,
            list(VALUES),
        )
        invalid = [row[0] for row in cursor.fetchall()]
    if invalid:
        raise RuntimeError(
            f"Cannot convert opd_visitassessment.opd_urgency to ENUM; invalid values: {invalid}"
        )

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
        ALTER TABLE opd_visitassessment
        ALTER COLUMN opd_urgency TYPE {ENUM_NAME}
        USING opd_urgency::text::{ENUM_NAME};
        """
    )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        """
        ALTER TABLE opd_visitassessment
        ALTER COLUMN opd_urgency TYPE varchar(10)
        USING opd_urgency::text;
        """
    )
    schema_editor.execute(f"DROP TYPE IF EXISTS {ENUM_NAME};")


class Migration(migrations.Migration):
    dependencies = [
        ("opd", "0007_visitassessment_examiner"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="visitassessment",
                    name="opd_urgency",
                    field=config.db_fields.PostgresEnumField(
                        enum_type=ENUM_NAME,
                        max_length=10,
                        choices=[
                            ("RED", "เร่งด่วนสีแดง"),
                            ("YELLOW", "เร่งด่วนสีเหลือง"),
                            ("NORMAL", "ปกติ"),
                        ],
                        default="NORMAL",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
