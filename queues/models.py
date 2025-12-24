from django.db import models
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

    updated_at = models.DateTimeField(auto_now=True)


class Queue(models.Model):
    class Status(models.TextChoices):
        WAITING = "WAITING", "รอ"
        CALLED = "CALLED", "เรียกแล้ว"
<<<<<<< HEAD
        DONE = "DONE", "เสร็จสิ้น"
=======
        OPD_DONE = "OPD_DONE", "ตรวจเสร็จสิ้น"
        FOLLOWUP = "FOLLOWUP", "นัดติดตาม"
>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16
        CANCELLED = "CANCELLED", "ยกเลิก"

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="queue")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)

    priority = models.IntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)


class TriageResult(models.Model):
    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="triage_result")

    ai_severity = models.CharField(max_length=10, choices=Visit.Severity.choices, blank=True, null=True)
    nurse_severity = models.CharField(max_length=10, choices=Visit.Severity.choices, blank=True, null=True)

    model_name = models.CharField(max_length=50, blank=True, null=True)
    confidence = models.FloatField(blank=True, null=True)

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


class TelemetryLog(models.Model):
    # ✅ แก้ตรงนี้: ไม่อ้าง "queues.Visit" แล้ว เพื่อกัน resolve ไม่เจอ
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
        # ✅ แก้ index ให้ migrate ผ่านแน่นอน (ไม่ใช้ "-ts")
        indexes = [
            models.Index(fields=["visit", "ts"]),
        ]
