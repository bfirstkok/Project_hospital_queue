from django.db import models
from django.utils import timezone


class VisitAssessment(models.Model):
    visit = models.OneToOneField("queues.Visit", on_delete=models.CASCADE, related_name="opd_assessment")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- clinical values ---
    known_copd_asthma = models.BooleanField(default=False)
    pain_score = models.PositiveSmallIntegerField(null=True, blank=True)  # 0-10

    bt = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)  # °C
    sys_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    dia_bp = models.PositiveSmallIntegerField(null=True, blank=True)

    fbs = models.PositiveSmallIntegerField(null=True, blank=True)  # mg%
    lab_k = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    lab_mg = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    lab_hct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # --- red non-numeric reasons ---
    anxious_family = models.BooleanField(default=False)
    non_toxic_bite = models.BooleanField(default=False)
    very_fatigue = models.BooleanField(default=False)
    blood_receive = models.BooleanField(default=False)

    # --- yellow special groups ---
    monk = models.BooleanField(default=False)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    child_under_5 = models.BooleanField(default=False)
    pregnant = models.BooleanField(default=False)
    ga_weeks = models.PositiveSmallIntegerField(null=True, blank=True)
    epilepsy = models.BooleanField(default=False)
    pulmonary_tb_mplus = models.BooleanField(default=False)

    low_immunity = models.BooleanField(default=False)
    low_immunity_detail = models.CharField(max_length=255, blank=True, default="")

    # --- free text ---
    chief_complaint = models.TextField(blank=True, default="")
    diagnosis = models.TextField(blank=True, default="")
    treatment = models.TextField(blank=True, default="")

    # --- next appointment ---
    next_appointment_at = models.DateTimeField(null=True, blank=True)
    next_appointment_note = models.CharField(max_length=255, blank=True, default="")
    followup_visit = models.ForeignKey(
        "queues.Visit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_from_assessment",
    )
    def compute_opd_urgency(self):
        """
        คำนวณความเร่งด่วน OPD
        """
        # ถ้ามี method priority อยู่แล้ว ใช้อันนั้น
        color, reasons = self.compute_opd_priority()

        # เก็บค่าลง field
        self.opd_urgency = color

        return color, reasons


    # ====== ผลสรุปตามเกณฑ์ ======
    class OpdUrgency(models.TextChoices):
        RED = "RED", "เร่งด่วนสีแดง"
        YELLOW = "YELLOW", "เร่งด่วนสีเหลือง"
        NORMAL = "NORMAL", "ปกติ"

    opd_urgency = models.CharField(max_length=10, choices=OpdUrgency.choices, default=OpdUrgency.NORMAL)
    opd_reason = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def compute_opd_priority(self):
        """
        return: ("RED"/"YELLOW"/None, reasons[list[str]])
        """
        reasons = []
        # RED rules
        if self.known_copd_asthma:
            reasons.append("Known COPD/Asthma + เหนื่อย (ไม่เข้า ER)")
        if self.pain_score is not None and self.pain_score >= 7:
            reasons.append(f"Pain score {self.pain_score} (≥7)")
        if self.fbs is not None and self.fbs >= 300:
            reasons.append(f"FBS {self.fbs} (≥300 mg%)")
        if self.lab_k is not None and float(self.lab_k) < 3.5:
            reasons.append(f"K {self.lab_k} (<3.5)")
        if self.lab_mg is not None and float(self.lab_mg) < 1.8:
            reasons.append(f"Mg {self.lab_mg} (<1.8)")
        if self.lab_hct is not None and float(self.lab_hct) < 25:
            reasons.append(f"Hct {self.lab_hct} (<25)")
        if self.bt is not None and float(self.bt) >= 39:
            reasons.append(f"BT {self.bt} (≥39°C)")
        if self.anxious_family:
            reasons.append("ญาติวิตกกังวลมาก")
        if self.non_toxic_bite:
            reasons.append("ถูกสัตว์ไม่มีพิษกัด/ข่วน")
        if self.very_fatigue:
            reasons.append("เหนื่อยเพลียมาก")
        if self.blood_receive:
            reasons.append("มารับเลือด")

        if reasons:
            return "RED", reasons

        # YELLOW rules
        y = []
        if self.monk:
            y.append("พระภิกษุ/สามเณร")
        if self.age is not None and self.age >= 80:
            y.append(f"อายุ {self.age} (≥80)")
        if self.child_under_5:
            y.append("เด็กอายุต่ำกว่า 5 ปี")
        if self.sys_bp is not None and self.dia_bp is not None and (self.sys_bp > 160 or self.dia_bp > 90):
            y.append(f"BP {self.sys_bp}/{self.dia_bp} (>160/90)")
        if self.pregnant and self.ga_weeks is not None and self.ga_weeks >= 28:
            y.append(f"ตั้งครรภ์ GA {self.ga_weeks} (≥28 wks)")
        if self.epilepsy:
            y.append("Epilepsy")
        if self.pulmonary_tb_mplus:
            y.append("Pulmonary TB M+ กินยาไม่ครบ 2 เดือน")
        if self.low_immunity:
            y.append("ภูมิต้านทานต่ำ")

        if y:
            return "YELLOW", y

        return None, []

    def save(self, *args, **kwargs):
        urg, reasons = self.compute_opd_urgency()
        self.opd_urgency = urg
        self.opd_reason = "\n".join(reasons) if reasons else ""
        super().save(*args, **kwargs)

    def __str__(self):
        return f"VisitAssessment(visit_id={self.visit_id}, urgency={self.opd_urgency})"
