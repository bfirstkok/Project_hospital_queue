from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient

from .care_workload import (
    MAX_PATIENTS_PER_NURSE,
    assign_visit_to_nurse,
    auto_assign_visit,
    nurse_workload_rows,
)
from .models import NurseCareAssignment, Queue, StaffDuty, StaffProfile, Visit


class NurseWorkloadManagementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(
            username="head-nurse-workload",
            password="secret",
            first_name="หัวหน้า",
            last_name="เวร",
        )
        self.nurse_a = user_model.objects.create_user(
            username="nurse-workload-a",
            password="secret",
            first_name="กมล",
            last_name="เอ",
        )
        self.nurse_b = user_model.objects.create_user(
            username="nurse-workload-b",
            password="secret",
            first_name="กมล",
            last_name="บี",
        )
        self.nurse_c = user_model.objects.create_user(
            username="nurse-workload-c",
            password="secret",
            first_name="กมล",
            last_name="ซี",
        )
        for nurse in (self.nurse_a, self.nurse_b, self.nurse_c):
            StaffProfile.objects.create(user=nurse, role=StaffProfile.Role.NURSE)
            StaffDuty.objects.create(
                user=nurse,
                duty_date=timezone.localdate(),
                is_present=True,
                is_available=True,
            )

        self.client.force_login(self.manager)
        self.patient_counter = 0

    def make_visit(self, *, status=Queue.Status.OBSERVATION_MONITORING, severity=Visit.Severity.YELLOW):
        self.patient_counter += 1
        patient = Patient.objects.create(
            first_name=f"Load{self.patient_counter}",
            last_name="Patient",
            national_id=f"8{self.patient_counter:012d}",
        )
        visit = Visit.objects.create(patient=patient, final_severity=severity)
        Queue.objects.create(visit=visit, status=status, priority=3)
        return visit

    def assign_direct(self, nurse, visit):
        return NurseCareAssignment.objects.create(
            nurse=nurse,
            visit=visit,
            assigned_by=self.manager,
        )

    def test_auto_assign_chooses_least_loaded_nurse(self):
        self.assign_direct(self.nurse_a, self.make_visit())
        self.assign_direct(self.nurse_a, self.make_visit())
        self.assign_direct(self.nurse_b, self.make_visit())
        target_visit = self.make_visit()

        assignment, count = auto_assign_visit(
            visit=target_visit,
            assigned_by=self.manager,
        )

        self.assertEqual(assignment.nurse, self.nurse_c)
        self.assertEqual(count, 1)

    def test_manual_assignment_refuses_fifth_patient(self):
        for _ in range(MAX_PATIENTS_PER_NURSE):
            self.assign_direct(self.nurse_a, self.make_visit())

        with self.assertRaisesMessage(ValueError, "nurse_full"):
            assign_visit_to_nurse(
                visit=self.make_visit(),
                nurse=self.nurse_a,
                assigned_by=self.manager,
            )

        self.assertEqual(
            NurseCareAssignment.objects.filter(nurse=self.nurse_a, is_active=True).count(),
            MAX_PATIENTS_PER_NURSE,
        )

    def test_workload_rows_expose_counts_capacity_and_cases(self):
        visit = self.make_visit()
        self.assign_direct(self.nurse_a, visit)

        rows = nurse_workload_rows()
        row = next(item for item in rows if item["user"] == self.nurse_a)

        self.assertEqual(row["patient_count"], 1)
        self.assertEqual(row["remaining_capacity"], 3)
        self.assertEqual(row["load_state"], "free")
        self.assertEqual(row["active_cases"][0].visit, visit)

    def test_personnel_dashboard_shows_head_nurse_workload_summary(self):
        visit = self.make_visit()
        self.assign_direct(self.nurse_a, visit)

        response = self.client.get(reverse("personnel_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ว่างรับเคส")
        self.assertContains(response, "ผู้ป่วยยังไม่มีผู้ดูแล")
        self.assertContains(response, "ส่งต่อเวร")
        self.assertContains(response, "1/4")
        self.assertContains(response, visit.queue.display_number)
        self.assertContains(response, 'id="staff-search"')
        self.assertContains(response, 'data-role="NURSE"')

    def test_going_off_shift_auto_handover_moves_active_case(self):
        visit = self.make_visit()
        old_assignment = self.assign_direct(self.nurse_a, visit)

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "set_attendance",
            "user_id": self.nurse_a.id,
            "is_present": "0",
        })

        self.assertRedirects(response, reverse("personnel_dashboard"))
        old_assignment.refresh_from_db()
        self.assertFalse(old_assignment.is_active)
        self.assertIsNotNone(old_assignment.ended_at)
        replacement = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertIn(replacement.nurse, {self.nurse_b, self.nurse_c})
        duty = StaffDuty.objects.get(user=self.nurse_a, duty_date=timezone.localdate())
        self.assertFalse(duty.is_present)
        self.assertFalse(duty.is_available)

    def test_pausing_availability_auto_handover_moves_active_case(self):
        visit = self.make_visit()
        self.assign_direct(self.nurse_a, visit)

        self.client.post(reverse("personnel_dashboard"), {
            "action": "set_availability",
            "user_id": self.nurse_a.id,
            "is_available": "0",
        })

        current = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertIn(current.nurse, {self.nurse_b, self.nurse_c})
        self.assertNotEqual(current.nurse, self.nurse_a)

    def test_named_handover_preserves_assignment_history(self):
        visit = self.make_visit()
        old_assignment = self.assign_direct(self.nurse_a, visit)

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "handover_nurse",
            "from_nurse_id": self.nurse_a.id,
            "to_nurse_id": self.nurse_b.id,
        })

        self.assertRedirects(response, reverse("personnel_dashboard"))
        old_assignment.refresh_from_db()
        self.assertFalse(old_assignment.is_active)
        self.assertIsNotNone(old_assignment.ended_at)
        new_assignment = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertEqual(new_assignment.nurse, self.nurse_b)
        self.assertEqual(new_assignment.assigned_by, self.manager)
        self.assertEqual(NurseCareAssignment.objects.filter(visit=visit).count(), 2)

    def test_existing_pre_monitoring_yellow_owner_can_be_changed(self):
        visit = self.make_visit(status=Queue.Status.WAITING_QUEUE)
        old_assignment = self.assign_direct(self.nurse_a, visit)

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "reassign_patient",
            "visit_id": visit.id,
            "nurse_id": self.nurse_b.id,
        })

        self.assertRedirects(response, reverse("personnel_dashboard"))
        old_assignment.refresh_from_db()
        self.assertFalse(old_assignment.is_active)
        current = NurseCareAssignment.objects.get(visit=visit, is_active=True)
        self.assertEqual(current.nurse, self.nurse_b)

    def test_unowned_waiting_queue_still_cannot_be_manually_assigned_from_personnel(self):
        visit = self.make_visit(status=Queue.Status.WAITING_QUEUE)

        self.client.post(reverse("personnel_dashboard"), {
            "action": "assign_patient",
            "visit_id": visit.id,
            "nurse_id": self.nurse_b.id,
        })

        self.assertFalse(NurseCareAssignment.objects.filter(visit=visit, is_active=True).exists())

    def test_terminal_queue_state_releases_nurse_capacity(self):
        visit = self.make_visit()
        assignment = self.assign_direct(self.nurse_a, visit)

        visit.queue.status = Queue.Status.OPD_DONE
        visit.queue.save(update_fields=["status"])

        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)
        self.assertIsNotNone(assignment.ended_at)

    def test_discharge_releases_nurse_capacity(self):
        visit = self.make_visit(status=Queue.Status.MONITORING, severity=Visit.Severity.GREEN)
        assignment = self.assign_direct(self.nurse_a, visit)

        response = self.client.post(reverse("discharge_visit", args=[visit.id]))

        self.assertRedirects(response, reverse("monitor_dashboard"))
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.DISCHARGED)

    def test_reassessment_case_shows_responsible_nurse_alert_on_personnel_page(self):
        visit = self.make_visit(status=Queue.Status.REASSESSMENT_REQUIRED)
        self.assign_direct(self.nurse_a, visit)

        response = self.client.get(reverse("personnel_dashboard"), {"filter": "reassessment"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ต้องประเมินซ้ำทันที")
        self.assertContains(response, self.nurse_a.get_full_name())
