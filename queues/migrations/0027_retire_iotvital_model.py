from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0026_acknowledge_non_wearable_critical_alerts"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # IoTVital duplicated active wearable readings already stored in
            # TelemetryLog. Retire it from the active schema without destroying
            # historical rows: the old table is renamed as a legacy archive.
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE queues_iotvital "
                        "RENAME TO queues_iotvital_legacy_archive"
                    ),
                    reverse_sql=(
                        "ALTER TABLE queues_iotvital_legacy_archive "
                        "RENAME TO queues_iotvital"
                    ),
                ),
            ],
            state_operations=[
                migrations.DeleteModel(
                    name="IoTVital",
                ),
            ],
        ),
    ]
