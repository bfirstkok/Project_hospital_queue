from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient
from queues.models import Queue, ShiftSchedule, StaffProfile, Visit, VisitWorkflowLog

from .models import VisitAssessment


class DoctorWorkspaceTests(TestCase):
    def setUp(self):
        self.doctor = get_user_model().objects.create_user(
            "doctor-room-one",
            password="test-pass",
            first_name="แพทย์",
            last_name="ห้องหนึ่ง",
        )
        StaffProfile.objects.create(user=self.doctor, role=StaffProfile.Role.DOCTOR)

        self.other_doctor = get_user_model().objects.create_user(
            "doctor-room-two",
            password="test-pass",
            first_name="แพทย์",
            last_name="ห้องสอง",
        )
        StaffProfile.objects.create(user=self.other_doctor, role=StaffProfile.Role.DOCTOR)

        patient_one = Patient.objects.create(
            first_name="ผู้ป่วย",
            last_name="ห้องหนึ่ง",
            national_id="1234567890123",
        )
        self.visit_room_one = Visit.objects.create(
            patient=patient_one,
            final_severity=Visit.Severity.GREEN,
        )
        Queue.objects.create(
            visit=self.visit_room_one,
            status=Queue.Status.CALLED,
            exam_room=1,
        )

        patient_two = Patient.objects.create(
            first_name="ผู้ป่วย",
            last_name="ห้องสอง",
            national_id="2234567890123",
        )
        self.visit_room_two = Visit.objects.create(
            patient=patient_two,
            final_severity=Visit.Severity.GREEN,
        )
        Queue.objects.create(
            visit=self.visit_room_two,
            status=Queue.Status.CALLED,
            exam_room=2,
        )

        self.client.force_login(self.doctor)

    def _assign_room_one_for_today(self):
        return ShiftSchedule.objects.create(
            user=self.doctor,
            shift_date=timezone.localdate(),
            start_time=time(0, 0),
            end_time=time.max,
            status=ShiftSchedule.Status.SCHEDULED,
            note="ประจำห้องตรวจ 1",
            created_by=self.doctor,
        )

    def test_regular_doctor_is_automatically_the_examiner(self):
        response = self.client.get(
            reverse("select_examiner", args=[self.visit_room_one.id])
        )
        self.assertRedirects(
            response,
            reverse("visit_assessment", args=[self.visit_room_one.id]),
        )

        session = self.client.session
        self.assertEqual(session["opd_examiner_id"], self.doctor.id)
        self.assertEqual(
            session["opd_examiner_visit_id"],
            self.visit_room_one.id,
        )

    def test_direct_assessment_uses_signed_in_doctor(self):
        response = self.client.get(
            reverse("visit_assessment", args=[self.visit_room_one.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "แพทย์ ห้องหนึ่ง")

    def test_assessment_shows_quick_phrases_for_common_notes(self):
        response = self.client.get(
            reverse("visit_assessment", args=[self.visit_room_one.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ข้อความที่ใช้บ่อย")
        self.assertContains(response, "Acute URI / ไข้หวัด")
        self.assertContains(response, "ให้ยาตามอาการ")
        self.assertContains(response, "รับไว้ติดตามอาการในโรงพยาบาล")

    def test_signed_in_doctor_is_saved_with_assessment(self):
        response = self.client.post(
            reverse("visit_assessment", args=[self.visit_room_one.id]),
            {},
        )

        self.assertRedirects(
            response,
            reverse("opd_visit_detail", args=[self.visit_room_one.id]),
        )
        assessment = VisitAssessment.objects.get(visit=self.visit_room_one)
        self.assertEqual(assessment.examiner, self.doctor)
        log = VisitWorkflowLog.objects.get(
            visit=self.visit_room_one,
            event_type=VisitWorkflowLog.EventType.DOCTOR_ASSESSMENT,
        )
        self.assertEqual(log.actor, self.doctor)
        self.assertEqual(log.actor_name, "แพทย์ ห้องหนึ่ง")

    def test_active_roster_assignment_forces_room_and_filters_other_rooms(self):
        self._assign_room_one_for_today()
        session = self.client.session
        session["opd_exam_room"] = 2
        session.save()

        response = self.client.get(reverse("opd_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_room"], 1)
        self.assertTrue(response.context["room_locked"])
        self.assertContains(response, "ผู้ป่วย ห้องหนึ่ง")
        self.assertNotContains(response, "ผู้ป่วย ห้องสอง")
        self.assertEqual(self.client.session["opd_exam_room"], 1)

    def test_room_selector_cannot_override_active_roster_assignment(self):
        self._assign_room_one_for_today()

        response = self.client.post(
            reverse("opd_room_select"),
            {"exam_room": "2"},
        )

        self.assertRedirects(response, reverse("opd_list"))
        self.assertEqual(self.client.session["opd_exam_room"], 1)

    def test_doctor_cannot_open_other_room_assessment_during_locked_shift(self):
        self._assign_room_one_for_today()

        response = self.client.get(
            reverse("visit_assessment", args=[self.visit_room_two.id])
        )

        self.assertRedirects(response, reverse("opd_list"))
        self.assertFalse(
            VisitAssessment.objects.filter(visit=self.visit_room_two).exists()
        )


class SuperuserDoctorSelectionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "admin",
            "admin@example.com",
            "test-pass",
        )
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
            national_id="3234567890123",
        )
        self.visit = Visit.objects.create(
            patient=patient,
            final_severity=Visit.Severity.GREEN,
        )
        Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.CALLED,
            exam_room=1,
        )
        self.client.force_login(self.admin)

    def test_superuser_can_choose_a_doctor_but_not_a_nurse(self):
        response = self.client.get(
            reverse("select_examiner", args=[self.visit.id])
        )
        self.assertContains(response, "สมชาย ใจดี")
        self.assertNotContains(response, "สมหญิง พยาบาล")

        response = self.client.post(
            reverse("select_examiner", args=[self.visit.id]),
            {"doctor_id": self.nurse.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "กรุณาเลือกแพทย์ผู้ตรวจก่อนเข้าประเมิน",
        )
