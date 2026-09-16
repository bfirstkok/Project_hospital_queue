from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from patients.models import Patient
from queues.models import Queue, VitalSign

from .models import TestScenarioRun


class SystemTestConsoleTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="system-admin", email="admin@example.test", password="test-password-123"
        )
        self.client.force_login(self.admin)

    def test_full_scenario_and_watch_data_do_not_change_bp(self):
        self.client.post(reverse("system_test:create_scenario"), {"scenario": TestScenarioRun.Scenario.FULL})
        run = TestScenarioRun.objects.get()
        before = VitalSign.objects.get(visit=run.visit)
        original_bp = (before.sys_bp, before.dia_bp)

        response = self.client.post(
            reverse("system_test:push_telemetry", args=[run.pk]), {"mode": "critical"}
        )

        self.assertRedirects(response, reverse("system_test:index"))
        after = VitalSign.objects.get(visit=run.visit)
        self.assertEqual((after.sys_bp, after.dia_bp), original_bp)
        self.assertGreaterEqual(after.pr, 130)
        self.assertLessEqual(after.o2sat, 89)

    def test_delete_removes_only_selected_synthetic_patient(self):
        real_patient = Patient.objects.create(first_name="ผู้ป่วย", last_name="จริง", national_id="1234567890123")
        self.client.post(reverse("system_test:create_scenario"), {"scenario": TestScenarioRun.Scenario.WAITING})
        run = TestScenarioRun.objects.get()
        synthetic_patient_id = run.patient_id

        self.client.post(reverse("system_test:delete_scenario", args=[run.pk]))

        self.assertTrue(Patient.objects.filter(pk=real_patient.pk).exists())
        self.assertFalse(Patient.objects.filter(pk=synthetic_patient_id).exists())

    def test_random_registered_patient_has_complete_profile_and_waiting_queue(self):
        response = self.client.post(reverse("system_test:create_random_registered_patient"))

        self.assertRedirects(response, reverse("system_test:index"))
        run = TestScenarioRun.objects.select_related("patient", "visit").get()
        patient = run.patient
        required_values = [
            patient.first_name,
            patient.last_name,
            patient.national_id,
            patient.birth_date,
            patient.phone,
            patient.email,
            patient.address_area,
            patient.address,
            patient.province,
            patient.district,
            patient.subdistrict,
            patient.postal_code,
            patient.blood_type,
            patient.chronic_diseases,
            patient.allergies,
            patient.medications,
            patient.height_cm,
            patient.weight_kg,
            patient.bp_sys,
            patient.bp_dia,
            patient.emergency_name,
            patient.emergency_relationship,
            patient.emergency_phone,
        ]
        self.assertTrue(all(value not in (None, "") for value in required_values))
        self.assertIn("[SYSTEM TEST]", run.visit.note)
        self.assertEqual(run.visit.queue.status, Queue.Status.WAITING_VITALS)
        self.assertTrue(VitalSign.objects.filter(visit=run.visit).exists())

    def test_database_explorer_masks_user_password(self):
        response = self.client.get(reverse("system_test:database_table", args=["auth", "user"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "••••••••")
        self.assertNotContains(response, self.admin.password)

    def test_database_root_is_available_to_superuser(self):
        response = self.client.get(reverse("database_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "จัดการฐานข้อมูล")

    def test_database_table_can_search_and_edit_allowed_patient_fields(self):
        patient = Patient.objects.create(
            first_name="สมชาย",
            last_name="ทดสอบค้นหา",
            national_id="1234567890998",
        )
        table_response = self.client.get(
            reverse("database_table_root", args=["patients", "patient"]),
            {"q": "ทดสอบค้นหา"},
        )
        self.assertEqual(table_response.status_code, 200)
        self.assertContains(table_response, "สมชาย")
        self.assertContains(table_response, "แก้ไข")

        edit_url = reverse(
            "database_record_edit_root",
            args=["patients", "patient", patient.pk],
        )
        response = self.client.post(edit_url, {
            "first_name": "สมหญิง",
            "last_name": patient.last_name,
            "national_id": patient.national_id,
            "gender": patient.gender,
            "nationality": patient.nationality,
            "phone": patient.phone,
            "hn": patient.hn,
            "address_area": patient.address_area,
            "address": patient.address,
            "blood_type": patient.blood_type,
            "chronic_diseases": patient.chronic_diseases,
            "allergies": patient.allergies,
            "medications": patient.medications,
            "emergency_name": patient.emergency_name,
            "emergency_relationship": patient.emergency_relationship,
            "emergency_phone": patient.emergency_phone,
            "note": patient.note,
            "province": patient.province,
            "district": patient.district,
            "subdistrict": patient.subdistrict,
            "postal_code": patient.postal_code,
        })
        self.assertRedirects(
            response,
            reverse("database_table_root", args=["patients", "patient"]),
        )
        patient.refresh_from_db()
        self.assertEqual(patient.first_name, "สมหญิง")

    def test_database_edit_never_exposes_user_password(self):
        response = self.client.get(reverse(
            "database_record_edit_root",
            args=["auth", "user", self.admin.pk],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="password"')
