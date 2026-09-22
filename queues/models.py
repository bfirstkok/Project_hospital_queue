import re
import uuid
from datetime import datetime, time, timedelta
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from config.db_fields import PostgresEnumField

class Visit(models.Model):
    class Severity(models.TextChoices):
        RED = "RED", "แดง - วิกฤต"
        PINK = "PINK", "ชมพู - ฉุกเฉิน"
        YELLOW = "YELLOW", "เหลือง - เร่งด่วน"
        GREEN = "GREEN", "เขียว - ไม่เร่งด่วน"
        WHITE = "WHITE", "ขาว - ผู้ป่วยทั่วไป"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="visits")
    tracking_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    registered_at = models.DateTimeField(auto_now_add=True)
    triaged_at = models.DateTimeField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    called_at = models.DateTimeField(blank=True, null=True)

    final_severity = PostgresEnumField(
        enum_type="triage_severity_enum",
        max_length=10,
        choices=Severity.choices,
        blank=True,
        null=True,
    )
    note = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Visit#{self.id} {self.patient}"
    
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)


class VitalSign(models.Model):
    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="vitals")

    rr = models.IntegerField("RR", blank=True, null=True)
    pr = models.IntegerField("PR", blank=True, null=True)
    sys_bp = models.IntegerField("Systolic BP", blank=True, null=True)
    dia_bp = models.IntegerField("Diastolic BP", blank=True, null=True)
    bt = models.FloatField("BT (°C)", blank=True, null=True)
    o2sat = models.IntegerField("O₂ Sat", blank=True, null=True)
    pain_score = models.PositiveSmallIntegerField(blank=True, null=True)
    urgent_symptoms = models.JSONField(default=list, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)

    updated_at = models.DateTimeField(auto_now=True)


class Queue(models.Model):
    class Status(models.TextChoices):
        WAITING_VITALS = "WAITING_VITALS", "Waiting vitals"
        WAITING_CONFIRMATION = "WAITING_CONFIRMATION", "Waiting confirmation"
        WAITING_QUEUE = "WAITING_QUEUE", "Waiting queue"
        WAITING = "WAITING", "Waiting"
        CALLED = "CALLED", "Called"
        MONITORING = "MONITORING", "Post-OPD monitoring"
        OBSERVATION_MONITORING = "OBSERVATION_MONITORING", "Observation monitoring"
        REASSESSMENT_REQUIRED = "REASSESSMENT_REQUIRED", "Reassessment required"
        EMERGENCY_TRANSFER = "EMERGENCY_TRANSFER", "Emergency transfer"
        OPD_DONE = "OPD_DONE", "OPD Done"
        FOLLOWUP = "FOLLOWUP", "Follow-up"
        DISCHARGED = "DISCHARGED", "Discharged"
        CANCELLED = "CANCELLED", "Cancelled"

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="queue")
    status = PostgresEnumField(
        enum_type="queue_status_enum",
        max_length=32,
        choices=Status.choices,
        default=Status.WAITING_VITALS,
    )

    priority = models.IntegerField(default=5)
    exam_room = models.PositiveSmallIntegerField(blank=True, null=True)
    manual_sequence = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text="เลขคิวที่เจ้าหน้าที่กำหนดเอง หากว่างจะใช้เลขอัตโนมัติ",
    )
    is_expedited = models.BooleanField(
        default=False,
        db_index=True,
        help_text="ลัดลำดับภายในระดับความเร่งด่วนเดิม โดยไม่เปลี่ยนผลคัดกรอง",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def display_sequence(self):
        """Return the sequence for the service day, which starts at 06:00."""
        if not self.created_at:
            return 1

        current_tz = timezone.get_current_timezone()
        created_local = timezone.localtime(self.created_at, current_tz)
        service_date = created_local.date()
        if created_local.time() < time(hour=6):
            service_date -= timedelta(days=1)

        service_day_start = timezone.make_aware(
            datetime.combine(service_date, time(hour=6)),
            current_tz,
        )
        next_service_day = service_day_start + timedelta(days=1)

        earlier_queues = Queue.objects.filter(
            created_at__gte=service_day_start,
            created_at__lt=next_service_day,
        ).filter(
            Q(created_at__lt=self.created_at)
            | Q(created_at=self.created_at, pk__lt=self.pk),
        ).count()
        return 1 + earlier_queues

    @property
    def display_number(self):
        if self.manual_sequence is not None:
            return f"Q{self.manual_sequence:03d}"
        return f"Q{self.display_sequence:03d}"


class TriageResult(models.Model):
    class MentalStatus(models.TextChoices):
        ALERT = "ALERT", "รู้สึกตัวดี"
        VERBAL = "VERBAL", "ตอบสนองต่อเสียงเรียก"
        PAIN = "PAIN", "ตอบสนองเมื่อกระตุ้นด้วยความเจ็บปวด"
        UNRESPONSIVE = "UNRESPONSIVE", "ไม่ตอบสนอง"

    class ExpectedResources(models.TextChoices):
        NONE = "0", "ไม่ใช้ทรัพยากรเพิ่มเติม"
        ONE = "1", "ใช้ 1 รายการ"
        MANY = "2_PLUS", "ใช้มากกว่า 1 รายการ"

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="triage_result")

    ai_severity = PostgresEnumField(
        enum_type="triage_severity_enum",
        max_length=10,
        choices=Visit.Severity.choices,
        blank=True,
        null=True,
    )
    nurse_severity = PostgresEnumField(
        enum_type="triage_severity_enum",
        max_length=10,
        choices=Visit.Severity.choices,
        blank=True,
        null=True,
    )

    model_name = models.CharField(max_length=50, blank=True, null=True)
    confidence = models.FloatField(blank=True, null=True)
    ai_reason = models.TextField(blank=True, default="")
    nurse_note = models.TextField(blank=True, default="")

    # Structured five-level triage decision points recorded by the nurse.
    # Null means the question has not been assessed yet; False is a deliberate
    # "no" answer and must remain distinguishable for model training.
    lifesaving_intervention = models.BooleanField(blank=True, null=True)
    high_risk_condition = models.BooleanField(blank=True, null=True)
    altered_mental_status = models.BooleanField(blank=True, null=True)
    mental_status = models.CharField(
        max_length=20,
        choices=MentalStatus.choices,
        blank=True,
        null=True,
    )
    severe_distress = models.BooleanField(blank=True, null=True)
    expected_resources = models.CharField(
        max_length=10,
        choices=ExpectedResources.choices,
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    location_updated_at = models.DateTimeField(blank=True, null=True)


class ConfirmedTriageCase(models.Model):
    """De-identified feature snapshot captured when a nurse confirms triage."""

    SNAPSHOT_VERSION = "v1"

    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name="confirmed_training_case",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="confirmed_triage_training_cases",
    )
    confirmed_at = models.DateTimeField(blank=True, null=True, db_index=True)

    age = models.PositiveSmallIntegerField(blank=True, null=True)
    nrs_pain = models.PositiveSmallIntegerField(blank=True, null=True)
    rr = models.IntegerField(blank=True, null=True)
    pr = models.IntegerField(blank=True, null=True)
    sys_bp = models.IntegerField(blank=True, null=True)
    dia_bp = models.IntegerField(blank=True, null=True)
    bt = models.FloatField(blank=True, null=True)
    o2sat = models.IntegerField(blank=True, null=True)
    chief_complain = models.TextField(blank=True, default="")
    urgent_symptoms = models.JSONField(default=list, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)

    lifesaving_intervention = models.BooleanField(blank=True, null=True)
    high_risk_condition = models.BooleanField(blank=True, null=True)
    altered_mental_status = models.BooleanField(blank=True, null=True)
    mental_status = models.CharField(
        max_length=20,
        choices=TriageResult.MentalStatus.choices,
        blank=True,
        null=True,
    )
    severe_distress = models.BooleanField(blank=True, null=True)
    expected_resources = models.CharField(
        max_length=10,
        choices=TriageResult.ExpectedResources.choices,
        blank=True,
        null=True,
    )

    ai_severity = PostgresEnumField(
        enum_type="triage_severity_enum",
        max_length=10,
        choices=Visit.Severity.choices,
        blank=True,
        null=True,
    )
    nurse_severity = PostgresEnumField(
        enum_type="triage_severity_enum",
        max_length=10,
        choices=Visit.Severity.choices,
    )
    model_name = models.CharField(max_length=120, blank=True, default="")
    confidence = models.FloatField(blank=True, null=True)
    ai_reason = models.TextField(blank=True, default="")
    nurse_note = models.TextField(blank=True, default="")

    is_ai_match = models.BooleanField(default=False, db_index=True)
    is_training_eligible = models.BooleanField(default=False, db_index=True)
    eligibility_note = models.CharField(max_length=220, blank=True, default="")
    snapshot_version = models.CharField(max_length=12, default=SNAPSHOT_VERSION)

    captured_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-confirmed_at", "-id"]
        indexes = [
            models.Index(fields=["nurse_severity", "-confirmed_at"]),
            models.Index(fields=["model_name", "-confirmed_at"]),
        ]

    def __str__(self):
        return f"ConfirmedTriageCase(visit={self.visit_id}, label={self.nurse_severity})"


class VisitWorkflowLog(models.Model):
    """Immutable accountability trail for actions performed during a visit."""

    class EventType(models.TextChoices):
        VITALS_RECORDED = "VITALS_RECORDED", "บันทึกสัญญาณชีพ"
        TRIAGE_CONFIRMED = "TRIAGE_CONFIRMED", "ยืนยันผลคัดกรอง"
        QUEUE_EXPEDITED = "QUEUE_EXPEDITED", "ลัดลำดับคิว"
        QUEUE_RESTORED = "QUEUE_RESTORED", "คืนลำดับคิวปกติ"
        QUEUE_NUMBER_CHANGED = "QUEUE_NUMBER_CHANGED", "เปลี่ยนเลขคิว"
        QUEUE_CALLED = "QUEUE_CALLED", "เรียกเข้าห้องตรวจ"
        QUEUE_TRANSFERRED = "QUEUE_TRANSFERRED", "ย้ายผู้ป่วย"
        NURSE_ASSIGNED = "NURSE_ASSIGNED", "มอบหมายพยาบาล"
        NURSE_REASSIGNED = "NURSE_REASSIGNED", "เปลี่ยนพยาบาลผู้รับผิดชอบ"
        NURSE_ASSIGNMENT_ENDED = "NURSE_ASSIGNMENT_ENDED", "สิ้นสุดการมอบหมายพยาบาล"
        CRITICAL_ALERT_CREATED = "CRITICAL_ALERT_CREATED", "สร้างสัญญาณเตือนวิกฤต"
        CRITICAL_ALERT_ACKNOWLEDGED = "CRITICAL_ALERT_ACKNOWLEDGED", "รับทราบสัญญาณเตือนวิกฤต"
        CRITICAL_ALERT_REVIEW_STARTED = "CRITICAL_ALERT_REVIEW_STARTED", "เริ่มตรวจผู้ป่วยจากสัญญาณเตือน"
        CRITICAL_ALERT_ESCALATED = "CRITICAL_ALERT_ESCALATED", "ยกระดับการดูแลจากสัญญาณเตือน"
        CRITICAL_ALERT_RESOLVED = "CRITICAL_ALERT_RESOLVED", "ปิดสัญญาณเตือนและกลับไปเฝ้าระวัง"
        CRITICAL_ALERT_FALSE_ALARM = "CRITICAL_ALERT_FALSE_ALARM", "ปิดสัญญาณเตือนเป็น false alarm"
        EMERGENCY_ACCEPTED = "EMERGENCY_ACCEPTED", "เจ้าหน้าที่ฉุกเฉินรับเคส"
        EMERGENCY_DISCHARGED = "EMERGENCY_DISCHARGED", "สิ้นสุดการรักษาฉุกเฉิน"
        EMERGENCY_REFERRED = "EMERGENCY_REFERRED", "ส่งต่อผู้ป่วยฉุกเฉิน"
        DOCTOR_ASSESSMENT = "DOCTOR_ASSESSMENT", "แพทย์บันทึกผลตรวจ"

    visit = models.ForeignKey(
        Visit,
        on_delete=models.CASCADE,
        related_name="workflow_logs",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="visit_workflow_logs",
    )
    actor_name = models.CharField(max_length=180, blank=True, default="")
    actor_role = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField(blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["visit", "-created_at"])]

    @classmethod
    def record(cls, *, visit, event_type, actor=None, description="", details=None):
        actor_name = "ระบบ"
        actor_role = "ระบบ"
        if actor and getattr(actor, "is_authenticated", False):
            actor_name = actor.get_full_name().strip() or actor.username
            if actor.is_superuser:
                actor_role = "ผู้ดูแลระบบสูงสุด"
            else:
                profile = getattr(actor, "hospital_staff_profile", None)
                actor_role = profile.get_role_display() if profile else "บุคลากร"
        return cls.objects.create(
            visit=visit,
            event_type=event_type,
            actor=actor if actor and getattr(actor, "is_authenticated", False) else None,
            actor_name=actor_name,
            actor_role=actor_role,
            description=description,
            details=details or {},
        )


class Device(models.Model):
    device_id = models.CharField(max_length=50, unique=True)
    api_key = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(blank=True, null=True)

    DEVICE_ID_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)$")

    @classmethod
    def suggest_next_device_id(cls, default_prefix="WATCH", default_width=3):
        """ดูเลข device_id ที่มากที่สุดที่มีอยู่ (เช่น WATCH011) แล้วเดาตัวถัดไป (WATCH012)."""
        best_prefix, best_width, best_num = default_prefix, default_width, 0

        for device_id in cls.objects.values_list("device_id", flat=True):
            match = cls.DEVICE_ID_PATTERN.match((device_id or "").strip())
            if not match:
                continue
            prefix, digits = match.group(1), match.group(2)
            num = int(digits)
            if num > best_num:
                best_prefix, best_width, best_num = prefix, len(digits), num

        next_num = best_num + 1
        return f"{best_prefix}{next_num:0{best_width}d}"

    def __str__(self):
        return self.device_id


class DeviceAssignment(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="assignments")
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="device_assignments")
    paired_at = models.DateTimeField(auto_now_add=True)
    unpaired_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["device", "is_active"]),
            models.Index(fields=["visit", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["device"],
                condition=Q(is_active=True),
                name="unique_active_assignment_per_device",
            ),
            models.UniqueConstraint(
                fields=["visit"],
                condition=Q(is_active=True),
                name="unique_active_assignment_per_visit",
            ),
        ]

    def __str__(self):
        state = "active" if self.is_active else "inactive"
        return f"{self.device.device_id} -> Visit#{self.visit_id} ({state})"


class StaffProfile(models.Model):
    """Persistent hospital role for a staff account."""

    class Role(models.TextChoices):
        DOCTOR = "DOCTOR", "แพทย์"
        NURSE = "NURSE", "พยาบาลวิชาชีพ (คัดกรอง/เฝ้าระวัง)"
        NURSE_ASSISTANT = "NURSE_ASSISTANT", "ผู้ช่วยพยาบาล (วัดสัญญาณชีพ)"
        EMERGENCY = "EMERGENCY", "เจ้าหน้าที่ฉุกเฉิน"
        STAFF = "STAFF", "เจ้าหน้าที่เวชระเบียน"
        QUEUE_OPERATOR = "QUEUE_OPERATOR", "เจ้าหน้าที่จัดคิว"
        BIOMEDICAL = "BIOMEDICAL", "เจ้าหน้าที่เครื่องมือแพทย์"

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="hospital_staff_profile",
    )
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.STAFF)
    photo = models.FileField(upload_to="staff_photos/%Y/%m/", blank=True)

    class Meta:
        ordering = ["role", "user__first_name", "user__username"]

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"


class StaffDuty(models.Model):
    """Daily attendance and availability managed by the ward coordinator."""

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="staff_duties",
    )
    duty_date = models.DateField(default=timezone.localdate)
    is_present = models.BooleanField(default=True)
    is_available = models.BooleanField(default=False)
    checked_in_at = models.DateTimeField(default=timezone.now)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "duty_date"],
                name="unique_staff_duty_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["duty_date", "is_present"]),
            models.Index(fields=["last_seen_at"]),
        ]
        ordering = ["user__first_name", "user__username"]

    def __str__(self):
        return f"{self.user} {self.duty_date}"


class ShiftSchedule(models.Model):
    """Planned staff roster; StaffDuty remains the actual attendance record."""

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "จัดเวรแล้ว"
        LEAVE = "LEAVE", "ลา"
        CANCELLED = "CANCELLED", "ยกเลิกเวร"

    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="shift_schedules",
    )
    shift_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = PostgresEnumField(
        enum_type="shift_schedule_status_enum",
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    note = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_shift_schedules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "shift_date", "start_time"],
                name="unique_staff_shift_start",
            ),
        ]
        indexes = [
            models.Index(fields=["shift_date", "status"]),
            models.Index(fields=["user", "shift_date"]),
        ]
        ordering = ["shift_date", "start_time", "user__first_name", "user__username"]

    def __str__(self):
        return f"{self.user} {self.shift_date} {self.start_time}-{self.end_time}"


class NurseCareAssignment(models.Model):
    """Current nurse responsible for a wearable-monitored visit."""

    nurse = models.ForeignKey(
        "auth.User",
        on_delete=models.PROTECT,
        related_name="patient_care_assignments",
    )
    visit = models.ForeignKey(
        Visit,
        on_delete=models.CASCADE,
        related_name="nurse_care_assignments",
    )
    assigned_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_patient_care_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["visit"],
                condition=Q(is_active=True),
                name="unique_active_nurse_per_visit",
            ),
        ]
        indexes = [
            models.Index(fields=["nurse", "is_active"]),
            models.Index(fields=["visit", "is_active"]),
        ]
        ordering = ["-assigned_at"]

    def __str__(self):
        state = "active" if self.is_active else "ended"
        return f"{self.nurse} -> Visit#{self.visit_id} ({state})"


class TelemetryLog(models.Model):
    # ไม่อ้าง "queues.Visit" เพื่อกัน resolve ไม่เจอ
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="telemetry_logs")
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True)

    ts = models.DateTimeField(default=timezone.now)

    bpm = models.IntegerField(blank=True, null=True)
    o2sat = models.IntegerField(blank=True, null=True)
    bt = models.FloatField(blank=True, null=True)
    rr = models.IntegerField(blank=True, null=True)
    sys_bp = models.IntegerField(blank=True, null=True)
    dia_bp = models.IntegerField(blank=True, null=True)

    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # ใช้ index แบบ field list เพื่อให้ migrate ได้ทุก database
        indexes = [
            models.Index(fields=["visit", "ts"]),
        ]



class CriticalAlert(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        IN_REVIEW = "IN_REVIEW", "Clinical review"
        ESCALATED = "ESCALATED", "Escalated"
        RESOLVED = "RESOLVED", "Resolved"
        FALSE_ALARM = "FALSE_ALARM", "False alarm"

    ACTIVE_STATUSES = (
        Status.NEW,
        Status.ACKNOWLEDGED,
        Status.IN_REVIEW,
        Status.ESCALATED,
    )

    class AlertType(models.TextChoices):
        LOW_O2 = "LOW_O2", "Low SpO2"
        LOW_BP = "LOW_BP", "Low systolic BP"
        HIGH_RR = "HIGH_RR", "High respiratory rate"
        HIGH_HEART_RATE = "HIGH_HEART_RATE", "High heart rate"
        LOW_HEART_RATE = "LOW_HEART_RATE", "Low heart rate"
        HIGH_TEMPERATURE = "HIGH_TEMPERATURE", "High temperature"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="critical_alerts")
    alert_type = models.CharField(max_length=24, choices=AlertType.choices)
    severity = PostgresEnumField(
        enum_type="triage_severity_enum",
        max_length=10,
        choices=Visit.Severity.choices,
        default=Visit.Severity.RED,
    )
    message = models.CharField(max_length=255)
    value = models.FloatField(null=True, blank=True)
    threshold = models.CharField(max_length=50, blank=True, default="")
    source = models.CharField(max_length=32, blank=True, default="vitals")
    status = PostgresEnumField(
        enum_type="critical_alert_status_enum",
        max_length=16,
        choices=Status.choices,
        default=Status.NEW,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_critical_alerts",
    )

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["visit", "status"]),
            models.Index(fields=["alert_type", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.alert_type} Visit#{self.visit_id} {self.status}"
