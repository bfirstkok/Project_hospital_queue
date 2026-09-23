from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from queues.models import ShiftSchedule, StaffProfile


class ShiftDutyAssignmentTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_superuser(
            username="duty-manager",
            email="manager@example.test",
            password="secret",
        )
        self.nurse = user_model.objects.create_user(
            username="duty-nurse",
            password="secret",
            first_name="พยาบาล",
            last_name="ทดสอบ",
        )
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        self.client.force_login(self.manager)

    def test_shift_page_exposes_explicit_duty_field(self):
        response = self.client.get(reverse("shift_schedule"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "หน้าที่ประจำเวร")
        self.assertContains(response, 'name="duty_assignment"', html=False)
        self.assertContains(response, "คัดกรองผู้ป่วย")
        self.assertContains(response, "ประจำห้องตรวจ 2")

    def test_shift_timetable_keeps_every_role_column_visible_when_filtered(self):
        response = self.client.get(
            reverse("shift_schedule"),
            {"role": StaffProfile.Role.NURSE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(response.context["role_columns"]),
            len(StaffProfile.Role.choices) + 1,
        )
        self.assertEqual(
            [row["role"] for row in response.context["role_columns"]],
            ["ADMIN", *list(StaffProfile.Role.values)],
        )
        self.assertContains(response, "ผู้ดูแลระบบ")
        for _value, label in StaffProfile.Role.choices:
            self.assertContains(response, label)

    def test_manager_superuser_can_be_scheduled_as_system_administrator(self):
        response = self.client.post(reverse("shift_schedule"), {
            "action": "save_shift",
            "day": timezone.localdate().isoformat(),
            "user_id": self.manager.id,
            "shift_date": timezone.localdate().isoformat(),
            "start_time": "08:00",
            "end_time": "16:00",
            "status": ShiftSchedule.Status.SCHEDULED,
            "duty_assignment": "ดูแลระบบและประสานงาน",
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        shift = ShiftSchedule.objects.get(user=self.manager)
        self.assertEqual(shift.note, "ดูแลระบบและประสานงาน")
        self.assertContains(response, "ผู้ดูแลระบบ")
        self.assertContains(response, "duty-manager")

    def test_scheduled_shift_requires_duty_assignment(self):
        response = self.client.post(reverse("shift_schedule"), {
            "action": "save_shift",
            "day": timezone.localdate().isoformat(),
            "user_id": self.nurse.id,
            "shift_date": timezone.localdate().isoformat(),
            "start_time": "08:00",
            "end_time": "16:00",
            "status": ShiftSchedule.Status.SCHEDULED,
            "duty_assignment": "",
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShiftSchedule.objects.exists())
        self.assertContains(response, "กรุณากำหนดหน้าที่ประจำเวร")

    def test_manager_can_assign_nurse_specific_duty(self):
        response = self.client.post(reverse("shift_schedule"), {
            "action": "save_shift",
            "day": timezone.localdate().isoformat(),
            "user_id": self.nurse.id,
            "shift_date": timezone.localdate().isoformat(),
            "start_time": "08:00",
            "end_time": "16:00",
            "status": ShiftSchedule.Status.SCHEDULED,
            "duty_assignment": "เฝ้าระวังผู้ป่วยสวมอุปกรณ์",
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        shift = ShiftSchedule.objects.get()
        self.assertEqual(shift.note, "เฝ้าระวังผู้ป่วยสวมอุปกรณ์")
        self.assertContains(response, "เฝ้าระวังผู้ป่วยสวมอุปกรณ์")
        self.assertContains(response, "บันทึกเวรและหน้าที่")

    def test_old_note_payload_remains_backward_compatible(self):
        response = self.client.post(reverse("shift_schedule"), {
            "action": "save_shift",
            "day": timezone.localdate().isoformat(),
            "user_id": self.nurse.id,
            "shift_date": timezone.localdate().isoformat(),
            "start_time": "16:00",
            "end_time": "00:00",
            "status": ShiftSchedule.Status.SCHEDULED,
            "note": "ประจำห้องตรวจ 3",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ShiftSchedule.objects.get().note, "ประจำห้องตรวจ 3")
