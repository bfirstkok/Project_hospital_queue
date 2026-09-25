from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from queues.models import StaffProfile
from .access import Capability, capabilities_for
from .models import AccountStatusLog


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

    def test_nurse_can_confirm_triage_but_not_record_vitals_or_open_doctor_room(self):
        nurse = self.make_user("nurse", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        self.assertEqual(self.client.get(reverse("waiting_confirmation")).status_code, 200)
        self.assertEqual(self.client.get(reverse("waiting_vitals")).status_code, 403)
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
                ("waiting_confirmation", "personnel_dashboard", "monitor_dashboard"),
                ("queue_list", "waiting_vitals", "register_patient", "opd_room_select", "emergency_transfers", "device_management", "dashboard:home"),
            ),
            (
                StaffProfile.Role.NURSE_ASSISTANT,
                "assistant-only",
                ("waiting_vitals",),
                ("queue_list", "waiting_confirmation", "register_patient", "patient_search", "personnel_dashboard", "opd_room_select", "device_management", "dashboard:home"),
            ),
            (
                StaffProfile.Role.EMERGENCY,
                "emergency-only",
                ("emergency_transfers", "patient_search"),
                ("queue_list", "waiting_vitals", "waiting_confirmation", "register_patient", "personnel_dashboard", "opd_room_select", "device_management", "dashboard:home"),
            ),
            (
                StaffProfile.Role.STAFF,
                "staff-only",
                ("register_patient", "patient_search"),
                ("queue_list", "waiting_vitals", "waiting_confirmation", "personnel_dashboard", "opd_room_select", "emergency_transfers", "device_management", "dashboard:home"),
            ),
            (
                StaffProfile.Role.QUEUE_OPERATOR,
                "queue-only",
                ("queue_list",),
                ("waiting_vitals", "waiting_confirmation", "register_patient", "patient_search", "opd_room_select", "emergency_transfers", "device_management", "dashboard:home"),
            ),
            (
                StaffProfile.Role.BIOMEDICAL,
                "biomedical-only",
                ("device_management",),
                ("queue_list", "waiting_vitals", "waiting_confirmation", "register_patient", "patient_search", "opd_room_select", "emergency_transfers", "dashboard:home"),
            ),
            (
                StaffProfile.Role.PHARMACIST,
                "pharmacist-only",
                ("pharmacy_worklist",),
                ("billing_worklist", "queue_list", "waiting_vitals", "waiting_confirmation", "register_patient", "opd_room_select", "device_management", "dashboard:home"),
            ),
            (
                StaffProfile.Role.CASHIER,
                "cashier-only",
                ("billing_worklist",),
                ("pharmacy_worklist", "queue_list", "waiting_vitals", "waiting_confirmation", "register_patient", "opd_room_select", "device_management", "dashboard:home"),
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

    def test_superuser_lands_on_operational_dashboard_not_test_control_center(self):
        admin = self.make_user("landing-admin", StaffProfile.Role.STAFF, superuser=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("role_landing"))

        self.assertRedirects(response, reverse("dashboard:home"))

    def test_login_page_describes_all_hospital_staff(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "เข้าสู่ระบบบุคลากร")
        self.assertContains(response, "สำหรับบุคลากรโรงพยาบาล")
        self.assertNotContains(response, "เข้าสู่ระบบ (พยาบาล)")

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

    def test_superuser_can_suspend_and_reactivate_staff_with_audit_history(self):
        admin = self.make_user("account-admin", StaffProfile.Role.STAFF, superuser=True)
        target = self.make_user("suspend-target", StaffProfile.Role.NURSE)
        self.client.force_login(admin)

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "set_account_status",
            "user_id": target.pk,
            "is_active": "0",
            "reason": "พักการใช้งานระหว่างตรวจสอบบัญชี",
        })
        self.assertRedirects(response, reverse("personnel_dashboard"))
        target.refresh_from_db()
        self.assertFalse(target.is_active)

        suspend_log = AccountStatusLog.objects.get(user=target)
        self.assertEqual(suspend_log.action, AccountStatusLog.Action.SUSPEND)
        self.assertEqual(suspend_log.actor, admin)
        self.assertEqual(suspend_log.reason, "พักการใช้งานระหว่างตรวจสอบบัญชี")

        page = self.client.get(reverse("personnel_dashboard"))
        self.assertContains(page, "suspend-target")
        self.assertContains(page, "ระงับบัญชี")

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "set_account_status",
            "user_id": target.pk,
            "is_active": "1",
            "reason": "ตรวจสอบแล้วสามารถกลับมาใช้งานได้",
        })
        self.assertRedirects(response, reverse("personnel_dashboard"))
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual(
            list(
                AccountStatusLog.objects.filter(user=target)
                .order_by("created_at", "id")
                .values_list("action", flat=True)
            ),
            [AccountStatusLog.Action.SUSPEND, AccountStatusLog.Action.ACTIVATE],
        )

    def test_account_status_change_requires_reason_and_protects_superuser(self):
        admin = self.make_user("status-admin", StaffProfile.Role.STAFF, superuser=True)
        target = self.make_user("reason-target", StaffProfile.Role.STAFF)
        self.client.force_login(admin)

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "set_account_status",
            "user_id": target.pk,
            "is_active": "0",
            "reason": "",
        })
        self.assertRedirects(response, reverse("personnel_dashboard"))
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertFalse(AccountStatusLog.objects.filter(user=target).exists())

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "set_account_status",
            "user_id": admin.pk,
            "is_active": "0",
            "reason": "ไม่ควรทำได้",
        })
        self.assertRedirects(response, reverse("personnel_dashboard"))
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)

    def test_account_can_review_its_own_role_and_duties(self):
        nurse = self.make_user("nurse-rights", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        response = self.client.get(reverse("my_permissions"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "พยาบาล")
        self.assertContains(response, "ยืนยันหรือแก้ผลคัดกรอง AI")
        self.assertContains(response, "ตั้งค่าบัญชี")
        self.assertNotContains(response, "วัดและบันทึกสัญญาณชีพ")
        self.assertNotContains(response, "ตรวจรักษาและบันทึกผลแพทย์")

    def test_superuser_can_simulate_staff_role_and_restore_admin(self):
        admin = self.make_user("role-sim-admin", StaffProfile.Role.STAFF, superuser=True)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("switch_test_role"),
            {"role": StaffProfile.Role.NURSE},
        )
        self.assertEqual(response.status_code, 302)

        permissions = self.client.get(reverse("my_permissions"))
        self.assertEqual(permissions.status_code, 200)
        self.assertContains(permissions, "โหมดทดสอบ: พยาบาลวิชาชีพ")
        self.assertContains(permissions, "ยืนยันหรือแก้ผลคัดกรอง AI")
        self.assertNotContains(permissions, "วัดและบันทึกสัญญาณชีพ")

        self.assertEqual(self.client.get(reverse("waiting_confirmation")).status_code, 200)
        self.assertEqual(self.client.get(reverse("waiting_vitals")).status_code, 403)
        self.assertEqual(self.client.get(reverse("system_test:index")).status_code, 403)

        response = self.client.post(reverse("switch_test_role"), {"role": "ADMIN"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("system_test:index")).status_code, 200)

    def test_regular_user_cannot_use_role_simulation_switcher(self):
        nurse = self.make_user("role-sim-nurse", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)

        response = self.client.post(
            reverse("switch_test_role"),
            {"role": StaffProfile.Role.DOCTOR},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("opd_room_select")).status_code, 403)
        self.assertEqual(self.client.get(reverse("waiting_confirmation")).status_code, 200)

    def test_account_settings_requires_login(self):
        response = self.client.get(reverse("account_settings"))
        self.assertEqual(response.status_code, 302)

    def test_user_can_change_own_password_and_remain_logged_in(self):
        nurse = self.make_user("password-nurse", StaffProfile.Role.NURSE)
        self.client.force_login(nurse)
        response = self.client.post(reverse("account_settings"), {
            "old_password": "test-password-123",
            "new_password1": "New-safe-password-456!",
            "new_password2": "New-safe-password-456!",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "เปลี่ยนรหัสผ่านเรียบร้อยแล้ว")
        nurse.refresh_from_db()
        self.assertTrue(nurse.check_password("New-safe-password-456!"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), nurse.pk)

    def test_operational_write_duties_do_not_overlap_between_roles(self):
        unique_owner = {
            Capability.REGISTER_PATIENT: StaffProfile.Role.STAFF,
            Capability.EDIT_PATIENT: StaffProfile.Role.STAFF,
            Capability.RECORD_VITALS: StaffProfile.Role.NURSE_ASSISTANT,
            Capability.CONFIRM_TRIAGE: StaffProfile.Role.NURSE,
            Capability.MANAGE_QUEUE: StaffProfile.Role.QUEUE_OPERATOR,
            Capability.DOCTOR_ASSESSMENT: StaffProfile.Role.DOCTOR,
            Capability.CREATE_PRESCRIPTION: StaffProfile.Role.DOCTOR,
            Capability.ISSUE_MEDICAL_CERTIFICATE: StaffProfile.Role.DOCTOR,
            Capability.MANAGE_PHARMACY: StaffProfile.Role.PHARMACIST,
            Capability.MANAGE_BILLING: StaffProfile.Role.CASHIER,
            Capability.ACKNOWLEDGE_ALERT: StaffProfile.Role.NURSE,
            Capability.END_MONITORING: StaffProfile.Role.NURSE,
            Capability.MANAGE_DEVICE: StaffProfile.Role.BIOMEDICAL,
        }
        for capability, expected_role in unique_owner.items():
            owners = []
            for role, _label in StaffProfile.Role.choices:
                user = self.make_user(f"owner-{role.lower()}-{capability}", role)
                if capability in capabilities_for(user):
                    owners.append(role)
            self.assertEqual(owners, [expected_role], capability)
