from django.db import models
from django.conf import settings
import random


class Patient(models.Model):
    GENDER_CHOICES = [
        ("M", "ชาย"),
        ("F", "หญิง"),
        ("O", "อื่นๆ"),
        ("UNKNOWN", "ไม่ระบุ"),
    ]

    BLOOD_CHOICES = [
        ("A", "A"), ("B", "B"), ("AB", "AB"), ("O", "O"),
        ("UNKNOWN", "ไม่ทราบ"),
    ]

    # ที่อยู่แบบกดเลือก (ปรับได้ตามจริง)
    ADDRESS_AREA_CHOICES = [
        ("AREA1", "โซน 1"),
        ("AREA2", "โซน 2"),
        ("AREA3", "โซน 3"),
        ("OTHER", "อื่นๆ"),
    ]

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    national_id = models.CharField(max_length=13, unique=True)

    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default="UNKNOWN")
    birth_date = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True, default="ไทย")
    age = models.PositiveIntegerField(null=True, blank=True)

    phone = models.CharField(max_length=20, blank=True, default="")

    # ✅ HN เจนอัตโนมัติ 6 หลัก และกันซ้ำ
    hn = models.CharField(max_length=6, unique=True, blank=True, default="", db_index=True)

    address_area = models.CharField(max_length=20, choices=ADDRESS_AREA_CHOICES, blank=True, default="")
    address = models.TextField(blank=True, default="")

    blood_type = models.CharField(max_length=10, choices=BLOOD_CHOICES, default="UNKNOWN")

    primary_doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="doctor_patients",
    )
    responsible_nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="nurse_patients",
    )

    chronic_diseases = models.TextField(blank=True, default="")
    allergies = models.TextField(blank=True, default="")
    medications = models.TextField(blank=True, default="")

    height_cm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    bp_sys = models.PositiveIntegerField(null=True, blank=True)  # ตัวบน
    bp_dia = models.PositiveIntegerField(null=True, blank=True)  # ตัวล่าง

    # ✅ ความดัน
    bp_sys = models.PositiveIntegerField(null=True, blank=True)
    bp_dia = models.PositiveIntegerField(null=True, blank=True)

    emergency_name = models.CharField(max_length=120, blank=True, default="")
    emergency_phone = models.CharField(max_length=20, blank=True, default="")

    note = models.TextField(blank=True, default="")

    def save(self, *args, **kwargs):
        # ถ้ายังไม่มี HN → สุ่ม 6 หลักให้เอง และกันซ้ำ
        if not self.hn:
            for _ in range(50):
                candidate = f"{random.randint(0, 999999):06d}"
                if not Patient.objects.filter(hn=candidate).exists():
                    self.hn = candidate
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name} (CID:{self.national_id}, HN:{self.hn})"
    
    province = models.CharField(max_length=100, blank=True, default="")
    district = models.CharField(max_length=100, blank=True, default="")
    subdistrict = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=5, blank=True, default="")


class Appointment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    date = models.DateField()
    time = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        t = self.time.strftime("%H:%M") if self.time else "-"
        return f"Appointment {self.date} {t} - Patient#{self.patient_id}"


class Assessment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="assessments")
    detail = models.TextField()
    assessed_at = models.DateTimeField(auto_now_add=True)
    assessor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="assessments_made",
    )

    def __str__(self):
        who = getattr(self.assessor, "username", None) or "unknown"
        return f"Assessment {self.assessed_at:%Y-%m-%d %H:%M} by {who} - Patient#{self.patient_id}"
