from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from patients.models import Patient
from queues.models import NurseCareAssignment, Queue, StaffProfile, Visit


class ResponsibleNurseDetailTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.viewer = user_model.objects.create_user(username="detail-viewer", password="secret")
        self.client.force_login(self.viewer)

        self.nurse = user_model.objects.create_user(
            username="nurse-detail",
            password="secret",
            first_name="พิมพ์ใจ",
            last_name="ใจดี",
        )
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)

        self.patient = Patient.objects.create(
            first_name="ผู้ป่วย",
            last_name="รายละเอียด",
            national_id="4444444444444",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            final_severity=Visit.Severity.YELLOW,
        )
        Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.WAITING_QUEUE,
            priority=3,
        )

    def test_monitor_visit_detail_shows_active_responsible_nurse(self):
        NurseCareAssignment.objects.create(
            nurse=self.nurse,
            visit=self.visit,
            assigned_by=self.viewer,
        )

        response = self.client.get(reverse("waiting_monitor_visit_detail", args=[self.visit.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "พยาบาลผู้ดูแล")
        self.assertContains(response, "พิมพ์ใจ ใจดี")
        self.assertContains(response, "nurse-detail")

    def test_yellow_detail_marks_missing_responsible_nurse(self):
        response = self.client.get(reverse("waiting_monitor_visit_detail", args=[self.visit.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ยังไม่มอบหมายพยาบาลผู้ดูแล")
