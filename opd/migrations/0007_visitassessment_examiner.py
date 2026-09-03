from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("opd", "0006_alter_visitassessment_created_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="visitassessment",
            name="examiner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="opd_assessments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="แพทย์ผู้ตรวจ",
            ),
        ),
    ]
