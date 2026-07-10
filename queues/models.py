from django.db import models
from django.db.models import Q
from django.utils import timezone

class Visit(models.Model):
    class Severity(models.TextChoices):
        RED = "RED", "แดง"
        YELLOW = "YELLOW", "เหลือง"
        GREEN = "GREEN", "เขียว"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="visits")

    registered_at = models.DateTimeField(auto_now_add=True)
    triaged_at = models.DateTimeField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    called_at = models.DateTimeField(blank=True, null=True)

    final_severity = models.CharField(max_length=10, choices=Severity.choices, blank=True, null=True)
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
        MONITORING = "MONITORING", "Monitoring"
        OPD_DONE = "OPD_DONE", "OPD Done"
        FOLLOWUP = "FOLLOWUP", "Follow-up"
        DISCHARGED = "DISCHARGED", "Discharged"
        CANCELLED = "CANCELLED", "Cancelled"

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="queue")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.WAITING_VITALS)

    priority = models.IntegerField(default=3)
    exam_room = models.PositiveSmallIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class TriageResult(models.Model):
    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="triage_result")

    ai_severity = models.CharField(max_length=10, choices=Visit.Severity.choices, blank=True, null=True)
    nurse_severity = models.CharField(max_length=10, choices=Visit.Severity.choices, blank=True, null=True)

    model_name = models.CharField(max_length=50, blank=True, null=True)
    confidence = models.FloatField(blank=True, null=True)
    ai_reason = models.TextField(blank=True, default="")
    nurse_note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    location_updated_at = models.DateTimeField(blank=True, null=True)


class Device(models.Model):
    device_id = models.CharField(max_length=50, unique=True)
    api_key = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(blank=True, null=True)

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


class IoTVital(models.Model):
    device_identifier = models.CharField(max_length=50)
    patient_identifier = models.CharField(max_length=50)
    device_db_id = models.IntegerField(null=True, blank=True, db_column="device_id")
    patient_db_id = models.IntegerField(null=True, blank=True, db_column="patient_id")

    heart_rate = models.IntegerField()
    spo2 = models.IntegerField()
    temperature = models.FloatField()
    respiratory_rate = models.IntegerField(null=True, blank=True)
    blood_pressure_sys = models.IntegerField(null=True, blank=True)
    blood_pressure_dia = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["device_identifier", "created_at"], name="queues_iotv_device__e34e63_idx"),
            models.Index(fields=["patient_identifier", "created_at"], name="queues_iotv_patient_d84c96_idx"),
            models.Index(fields=["patient_db_id", "created_at"], name="queues_iotv_patient_53d99b_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.patient_identifier} {self.device_identifier} {self.created_at:%Y-%m-%d %H:%M:%S}"


class CriticalAlert(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"

    class AlertType(models.TextChoices):
        LOW_O2 = "LOW_O2", "Low SpO2"
        LOW_BP = "LOW_BP", "Low systolic BP"
        HIGH_RR = "HIGH_RR", "High respiratory rate"

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="critical_alerts")
    alert_type = models.CharField(max_length=24, choices=AlertType.choices)
    severity = models.CharField(max_length=10, default=Visit.Severity.RED)
    message = models.CharField(max_length=255)
    value = models.FloatField(null=True, blank=True)
    threshold = models.CharField(max_length=50, blank=True, default="")
    source = models.CharField(max_length=32, blank=True, default="vitals")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
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
