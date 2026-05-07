# Generated manually for PostgreSQL-compatible monitoring statuses.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("queues", "0005_alter_queue_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="queue",
            name="status",
            field=models.CharField(
                choices=[
                    ("WAITING", "Waiting"),
                    ("CALLED", "Called"),
                    ("MONITORING", "Monitoring"),
                    ("OPD_DONE", "OPD Done"),
                    ("FOLLOWUP", "Follow-up"),
                    ("DISCHARGED", "Discharged"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="WAITING",
                max_length=12,
            ),
        ),
    ]
