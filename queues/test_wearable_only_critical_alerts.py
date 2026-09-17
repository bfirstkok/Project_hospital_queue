from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from patients.models import Patient
from queues import views as queue_views
from queues.models import (
    CriticalAlert,
    NurseCareAssignment,
    Queue,
    StaffProfile,
    Visit,
    VitalSign,
)


class WearableOnlyCriticalAlertTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.nurse = user_model.objects.create_user(
            username="wearable-alert-nurse",
            password="secret",
        )
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        self.patient = Patient.objects.create(
            first_name="ทดสอบ",
            last_name="แจ้งเตือนนาฬิกา",
            national_id="7777777777777",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            final_severity=Visit.Severity.YELLOW,
        )
        Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.OBSERVATION_MONITORING,
            priority=3,
        )
        NurseCareAssignment.objects.create(nurse=self.nurse, visit=self.visit)
        self.client.force_login(self.nurse)

    def test_manual_triage_critical_values_are_not_left_as_new_notifications(self):
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=132,
            o2sat=89,
            bt=39.5,
            rr=34,
            sys_bp=84,
            dia_bp=55,
        )

        queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="triage",
        )

        self.assertFalse(
            CriticalAlert.objects.filter(
                visit=self.visit,
                source="triage",
                status=CriticalAlert.Status.NEW,
            ).exists()
        )
        self.assertTrue(
            CriticalAlert.objects.filter(
                visit=self.visit,
                source="triage",
                status=CriticalAlert.Status.ACKNOWLEDGED,
            ).exists()
        )

        response = self.client.get(reverse("my_critical_alerts"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)

    def test_wearable_critical_values_remain_new_and_are_shown(self):
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=128,
            o2sat=92,
            bt=39.4,
            rr=32,
        )

        queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="iot_vitals",
        )

        new_alerts = CriticalAlert.objects.filter(
            visit=self.visit,
            source="iot_vitals",
            status=CriticalAlert.Status.NEW,
        )
        self.assertTrue(new_alerts.exists())

        response = self.client.get(reverse("my_critical_alerts"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], new_alerts.count())
        self.assertTrue(response.json()["alerts"])

    def test_system_test_iot_is_treated_as_wearable_but_plain_system_test_is_suppressed(self):
        plain = CriticalAlert.objects.create(
            visit=self.visit,
            alert_type=CriticalAlert.AlertType.LOW_O2,
            message="manual system test",
            value=88,
            threshold="< 95",
            source="system_test",
        )
        wearable = CriticalAlert.objects.create(
            visit=self.visit,
            alert_type=CriticalAlert.AlertType.HIGH_HEART_RATE,
            message="wearable system test",
            value=130,
            threshold=">= 120",
            source="system_test_iot",
        )

        plain.refresh_from_db()
        wearable.refresh_from_db()
        self.assertEqual(plain.status, CriticalAlert.Status.ACKNOWLEDGED)
        self.assertEqual(wearable.status, CriticalAlert.Status.NEW)
