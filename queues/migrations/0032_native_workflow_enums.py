from django.db import migrations

import config.db_fields


TRIAGE_SEVERITY = (
    "triage_severity_enum",
    ("RED", "PINK", "YELLOW", "GREEN", "WHITE"),
)
QUEUE_STATUS = (
    "queue_status_enum",
    (
        "WAITING_VITALS",
        "WAITING_CONFIRMATION",
        "WAITING_QUEUE",
        "WAITING",
        "CALLED",
        "MONITORING",
        "OBSERVATION_MONITORING",
        "REASSESSMENT_REQUIRED",
        "EMERGENCY_TRANSFER",
        "OPD_DONE",
        "FOLLOWUP",
        "DISCHARGED",
        "CANCELLED",
    ),
)
CRITICAL_ALERT_STATUS = (
    "critical_alert_status_enum",
    (
        "NEW",
        "ACKNOWLEDGED",
        "IN_REVIEW",
        "ESCALATED",
        "RESOLVED",
        "FALSE_ALARM",
    ),
)
SHIFT_STATUS = (
    "shift_schedule_status_enum",
    ("SCHEDULED", "LEAVE", "CANCELLED"),
)


def _assert_valid_values(schema_editor, table, column, values):
    placeholders = ", ".join(["%s"] * len(values))
    sql = (
        f'SELECT DISTINCT "{column}"::text FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL '
        f'AND "{column}"::text NOT IN ({placeholders}) '
        f'ORDER BY 1'
    )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql, list(values))
        invalid = [row[0] for row in cursor.fetchall()]
    if invalid:
        raise RuntimeError(
            f"Cannot convert {table}.{column} to ENUM; invalid values: {invalid}"
        )


def _create_enum(schema_editor, enum_name, values):
    quoted_values = ", ".join(f"'{value}'" for value in values)
    schema_editor.execute(
        f"""
        DO $$
        BEGIN
            CREATE TYPE {enum_name} AS ENUM ({quoted_values});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def _alter_to_enum(schema_editor, table, column, enum_name):
    schema_editor.execute(
        f"""
        ALTER TABLE "{table}"
        ALTER COLUMN "{column}" TYPE {enum_name}
        USING "{column}"::text::{enum_name};
        """
    )


def _alter_to_varchar(schema_editor, table, column, length):
    schema_editor.execute(
        f"""
        ALTER TABLE "{table}"
        ALTER COLUMN "{column}" TYPE varchar({length})
        USING "{column}"::text;
        """
    )


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    severity_values = TRIAGE_SEVERITY[1]
    queue_values = QUEUE_STATUS[1]
    alert_values = CRITICAL_ALERT_STATUS[1]
    shift_values = SHIFT_STATUS[1]

    for table, column, values in (
        ("queues_visit", "final_severity", severity_values),
        ("queues_triageresult", "ai_severity", severity_values),
        ("queues_triageresult", "nurse_severity", severity_values),
        ("queues_criticalalert", "severity", severity_values),
        ("queues_queue", "status", queue_values),
        ("queues_criticalalert", "status", alert_values),
        ("queues_shiftschedule", "status", shift_values),
    ):
        _assert_valid_values(schema_editor, table, column, values)

    for enum_name, values in (
        TRIAGE_SEVERITY,
        QUEUE_STATUS,
        CRITICAL_ALERT_STATUS,
        SHIFT_STATUS,
    ):
        _create_enum(schema_editor, enum_name, values)

    for table, column in (
        ("queues_visit", "final_severity"),
        ("queues_triageresult", "ai_severity"),
        ("queues_triageresult", "nurse_severity"),
        ("queues_criticalalert", "severity"),
    ):
        _alter_to_enum(schema_editor, table, column, TRIAGE_SEVERITY[0])

    _alter_to_enum(schema_editor, "queues_queue", "status", QUEUE_STATUS[0])
    _alter_to_enum(
        schema_editor,
        "queues_criticalalert",
        "status",
        CRITICAL_ALERT_STATUS[0],
    )
    _alter_to_enum(
        schema_editor,
        "queues_shiftschedule",
        "status",
        SHIFT_STATUS[0],
    )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    for table, column, length in (
        ("queues_visit", "final_severity", 10),
        ("queues_triageresult", "ai_severity", 10),
        ("queues_triageresult", "nurse_severity", 10),
        ("queues_criticalalert", "severity", 10),
        ("queues_queue", "status", 32),
        ("queues_criticalalert", "status", 16),
        ("queues_shiftschedule", "status", 16),
    ):
        _alter_to_varchar(schema_editor, table, column, length)

    for enum_name in (
        TRIAGE_SEVERITY[0],
        QUEUE_STATUS[0],
        CRITICAL_ALERT_STATUS[0],
        SHIFT_STATUS[0],
    ):
        schema_editor.execute(f"DROP TYPE IF EXISTS {enum_name};")


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0031_clinical_alert_workflow"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="visit",
                    name="final_severity",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="triage_severity_enum",
                        max_length=10,
                        choices=[
                            ("RED", "แดง - วิกฤต"),
                            ("PINK", "ชมพู - ฉุกเฉิน"),
                            ("YELLOW", "เหลือง - เร่งด่วน"),
                            ("GREEN", "เขียว - ไม่เร่งด่วน"),
                            ("WHITE", "ขาว - ผู้ป่วยทั่วไป"),
                        ],
                        blank=True,
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name="triageresult",
                    name="ai_severity",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="triage_severity_enum",
                        max_length=10,
                        choices=[
                            ("RED", "แดง - วิกฤต"),
                            ("PINK", "ชมพู - ฉุกเฉิน"),
                            ("YELLOW", "เหลือง - เร่งด่วน"),
                            ("GREEN", "เขียว - ไม่เร่งด่วน"),
                            ("WHITE", "ขาว - ผู้ป่วยทั่วไป"),
                        ],
                        blank=True,
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name="triageresult",
                    name="nurse_severity",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="triage_severity_enum",
                        max_length=10,
                        choices=[
                            ("RED", "แดง - วิกฤต"),
                            ("PINK", "ชมพู - ฉุกเฉิน"),
                            ("YELLOW", "เหลือง - เร่งด่วน"),
                            ("GREEN", "เขียว - ไม่เร่งด่วน"),
                            ("WHITE", "ขาว - ผู้ป่วยทั่วไป"),
                        ],
                        blank=True,
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name="queue",
                    name="status",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="queue_status_enum",
                        max_length=32,
                        choices=[
                            ("WAITING_VITALS", "Waiting vitals"),
                            ("WAITING_CONFIRMATION", "Waiting confirmation"),
                            ("WAITING_QUEUE", "Waiting queue"),
                            ("WAITING", "Waiting"),
                            ("CALLED", "Called"),
                            ("MONITORING", "Post-OPD monitoring"),
                            ("OBSERVATION_MONITORING", "Observation monitoring"),
                            ("REASSESSMENT_REQUIRED", "Reassessment required"),
                            ("EMERGENCY_TRANSFER", "Emergency transfer"),
                            ("OPD_DONE", "OPD Done"),
                            ("FOLLOWUP", "Follow-up"),
                            ("DISCHARGED", "Discharged"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="WAITING_VITALS",
                    ),
                ),
                migrations.AlterField(
                    model_name="criticalalert",
                    name="severity",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="triage_severity_enum",
                        max_length=10,
                        choices=[
                            ("RED", "แดง - วิกฤต"),
                            ("PINK", "ชมพู - ฉุกเฉิน"),
                            ("YELLOW", "เหลือง - เร่งด่วน"),
                            ("GREEN", "เขียว - ไม่เร่งด่วน"),
                            ("WHITE", "ขาว - ผู้ป่วยทั่วไป"),
                        ],
                        default="RED",
                    ),
                ),
                migrations.AlterField(
                    model_name="criticalalert",
                    name="status",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="critical_alert_status_enum",
                        max_length=16,
                        choices=[
                            ("NEW", "New"),
                            ("ACKNOWLEDGED", "Acknowledged"),
                            ("IN_REVIEW", "Clinical review"),
                            ("ESCALATED", "Escalated"),
                            ("RESOLVED", "Resolved"),
                            ("FALSE_ALARM", "False alarm"),
                        ],
                        default="NEW",
                    ),
                ),
                migrations.AlterField(
                    model_name="shiftschedule",
                    name="status",
                    field=config.db_fields.PostgresEnumField(
                        enum_type="shift_schedule_status_enum",
                        max_length=16,
                        choices=[
                            ("SCHEDULED", "จัดเวรแล้ว"),
                            ("LEAVE", "ลา"),
                            ("CANCELLED", "ยกเลิกเวร"),
                        ],
                        default="SCHEDULED",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
