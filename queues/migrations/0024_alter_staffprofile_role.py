from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("queues", "0023_shiftschedule")]

    operations = [
        migrations.AlterField(
            model_name="staffprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("DOCTOR", "แพทย์"),
                    ("NURSE", "พยาบาลวิชาชีพ (คัดกรอง/เฝ้าระวัง)"),
                    ("NURSE_ASSISTANT", "ผู้ช่วยพยาบาล (วัดสัญญาณชีพ)"),
                    ("EMERGENCY", "เจ้าหน้าที่ฉุกเฉิน"),
                    ("STAFF", "เจ้าหน้าที่เวชระเบียน"),
                    ("QUEUE_OPERATOR", "เจ้าหน้าที่จัดคิว"),
                    ("BIOMEDICAL", "เจ้าหน้าที่เครื่องมือแพทย์"),
                ],
                default="STAFF",
                max_length=24,
            ),
        ),
    ]
