from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def acknowledge_non_wearable_alerts(apps, schema_editor):
    CriticalAlert = apps.get_model("queues", "CriticalAlert")
    wearable_sources = Q(source__startswith="iot") | Q(source__endswith="_iot")
    CriticalAlert.objects.filter(status="NEW").exclude(wearable_sources).update(
        status="ACKNOWLEDGED",
        acknowledged_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0025_queue_is_expedited_queue_manual_sequence_and_more"),
    ]

    operations = [
        migrations.RunPython(acknowledge_non_wearable_alerts, migrations.RunPython.noop),
    ]
