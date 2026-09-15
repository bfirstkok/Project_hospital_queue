from django.conf import settings
from django.db import models


class TestScenarioRun(models.Model):
    class Scenario(models.TextChoices):
        FULL = "FULL", "ครบกระบวนการและติดตามด้วยนาฬิกา"
        WAITING = "WAITING", "ผู้ป่วยใหม่รอวัดสัญญาณชีพ"
        EMERGENCY = "EMERGENCY", "ผู้ป่วยฉุกเฉินส่งต่อทันที"

    scenario = models.CharField(max_length=16, choices=Scenario.choices)
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True)
    visit = models.ForeignKey("queues.Visit", on_delete=models.SET_NULL, null=True, blank=True)
    device = models.ForeignKey("queues.Device", on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"TEST-{self.pk} {self.scenario}"
