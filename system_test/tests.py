from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from patients.models import Patient
from queues.models import VitalSign

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

    def test_database_explorer_masks_user_password(self):
        response = self.client.get(reverse("system_test:database_table", args=["auth", "user"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "••••••••")
        self.assertNotContains(response, self.admin.password)

    def test_database_root_is_available_to_superuser(self):
        response = self.client.get(reverse("database_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Database Management")
