from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient
from queues.models import NurseCareAssignment, Queue, StaffDuty, StaffProfile, TriageResult, Visit


class YellowNurseAssignmentTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.coordinator = user_model.objects.create_user(
            username="coordinator",
            password="test-pass-123",
            first_name="ผู้ประสานงาน",
        )
        self.client.force_login(self.coordinator)

        self.nurse = user_model.objects.create_user(
            username="nurse_yellow",
            password="unused-pass",
            first_name="พิมพ์ใจ",
            last_name="พยาบาล",
        )
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        StaffDuty.objects.create(
            user=self.nurse,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
        )

        self.patient = Patient.objects.create(
            first_name="ผู้ป่วย",
            last_name="ทดสอบสีเหลือง",
            national_id="5555555555555",
        )

    def make_waiting_visit(self):
        visit = Visit.objects.create(patient=self.patient)
        Queue.objects.create(
            visit=visit,
            status=Queue.Status.WAITING_CONFIRMATION,
        )
        TriageResult.objects.create(
            visit=visit,
            ai_severity=Visit.Severity.YELLOW,
            ai_reason="yellow test",
        )
        return visit

    def make_active_assignment(self, nurse, number):
        patient = Patient.objects.create(
            first_name=f"โหลด{number}",
            last_name="พยาบาล",
            national_id=f"666666666{number:04d}",
        )
        visit = Visit.objects.create(patient=patient, final_severity=Visit.Severity.YELLOW)
        Queue.objects.create(
            visit=visit,
            status=Queue.Status.OBSERVATION_MONITORING,
        )
        return NurseCareAssignment.objects.create(
            nurse=nurse,
            visit=visit,
            assigned_by=self.coordinator,
        )

    def yellow_payload(self, **extra):
        payload = {
            "severity": Visit.Severity.YELLOW,
            "yellow_assignment_required": "1",
        }
        payload.update(extra)
        return payload

    def test_waiting_confirmation_shows_workload_and_auto_recommendation(self):
        self.make_waiting_visit()

        response = self.client.get(reverse("waiting_confirmation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "พยาบาลผู้รับผิดชอบ")
        self.assertContains(response, "พิมพ์ใจ พยาบาล")
        self.assertContains(response, "เลือกอัตโนมัติ")
        self.assertContains(response, "0/4")
        self.assertContains(response, "เหลือ 4 คน")
        self.assertContains(response, "ยืนยันพยาบาลและสีเหลือง")

    def test_yellow_confirmation_auto_assigns_when_nurse_id_is_omitted(self):
        visit = self.make_waiting_visit()

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            self.yellow_payload(),
        )

        self.assertRedirects(response, reverse("queue_list"))
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        assignment = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertEqual(assignment.nurse, self.nurse)
        self.assertEqual(visit.final_severity, Visit.Severity.YELLOW)
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)

    def test_yellow_confirmation_assigns_selected_available_nurse(self):
        visit = self.make_waiting_visit()

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            self.yellow_payload(nurse_id=str(self.nurse.id)),
        )

        self.assertRedirects(response, reverse("queue_list"))
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        self.assertEqual(visit.final_severity, Visit.Severity.YELLOW)
        self.assertIsNotNone(visit.confirmed_at)
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)
        assignment = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertEqual(assignment.nurse, self.nurse)
        self.assertEqual(assignment.assigned_by, self.coordinator)

    def test_auto_assignment_chooses_least_loaded_nurse(self):
        user_model = get_user_model()
        second_nurse = user_model.objects.create_user(
            username="nurse_less_busy",
            password="unused-pass",
            first_name="กมล",
            last_name="ว่างกว่า",
        )
        StaffProfile.objects.create(user=second_nurse, role=StaffProfile.Role.NURSE)
        StaffDuty.objects.create(
            user=second_nurse,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
        )
        self.make_active_assignment(self.nurse, 1)
        self.make_active_assignment(self.nurse, 2)
        visit = self.make_waiting_visit()

        response = self.client.get(reverse("waiting_confirmation"))
        self.assertContains(response, "2/4")
        self.assertContains(response, "0/4")

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            self.yellow_payload(),
        )

        self.assertRedirects(response, reverse("queue_list"))
        assignment = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertEqual(assignment.nurse, second_nurse)

    def test_nurse_at_four_patient_capacity_cannot_receive_another(self):
        for number in range(1, 5):
            self.make_active_assignment(self.nurse, number)
        visit = self.make_waiting_visit()

        page = self.client.get(reverse("waiting_confirmation"))
        self.assertContains(page, "4/4")
        self.assertContains(page, "เต็ม")

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            self.yellow_payload(),
        )

        self.assertRedirects(response, reverse("waiting_confirmation"))
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_CONFIRMATION)
        self.assertFalse(NurseCareAssignment.objects.filter(visit=visit, is_active=True).exists())

    def test_unavailable_selected_nurse_cannot_be_assigned(self):
        visit = self.make_waiting_visit()
        StaffDuty.objects.filter(user=self.nurse).update(is_available=False)

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            self.yellow_payload(nurse_id=str(self.nurse.id)),
        )

        self.assertRedirects(response, reverse("waiting_confirmation"))
        visit.refresh_from_db()
        self.assertIsNone(visit.final_severity)
        self.assertFalse(NurseCareAssignment.objects.filter(visit=visit, is_active=True).exists())
