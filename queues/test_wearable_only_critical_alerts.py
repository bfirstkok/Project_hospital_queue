from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from patients.models import Patient
from queues import views as queue_views
from queues.models import (
    CriticalAlert,
    NurseCareAssignment,
    Queue,
    StaffProfile,
    Visit,
    VisitWorkflowLog,
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
                status=CriticalAlert.Status.RESOLVED,
            ).exists()
        )

        response = self.client.get(reverse("my_critical_alerts"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)

    def test_my_alerts_returns_django_reversed_action_urls(self):
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=90,
            o2sat=92,
            bt=37.0,
            rr=20,
        )
        alert = queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="iot_vitals",
        )[0]

        response = self.client.get(reverse("my_critical_alerts"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()["alerts"][0]
        self.assertEqual(payload["id"], alert.id)
        self.assertEqual(payload["actions"]["ack"], reverse("acknowledge_alert", args=[alert.id]))
        self.assertEqual(payload["actions"]["review"], reverse("start_alert_review", args=[alert.id]))
        self.assertEqual(payload["actions"]["resolve"], reverse("resolve_alert", args=[alert.id]))
        self.assertEqual(payload["actions"]["false_alarm"], reverse("false_alarm_alert", args=[alert.id]))
        self.assertEqual(payload["actions"]["escalate"], reverse("escalate_alert", args=[alert.id]))

    def test_global_alert_page_sets_csrf_cookie_and_allows_real_csrf_checked_post(self):
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=90,
            o2sat=92,
            bt=37.0,
            rr=20,
        )
        alert = queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="iot_vitals",
        )[0]

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.nurse)
        page = csrf_client.get(reverse("monitor_dashboard"))

        self.assertEqual(page.status_code, 200)
        self.assertIn("csrftoken", csrf_client.cookies)
        self.assertContains(page, 'id="globalCsrfToken"')

        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(
            reverse("acknowledge_alert", args=[alert.id]),
            HTTP_X_CSRFTOKEN=token,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        alert.refresh_from_db()
        self.assertEqual(alert.status, CriticalAlert.Status.ACKNOWLEDGED)

    def test_wearable_alert_acknowledgement_records_actor_and_workflow_log(self):
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=128,
            o2sat=92,
            bt=37.0,
            rr=20,
        )
        created = queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="iot_vitals",
        )
        alert = created[0]

        created_log = VisitWorkflowLog.objects.get(
            visit=self.visit,
            event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_CREATED,
            details__alert_id=alert.id,
        )
        self.assertIsNone(created_log.actor)
        self.assertEqual(created_log.actor_name, "ระบบ")
        self.assertEqual(created_log.details["source"], "iot_vitals")

        response = self.client.post(reverse("acknowledge_alert", args=[alert.id]))
        self.assertEqual(response.status_code, 200)

        alert.refresh_from_db()
        self.assertEqual(alert.status, CriticalAlert.Status.ACKNOWLEDGED)
        self.assertEqual(alert.acknowledged_by, self.nurse)
        self.assertIsNotNone(alert.acknowledged_at)

        acknowledged_log = VisitWorkflowLog.objects.get(
            visit=self.visit,
            event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ACKNOWLEDGED,
            details__alert_id=alert.id,
        )
        self.assertEqual(acknowledged_log.actor, self.nurse)
        self.assertEqual(acknowledged_log.details["alert_type"], alert.alert_type)

        second_response = self.client.post(reverse("acknowledge_alert", args=[alert.id]))
        self.assertEqual(second_response.status_code, 200)
        self.assertTrue(second_response.json()["already_acknowledged"])
        self.assertEqual(
            VisitWorkflowLog.objects.filter(
                visit=self.visit,
                event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ACKNOWLEDGED,
                details__alert_id=alert.id,
            ).count(),
            1,
        )

    def test_wearable_alert_uses_clinical_review_flow_without_retriage(self):
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=128,
            o2sat=92,
            bt=37.0,
            rr=20,
        )
        alert = queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="iot_vitals",
        )[0]

        self.visit.queue.refresh_from_db()
        self.assertEqual(
            self.visit.queue.status,
            Queue.Status.OBSERVATION_MONITORING,
        )

        self.client.post(reverse("acknowledge_alert", args=[alert.id]))
        response = self.client.post(reverse("start_alert_review", args=[alert.id]))
        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        self.assertEqual(alert.status, CriticalAlert.Status.IN_REVIEW)

        response = self.client.post(reverse("escalate_alert", args=[alert.id]))
        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        self.assertEqual(alert.status, CriticalAlert.Status.ESCALATED)
        self.assertTrue(
            VisitWorkflowLog.objects.filter(
                visit=self.visit,
                event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ESCALATED,
                actor=self.nurse,
                details__alert_id=alert.id,
            ).exists()
        )

        response = self.client.post(reverse("resolve_alert", args=[alert.id]))
        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        self.assertEqual(alert.status, CriticalAlert.Status.RESOLVED)
        self.visit.queue.refresh_from_db()
        self.assertEqual(
            self.visit.queue.status,
            Queue.Status.OBSERVATION_MONITORING,
        )

    def test_nurse_cannot_acknowledge_alert_owned_by_another_nurse(self):
        other_nurse = get_user_model().objects.create_user(
            username="other-alert-nurse",
            password="secret",
        )
        StaffProfile.objects.create(user=other_nurse, role=StaffProfile.Role.NURSE)
        vitals = VitalSign.objects.create(
            visit=self.visit,
            pr=130,
            o2sat=98,
            bt=37.0,
            rr=20,
        )
        alert = queue_views.create_critical_alerts_for_visit(
            self.visit,
            vitals,
            source="iot_vitals",
        )[0]

        self.client.force_login(other_nurse)
        response = self.client.post(reverse("acknowledge_alert", args=[alert.id]))

        self.assertEqual(response.status_code, 403)
        alert.refresh_from_db()
        self.assertEqual(alert.status, CriticalAlert.Status.NEW)
        self.assertIsNone(alert.acknowledged_by)
        self.assertFalse(
            VisitWorkflowLog.objects.filter(
                visit=self.visit,
                event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ACKNOWLEDGED,
                details__alert_id=alert.id,
            ).exists()
        )

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
        self.assertEqual(plain.status, CriticalAlert.Status.RESOLVED)
        self.assertEqual(wearable.status, CriticalAlert.Status.NEW)
