from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from config.db_fields import PostgresEnumField


class VisitAssessment(models.Model):
    visit = models.OneToOneField(
        "queues.Visit",
        on_delete=models.CASCADE,
        related_name="opd_assessment"
    )
    examiner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opd_assessments",
        verbose_name="แพทย์ผู้ตรวจ",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- clinical values ---
    known_copd_asthma = models.BooleanField(default=False)
    pain_score = models.PositiveSmallIntegerField(null=True, blank=True)

    bt = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    sys_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    dia_bp = models.PositiveSmallIntegerField(null=True, blank=True)

    fbs = models.PositiveSmallIntegerField(null=True, blank=True)
    lab_k = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    lab_mg = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    lab_hct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # --- flags ---
    anxious_family = models.BooleanField(default=False)
    non_toxic_bite = models.BooleanField(default=False)
    very_fatigue = models.BooleanField(default=False)
    blood_receive = models.BooleanField(default=False)

    monk = models.BooleanField(default=False)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    child_under_5 = models.BooleanField(default=False)
    pregnant = models.BooleanField(default=False)
    ga_weeks = models.PositiveSmallIntegerField(null=True, blank=True)
    epilepsy = models.BooleanField(default=False)
    pulmonary_tb_mplus = models.BooleanField(default=False)

    low_immunity = models.BooleanField(default=False)
    low_immunity_detail = models.CharField(max_length=255, blank=True, default="")

    chief_complaint = models.TextField(blank=True, default="")
    diagnosis = models.TextField(blank=True, default="")
    treatment = models.TextField(blank=True, default="")

    next_appointment_at = models.DateTimeField(null=True, blank=True)
    next_appointment_note = models.CharField(max_length=255, blank=True, default="")
    followup_visit = models.ForeignKey(
        "queues.Visit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_from_assessment",
    )

    # ===== urgency result =====
    class OpdUrgency(models.TextChoices):
        RED = "RED", "เร่งด่วนสีแดง"
        YELLOW = "YELLOW", "เร่งด่วนสีเหลือง"
        NORMAL = "NORMAL", "ปกติ"

    opd_urgency = PostgresEnumField(
        enum_type="opd_urgency_enum",
        max_length=10,
        choices=OpdUrgency.choices,
        default=OpdUrgency.NORMAL,
    )
    opd_reason = models.TextField(blank=True, default="")

    # -------------------------
    # logic
    # -------------------------
    def compute_opd_priority(self):
        reasons = []

        # RED
        if self.known_copd_asthma:
            reasons.append("Known COPD/Asthma")
        if self.pain_score is not None and self.pain_score >= 7:
            reasons.append(f"Pain score {self.pain_score}")
        if self.fbs is not None and self.fbs >= 300:
            reasons.append(f"FBS {self.fbs}")
        if self.lab_k is not None and float(self.lab_k) < 3.5:
            reasons.append(f"K {self.lab_k}")
        if self.bt is not None and float(self.bt) >= 39:
            reasons.append(f"BT {self.bt}")

        if reasons:
            return self.OpdUrgency.RED, reasons

        # YELLOW
        y = []
        if self.monk:
            y.append("พระภิกษุ")
        if self.age and self.age >= 80:
            y.append(f"อายุ {self.age}")
        if self.child_under_5:
            y.append("เด็ก < 5 ปี")

        if y:
            return self.OpdUrgency.YELLOW, y

        return self.OpdUrgency.NORMAL, []

    def save(self, *args, **kwargs):
        urg, reasons = self.compute_opd_priority()
        self.opd_urgency = urg
        self.opd_reason = "\n".join(reasons)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"VisitAssessment(visit_id={self.visit_id}, urgency={self.opd_urgency})"



class Prescription(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "ฉบับร่าง"
        SENT = "SENT", "รอห้องยา"
        PREPARING = "PREPARING", "กำลังจัดยา"
        READY = "READY", "พร้อมจ่ายยา"
        DISPENSED = "DISPENSED", "จ่ายยาแล้ว"
        CANCELLED = "CANCELLED", "ยกเลิก"

    visit = models.OneToOneField(
        "queues.Visit",
        on_delete=models.CASCADE,
        related_name="prescription",
    )
    prescribed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prescriptions_written",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    note = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    dispensed_at = models.DateTimeField(null=True, blank=True)
    dispensed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions_dispensed",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Prescription Visit#{self.visit_id} ({self.status})"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(
        Prescription,
        on_delete=models.CASCADE,
        related_name="items",
    )
    medication_name = models.CharField(max_length=180)
    strength = models.CharField(max_length=80, blank=True, default="")
    dosage = models.CharField(max_length=120, blank=True, default="")
    frequency = models.CharField(max_length=120, blank=True, default="")
    duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=40, blank=True, default="หน่วย")
    instructions = models.CharField(max_length=255, blank=True, default="")
    unit_price = models.DecimalField(max_digits=9, decimal_places=2, default=0)

    @property
    def line_total(self):
        return Decimal(self.quantity or 0) * (self.unit_price or Decimal("0.00"))

    def __str__(self):
        return f"{self.medication_name} x {self.quantity}"


class PatientCoverage(models.Model):
    class CoverageType(models.TextChoices):
        SELF_PAY = "SELF_PAY", "ชำระเงินเอง"
        UCS = "UCS", "บัตรทอง / สปสช."
        SSS = "SSS", "ประกันสังคม"
        CSMBS = "CSMBS", "ข้าราชการ"
        PRIVATE = "PRIVATE", "ประกันเอกชน"
        OTHER = "OTHER", "สิทธิอื่น ๆ"

    patient = models.OneToOneField(
        "patients.Patient",
        on_delete=models.CASCADE,
        related_name="coverage",
    )
    coverage_type = models.CharField(max_length=16, choices=CoverageType.choices, default=CoverageType.SELF_PAY)
    member_no = models.CharField(max_length=80, blank=True, default="")
    coverage_percent = models.PositiveSmallIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.patient_id} · {self.get_coverage_type_display()}"


class Bill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "รอสรุปค่าใช้จ่าย"
        READY = "READY", "รอชำระเงิน"
        PAID = "PAID", "ชำระแล้ว"
        WAIVED = "WAIVED", "ไม่มีค่าใช้จ่ายผู้ป่วย"
        CANCELLED = "CANCELLED", "ยกเลิก"

    visit = models.OneToOneField(
        "queues.Visit",
        on_delete=models.CASCADE,
        related_name="bill",
    )
    coverage = models.ForeignKey(
        PatientCoverage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bills",
    )
    consultation_fee = models.DecimalField(max_digits=9, decimal_places=2, default=200)
    medicine_total = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    other_fee = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    covered_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    patient_due = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments_received",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def recalculate(self, save=True):
        prescription = getattr(self.visit, "prescription", None)
        medicine_total = Decimal("0.00")
        if prescription and prescription.status != Prescription.Status.CANCELLED:
            medicine_total = sum((item.line_total for item in prescription.items.all()), Decimal("0.00"))
        subtotal = (self.consultation_fee or Decimal("0.00")) + medicine_total + (self.other_fee or Decimal("0.00"))
        percent = self.coverage.coverage_percent if self.coverage and self.coverage.is_active else 0
        percent = max(0, min(int(percent or 0), 100))
        covered = (subtotal * Decimal(percent) / Decimal("100")).quantize(Decimal("0.01"))
        due = subtotal - covered

        self.medicine_total = medicine_total
        self.subtotal = subtotal
        self.covered_amount = covered
        self.patient_due = due
        if self.status not in {self.Status.PAID, self.Status.CANCELLED}:
            self.status = self.Status.WAIVED if due == 0 else self.Status.READY
        if save:
            self.save()
        return self

    def __str__(self):
        return f"Bill Visit#{self.visit_id} ({self.status})"


class MedicalCertificate(models.Model):
    visit = models.OneToOneField(
        "queues.Visit",
        on_delete=models.CASCADE,
        related_name="medical_certificate",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="medical_certificates_issued",
    )
    diagnosis_snapshot = models.TextField(blank=True, default="")
    recommendation = models.TextField(blank=True, default="")
    rest_from = models.DateField(null=True, blank=True)
    rest_to = models.DateField(null=True, blank=True)
    issued_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"MedicalCertificate Visit#{self.visit_id}"
