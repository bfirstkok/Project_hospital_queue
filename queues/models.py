from django.conf import settings
from django.db import models

class Visit(models.Model):
    class Severity(models.TextChoices):
        RED = "RED", "แดง"
        YELLOW = "YELLOW", "เหลือง"
        GREEN = "GREEN", "เขียว"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="visits")

    # เวลา log สำคัญ (อาจารย์ดู)
    registered_at = models.DateTimeField(auto_now_add=True)     # เวลาลงทะเบียน
    triaged_at = models.DateTimeField(blank=True, null=True)    # เวลาคัดกรอง/ยืนยันสี
    called_at = models.DateTimeField(blank=True, null=True)     # เวลาเรียกตรวจ

    # ระดับความรุนแรงที่ยืนยันแล้ว
    final_severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.GREEN)

    note = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Visit#{self.id} {self.patient}"

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
        DONE = "DONE", "เสร็จสิ้น"
        CANCELLED = "CANCELLED", "ยกเลิก"

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="queue")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)

    # สำหรับจัดลำดับในคิว (เลขน้อยมาก่อน)
    priority = models.IntegerField(default=3)

    created_at = models.DateTimeField(auto_now_add=True)

class TriageResult(models.Model):
    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="triage_result")

    ai_severity = models.CharField(max_length=10, blank=True, null=True)   # ผลที่ AI แนะนำ
    nurse_severity = models.CharField(max_length=10, blank=True, null=True) # สีที่พยาบาลเลือก/ยืนยัน

    model_name = models.CharField(max_length=50, blank=True, null=True)
    confidence = models.FloatField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
