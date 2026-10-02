import json
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from queues.models import DeviceCommand, Queue, Visit, VisitWorkflowLog, VitalSign
from opd.models import Bill, BillBalanceTransfer, PatientCoverage, Prescription, VisitAssessment
from .models import Patient, PatientAccessToken, PatientPin


class PatientJourneyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="journey-admin",
            email="journey@example.test",
            password="secret",
        )
        self.client.force_login(self.user)
        self.patient = Patient.objects.create(
            first_name="ทดสอบ",
            last_name="เส้นทางผู้ป่วย",
            national_id="9222222222222",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            final_severity=Visit.Severity.GREEN,
        )
        self.queue = Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.OPD_DONE,
            priority=4,
        )
        VisitAssessment.objects.create(
            visit=self.visit,
            examiner=self.user,
            diagnosis="ทดสอบ",
            treatment="ติดตามอาการ",
        )

    def history(self):
        return self.client.get(reverse("patient_history", args=[self.patient.id]))

    def test_opd_done_is_doctor_complete_not_whole_visit_complete(self):
        response = self.history()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "สถานะปัจจุบันของผู้ป่วย")
        self.assertContains(response, "ขั้นตอนหลังตรวจ")
        self.assertContains(response, "แพทย์ตรวจเสร็จ")
        self.assertContains(response, "รอแผนหลังตรวจ/ใบสั่งยา")
        self.assertNotContains(response, "ขั้นตอนที่ต้องดำเนินการของ Visit นี้เสร็จแล้ว")

    def test_no_prescription_can_skip_pharmacy_after_bill_exists_and_finish_after_payment(self):
        bill = Bill.objects.create(
            visit=self.visit,
            status=Bill.Status.READY,
        )

        before = self.history()
        self.assertContains(before, "ไม่มีรายการยาที่ต้องรับ")
        self.assertContains(before, "การเงิน")
        self.assertContains(before, "รอชำระเงิน")

        bill.status = Bill.Status.PAID
        bill.save(update_fields=["status", "updated_at"])

        after = self.history()
        self.assertContains(after, "พร้อมกลับบ้าน · รอปิด Visit")
        self.assertContains(after, "รับยาและชำระเงินครบแล้ว · รอเจ้าหน้าที่จัดคิวปิด Visit")

        self.queue.status = Queue.Status.DISCHARGED
        self.queue.save(update_fields=["status"])
        VisitWorkflowLog.record(
            visit=self.visit,
            event_type=VisitWorkflowLog.EventType.PATIENT_DEPARTED,
            actor=self.user,
            description="ยืนยันว่าผู้ป่วยออกจากโรงพยาบาลแล้ว",
            details={"departure_status": "LEFT_FACILITY", "planned_destination": "HOME"},
        )
        departed = self.history()
        self.assertContains(departed, "ผู้ป่วยออกจากโรงพยาบาลแล้ว")
        self.assertContains(departed, "ยืนยันโดย")

    def test_prescription_must_be_dispensed_even_when_bill_is_paid(self):
        prescription = Prescription.objects.create(
            visit=self.visit,
            prescribed_by=self.user,
            status=Prescription.Status.SENT,
        )
        Bill.objects.create(
            visit=self.visit,
            status=Bill.Status.PAID,
        )

        waiting = self.history()
        self.assertContains(waiting, "ห้องยา")
        self.assertContains(waiting, "รอห้องยา")
        self.assertNotContains(waiting, "ขั้นตอนที่ต้องดำเนินการของ Visit นี้เสร็จแล้ว")

        prescription.status = Prescription.Status.DISPENSED
        prescription.save(update_fields=["status", "updated_at"])

        done = self.history()
        self.assertContains(done, "พร้อมกลับบ้าน · รอปิด Visit")
        self.assertContains(done, "รอเจ้าหน้าที่จัดคิวปิด Visit")


class PatientAgeDisplayTests(TestCase):
    @patch("patients.models.timezone.localdate", return_value=date(2026, 8, 21))
    def test_age_display_uses_years_months_and_days_from_birth_date(self, _localdate):
        patient = Patient(birth_date=date(1959, 5, 9), age=66)

        self.assertEqual(patient.age_breakdown, (67, 3, 12))
        self.assertEqual(patient.age_display, "67 ปี 3 เดือน 12 วัน")

    def test_age_display_falls_back_to_approximate_years(self):
        self.assertEqual(Patient(age=67).age_display, "67 ปี")
        self.assertEqual(Patient().age_display, "-")

    @patch("patients.forms.timezone.localdate", return_value=date(2026, 8, 21))
    @patch("patients.models.timezone.localdate", return_value=date(2026, 8, 21))
    def test_staff_can_add_birth_date_without_creating_a_visit(self, _model_date, _form_date):
        user = get_user_model().objects.create_user(username="nurse", password="test-only-password")
        patient = Patient.objects.create(
            first_name="ทดสอบ",
            last_name="วันเกิด",
            national_id="1111111111119",
            age=67,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("update_patient_birth_date", args=[patient.id]),
            {"birth_date": "1959-05-09"},
        )

        self.assertRedirects(response, reverse("patient_history", args=[patient.id]))
        patient.refresh_from_db()
        self.assertEqual(patient.birth_date, date(1959, 5, 9))
        self.assertEqual(patient.age_display, "67 ปี 3 เดือน 12 วัน")
        self.assertEqual(patient.visits.count(), 0)

    def test_staff_can_edit_patient_without_creating_a_visit_or_queue(self):
        user = get_user_model().objects.create_user(username="editor", password="test-only-password")
        patient = Patient.objects.create(
            first_name="ชื่อเดิม",
            last_name="นามสกุลเดิม",
            national_id="1111111111119",
            age=45,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("edit_patient", args=[patient.id]))
        self.assertContains(response, "แก้ไขข้อมูลผู้ป่วย")
        self.assertContains(response, "ชื่อเดิม")

        response = self.client.post(
            reverse("edit_patient", args=[patient.id]),
            {
                "first_name": "ชื่อใหม่",
                "last_name": patient.last_name,
                "national_id": patient.national_id,
                "gender": patient.gender,
                "age": 46,
                "phone": "0800000000",
                "blood_type": patient.blood_type,
                "province": "",
                "district": "",
                "subdistrict": "",
                "postal_code": "",
            },
        )

        self.assertRedirects(response, reverse("patient_history", args=[patient.id]))
        patient.refresh_from_db()
        self.assertEqual(patient.first_name, "ชื่อใหม่")
        self.assertEqual(patient.age, 46)
        self.assertEqual(patient.phone, "0800000000")
        self.assertEqual(patient.visits.count(), 0)
        self.assertEqual(Queue.objects.count(), 0)


@override_settings(PATIENT_APP_ORIGINS={"https://bfirstkok.github.io"})
class PublicPatientApiTests(TestCase):
    endpoint = "/api/patient/register/"

    def setUp(self):
        cache.clear()
        self.payload = {
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "national_id": "1234567890123",
            "gender": "M",
            "age": 31,
            "phone": "0812345678",
            "blood_type": "UNKNOWN",
            "note": "เวียนหัว",
            "consent": True,
        }

    def post_registration(self, payload=None):
        return self.client.post(
            self.endpoint,
            data=json.dumps(payload or self.payload),
            content_type="application/json",
            HTTP_ORIGIN="https://bfirstkok.github.io",
        )

    def post_login(self, national_id=None):
        return self.client.post(
            "/api/patient/login/",
            data=json.dumps({"national_id": national_id or self.payload["national_id"]}),
            content_type="application/json",
            HTTP_ORIGIN="https://bfirstkok.github.io",
        )

    def test_registration_creates_waiting_vitals_visit_without_vital_values(self):
        response = self.post_registration()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://bfirstkok.github.io")
        visit = Visit.objects.select_related("queue", "vitals").get()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_VITALS)
        self.assertIsNone(visit.vitals.sys_bp)
        self.assertIsNone(visit.vitals.dia_bp)
        self.assertEqual(response.json()["tracking_token"], str(visit.tracking_token))
        self.assertTrue(response.json()["access_token"])

    def test_duplicate_submit_rejects_active_visit(self):
        first = self.post_registration()
        second = self.post_registration()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertFalse(second.json()["ok"])
        self.assertIn("มีคิวที่กำลังรับบริการอยู่แล้ว", second.json()["error"])
        self.assertEqual(second.json()["active_queue"]["queue_number"], first.json()["queue_number"])
        self.assertEqual(Visit.objects.count(), 1)

    def test_patient_can_book_again_after_previous_queue_is_cancelled(self):
        first = self.post_registration()
        queue = Queue.objects.get()
        queue.status = Queue.Status.CANCELLED
        queue.save(update_fields=["status"])

        second = self.post_registration()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(Visit.objects.count(), 2)
        self.assertNotEqual(first.json()["tracking_token"], second.json()["tracking_token"])

    def test_staff_registration_rejects_duplicate_active_queue(self):
        user = get_user_model().objects.create_user(username="duplicate-guard", password="test-only-password")
        self.client.force_login(user)
        staff_payload = {
            "first_name": self.payload["first_name"],
            "last_name": self.payload["last_name"],
            "national_id": self.payload["national_id"],
            "gender": self.payload["gender"],
            "age": self.payload["age"],
            "phone": self.payload["phone"],
            "email": "staff-patient@example.com",
            "blood_type": self.payload["blood_type"],
            "chronic_diseases": "ไม่มีโรคประจำตัว",
            "allergies": "ไม่มีประวัติแพ้ยา",
            "medications": "ไม่มียาที่ใช้ประจำ",
            "note": self.payload["note"],
        }

        first = self.client.post(reverse("register_patient"), staff_payload)
        second = self.client.post(reverse("register_patient"), staff_payload)

        registered_patient = Patient.objects.get(national_id=staff_payload["national_id"])
        self.assertRedirects(first, reverse("patient_history", args=[registered_patient.id]))
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "ที่กำลังรับบริการอยู่แล้ว")
        self.assertEqual(Visit.objects.count(), 1)
        self.assertEqual(Queue.objects.count(), 1)

    def test_staff_registration_page_matches_patient_registration_flow(self):
        user = get_user_model().objects.create_user(username="registration-ui", password="test-only-password")
        self.client.force_login(user)

        response = self.client.get(reverse("register_patient"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ลงทะเบียนผู้ป่วยใหม่")
        self.assertContains(response, "service-steps")
        self.assertContains(response, "วัดสัญญาณชีพ")
        self.assertContains(response, "รอเรียกคิว")
        self.assertContains(response, "วัน/เดือน/ปีเกิด")
        self.assertContains(response, 'aria-label="ปีเกิด (พ.ศ.)"')
        self.assertContains(response, "แพ้ยากลุ่มเพนิซิลลิน (Penicillin)")
        self.assertContains(response, "แพ้ยาแก้ปวด (NSAIDs / แอสไพริน)")
        self.assertContains(response, "ยาลดกรด / ยาโรคกระเพาะ")
        self.assertContains(response, "ยาไทรอยด์")
        self.assertContains(response, "-- เลือกจังหวัด (77 จังหวัด) --")
        self.assertContains(response, 'id="addEmergencyContact"')
        self.assertContains(response, 'name="emergency_name_1"')
        self.assertNotContains(response, 'name="username"')
        self.assertNotContains(response, 'name="password"')

    def test_staff_registration_can_search_and_select_existing_patient(self):
        user = get_user_model().objects.create_user(username="existing-patient-ui", password="test-only-password")
        self.client.force_login(user)
        patient = Patient.objects.create(
            first_name="สมชาย",
            last_name="ผู้ป่วยเดิม",
            national_id="4555555555555",
            phone="0812345678",
        )

        search = self.client.get(reverse("register_patient"), {"existing_q": patient.hn})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, "สมชาย ผู้ป่วยเดิม")
        self.assertContains(search, "เลือกและรับบริการครั้งใหม่")

        selected = self.client.get(reverse("register_patient"), {"existing_patient": patient.id})
        self.assertContains(selected, f'name="existing_patient_id" value="{patient.id}"')
        self.assertContains(selected, "ระบบจะสร้าง Visit และคิวใหม่ โดยไม่สร้างผู้ป่วยซ้ำ")
        self.assertContains(selected, 'readonly aria-readonly="true"')

    def test_staff_registration_creates_new_visit_for_selected_existing_patient(self):
        user = get_user_model().objects.create_user(username="existing-patient-visit", password="test-only-password")
        self.client.force_login(user)
        patient = Patient.objects.create(
            first_name="สมหญิง",
            last_name="กลับมาตรวจ",
            national_id="4444444444444",
            gender="F",
            phone="0899999999",
        )

        response = self.client.post(reverse("register_patient"), {
            "existing_patient_id": patient.id,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "national_id": patient.national_id,
            "gender": patient.gender,
            "phone": patient.phone,
            "blood_type": "UNKNOWN",
            "note": "กลับมาตรวจอาการเวียนศีรษะ",
        })

        self.assertRedirects(response, reverse("patient_history", args=[patient.id]))
        self.assertEqual(Patient.objects.filter(national_id=patient.national_id).count(), 1)
        self.assertEqual(patient.visits.count(), 1)
        self.assertEqual(patient.visits.get().queue.status, Queue.Status.WAITING_VITALS)

    def test_staff_registration_marks_patient_portal_core_fields_in_ui(self):
        user = get_user_model().objects.create_user(username="required-fields", password="test-only-password")
        self.client.force_login(user)

        response = self.client.get(reverse("register_patient"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'data-choice-field')
        self.assertContains(response, 'data-target="chronic_diseases"')
        self.assertContains(response, 'data-target="allergies"')
        self.assertContains(response, 'data-target="medications"')
        self.assertContains(response, 'const isEditMode = false')
        self.assertContains(response, "รับคิวด่วน")
        self.assertContains(response, 'class="optional-register-panel"')
        self.assertContains(response, reverse("role_landing"))
        self.assertNotContains(response, 'name="email" type="email" maxlength="254" autocomplete="email" placeholder="patient@example.com" required')

    def test_staff_can_register_with_only_fast_registration_fields(self):
        user = get_user_model().objects.create_user(username="fast-registration", password="test-only-password")
        self.client.force_login(user)

        response = self.client.post(reverse("register_patient"), {
            "first_name": "ทดสอบ",
            "last_name": "รับคิวด่วน",
            "national_id": "4777777777777",
            "gender": "UNKNOWN",
            "phone": "0812345678",
            "blood_type": "UNKNOWN",
            "note": "เวียนศีรษะและอ่อนเพลีย",
            "consent": "on",
        })

        patient = Patient.objects.get(national_id="4777777777777")
        self.assertRedirects(response, reverse("patient_history", args=[patient.id]))
        self.assertEqual(patient.email, None)
        self.assertEqual(patient.chronic_diseases, "")
        self.assertEqual(patient.visits.get().queue.status, Queue.Status.WAITING_VITALS)

    def test_staff_fast_registration_requires_chief_complaint(self):
        user = get_user_model().objects.create_user(username="missing-complaint", password="test-only-password")
        self.client.force_login(user)

        response = self.client.post(reverse("register_patient"), {
            "first_name": "ทดสอบ",
            "last_name": "ไม่มีอาการ",
            "national_id": "4666666666666",
            "phone": "0812345678",
            "consent": "on",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "กรุณาระบุอาการสำคัญหรือเหตุผลที่มารับบริการ")
        self.assertFalse(Patient.objects.filter(national_id="4666666666666").exists())

    def test_staff_registration_saves_up_to_three_emergency_contacts_like_patient_portal(self):
        user = get_user_model().objects.create_user(username="multi-contact", password="test-only-password")
        self.client.force_login(user)

        response = self.client.post(reverse("register_patient"), {
            "first_name": "สมหญิง",
            "last_name": "หลายผู้ติดต่อ",
            "national_id": "4888888888888",
            "gender": "F",
            "birth_date": "1996-09-22",
            "age": "30",
            "phone": "0811111111",
            "email": "multi-contact@example.com",
            "blood_type": "A",
            "chronic_diseases": "ไม่มีโรคประจำตัว",
            "allergies": "ไม่มีประวัติแพ้ยา",
            "medications": "ไม่มียาที่ใช้ประจำ",
            "note": "มีไข้ / หนาวสั่น",
            "emergency_name_1": "สมชาย ใจดี",
            "emergency_relationship_1": "SPOUSE",
            "emergency_phone_1": "0891111111",
            "emergency_name_2": "สมศรี ใจดี",
            "emergency_relationship_2": "MOTHER",
            "emergency_phone_2": "0892222222",
        })

        patient = Patient.objects.get(national_id="4888888888888")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(patient.emergency_contacts), 2)
        self.assertEqual(patient.emergency_contacts[0]["name"], "สมชาย ใจดี")
        self.assertEqual(patient.emergency_contacts[1]["relationship"], "MOTHER")
        self.assertEqual(patient.emergency_name, "สมชาย ใจดี")
        self.assertEqual(patient.emergency_relationship, "SPOUSE")
        self.assertEqual(patient.emergency_phone, "0891111111")

        edit = self.client.get(reverse("edit_patient", args=[patient.id]))
        self.assertContains(edit, "สมชาย ใจดี")
        self.assertContains(edit, "สมศรี ใจดี")
        self.assertContains(edit, 'name="emergency_name_2"')

    def test_invalid_registration_returns_field_errors(self):
        self.payload["national_id"] = "123"
        response = self.post_registration()

        self.assertEqual(response.status_code, 400)
        self.assertIn("national_id", response.json()["errors"])
        self.assertEqual(Patient.objects.count(), 0)

    def test_queue_status_does_not_expose_patient_or_severity(self):
        self.post_registration()
        visit = Visit.objects.select_related("queue").get()
        response = self.client.get(
            f"/api/patient/queue/{visit.tracking_token}/",
            HTTP_ORIGIN="https://bfirstkok.github.io",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], Queue.Status.WAITING_VITALS)
        self.assertNotIn("patient", payload)
        self.assertNotIn("severity", payload)

    def test_unknown_tracking_token_returns_json_404(self):
        response = self.client.get(f"/api/patient/queue/{uuid.uuid4()}/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])


    def test_login_me_and_queue_with_bearer_token(self):
        self.post_registration()
        login_response = self.post_login()

        self.assertEqual(login_response.status_code, 200)
        self.assertIn("Content-Type", login_response["Access-Control-Allow-Headers"])
        self.assertIn("Authorization", login_response["Access-Control-Allow-Headers"])
        self.assertIn("X-Requested-With", login_response["Access-Control-Allow-Headers"])
        token = login_response.json()["access_token"]
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "HTTP_ORIGIN": "https://bfirstkok.github.io",
        }

        me_response = self.client.get("/api/patient/me/", **headers)
        queue_response = self.client.get("/api/patient/queue/", **headers)

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(me_response.json()["profile"]["hn"], Patient.objects.get().hn)
        self.assertEqual(me_response.json()["profile"]["national_id"], Patient.objects.get().national_id)
        self.assertEqual(queue_response.json()["status"], Queue.Status.WAITING_VITALS)

    def test_registration_accepts_birth_date_and_profile_returns_full_age(self):
        self.payload["birth_date"] = "1959-05-09"
        registration = self.post_registration()
        token = registration.json()["access_token"]

        response = self.client.get(
            "/api/patient/me/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_ORIGIN="https://bfirstkok.github.io",
        )

        self.assertEqual(registration.status_code, 201)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"]["birth_date"], "1959-05-09")
        self.assertRegex(
            response.json()["profile"]["age_display"],
            r"^\d+ ปี \d+ เดือน \d+ วัน$",
        )

    def test_protected_endpoints_reject_missing_or_tampered_token(self):
        self.post_registration()
        token = self.post_login().json()["access_token"]

        missing = self.client.get("/api/patient/me/")
        tampered = self.client.get(
            "/api/patient/queue/",
            HTTP_AUTHORIZATION=f"Bearer {token}tampered",
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(tampered.status_code, 401)

    def test_login_rejects_unknown_patient(self):
        response = self.post_login("9999999999999")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["ok"])


class PatientWorkflowHistoryTests(TestCase):
    def test_history_displays_accountable_staff_and_audit_timeline(self):
        staff = get_user_model().objects.create_superuser(
            username="auditor",
            password="test-password",
            first_name="ผู้ตรวจ",
            last_name="ระบบ",
        )
        patient = Patient.objects.create(
            first_name="ผู้ป่วย",
            last_name="มีประวัติ",
            national_id="7555555555555",
        )
        visit = Visit.objects.create(patient=patient)
        Queue.objects.create(visit=visit, status=Queue.Status.WAITING_QUEUE)
        VitalSign.objects.create(visit=visit, rr=18, pr=80, o2sat=98)
        VisitWorkflowLog.record(
            visit=visit,
            event_type=VisitWorkflowLog.EventType.VITALS_RECORDED,
            actor=staff,
            description="ตรวจสัญญาณชีพครบถ้วน",
        )
        VisitWorkflowLog.record(
            visit=visit,
            event_type=VisitWorkflowLog.EventType.TRIAGE_CONFIRMED,
            actor=staff,
            description="ยืนยันผลคัดกรอง",
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("patient_history", args=[patient.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ผู้รับผิดชอบในกระบวนการ")
        self.assertContains(response, "ประวัติการดำเนินการ (Audit Log)")
        self.assertContains(response, "ผู้ตรวจ ระบบ")
        self.assertContains(response, "ตรวจสัญญาณชีพครบถ้วน")


class PatientAdminCascadeDeleteTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="patient-delete-admin",
            email="patient-delete@example.test",
            password="secret",
        )
        self.client.force_login(self.admin_user)
        self.patient = Patient.objects.create(
            first_name="Delete",
            last_name="Cascade",
            national_id="9333333333333",
        )
        PatientAccessToken.objects.create(
            patient=self.patient,
            token_hash="a" * 64,
            token_version=self.patient.token_version,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        PatientPin.objects.create(
            patient=self.patient,
            pin_hash="test-pin-hash",
        )

        from queues.models import (
            ConfirmedTriageCase,
            CriticalAlert,
            Device,
            DeviceCommand,
            TelemetryLog,
            Visit,
            VisitWorkflowLog,
        )

        self.visit = Visit.objects.create(
            patient=self.patient,
            final_severity=Visit.Severity.YELLOW,
        )
        ConfirmedTriageCase.objects.create(
            visit=self.visit,
            nurse_severity=Visit.Severity.YELLOW,
        )
        TelemetryLog.objects.create(
            visit=self.visit,
            bpm=92,
        )
        CriticalAlert.objects.create(
            visit=self.visit,
            alert_type=CriticalAlert.AlertType.HIGH_HEART_RATE,
            message="test alert",
        )
        VisitWorkflowLog.objects.create(
            visit=self.visit,
            event_type=VisitWorkflowLog.EventType.TRIAGE_CONFIRMED,
            actor_name="ระบบทดสอบ",
            actor_role="ระบบ",
            description="test workflow log",
        )
        device = Device.objects.create(device_id="WATCH997", api_key="test-device-key")
        self.device_command = DeviceCommand.objects.create(
            device=device,
            visit=self.visit,
            command_type=DeviceCommand.CommandType.BUZZER,
            expires_at=timezone.now() + timedelta(minutes=1),
        )

    def test_admin_can_delete_patient_with_read_only_portal_credentials(self):
        delete_url = reverse("admin:patients_patient_delete", args=[self.patient.id])

        confirm = self.client.get(delete_url)

        self.assertEqual(confirm.status_code, 200)
        self.assertNotContains(confirm, "ไม่สามารถลบ")
        self.assertNotContains(confirm, "patient access token")
        self.assertNotContains(confirm, "patient pin")
        self.assertNotContains(confirm, "confirmed triage case")
        self.assertNotContains(confirm, "telemetry log")
        self.assertNotContains(confirm, "critical alert")
        self.assertNotContains(confirm, "device command")
        self.assertNotContains(confirm, "visit workflow log")

        response = self.client.post(delete_url, {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertFalse(PatientAccessToken.objects.filter(patient_id=self.patient.pk).exists())
        self.assertFalse(PatientPin.objects.filter(patient_id=self.patient.pk).exists())

        from queues.models import (
            ConfirmedTriageCase,
            CriticalAlert,
            TelemetryLog,
            VisitWorkflowLog,
        )
        self.assertFalse(ConfirmedTriageCase.objects.filter(visit_id=self.visit.id).exists())
        self.assertFalse(TelemetryLog.objects.filter(visit_id=self.visit.id).exists())
        self.assertFalse(CriticalAlert.objects.filter(visit_id=self.visit.id).exists())
        self.assertFalse(VisitWorkflowLog.objects.filter(visit_id=self.visit.id).exists())
        self.assertFalse(DeviceCommand.objects.filter(pk=self.device_command.pk).exists())

    def test_device_command_delete_permission_does_not_block_patient_cascade(self):
        staff_admin = get_user_model().objects.create_user(
            username="patient-delete-limited-admin",
            email="patient-delete-limited@example.test",
            password="secret",
            is_staff=True,
        )
        delete_permissions = Permission.objects.filter(
            codename__startswith="delete_",
        ).exclude(
            content_type__app_label="queues",
            codename="delete_devicecommand",
        )
        staff_admin.user_permissions.add(*delete_permissions)
        self.client.force_login(staff_admin)

        delete_url = reverse("admin:patients_patient_delete", args=[self.patient.id])
        confirm = self.client.get(delete_url)

        self.assertEqual(confirm.status_code, 200)
        self.assertNotContains(confirm, "ไม่สามารถลบ")
        self.assertNotContains(confirm, "device command")

        response = self.client.post(delete_url, {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertFalse(DeviceCommand.objects.filter(pk=self.device_command.pk).exists())


class PatientUrgentBillRolloverTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="urgent-rollover-admin",
            email="urgent-rollover@example.test",
            password="secret",
        )
        self.client.force_login(self.user)
        self.patient = Patient.objects.create(
            first_name="สมชาย",
            last_name="มียอดค้าง",
            national_id="9333333333333",
            phone="0812345678",
        )
        self.old_visit = Visit.objects.create(patient=self.patient, note="Visit เดิม")
        self.old_queue = Queue.objects.create(visit=self.old_visit, status=Queue.Status.OPD_DONE)
        self.old_bill = Bill.objects.create(
            visit=self.old_visit,
            status=Bill.Status.READY,
            subtotal="300.00",
            covered_amount="180.00",
            patient_due="120.00",
        )
        self.registration_payload = {
            "existing_patient_id": str(self.patient.pk),
            "first_name": self.patient.first_name,
            "last_name": self.patient.last_name,
            "national_id": self.patient.national_id,
            "gender": "UNKNOWN",
            "phone": self.patient.phone,
            "blood_type": "UNKNOWN",
            "note": "อาการเร่งด่วนครั้งใหม่",
        }

    def test_patient_dashboard_shows_tabs_and_unpaid_balance(self):
        response = self.client.get(reverse("patient_search"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ภาพรวมสถานะผู้ป่วย")
        self.assertContains(response, "รอตรวจวัด")
        self.assertContains(response, "มียอดค้างชำระ")
        self.assertContains(response, "120.00 บาท")

        filtered = self.client.get(reverse("patient_search"), {"tab": "unpaid"})
        self.assertContains(filtered, self.patient.hn)

    def test_registration_blocks_unpaid_bill_without_urgent_override(self):
        response = self.client.post(reverse("register_patient"), self.registration_payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ผู้ป่วยยังมียอดค้าง")
        self.assertContains(response, "เริ่ม Visit ใหม่กรณีเร่งด่วน")
        self.assertEqual(Visit.objects.filter(patient=self.patient).count(), 1)
        self.old_bill.refresh_from_db()
        self.assertEqual(self.old_bill.status, Bill.Status.READY)

    def test_urgent_bypass_requires_reason(self):
        payload = {**self.registration_payload, "urgent_bypass": "1"}

        response = self.client.post(reverse("register_patient"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "กรุณาระบุเหตุผล")
        self.assertEqual(Visit.objects.filter(patient=self.patient).count(), 1)
        self.assertFalse(BillBalanceTransfer.objects.exists())

    def test_urgent_bypass_moves_due_to_new_bill_without_reapplying_coverage(self):
        payload = {
            **self.registration_payload,
            "urgent_bypass": "1",
            "urgent_bypass_reason": "ผู้ป่วยมีอาการเร่งด่วน ต้องเริ่มประเมินทันที",
        }

        response = self.client.post(reverse("register_patient"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Visit.objects.filter(patient=self.patient).count(), 2)
        new_visit = Visit.objects.exclude(pk=self.old_visit.pk).get(patient=self.patient)
        transfer = BillBalanceTransfer.objects.get(source_bill=self.old_bill)
        self.assertEqual(transfer.target_visit, new_visit)
        self.assertEqual(transfer.amount, 120)
        self.assertEqual(transfer.transferred_by, self.user)
        self.assertIn("เร่งด่วน", transfer.reason)

        self.old_bill.refresh_from_db()
        self.old_visit.refresh_from_db()
        self.assertEqual(self.old_bill.status, Bill.Status.TRANSFERRED)
        self.assertEqual(self.old_bill.patient_due, 120)
        self.assertEqual(self.old_queue.status, Queue.Status.OPD_DONE)
        self.assertEqual(self.old_visit.superseded_by, new_visit)
        self.assertEqual(
            VisitWorkflowLog.objects.filter(
                event_type=VisitWorkflowLog.EventType.URGENT_BILL_ROLLOVER,
            ).count(),
            2,
        )

        coverage = PatientCoverage.objects.create(
            patient=self.patient,
            coverage_type=PatientCoverage.CoverageType.UCS,
            coverage_percent=100,
            is_active=True,
        )
        new_bill = Bill.objects.get(visit=new_visit)
        new_bill.coverage = coverage
        new_bill.save(update_fields=["coverage", "updated_at"])
        new_bill.recalculate()
        self.assertEqual(new_bill.covered_amount, 200)
        self.assertEqual(new_bill.patient_due, 120)
        self.assertEqual(new_bill.status, Bill.Status.READY)
        self.assertIsNone(new_bill.billing_queue_entered_at)

        detail = self.client.get(reverse("billing_detail", args=[new_bill.pk]))
        self.assertContains(detail, f"ยอดค้างเดิมจาก Visit #{self.old_visit.pk}")
        old_detail = self.client.get(reverse("billing_detail", args=[self.old_bill.pk]))
        self.assertContains(old_detail, f"Visit #{new_visit.pk}")
        self.assertNotContains(old_detail, "ยืนยันรับชำระ")
        old_bill_list = self.client.get(reverse("billing_worklist"))
        self.assertContains(old_bill_list, f"โอนยอดไป Visit #{new_visit.pk}")

        new_bill.billing_queue_entered_at = timezone.now()
        new_bill.save(update_fields=["billing_queue_entered_at", "updated_at"])
        worklist = self.client.get(reverse("billing_worklist"))
        self.assertContains(worklist, f"ยอดค้างเดิมจาก Visit #{self.old_visit.pk}")

        blocked_payment = self.client.post(reverse("billing_pay", args=[self.old_bill.pk]))
        self.assertEqual(blocked_payment.status_code, 302)
        self.old_bill.refresh_from_db()
        self.assertEqual(self.old_bill.status, Bill.Status.TRANSFERRED)

        new_bill.status = Bill.Status.PAID
        new_bill.paid_at = timezone.now()
        new_bill.save(update_fields=["status", "paid_at", "updated_at"])
        new_visit.queue.status = Queue.Status.DISCHARGED
        new_visit.queue.save(update_fields=["status"])
        next_visit_response = self.client.post(reverse("register_patient"), self.registration_payload)
        self.assertEqual(next_visit_response.status_code, 302)
        self.assertEqual(Visit.objects.filter(patient=self.patient).count(), 3)
