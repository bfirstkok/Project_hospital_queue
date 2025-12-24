from django.db import models
from django.utils import timezone


class VisitAssessment(models.Model):
    visit = models.OneToOneField(
        "queues.Visit",
        on_delete=models.CASCADE,
        related_name="opd_assessment"
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

    opd_urgency = models.CharField(
        max_length=10,
        choices=OpdUrgency.choices,
        default=OpdUrgency.NORMAL
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

