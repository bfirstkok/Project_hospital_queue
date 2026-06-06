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
    called_at = models.DateTimeField(blank=True, null=True)

    final_severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.GREEN)
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
        WAITING = "WAITING", "Waiting"
        CALLED = "CALLED", "Called"
        MONITORING = "MONITORING", "Monitoring"
        OPD_DONE = "OPD_DONE", "OPD Done"
        FOLLOWUP = "FOLLOWUP", "Follow-up"
        DISCHARGED = "DISCHARGED", "Discharged"
        CANCELLED = "CANCELLED", "Cancelled"

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="queue")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WAITING)

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
