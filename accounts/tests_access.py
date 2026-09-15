from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from queues.models import StaffProfile


class RoleAccessTests(TestCase):
    def make_user(self, username, role, *, superuser=False):
        user = get_user_model().objects.create_user(
            username=username,
            password="test-password-123",
            is_superuser=superuser,
            is_staff=superuser,
        )
        if not superuser:
            StaffProfile.objects.update_or_create(user=user, defaults={"role": role})
        return user

    def test_nurse_can_open_vitals_but_not_doctor_room(self):
        nurse = self.make_user("nurse", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse("waiting_vitals")).status_code, 200)
        self.assertEqual(self.client.get(reverse("opd_room_select")).status_code, 403)

    def test_doctor_can_open_room_but_not_confirm_triage(self):
        doctor = self.make_user("doctor", StaffProfile.Role.DOCTOR)
        self.client.force_login(doctor)
        self.assertEqual(self.client.get(reverse("opd_room_select")).status_code, 200)
        self.assertEqual(self.client.get(reverse("waiting_confirmation")).status_code, 403)

    def test_each_role_is_confined_to_its_own_workflow(self):
        cases = (
            (
                StaffProfile.Role.DOCTOR,
                "doctor-only",
                ("opd_room_select",),
                ("queue_list", "waiting_vitals", "personnel_dashboard", "register_patient", "dashboard:home", "dashboard"),
            ),
            (
                StaffProfile.Role.NURSE,
                "nurse-only",
                ("queue_list", "waiting_vitals", "waiting_confirmation", "personnel_dashboard"),
                ("opd_room_select", "dashboard:home", "dashboard:waiting_time_report"),
            ),
            (
                StaffProfile.Role.NURSE_ASSISTANT,
                "assistant-only",
                ("waiting_vitals", "register_patient", "patient_search"),
                ("queue_list", "waiting_confirmation", "personnel_dashboard", "opd_room_select", "dashboard:home"),
            ),
            (
                StaffProfile.Role.EMERGENCY,
                "emergency-only",
                ("queue_list", "emergency_transfers"),
                ("waiting_vitals", "waiting_confirmation", "register_patient", "personnel_dashboard", "opd_room_select", "dashboard:home"),
            ),
            (
                StaffProfile.Role.STAFF,
                "staff-only",
                ("queue_list", "register_patient", "patient_search"),
                ("waiting_vitals", "waiting_confirmation", "personnel_dashboard", "opd_room_select", "dashboard:home"),
            ),
        )
        for role, username, allowed, denied in cases:
            with self.subTest(role=role):
                self.client.force_login(self.make_user(username, role))
                for url_name in allowed:
                    self.assertEqual(self.client.get(reverse(url_name)).status_code, 200, url_name)
                for url_name in denied:
                    self.assertEqual(self.client.get(reverse(url_name)).status_code, 403, url_name)
                self.client.logout()

    def test_only_superuser_can_open_system_test(self):
        nurse = self.make_user("regular-nurse", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse("system_test:index")).status_code, 403)
        self.client.force_login(self.make_user("root-admin", StaffProfile.Role.STAFF, superuser=True))
        self.assertEqual(self.client.get(reverse("system_test:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("database_index")).status_code, 200)

    def test_personnel_changes_are_superuser_only(self):
        nurse = self.make_user("viewer", StaffProfile.Role.NURSE)
        target = self.make_user("target", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "set_attendance",
            "user_id": target.pk,
            "is_present": "1",
        })
        self.assertEqual(response.status_code, 403)

    def test_account_can_review_its_own_role_and_duties(self):
        nurse = self.make_user("nurse-rights", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        response = self.client.get(reverse("my_permissions"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "พยาบาล")
        self.assertContains(response, "วัดและบันทึกสัญญาณชีพ")
        self.assertNotContains(response, "ตรวจรักษาและบันทึกผลแพทย์")
