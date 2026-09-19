from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("queues", "0027_retire_iotvital_model"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffprofile",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="hospital_staff_profile",
                to="auth.user",
            ),
        ),
    ]
