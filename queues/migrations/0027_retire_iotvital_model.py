from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0026_acknowledge_non_wearable_critical_alerts"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # IoTVital duplicated active wearable readings already stored in
            # TelemetryLog. Remove it from Django's active model state now, but
            # keep the legacy physical table temporarily so production history
            # can be backed up/verified before a later destructive cleanup.
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name="IoTVital",
                ),
            ],
        ),
    ]
