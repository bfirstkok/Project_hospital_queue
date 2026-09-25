from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient
from queues.models import Queue, ShiftSchedule, StaffProfile, Visit, VisitWorkflowLog, VitalSign

from .models import Bill, PatientCoverage, Prescription, PrescriptionItem, VisitAssessment


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


    def test_assessment_prefills_known_registration_and_triage_data(self):
        today = timezone.localdate()
        patient = self.visit_room_one.patient
        patient.birth_date = date(today.year - 21, today.month, min(today.day, 28))
        patient.bp_sys = 124
        patient.bp_dia = 78
        patient.save(update_fields=["birth_date", "bp_sys", "bp_dia"])

        self.visit_room_one.note = "ไอ มีไข้ และอ่อนเพลีย"
        self.visit_room_one.save(update_fields=["note"])
        VitalSign.objects.create(
            visit=self.visit_room_one,
            pain_score=6,
            bt=38.1,
            sys_bp=118,
            dia_bp=74,
            pr=96,
            rr=22,
            o2sat=97,
            risk_flags=["pregnant", "immunocompromised"],
        )

        response = self.client.get(
            reverse("visit_assessment", args=[self.visit_room_one.id])
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form["age"].value(), 21)
        self.assertTrue(form.fields["age"].disabled)
        self.assertEqual(form["chief_complaint"].value(), "ไอ มีไข้ และอ่อนเพลีย")
        self.assertEqual(form["pain_score"].value(), 6)
        self.assertEqual(form["bt"].value(), 38.1)
        self.assertEqual(form["sys_bp"].value(), 118)
        self.assertEqual(form["dia_bp"].value(), 74)
        self.assertTrue(form["pregnant"].value())
        self.assertTrue(form["low_immunity"].value())
        self.assertContains(response, "ข้อมูลที่ระบบกรอกให้จาก Registration / Triage")
        self.assertContains(response, "AUTO PREFILL")
        self.assertContains(response, "ตั้งครรภ์")
        self.assertContains(response, "ภูมิคุ้มกันต่ำ")

    def test_signed_in_doctor_is_saved_with_assessment(self):
        response = self.client.post(
            reverse("visit_assessment", args=[self.visit_room_one.id]),
            {},
        )

        self.assertRedirects(
            response,
            reverse("opd_care_plan", args=[self.visit_room_one.id]),
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



class OpdDownstreamWorkflowTests(TestCase):
    def setUp(self):
        self.doctor = get_user_model().objects.create_user(
            "workflow-doctor",
            password="test-pass",
            first_name="แพทย์",
            last_name="Workflow",
        )
        StaffProfile.objects.create(user=self.doctor, role=StaffProfile.Role.DOCTOR)
        self.pharmacist = get_user_model().objects.create_user(
            "workflow-pharmacist",
            password="test-pass",
            first_name="เภสัช",
            last_name="ทดสอบ",
        )
        StaffProfile.objects.create(user=self.pharmacist, role=StaffProfile.Role.PHARMACIST)
        self.cashier = get_user_model().objects.create_user(
            "workflow-cashier",
            password="test-pass",
            first_name="การเงิน",
            last_name="ทดสอบ",
        )
        StaffProfile.objects.create(user=self.cashier, role=StaffProfile.Role.CASHIER)
        patient = Patient.objects.create(
            first_name="ผู้ป่วย",
            last_name="ปลายทาง",
            national_id="4234567890123",
        )
        self.visit = Visit.objects.create(
            patient=patient,
            final_severity=Visit.Severity.GREEN,
        )
        Queue.objects.create(visit=self.visit, status=Queue.Status.OPD_DONE, exam_room=1)
        VisitAssessment.objects.create(
            visit=self.visit,
            examiner=self.doctor,
            diagnosis="Acute URI",
            treatment="Symptomatic treatment",
        )

    def test_doctor_can_build_prescription_and_send_to_pharmacy(self):
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("opd_care_plan", args=[self.visit.id]),
            {
                "action": "add_medication",
                "medication_name": "Paracetamol",
                "strength": "500 mg",
                "dosage": "ครั้งละ 1 เม็ด",
                "frequency": "วันละ 3 ครั้ง",
                "duration_days": "5",
                "quantity": "15",
                "unit": "เม็ด",
                "unit_price": "2.00",
            },
        )
        self.assertRedirects(response, reverse("opd_care_plan", args=[self.visit.id]))
        prescription = Prescription.objects.get(visit=self.visit)
        self.assertEqual(prescription.items.count(), 1)
        self.assertEqual(prescription.status, Prescription.Status.DRAFT)

        response = self.client.post(
            reverse("opd_care_plan", args=[self.visit.id]),
            {"action": "send_pharmacy"},
        )
        self.assertRedirects(response, reverse("opd_care_plan", args=[self.visit.id]))
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, Prescription.Status.SENT)
        self.assertTrue(Bill.objects.filter(visit=self.visit).exists())

    def test_doctor_cannot_send_billing_before_draft_prescription_is_sent(self):
        prescription = Prescription.objects.create(
            visit=self.visit,
            prescribed_by=self.doctor,
            status=Prescription.Status.DRAFT,
        )
        PrescriptionItem.objects.create(
            prescription=prescription,
            medication_name="Paracetamol",
            quantity=10,
            unit="เม็ด",
            unit_price="2.00",
        )
        self.client.force_login(self.doctor)

        response = self.client.post(
            reverse("opd_care_plan", args=[self.visit.id]),
            {"action": "send_billing"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "กรุณากด “ส่งใบสั่งยาไปห้องยา” ก่อนส่งการเงิน")
        self.assertFalse(Bill.objects.filter(visit=self.visit).exists())

    def test_pharmacist_can_dispense_and_cashier_can_apply_coverage_and_pay(self):
        prescription = Prescription.objects.create(
            visit=self.visit,
            prescribed_by=self.doctor,
            status=Prescription.Status.SENT,
            sent_at=timezone.now(),
        )
        PrescriptionItem.objects.create(
            prescription=prescription,
            medication_name="Paracetamol",
            quantity=10,
            unit="เม็ด",
            unit_price="2.00",
        )

        self.client.force_login(self.pharmacist)
        response = self.client.post(
            reverse("pharmacy_update_status", args=[prescription.id]),
            {"status": Prescription.Status.DISPENSED},
        )
        self.assertRedirects(response, reverse("pharmacy_worklist"))
        prescription.refresh_from_db()
        self.assertEqual(prescription.status, Prescription.Status.DISPENSED)

        bill = Bill.objects.get(visit=self.visit)
        self.client.force_login(self.cashier)
        response = self.client.post(
            reverse("billing_detail", args=[bill.id]),
            {
                "coverage_type": PatientCoverage.CoverageType.UCS,
                "coverage_percent": "100",
                "member_no": "UCS-001",
                "other_fee": "0",
            },
        )
        self.assertRedirects(response, reverse("billing_detail", args=[bill.id]))
        bill.refresh_from_db()
        self.assertEqual(bill.patient_due, 0)

        response = self.client.post(reverse("billing_pay", args=[bill.id]))
        self.assertRedirects(response, reverse("billing_receipt", args=[bill.id]))
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.WAIVED)

    def test_aftercare_marks_no-medication_paid_visit_complete(self):
        Bill.objects.create(
            visit=self.visit,
            status=Bill.Status.PAID,
            consultation_fee="200.00",
            subtotal="200.00",
            patient_due="200.00",
            received_by=self.cashier,
            paid_at=timezone.now(),
        )
        self.client.force_login(self.doctor)

        response = self.client.get(reverse("opd_care_plan", args=[self.visit.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["visit_complete"])
        self.assertTrue(response.context["pharmacy_skipped"])
        self.assertContains(response, "เสร็จสิ้นการรับบริการ")
        self.assertContains(response, "ไม่มีการสั่งยา · ข้ามขั้นตอน")
        self.assertContains(response, "ไม่ต้องเข้าห้องยา")
        self.assertContains(response, "ผู้ป่วยสามารถกลับบ้านได้")
        self.assertNotContains(response, "ส่งค่าใช้จ่ายไปการเงิน")

    def test_doctor_can_issue_medical_certificate(self):
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("opd_care_plan", args=[self.visit.id]),
            {
                "action": "issue_certificate",
                "recommendation": "ควรพัก 2 วัน",
                "rest_from": "2026-09-23",
                "rest_to": "2026-09-24",
            },
        )
        self.assertRedirects(response, reverse("opd_care_plan", args=[self.visit.id]))
        certificate = self.visit.medical_certificate
        self.assertEqual(certificate.diagnosis_snapshot, "Acute URI")
        response = self.client.get(reverse("medical_certificate_print", args=[certificate.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ใบรับรองแพทย์")
