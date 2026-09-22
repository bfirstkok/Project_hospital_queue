from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("patients", "0012_native_appointment_status_enum"),
        ("queues", "0035_add_pharmacy_cashier_workflow_roles"),
        ("opd", "0008_native_opd_urgency_enum"),
    ]

    operations = [
        migrations.CreateModel(
            name="PatientCoverage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("coverage_type", models.CharField(choices=[("SELF_PAY", "ชำระเงินเอง"), ("UCS", "บัตรทอง / สปสช."), ("SSS", "ประกันสังคม"), ("CSMBS", "ข้าราชการ"), ("PRIVATE", "ประกันเอกชน"), ("OTHER", "สิทธิอื่น ๆ")], default="SELF_PAY", max_length=16)),
                ("member_no", models.CharField(blank=True, default="", max_length=80)),
                ("coverage_percent", models.PositiveSmallIntegerField(default=0)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("patient", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="coverage", to="patients.patient")),
            ],
        ),
        migrations.CreateModel(
            name="Prescription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("DRAFT", "ฉบับร่าง"), ("SENT", "รอห้องยา"), ("PREPARING", "กำลังจัดยา"), ("READY", "พร้อมจ่ายยา"), ("DISPENSED", "จ่ายยาแล้ว"), ("CANCELLED", "ยกเลิก")], db_index=True, default="DRAFT", max_length=16)),
                ("note", models.TextField(blank=True, default="")),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("dispensed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("dispensed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="prescriptions_dispensed", to=settings.AUTH_USER_MODEL)),
                ("prescribed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="prescriptions_written", to=settings.AUTH_USER_MODEL)),
                ("visit", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="prescription", to="queues.visit")),
            ],
        ),
        migrations.CreateModel(
            name="MedicalCertificate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("diagnosis_snapshot", models.TextField(blank=True, default="")),
                ("recommendation", models.TextField(blank=True, default="")),
                ("rest_from", models.DateField(blank=True, null=True)),
                ("rest_to", models.DateField(blank=True, null=True)),
                ("issued_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("issued_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="medical_certificates_issued", to=settings.AUTH_USER_MODEL)),
                ("visit", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="medical_certificate", to="queues.visit")),
            ],
        ),
        migrations.CreateModel(
            name="Bill",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("consultation_fee", models.DecimalField(decimal_places=2, default=Decimal("200"), max_digits=9)),
                ("medicine_total", models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ("other_fee", models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ("subtotal", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("covered_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("patient_due", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("status", models.CharField(choices=[("DRAFT", "รอสรุปค่าใช้จ่าย"), ("READY", "รอชำระเงิน"), ("PAID", "ชำระแล้ว"), ("WAIVED", "ไม่มีค่าใช้จ่ายผู้ป่วย"), ("CANCELLED", "ยกเลิก")], db_index=True, default="DRAFT", max_length=16)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("coverage", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bills", to="opd.patientcoverage")),
                ("received_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments_received", to=settings.AUTH_USER_MODEL)),
                ("visit", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="bill", to="queues.visit")),
            ],
        ),
        migrations.CreateModel(
            name="PrescriptionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("medication_name", models.CharField(max_length=180)),
                ("strength", models.CharField(blank=True, default="", max_length=80)),
                ("dosage", models.CharField(blank=True, default="", max_length=120)),
                ("frequency", models.CharField(blank=True, default="", max_length=120)),
                ("duration_days", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("unit", models.CharField(blank=True, default="หน่วย", max_length=40)),
                ("instructions", models.CharField(blank=True, default="", max_length=255)),
                ("unit_price", models.DecimalField(decimal_places=2, default=0, max_digits=9)),
                ("prescription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="opd.prescription")),
            ],
        ),
    ]
