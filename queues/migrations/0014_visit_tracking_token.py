import uuid

from django.db import migrations, models


def populate_tracking_tokens(apps, schema_editor):
    Visit = apps.get_model("queues", "Visit")
    for visit_id in Visit.objects.filter(tracking_token__isnull=True).values_list("id", flat=True).iterator():
        Visit.objects.filter(id=visit_id).update(tracking_token=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [
        ("queues", "0013_alter_criticalalert_id_alter_device_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="visit",
            name="tracking_token",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_tracking_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="visit",
            name="tracking_token",
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
