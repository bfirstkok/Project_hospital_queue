from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("queues", "0015_merge_0014_branches"),
    ]

    operations = [
        migrations.AlterField(
            model_name="queue",
            name="status",
            field=models.CharField(
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
                max_length=32,
            ),
        ),
    ]
