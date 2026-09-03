from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from patients.models import Patient
from queues.models import Queue, StaffProfile, Visit

from .models import VisitAssessment


class DoctorSelectionTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user("operator", password="test-pass")
        StaffProfile.objects.create(user=self.operator, role=StaffProfile.Role.STAFF)
        self.doctor = get_user_model().objects.create_user(
            "doctor-one",
            password="test-pass",
            first_name="สมชาย",
            last_name="ใจดี",
        )
        StaffProfile.objects.create(user=self.doctor, role=StaffProfile.Role.DOCTOR)
        self.nurse = get_user_model().objects.create_user(
            "nurse-one",
            password="test-pass",
            first_name="สมหญิง",
            last_name="พยาบาล",
        )
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        patient = Patient.objects.create(
            first_name="ทดสอบ",
            last_name="ระบบ",
            national_id="1234567890123",
        )
        self.visit = Visit.objects.create(patient=patient, final_severity=Visit.Severity.GREEN)
        Queue.objects.create(visit=self.visit, status=Queue.Status.CALLED, exam_room=1)
        self.client.force_login(self.operator)

    def test_assessment_requires_doctor_selection(self):
        response = self.client.get(reverse("visit_assessment", args=[self.visit.id]))

        self.assertRedirects(response, reverse("select_examiner", args=[self.visit.id]))

    def test_picker_only_lists_named_doctors(self):
        response = self.client.get(reverse("select_examiner", args=[self.visit.id]))

        self.assertContains(response, "สมชาย ใจดี")
        self.assertNotContains(response, "สมหญิง พยาบาล")

    def test_nurse_cannot_be_selected_as_examiner(self):
        response = self.client.post(
            reverse("select_examiner", args=[self.visit.id]),
            {"doctor_id": self.nurse.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "กรุณาเลือกแพทย์ผู้ตรวจก่อนเข้าประเมิน")
        self.assertNotIn("opd_examiner_id", self.client.session)

    def test_selected_doctor_is_saved_with_assessment(self):
        response = self.client.post(
            reverse("select_examiner", args=[self.visit.id]),
            {"doctor_id": self.doctor.id},
        )
        self.assertRedirects(response, reverse("visit_assessment", args=[self.visit.id]))

        response = self.client.post(reverse("visit_assessment", args=[self.visit.id]), {})

        self.assertRedirects(response, reverse("opd_visit_detail", args=[self.visit.id]))
        assessment = VisitAssessment.objects.get(visit=self.visit)
        self.assertEqual(assessment.examiner, self.doctor)
