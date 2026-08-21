from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("queues", "0017_five_level_triage")]

    operations = [
        migrations.AddField(
            model_name="triageresult",
            name="lifesaving_intervention",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="triageresult",
            name="high_risk_condition",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="triageresult",
            name="altered_mental_status",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="triageresult",
            name="mental_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ALERT", "รู้สึกตัวดี"),
                    ("VERBAL", "ตอบสนองต่อเสียงเรียก"),
                    ("PAIN", "ตอบสนองเมื่อกระตุ้นด้วยความเจ็บปวด"),
                    ("UNRESPONSIVE", "ไม่ตอบสนอง"),
                ],
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="triageresult",
            name="severe_distress",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="triageresult",
            name="expected_resources",
            field=models.CharField(
                blank=True,
                choices=[
                    ("0", "ไม่ใช้ทรัพยากรเพิ่มเติม"),
                    ("1", "ใช้ 1 รายการ"),
                    ("2_PLUS", "ใช้มากกว่า 1 รายการ"),
                ],
                max_length=10,
                null=True,
            ),
        ),
    ]
