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
