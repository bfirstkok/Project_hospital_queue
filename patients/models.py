from django.db import models

class Patient(models.Model):
    cid = models.CharField("เลขบัตรประชาชน", max_length=13, unique=True)
    hn = models.CharField("HN", max_length=20, blank=True, null=True)

    first_name = models.CharField("ชื่อ", max_length=100)
    last_name = models.CharField("นามสกุล", max_length=100)

    phone = models.CharField("เบอร์โทร", max_length=20, blank=True, null=True)
    birth_date = models.DateField("วันเกิด", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.cid})"
