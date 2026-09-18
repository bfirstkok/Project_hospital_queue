from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient
from queues.models import Queue, StaffProfile, Visit, VisitWorkflowLog


class PatientTransferTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.operator = users.objects.create_user("queue-transfer", password="secret")
        StaffProfile.objects.create(user=self.operator, role=StaffProfile.Role.QUEUE_OPERATOR)
        self.doctor = users.objects.create_user("doctor-transfer", password="secret")
        StaffProfile.objects.create(user=self.doctor, role=StaffProfile.Role.DOCTOR)
        self.nurse = users.objects.create_user("nurse-transfer", password="secret")
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        patient = Patient.objects.create(first_name="Demo", last_name="Transfer")
        self.visit = Visit.objects.create(patient=patient, final_severity=Visit.Severity.GREEN, confirmed_at=timezone.now(), called_at=timezone.now())
        self.queue = Queue.objects.create(visit=self.visit, status=Queue.Status.CALLED, exam_room=1, priority=4)
        self.url = reverse("transfer_patient", args=[self.visit.id])

    def test_queue_operator_moves_patient_to_another_room_and_logs_reason(self):
        self.client.force_login(self.operator)
        response = self.client.post(self.url, {"destination": "room_2", "reason": "room one unavailable", "next": "queue"})
        self.assertRedirects(response, reverse("queue_list"))
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.exam_room, 2)
        log = VisitWorkflowLog.objects.filter(visit=self.visit).latest("created_at")
        self.assertEqual(log.actor, self.operator)
        self.assertEqual(log.details["action"], "room_transfer")
        self.assertEqual(log.details["from_room"], 1)
        self.assertEqual(log.details["to_room"], 2)

    def test_queue_operator_returns_patient_to_waiting(self):
        self.client.force_login(self.operator)
        response = self.client.post(self.url, {"destination": "waiting", "reason": "return to waiting", "next": "queue"})
        self.assertRedirects(response, reverse("queue_list"))
        self.queue.refresh_from_db()
        self.visit.refresh_from_db()
        self.assertEqual(self.queue.status, Queue.Status.WAITING_QUEUE)
        self.assertIsNone(self.queue.exam_room)
        self.assertIsNone(self.visit.called_at)

    def test_doctor_can_transfer_patient(self):
        self.client.force_login(self.doctor)
        session = self.client.session
        session["opd_exam_room"] = 1
        session.save()
        response = self.client.post(self.url, {"destination": "room_3", "reason": "doctor handoff", "next": "opd"})
        self.assertRedirects(response, reverse("opd_list"))
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.exam_room, 3)

    def test_regular_nurse_cannot_transfer_patient(self):
        self.client.force_login(self.nurse)
        response = self.client.post(self.url, {"destination": "room_2", "reason": "permission check"})
        self.assertEqual(response.status_code, 403)
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.exam_room, 1)

    def test_transfer_requires_reason(self):
        self.client.force_login(self.operator)
        self.client.post(self.url, {"destination": "room_2", "reason": "", "next": "queue"})
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.exam_room, 1)

    def test_queue_page_removes_triage_controls(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("queue_list"))
        self.assertNotContains(response, "เปลี่ยนระดับ…")
        self.assertNotContains(response, ">ประเมิน<", html=False)
        self.assertContains(response, "ย้ายผู้ป่วย")
