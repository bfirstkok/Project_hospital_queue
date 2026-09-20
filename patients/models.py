from django.db import models
from django.conf import settings
from django.utils import timezone
from dateutil.relativedelta import relativedelta
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

    EMERGENCY_RELATIONSHIP_CHOICES = [
        ("FATHER", "พ่อ"),
        ("MOTHER", "แม่"),
        ("SPOUSE", "คู่สมรส"),
        ("CHILD", "บุตร"),
        ("SIBLING", "พี่น้อง"),
        ("RELATIVE", "ญาติ"),
        ("FRIEND", "เพื่อน"),
        ("CAREGIVER", "ผู้ดูแล"),
        ("OTHER", "อื่น ๆ"),
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
    email = models.EmailField(blank=True, null=True)

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

    # ความดันโลหิตล่าสุดที่เก็บในข้อมูลผู้ป่วย (ถ้ามี)
    bp_sys = models.PositiveIntegerField(null=True, blank=True)  # ตัวบน
    bp_dia = models.PositiveIntegerField(null=True, blank=True)  # ตัวล่าง

    emergency_name = models.CharField(max_length=120, blank=True, default="")
    emergency_relationship = models.CharField(
        max_length=20,
        choices=EMERGENCY_RELATIONSHIP_CHOICES,
        blank=True,
        default="",
    )
    emergency_phone = models.CharField(max_length=20, blank=True, default="")

    note = models.TextField(blank=True, default="")

    @property
    def age_breakdown(self):
        """Return the current age as years, months and days when DOB is known."""
        if not self.birth_date:
            return None
        today = timezone.localdate()
        if self.birth_date > today:
            return None
        difference = relativedelta(today, self.birth_date)
        return difference.years, difference.months, difference.days

    @property
    def age_display(self):
        breakdown = self.age_breakdown
        if breakdown:
            years, months, days = breakdown
            return f"{years} ปี {months} เดือน {days} วัน"
        if self.age is not None:
            return f"{self.age} ปี"
        return "-"

    @property
    def age_years(self):
        breakdown = self.age_breakdown
        return breakdown[0] if breakdown else self.age

    def save(self, *args, **kwargs):
        breakdown = self.age_breakdown
        if breakdown:
            self.age = breakdown[0]
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


class PatientAccessToken(models.Model):
    """Stores only a hash of a patient portal bearer token."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="access_tokens")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PatientAccessToken(patient={self.patient_id}, expires={self.expires_at:%Y-%m-%d %H:%M})"


class PatientPin(models.Model):
    """Server-side PIN state. PIN values are stored only as Django password hashes."""

    patient = models.OneToOneField(Patient, on_delete=models.CASCADE, related_name="pin")
    pin_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    lockout_level = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PatientPin(patient={self.patient_id}, level={self.lockout_level})"


class OtpChallenge(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"

    class Purpose(models.TextChoices):
        PIN_RESET = "PIN_RESET", "PIN reset"

    national_id = models.CharField(max_length=13, db_index=True)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    purpose = models.CharField(max_length=24, choices=Purpose.choices, default=Purpose.PIN_RESET)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["national_id", "channel", "purpose", "created_at"],
                name="otp_lookup_created_idx",
            ),
        ]

    def __str__(self):
        return f"OtpChallenge(channel={self.channel}, purpose={self.purpose}, consumed={bool(self.consumed_at)})"


class Appointment(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        ATTENDED = "ATTENDED", "Attended"
        MISSED = "MISSED", "Missed"
        CANCELLED = "CANCELLED", "Cancelled"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    date = models.DateField()
    time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    note = models.CharField(max_length=255, blank=True, default="")
    attended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        t = self.time.strftime("%H:%M") if self.time else "-"
        return f"Appointment {self.date} {t} {self.status} - Patient#{self.patient_id}"
