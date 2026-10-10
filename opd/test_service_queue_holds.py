"""Regression coverage for service-queue holds, independent of clinical state."""

from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient
from queues.models import Queue, StaffProfile, Visit, VisitWorkflowLog

from .models import Bill, Prescription, PrescriptionItem


class _QueueRowParser(HTMLParser):
    """Read stable row attributes without depending on labels or whitespace."""

    def __init__(self, row_class, selection_parameter):
        super().__init__()
        self.row_class = row_class
        self.selection_parameter = selection_parameter
        self.rows = {}

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if self.row_class not in attributes.get("class", "").split():
            return
        link = attributes.get("data-href", attributes.get("href", ""))
        record_ids = parse_qs(urlsplit(link).query).get(self.selection_parameter, [])
        if record_ids:
            self.rows[int(record_ids[0])] = attributes


class _ServiceQueueHoldTests:
    def setUp(self):
        self.doctor = get_user_model().objects.create_user("hold-doctor", password="test-pass")
        StaffProfile.objects.create(user=self.doctor, role=StaffProfile.Role.DOCTOR)
        self.operator = get_user_model().objects.create_user(
            "hold-operator", password="test-pass", first_name="ทดสอบ", last_name="คิวบริการ"
        )
        StaffProfile.objects.create(user=self.operator, role=self.operator_role)
        self.other_operator = get_user_model().objects.create_user("hold-other", password="test-pass")
        StaffProfile.objects.create(user=self.other_operator, role=self.other_role)
        first_time = timezone.now() - timedelta(minutes=30)
        self.records = []
        for index, status in enumerate(self.active_statuses):
            patient = Patient.objects.create(
                first_name="ทดสอบ",
                last_name=f"คิวบริการ{index + 1}",
                national_id=f"990000000000{index + 1}",
            )
            visit = Visit.objects.create(patient=patient, final_severity=Visit.Severity.GREEN)
            Queue.objects.create(visit=visit, status=Queue.Status.OPD_DONE, manual_sequence=41 + index)
            self.records.append(self._create_record(visit, status, first_time + timedelta(minutes=index)))
        self.first, self.second, self.third = self.records
        self.client.force_login(self.operator)

    def _post(self, record, action):
        return self.client.post(reverse(self.action_route, args=[record.pk]), {"action": action})

    def _worklist(self, record=None):
        query = {self.selection_parameter: record.pk} if record else {}
        response = self.client.get(reverse(self.worklist_route), query)
        self.assertEqual(response.status_code, 200)
        return response

    def _rows(self, response):
        return {row.pk: row for row in response.context[self.rows_context]}

    def _logs(self, record=None):
        logs = VisitWorkflowLog.objects.filter(event_type=VisitWorkflowLog.EventType.SERVICE_QUEUE_ACTION)
        return logs.filter(visit=record.visit) if record else logs

    def _snapshot(self, record):
        # A queue hold must not silently complete medication work or settle debt.
        record.refresh_from_db()
        return (
            Prescription.objects.filter(visit=record.visit).values(
                "status", "note", "sent_at", "dispensed_at", "dispensed_by_id"
            ).get(),
            list(PrescriptionItem.objects.filter(prescription__visit=record.visit).values(
                "medication_name", "quantity", "unit_price", "instructions"
            )),
            Bill.objects.filter(visit=record.visit).values(
                "status", "consultation_fee", "medicine_total", "other_fee", "subtotal",
                "covered_amount", "patient_due", "paid_at", "received_by_id", "pharmacy_skipped"
            ).get(),
            Queue.objects.filter(visit=record.visit).values("status", "manual_sequence").get(),
        )

    def _assert_unchanged_queue(self, record, before):
        record.refresh_from_db()
        self.assertEqual(tuple(getattr(record, field) for field in self.queue_fields), before)

    def test_skip_before_call_preserves_clinical_and_financial_state(self):
        before = self._snapshot(self.first)
        entered_at = getattr(self.first, self.entered_field)
        self.assertIsNone(getattr(self.first, self.called_field))

        response = self._post(self.first, "skip")

        self.assertRedirects(response, reverse(self.worklist_route), fetch_redirect_response=False)
        self.first.refresh_from_db()
        self.assertIsNotNone(getattr(self.first, self.skipped_field))
        self.assertIsNone(getattr(self.first, self.called_field))
        self.assertEqual(getattr(self.first, self.entered_field), entered_at)
        self.assertEqual(self._snapshot(self.first), before)

    def test_uncalled_non_head_can_be_held_without_disturbing_fifo_head(self):
        response = self._worklist()
        self.assertFalse(self._rows(response)[self.third.pk].can_call_queue)
        self.assertTrue(self._rows(response)[self.third.pk].can_skip_queue)

        self._post(self.third, "skip")

        rows = self._rows(self._worklist())
        self.assertTrue(rows[self.third.pk].queue_is_skipped)
        self.assertTrue(rows[self.first.pk].can_call_queue)
        self.assertEqual(rows[self.second.pk].queue_position, 2)

    def test_held_rows_remain_in_context_but_not_default_selection_or_waiting_positions(self):
        self._post(self.first, "skip")

        response = self._worklist()
        rows = self._rows(response)

        self.assertEqual(set(rows), {record.pk for record in self.records})
        self.assertEqual(response.context[self.selected_context].pk, self.second.pk)
        self.assertTrue(rows[self.first.pk].queue_is_skipped)
        self.assertIsNone(rows[self.first.pk].queue_position)
        self.assertFalse(rows[self.first.pk].can_call_queue)
        self.assertFalse(rows[self.first.pk].can_skip_queue)
        self.assertEqual(rows[self.second.pk].queue_position, 1)
        self.assertEqual(rows[self.third.pk].queue_position, 2)
        self.assertTrue(rows[self.second.pk].can_call_queue)

    def test_only_held_records_have_no_automatic_selection(self):
        for record in self.records:
            self._post(record, "skip")

        response = self._worklist()

        self.assertEqual(len(response.context[self.rows_context]), 3)
        self.assertIsNone(response.context[self.selected_context])
        self._assert_counts(response, active=0, skipped=3, per_status=(0, 0, 0))

    def test_explicit_held_selection_is_available_for_requeue(self):
        self._post(self.first, "skip")

        response = self._worklist(self.first)

        selected = response.context[self.selected_context]
        self.assertEqual(selected.pk, self.first.pk)
        self.assertTrue(selected.queue_is_skipped)
        self.assertContains(response, f'action="{reverse(self.action_route, args=[self.first.pk])}"')
        self.assertContains(response, 'name="action" value="requeue"')

    def test_waiting_counts_exclude_held_records_for_every_active_status(self):
        self._assert_counts(self._worklist(), active=3, skipped=0, per_status=(1, 1, 1))
        for index, record in enumerate(self.records):
            with self.subTest(status=record.status):
                self._post(record, "skip")
                remaining = tuple(int(position > index) for position in range(3))
                self._assert_counts(
                    self._worklist(), active=2 - index, skipped=index + 1, per_status=remaining
                )

    def test_server_html_hides_held_rows_and_exposes_separate_filter(self):
        self._post(self.first, "skip")

        response = self._worklist()
        parser = _QueueRowParser(self.row_class, self.selection_parameter)
        parser.feed(response.content.decode())

        self.assertEqual(set(parser.rows), {record.pk for record in self.records})
        held = parser.rows[self.first.pk]
        self.assertEqual(held.get("data-queue-skipped"), "true")
        self.assertIn("hidden", held)
        self.assertNotIn("selected", held.get("class", "").split())
        for record in (self.second, self.third):
            self.assertEqual(parser.rows[record.pk].get("data-queue-skipped"), "false")
            self.assertNotIn("hidden", parser.rows[record.pk])
        self.assertContains(response, f'{self.filter_attribute}="SKIPPED"')
        self.assertContains(response, 'name="action" value="skip"')

    def test_next_patient_can_be_called_after_an_uncalled_hold(self):
        self._post(self.first, "skip")

        response = self._post(self.second, "call")

        self.assertRedirects(response, reverse(self.worklist_route), fetch_redirect_response=False)
        self.second.refresh_from_db()
        self.assertIsNotNone(getattr(self.second, self.called_field))
        rows = self._rows(self._worklist())
        self.assertTrue(rows[self.second.pk].queue_is_called)
        self.assertTrue(rows[self.second.pk].can_skip_queue)
        self.assertFalse(rows[self.third.pk].can_call_queue)
        self.assertEqual(self._logs(self.second).get().details["action"], "call")

    def test_call_still_requires_fifo_and_no_other_called_patient(self):
        self._post(self.third, "call")
        self.third.refresh_from_db()
        self.assertIsNone(getattr(self.third, self.called_field))
        self.assertFalse(self._logs().exists())
        self._post(self.first, "call")
        self._post(self.second, "call")
        self.second.refresh_from_db()
        self.assertIsNone(getattr(self.second, self.called_field))
        self.assertEqual(self._logs().count(), 1)

    def test_requeue_joins_tail_and_preserves_ticket_and_business_state(self):
        original = self._rows(self._worklist())[self.first.pk].queue_display_number
        before = self._snapshot(self.first)
        self._post(self.first, "skip")

        response = self._post(self.first, "requeue")

        self.assertRedirects(response, reverse(self.worklist_route), fetch_redirect_response=False)
        self.first.refresh_from_db()
        self.assertIsNone(getattr(self.first, self.skipped_field))
        self.assertIsNone(getattr(self.first, self.called_field))
        self.assertGreater(getattr(self.first, self.entered_field), getattr(self.third, self.entered_field))
        response = self._worklist()
        records = response.context[self.rows_context]
        self.assertEqual([record.pk for record in records], [self.second.pk, self.third.pk, self.first.pk])
        self.assertEqual([record.queue_position for record in records], [1, 2, 3])
        self.assertEqual(records[-1].queue_display_number, original)
        self.assertEqual(original, "Q041")
        self.assertEqual(self._snapshot(self.first), before)
        self._assert_counts(response, active=3, skipped=0, per_status=(1, 1, 1))

    def test_successful_hold_and_requeue_have_attributed_audit_events(self):
        self._post(self.first, "skip")
        self._post(self.first, "requeue")

        logs = list(self._logs(self.first).order_by("id"))

        self.assertEqual([log.details["action"] for log in logs], ["skip", "requeue"])
        for log in logs:
            self.assertEqual(log.actor_id, self.operator.pk)
            self.assertEqual(log.actor_name, self.operator.get_full_name())
            self.assertEqual(log.actor_role, self.operator.hospital_staff_profile.get_role_display())
            self.assertEqual(log.details["service"], self.service)
            self.assertEqual(log.details["record_id"], self.first.pk)
            self.assertTrue(log.description)

    def test_duplicate_hold_and_invalid_requeue_do_not_mutate_or_add_audit(self):
        self._post(self.first, "requeue")
        self.assertFalse(self._logs().exists())
        self._post(self.first, "skip")
        self.first.refresh_from_db()
        before = tuple(getattr(self.first, field) for field in self.queue_fields)

        self._post(self.first, "skip")

        self._assert_unchanged_queue(self.first, before)
        self.assertEqual(self._logs().count(), 1)

    def test_queue_actions_require_post(self):
        before = tuple(getattr(self.first, field) for field in self.queue_fields)

        response = self.client.get(reverse(self.action_route, args=[self.first.pk]), {"action": "skip"})

        self.assertEqual(response.status_code, 405)
        self._assert_unchanged_queue(self.first, before)
        self.assertFalse(self._logs().exists())

    def test_invalid_actions_are_bad_requests_without_mutations(self):
        before = tuple(getattr(self.first, field) for field in self.queue_fields)
        for action in ("", "cancel", "SKIP"):
            with self.subTest(action=action):
                self.assertEqual(self._post(self.first, action).status_code, 400)
                self._assert_unchanged_queue(self.first, before)
        self.assertFalse(self._logs().exists())

    def test_other_service_role_cannot_manage_this_queue(self):
        before = tuple(getattr(self.first, field) for field in self.queue_fields)
        self.client.force_login(self.other_operator)
        for action in ("skip", "call", "requeue"):
            with self.subTest(action=action):
                self.assertEqual(self._post(self.first, action).status_code, 403)
                self._assert_unchanged_queue(self.first, before)
        self.assertFalse(self._logs().exists())

    def test_anonymous_user_cannot_manage_queue(self):
        before = tuple(getattr(self.first, field) for field in self.queue_fields)
        self.client.logout()

        response = self._post(self.first, "skip")

        self.assertEqual(response.status_code, 302)
        self._assert_unchanged_queue(self.first, before)
        self.assertFalse(self._logs().exists())

    def test_terminal_records_cannot_be_held(self):
        for status in self.terminal_statuses:
            with self.subTest(status=status):
                self.first.status = status
                self.first.save(update_fields=["status"])
                before = self._snapshot(self.first)
                response = self._post(self.first, "skip")
                self.assertRedirects(response, reverse(self.worklist_route), fetch_redirect_response=False)
                self.first.refresh_from_db()
                self.assertIsNone(getattr(self.first, self.skipped_field))
                self.assertIsNone(getattr(self.first, self.called_field))
                self.assertEqual(self._snapshot(self.first), before)
        self.assertFalse(self._logs().exists())

    def test_public_board_separates_held_tickets_and_never_exposes_patient_identity(self):
        self._post(self.first, "skip")
        self.client.logout()

        response = self.client.get(reverse(self.display_route))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [record.pk for record in response.context["queue_items"]],
            [self.second.pk, self.third.pk],
        )
        held = response.context["skipped_queue_items"]
        self.assertEqual([record.pk for record in held], [self.first.pk])
        self.assertEqual(held[0].queue_display_number, "Q041")
        self.assertTrue(held[0].queue_is_skipped)
        self.assertContains(response, 'aria-label="คิวค้าง / ไม่มา"')
        for record in self.records:
            patient = record.visit.patient
            self.assertNotContains(response, f"{patient.first_name} {patient.last_name}")
            self.assertNotContains(response, patient.national_id)
            if patient.hn:
                self.assertNotContains(response, patient.hn)

    def test_public_board_with_only_held_tickets_has_no_waiting_rows(self):
        for record in self.records:
            self._post(record, "skip")
        self.client.logout()

        response = self.client.get(reverse(self.display_route))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["queue_items"], [])
        self.assertEqual(len(response.context["skipped_queue_items"]), 3)


class PharmacyQueueHoldTests(_ServiceQueueHoldTests, TestCase):
    service = "pharmacy"
    operator_role = StaffProfile.Role.PHARMACIST
    other_role = StaffProfile.Role.CASHIER
    action_route = "pharmacy_queue_action"
    worklist_route = "pharmacy_worklist"
    display_route = "pharmacy_queue_display"
    rows_context = "prescriptions"
    selected_context = "selected_rx"
    selection_parameter = "prescription_id"
    row_class = "ph-queue-row"
    filter_attribute = "data-filter"
    entered_field = "pharmacy_queue_entered_at"
    called_field = "pharmacy_queue_called_at"
    skipped_field = "pharmacy_queue_skipped_at"
    queue_fields = (entered_field, called_field, skipped_field)
    active_statuses = (Prescription.Status.SENT, Prescription.Status.PREPARING, Prescription.Status.READY)
    terminal_statuses = (Prescription.Status.DRAFT, Prescription.Status.DISPENSED, Prescription.Status.CANCELLED)

    def _create_record(self, visit, status, entered_at):
        prescription = Prescription.objects.create(
            visit=visit, prescribed_by=self.doctor, status=status, sent_at=entered_at,
            pharmacy_queue_entered_at=entered_at, note="ข้อมูลทดสอบ"
        )
        PrescriptionItem.objects.create(
            prescription=prescription, medication_name="Test medication", quantity=4,
            unit_price=Decimal("7.50"), instructions="ข้อมูลทดสอบ"
        )
        Bill.objects.create(
            visit=visit, status=Bill.Status.READY, consultation_fee=Decimal("200.00"),
            medicine_total=Decimal("30.00"), other_fee=Decimal("25.00"),
            subtotal=Decimal("255.00"), patient_due=Decimal("255.00"),
            billing_queue_entered_at=entered_at,
        )
        return prescription

    def _assert_counts(self, response, *, active, skipped, per_status):
        counts = response.context["pharmacy_status_counts"]
        self.assertEqual(counts["ACTIVE"], active)
        self.assertEqual(counts["SKIPPED"], skipped)
        self.assertEqual(tuple(counts[status] for status in self.active_statuses), per_status)


class BillingQueueHoldTests(_ServiceQueueHoldTests, TestCase):
    service = "billing"
    operator_role = StaffProfile.Role.CASHIER
    other_role = StaffProfile.Role.PHARMACIST
    action_route = "billing_queue_action"
    worklist_route = "billing_worklist"
    display_route = "billing_queue_display"
    rows_context = "bills"
    selected_context = "selected_bill"
    selection_parameter = "bill_id"
    row_class = "finance-queue-item"
    filter_attribute = "data-bill-filter"
    entered_field = "billing_queue_entered_at"
    called_field = "billing_queue_called_at"
    skipped_field = "billing_queue_skipped_at"
    queue_fields = (entered_field, called_field, skipped_field)
    active_statuses = (Bill.Status.DRAFT, Bill.Status.READY, Bill.Status.WAIVED)
    terminal_statuses = (Bill.Status.PAID, Bill.Status.CANCELLED, Bill.Status.TRANSFERRED)

    def _create_record(self, visit, status, entered_at):
        Prescription.objects.create(
            visit=visit, prescribed_by=self.doctor, status=Prescription.Status.SENT,
            sent_at=entered_at, pharmacy_queue_entered_at=entered_at,
        )
        return Bill.objects.create(
            visit=visit, status=status, consultation_fee=Decimal("200.00"),
            other_fee=Decimal("25.00"), subtotal=Decimal("225.00"),
            covered_amount=Decimal("225.00") if status == Bill.Status.WAIVED else Decimal("0.00"),
            patient_due=Decimal("0.00") if status == Bill.Status.WAIVED else Decimal("225.00"),
            billing_queue_entered_at=entered_at,
        )

    def _assert_counts(self, response, *, active, skipped, per_status):
        summary = response.context["billing_summary"]
        self.assertEqual(summary["active"], active)
        self.assertEqual(summary["skipped"], skipped)
        self.assertEqual(summary["review"], per_status[0])
        self.assertEqual(summary["awaiting_payment"], per_status[1] + per_status[2])

    def test_paid_timestamp_blocks_hold_even_with_a_waiting_status(self):
        for status in (Bill.Status.READY, Bill.Status.WAIVED):
            with self.subTest(status=status):
                self.first.status = status
                self.first.paid_at = timezone.now()
                self.first.save(update_fields=["status", "paid_at"])
                before = self._snapshot(self.first)

                self._post(self.first, "skip")

                self.first.refresh_from_db()
                self.assertIsNone(self.first.billing_queue_skipped_at)
                self.assertEqual(self._snapshot(self.first), before)
                rows = self._rows(self._worklist(self.first))
                if self.first.pk in rows:
                    self.assertFalse(rows[self.first.pk].can_skip_queue)
        self.assertFalse(self._logs().exists())

    def test_bill_not_entered_into_service_queue_cannot_be_held(self):
        self.first.billing_queue_entered_at = None
        self.first.save(update_fields=["billing_queue_entered_at"])
        before = self._snapshot(self.first)

        self._post(self.first, "skip")

        self.first.refresh_from_db()
        self.assertIsNone(self.first.billing_queue_skipped_at)
        self.assertEqual(self._snapshot(self.first), before)
        self.assertNotIn(self.first.pk, self._rows(self._worklist()))
        self.assertFalse(self._logs().exists())
