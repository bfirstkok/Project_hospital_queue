"""Patient-facing journey times must come from real, stage-specific evidence."""

import json
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from opd.models import Bill, Prescription, VisitAssessment
from queues.models import Queue, Visit, VisitWorkflowLog, VitalSign

from .models import Patient
from .views import _patient_journey_for_visit, _serialize_patient_journey


@override_settings(
    PATIENT_APP_ORIGINS={"https://patient.example.com"},
    PATIENT_TOKEN_MAX_AGE=3600,
)
class PatientJourneyTimestampTests(TestCase):
    step_keys = {
        "registration", "vitals", "triage", "queue", "doctor", "pharmacy", "billing", "complete",
    }

    def setUp(self):
        cache.clear()
        self.patient = Patient.objects.create(
            first_name="ทดสอบ",
            last_name="เวลารับบริการ",
            national_id="9900000000001",
            phone="0890000001",
        )
        self.staff = get_user_model().objects.create_user(
            username="private-staff-identity",
            first_name="ชื่อเจ้าหน้าที่ห้ามเปิดเผย",
            last_name="นามสกุลห้ามเปิดเผย",
            password="test-only-password",
        )
        self.visit = Visit.objects.create(patient=self.patient)
        Visit.objects.filter(pk=self.visit.pk).update(registered_at=self.at(0))
        self.visit.refresh_from_db()
        self.queue = Queue.objects.create(visit=self.visit, status=Queue.Status.WAITING_VITALS)
        self._token = None

    @staticmethod
    def at(minutes):
        return datetime(2026, 1, 2, 2, 0, tzinfo=datetime_timezone.utc) + timedelta(minutes=minutes)

    def set_visit(self, **fields):
        Visit.objects.filter(pk=self.visit.pk).update(**fields)
        self.visit.refresh_from_db()

    def set_queue(self, status):
        Queue.objects.filter(pk=self.queue.pk).update(status=status)
        self.queue.refresh_from_db()
        self.visit.refresh_from_db()

    def assessment(self, created_at=None, visit=None):
        visit = visit or self.visit
        assessment = VisitAssessment.objects.create(
            visit=visit, examiner=self.staff, diagnosis="ทดสอบ", treatment="รับยาและกลับบ้าน",
        )
        VisitAssessment.objects.filter(pk=assessment.pk).update(created_at=created_at or self.at(35))
        assessment.refresh_from_db()
        visit.refresh_from_db()
        return assessment

    def prescription(self, status, **fields):
        prescription = Prescription.objects.create(
            visit=self.visit, prescribed_by=self.staff, status=status, **fields,
        )
        Prescription.objects.filter(pk=prescription.pk).update(created_at=self.at(36))
        prescription.refresh_from_db()
        self.visit.refresh_from_db()
        return prescription

    def bill(self, status, **fields):
        bill = Bill.objects.create(visit=self.visit, status=status, **fields)
        Bill.objects.filter(pk=bill.pk).update(created_at=self.at(37))
        bill.refresh_from_db()
        self.visit.refresh_from_db()
        return bill

    def log(self, event_type, minutes, visit=None, **details):
        log = VisitWorkflowLog.objects.create(
            visit=visit or self.visit,
            event_type=event_type,
            actor=self.staff,
            actor_name=self.staff.get_full_name(),
            actor_role="บทบาทภายในห้ามเปิดเผย",
            description="ข้อมูลเจ้าหน้าที่ภายในห้ามเปิดเผย",
            details=details,
        )
        VisitWorkflowLog.objects.filter(pk=log.pk).update(created_at=self.at(minutes))
        log.refresh_from_db()
        return log

    def journey(self, logs=None, visit=None):
        visit = Visit.objects.select_related(
            "queue", "triage_result", "opd_assessment", "prescription", "bill",
        ).get(pk=(visit or self.visit).pk)
        if logs is None:
            logs = list(visit.workflow_logs.all())
        return _serialize_patient_journey(_patient_journey_for_visit(visit, logs))

    def assert_times(self, journey, expected):
        steps = {step["key"]: step for step in journey["steps"]}
        self.assertEqual(set(steps), self.step_keys)
        for key, timestamp in expected.items():
            with self.subTest(step=key):
                self.assertIn("timestamp", steps[key], "Every step exposes an explicit nullable timestamp")
                actual = steps[key]["timestamp"]
                if timestamp is None:
                    self.assertIsNone(actual)
                else:
                    self.assertIsInstance(actual, str)
                    self.assertEqual(parse_datetime(actual), timestamp)
        return steps

    def headers(self):
        if self._token is None:
            response = self.client.post(
                reverse("public_patient_login"),
                data=json.dumps({"national_id": self.patient.national_id}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self._token = response.json()["access_token"]
        return {"HTTP_AUTHORIZATION": f"Bearer {self._token}"}

    def endpoint_journeys(self):
        tracking = self.client.get(reverse("public_patient_queue_status", args=[self.visit.tracking_token]))
        current = self.client.get(reverse("public_authenticated_patient_queue"), **self.headers())
        profile = self.client.get(reverse("public_patient_me"), **self.headers())
        for response in (tracking, current, profile):
            self.assertEqual(response.status_code, 200)
        return {
            "tracking": tracking.json()["patient_journey"],
            "authenticated_queue": current.json()["patient_journey"],
            "me_active_queue": profile.json()["active_queue"]["patient_journey"],
            "me_visits": profile.json()["visits"][0]["patient_journey"],
        }

    def progressed_visit(self):
        self.set_queue(Queue.Status.OPD_DONE)
        self.set_visit(final_severity=Visit.Severity.GREEN)
        self.assessment()

    def test_all_patient_endpoints_preserve_real_stage_timestamps_and_privacy(self):
        self.progressed_visit()
        self.set_visit(triaged_at=self.at(15), confirmed_at=self.at(20), called_at=self.at(30))
        self.log(VisitWorkflowLog.EventType.VITALS_RECORDED, 10)
        self.log(VisitWorkflowLog.EventType.TRIAGE_CONFIRMED, 19, final_severity="GREEN")
        self.log(VisitWorkflowLog.EventType.QUEUE_CALLED, 29, exam_room=2)
        self.log(VisitWorkflowLog.EventType.DOCTOR_ASSESSMENT, 40)
        self.prescription(Prescription.Status.DISPENSED, dispensed_at=self.at(60), sent_at=self.at(45))
        self.bill(Bill.Status.PAID, paid_at=self.at(50), received_by=self.staff)
        self.log(VisitWorkflowLog.EventType.PATIENT_DEPARTED, 70)
        expected = {
            "registration": self.at(0), "vitals": self.at(10), "triage": self.at(19),
            "queue": self.at(29), "doctor": self.at(40), "pharmacy": self.at(60),
            "billing": self.at(50), "complete": self.at(70),
        }

        for endpoint, journey in self.endpoint_journeys().items():
            with self.subTest(endpoint=endpoint):
                steps = self.assert_times(journey, expected)
                self.assertEqual(steps["doctor"]["detail"], "แพทย์ตรวจแล้ว")
                self.assertEqual(steps["triage"]["detail"], "คัดกรองแล้ว")
                self.assertEqual(steps["complete"]["detail"], "ผู้ป่วยออกจากโรงพยาบาลแล้ว")
                serialized = json.dumps(journey, ensure_ascii=False)
                for private in (
                    self.staff.username, self.staff.first_name, self.staff.last_name,
                    "บทบาทภายในห้ามเปิดเผย", "ข้อมูลเจ้าหน้าที่ภายในห้ามเปิดเผย", "GREEN",
                ):
                    self.assertNotIn(private, serialized)
                self.assertTrue(all("actor" not in step and "details" not in step for step in journey["steps"]))

    def test_missing_evidence_is_explicitly_null_on_every_patient_endpoint(self):
        expected = {key: None for key in self.step_keys}
        expected["registration"] = self.at(0)
        for endpoint, journey in self.endpoint_journeys().items():
            with self.subTest(endpoint=endpoint):
                self.assert_times(journey, expected)

    def test_confirmation_and_call_use_audit_evidence_then_dedicated_field_fallback(self):
        self.progressed_visit()
        self.set_visit(confirmed_at=self.at(20), called_at=self.at(30))
        self.assert_times(self.journey(), {"triage": self.at(20), "queue": self.at(30)})
        # Legacy fields can be backfilled or cleared during a transfer; the
        # corresponding audit event is stronger evidence when it exists.
        self.log(VisitWorkflowLog.EventType.TRIAGE_CONFIRMED, 25)
        self.log(VisitWorkflowLog.EventType.QUEUE_CALLED, 32)
        self.assert_times(self.journey(), {"triage": self.at(25), "queue": self.at(32)})

    def test_visit_confirmation_and_call_fields_supply_times_without_audit_logs(self):
        self.progressed_visit()
        self.set_visit(confirmed_at=self.at(20), called_at=self.at(30))
        self.assert_times(self.journey(), {"triage": self.at(20), "queue": self.at(30)})

    def test_vitals_uses_latest_recording_not_ai_triage_or_vital_row_edit_time(self):
        self.progressed_visit()
        self.set_visit(triaged_at=self.at(17))
        vitals = VitalSign.objects.create(visit=self.visit, pr=80, o2sat=99)
        VitalSign.objects.filter(pk=vitals.pk).update(updated_at=self.at(19))
        self.log(VisitWorkflowLog.EventType.VITALS_RECORDED, 8)
        self.log(VisitWorkflowLog.EventType.VITALS_RECORDED, 12)
        self.assert_times(self.journey(), {"vitals": self.at(12)})

    def test_vitals_without_recording_audit_does_not_invent_time_from_triaged_at(self):
        self.progressed_visit()
        self.set_visit(triaged_at=self.at(17))
        VitalSign.objects.create(visit=self.visit, pr=80, o2sat=99)
        steps = self.assert_times(self.journey(), {"vitals": None, "triage": None, "queue": None})
        self.assertEqual(steps["vitals"]["state"], "done")

    def test_doctor_uses_assessment_created_at_only_when_no_assessment_audit_exists(self):
        self.progressed_visit()
        VisitAssessment.objects.filter(visit=self.visit).update(updated_at=self.at(90))
        self.assert_times(self.journey(), {"doctor": self.at(35)})
        self.log(VisitWorkflowLog.EventType.DOCTOR_ASSESSMENT, 40)
        self.log(VisitWorkflowLog.EventType.PRESCRIPTION_CREATED, 80)
        self.assert_times(self.journey(), {"doctor": self.at(40)})

    def test_repeated_workflow_events_choose_newest_time_regardless_of_input_order(self):
        self.progressed_visit()
        prescription = self.prescription(Prescription.Status.DISPENSED)
        bill = self.bill(Bill.Status.PAID)
        event_steps = [
            (VisitWorkflowLog.EventType.VITALS_RECORDED, "vitals", {}),
            (VisitWorkflowLog.EventType.TRIAGE_CONFIRMED, "triage", {}),
            (VisitWorkflowLog.EventType.QUEUE_CALLED, "queue", {}),
            (VisitWorkflowLog.EventType.DOCTOR_ASSESSMENT, "doctor", {}),
            (VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, "pharmacy", {
                "status": Prescription.Status.DISPENSED, "prescription_id": prescription.pk,
            }),
            (VisitWorkflowLog.EventType.PAYMENT_RECEIVED, "billing", {
                "status": Bill.Status.PAID, "bill_id": bill.pk,
            }),
            (VisitWorkflowLog.EventType.PATIENT_DEPARTED, "complete", {}),
        ]
        logs = []
        expected = {}
        for index, (event_type, step, details) in enumerate(event_steps):
            # Insert the newest event first, so primary-key order is not time order either.
            logs.append(self.log(event_type, 50 + index, **details))
            logs.append(self.log(event_type, 10 + index, **details))
            expected[step] = self.at(50 + index)
        orders = (
            sorted(logs, key=lambda log: log.created_at),
            sorted(logs, key=lambda log: log.created_at, reverse=True),
            logs[1::2] + list(reversed(logs[::2])),
        )
        for index, ordered_logs in enumerate(orders):
            with self.subTest(input_order=index):
                self.assert_times(self.journey(logs=ordered_logs), expected)

    def test_dispensed_timestamp_field_takes_priority_over_matching_status_audit(self):
        self.progressed_visit()
        prescription = self.prescription(
            Prescription.Status.DISPENSED, dispensed_at=self.at(60), sent_at=self.at(40),
        )
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 65,
                 status=Prescription.Status.DISPENSED, prescription_id=prescription.pk)
        self.assert_times(self.journey(), {"pharmacy": self.at(60)})

    def test_dispensed_fallback_ignores_other_statuses_and_other_prescription_ids(self):
        self.progressed_visit()
        prescription = self.prescription(Prescription.Status.DISPENSED, sent_at=self.at(40))
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 60,
                 status=Prescription.Status.DISPENSED, prescription_id=prescription.pk)
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 80,
                 status=Prescription.Status.READY, prescription_id=prescription.pk)
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 90,
                 status=Prescription.Status.DISPENSED, prescription_id=prescription.pk + 999)
        self.assert_times(self.journey(), {"pharmacy": self.at(60)})

    def test_dispensed_without_dispense_evidence_does_not_use_sent_or_entered_time(self):
        self.progressed_visit()
        prescription = self.prescription(
            Prescription.Status.DISPENSED, sent_at=self.at(40), pharmacy_queue_entered_at=self.at(41),
        )
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 42,
                 status=Prescription.Status.SENT, prescription_id=prescription.pk)
        self.assert_times(self.journey(), {"pharmacy": None})

    def test_sent_prescription_uses_current_status_log_then_sent_at(self):
        self.progressed_visit()
        prescription = self.prescription(Prescription.Status.SENT, sent_at=self.at(40))
        self.assert_times(self.journey(), {"pharmacy": self.at(40)})
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 45,
                 status=Prescription.Status.SENT, prescription_id=prescription.pk)
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 70,
                 status=Prescription.Status.READY, prescription_id=prescription.pk)
        self.assert_times(self.journey(), {"pharmacy": self.at(45)})

    def test_preparing_and_ready_prescriptions_require_matching_current_status_audit(self):
        self.progressed_visit()
        prescription = self.prescription(
            Prescription.Status.PREPARING, sent_at=self.at(40), pharmacy_queue_entered_at=self.at(41),
        )
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 42,
                 status=Prescription.Status.SENT, prescription_id=prescription.pk)
        for status in (Prescription.Status.PREPARING, Prescription.Status.READY):
            with self.subTest(status=status):
                Prescription.objects.filter(pk=prescription.pk).update(status=status)
                self.assert_times(self.journey(), {"pharmacy": None})
                self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 50,
                         status=status, prescription_id=prescription.pk)
                self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 80,
                         status=Prescription.Status.DISPENSED, prescription_id=prescription.pk)
                self.assert_times(self.journey(), {"pharmacy": self.at(50)})

    def test_draft_prescription_uses_creation_evidence_not_updated_at(self):
        self.progressed_visit()
        prescription = self.prescription(Prescription.Status.DRAFT)
        Prescription.objects.filter(pk=prescription.pk).update(updated_at=self.at(90))
        self.assert_times(self.journey(), {"pharmacy": self.at(36)})
        self.log(VisitWorkflowLog.EventType.PRESCRIPTION_CREATED, 39, prescription_id=prescription.pk)
        self.assert_times(self.journey(), {"pharmacy": self.at(39)})

    def test_paid_and_waived_bills_prefer_paid_at_over_payment_audit(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.PAID, paid_at=self.at(60), billing_queue_entered_at=self.at(40))
        for status in (Bill.Status.PAID, Bill.Status.WAIVED):
            with self.subTest(status=status):
                Bill.objects.filter(pk=bill.pk).update(status=status)
                self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 70, status=status, bill_id=bill.pk)
                self.assert_times(self.journey(), {"billing": self.at(60)})

    def test_payment_fallback_requires_payment_for_the_current_bill_and_status(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.PAID, billing_queue_entered_at=self.at(40))
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 60, status=Bill.Status.PAID, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 80, status=Bill.Status.WAIVED, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 90, status=Bill.Status.PAID, bill_id=bill.pk + 999)
        self.assert_times(self.journey(), {"billing": self.at(60)})

    def test_paid_or_waived_without_payment_evidence_has_no_payment_time(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.PAID, billing_queue_entered_at=self.at(40))
        self.log(VisitWorkflowLog.EventType.BILL_CREATED, 38, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 40, bill_id=bill.pk)
        for status in (Bill.Status.PAID, Bill.Status.WAIVED):
            with self.subTest(status=status):
                Bill.objects.filter(pk=bill.pk).update(status=status)
                steps = self.assert_times(self.journey(), {"billing": None})
                self.assertEqual(steps["billing"]["state"], "done")

    def test_waived_payment_audit_supplies_time_when_paid_at_is_missing(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.WAIVED)
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 70, status=Bill.Status.WAIVED, bill_id=bill.pk)
        self.assert_times(self.journey(), {"billing": self.at(70)})

    def test_payment_audit_without_status_is_not_proof_of_the_current_payment(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.PAID)
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 70, bill_id=bill.pk)
        self.assert_times(self.journey(), {"billing": None})

    def test_unpaid_bill_prefers_queue_entry_field_and_never_stale_paid_at(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.READY, billing_queue_entered_at=self.at(45), paid_at=self.at(90))
        self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 50, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 90, status=Bill.Status.PAID, bill_id=bill.pk)
        self.assert_times(self.journey(), {"billing": self.at(45)})

    def test_unpaid_bill_queue_entry_log_precedes_creation_fallback(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.READY)
        self.assert_times(self.journey(), {"billing": self.at(37)})
        self.log(VisitWorkflowLog.EventType.BILL_CREATED, 39, bill_id=bill.pk)
        self.assert_times(self.journey(), {"billing": self.at(39)})
        self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 45, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 90, bill_id=bill.pk + 999)
        self.assert_times(self.journey(), {"billing": self.at(45)})

    def test_transferred_bill_uses_latest_rollover_for_its_own_source_bill(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.TRANSFERRED, billing_queue_entered_at=self.at(40))
        newest = self.log(VisitWorkflowLog.EventType.URGENT_BILL_ROLLOVER, 70, source_bill_id=bill.pk)
        oldest = self.log(VisitWorkflowLog.EventType.URGENT_BILL_ROLLOVER, 60, source_bill_id=bill.pk)
        unrelated = self.log(VisitWorkflowLog.EventType.URGENT_BILL_ROLLOVER, 90,
                             source_bill_id=bill.pk + 999)
        entered = self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 100, bill_id=bill.pk)
        created = self.log(VisitWorkflowLog.EventType.BILL_CREATED, 110, bill_id=bill.pk)
        steps = self.assert_times(
            self.journey(logs=[oldest, unrelated, entered, newest, created]),
            {"billing": self.at(70)},
        )
        self.assertEqual(steps["billing"]["state"], "current")
        self.assertEqual(steps["billing"]["detail"], bill.get_status_display())

    def test_transferred_bill_without_rollover_audit_has_no_transfer_time(self):
        self.progressed_visit()
        bill = self.bill(Bill.Status.TRANSFERRED, billing_queue_entered_at=self.at(40))
        self.log(VisitWorkflowLog.EventType.BILL_CREATED, 38, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 40, bill_id=bill.pk)
        self.log(VisitWorkflowLog.EventType.URGENT_BILL_ROLLOVER, 90, source_bill_id=bill.pk + 999)
        self.assert_times(self.journey(), {"billing": None})

    def test_legacy_status_logs_without_object_ids_still_supply_real_times(self):
        self.progressed_visit()
        self.prescription(Prescription.Status.DISPENSED)
        self.bill(Bill.Status.PAID)
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 60, status=Prescription.Status.DISPENSED)
        self.log(VisitWorkflowLog.EventType.PAYMENT_RECEIVED, 70, status=Bill.Status.PAID)
        self.assert_times(self.journey(), {"pharmacy": self.at(60), "billing": self.at(70)})

    def test_skipped_steps_do_not_expose_cancellation_or_creation_times(self):
        self.progressed_visit()
        prescription = self.prescription(Prescription.Status.CANCELLED)
        bill = self.bill(Bill.Status.CANCELLED)
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 60,
                 status=Prescription.Status.CANCELLED, prescription_id=prescription.pk)
        self.log(VisitWorkflowLog.EventType.BILL_CREATED, 65, bill_id=bill.pk)
        steps = self.assert_times(self.journey(), {"pharmacy": None, "billing": None})
        self.assertEqual(steps["pharmacy"]["state"], "skipped")
        self.assertEqual(steps["billing"]["state"], "skipped")

    def test_future_pending_steps_have_no_time_even_with_inconsistent_stale_logs(self):
        prescription = self.prescription(Prescription.Status.READY, sent_at=self.at(40))
        bill = self.bill(Bill.Status.READY, billing_queue_entered_at=self.at(45))
        self.log(VisitWorkflowLog.EventType.TRIAGE_CONFIRMED, 20)
        self.log(VisitWorkflowLog.EventType.QUEUE_CALLED, 30)
        self.log(VisitWorkflowLog.EventType.PHARMACY_STATUS_CHANGED, 60,
                 status=Prescription.Status.READY, prescription_id=prescription.pk)
        self.log(VisitWorkflowLog.EventType.BILLING_QUEUE_ENTERED, 45, bill_id=bill.pk)
        self.assert_times(self.journey(), {
            "vitals": None, "triage": None, "queue": None, "doctor": None,
            "pharmacy": None, "billing": None, "complete": None,
        })

    def test_payment_and_dispensing_completion_do_not_invent_departure_time(self):
        self.progressed_visit()
        self.prescription(Prescription.Status.DISPENSED, dispensed_at=self.at(60))
        self.bill(Bill.Status.PAID, paid_at=self.at(70))
        steps = self.assert_times(self.journey(), {"complete": None})
        self.assertEqual(steps["complete"]["state"], "current")
        self.set_queue(Queue.Status.DISCHARGED)
        steps = self.assert_times(self.journey(), {"complete": None})
        self.assertEqual(steps["complete"]["state"], "done")

    def test_departure_time_stays_fixed_when_the_tracking_endpoint_is_polled(self):
        self.progressed_visit()
        self.log(VisitWorkflowLog.EventType.PATIENT_DEPARTED, 80)
        url = reverse("public_patient_queue_status", args=[self.visit.tracking_token])
        for _ in range(2):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assert_times(response.json()["patient_journey"], {"complete": self.at(80)})
            self.assertNotEqual(parse_datetime(response.json()["updated_at"]), self.at(80))

    def test_discharged_emergency_visit_uses_exact_emergency_discharge_audit_time(self):
        self.set_queue(Queue.Status.DISCHARGED)
        newest = self.log(VisitWorkflowLog.EventType.EMERGENCY_DISCHARGED, 80)
        oldest = self.log(VisitWorkflowLog.EventType.EMERGENCY_DISCHARGED, 70)
        steps = self.assert_times(self.journey(logs=[oldest, newest]), {"complete": self.at(80)})
        self.assertEqual(steps["complete"]["state"], "done")
        response = self.client.get(reverse("public_patient_queue_status", args=[self.visit.tracking_token]))
        self.assertEqual(response.status_code, 200)
        self.assert_times(response.json()["patient_journey"], {"complete": self.at(80)})

    def test_emergency_discharge_audit_cannot_complete_a_visit_still_in_transfer(self):
        self.set_queue(Queue.Status.EMERGENCY_TRANSFER)
        self.log(VisitWorkflowLog.EventType.EMERGENCY_DISCHARGED, 80)
        steps = self.assert_times(self.journey(), {"complete": None})
        self.assertEqual(steps["complete"]["state"], "current")

    def test_emergency_referral_is_not_proof_of_departure_even_if_queue_is_discharged(self):
        self.set_queue(Queue.Status.DISCHARGED)
        self.log(VisitWorkflowLog.EventType.EMERGENCY_REFERRED, 80)
        steps = self.assert_times(self.journey(), {"complete": None})
        self.assertEqual(steps["complete"]["state"], "done")

    def test_patient_departure_audit_takes_priority_over_emergency_discharge_fallback(self):
        self.set_queue(Queue.Status.DISCHARGED)
        self.log(VisitWorkflowLog.EventType.PATIENT_DEPARTED, 70)
        self.log(VisitWorkflowLog.EventType.EMERGENCY_DISCHARGED, 90)
        self.assert_times(self.journey(), {"complete": self.at(70)})

    def test_closed_visit_history_keeps_its_own_times_separate_from_active_visit(self):
        closed = Visit.objects.create(patient=self.patient)
        Visit.objects.filter(pk=closed.pk).update(registered_at=self.at(-1440), confirmed_at=self.at(-1420))
        closed.refresh_from_db()
        Queue.objects.create(visit=closed, status=Queue.Status.DISCHARGED)
        self.assessment(created_at=self.at(-1400), visit=closed)
        self.log(VisitWorkflowLog.EventType.VITALS_RECORDED, -1430, visit=closed)
        self.log(VisitWorkflowLog.EventType.PATIENT_DEPARTED, -1380, visit=closed)
        response = self.client.get(reverse("public_patient_me"), **self.headers())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assert_times(body["active_queue"]["patient_journey"], {
            "registration": self.at(0), "vitals": None, "triage": None, "complete": None,
        })
        self.assertEqual(len(body["visits"]), 2)
        history = next(visit for visit in body["visits"] if visit["status"] == Queue.Status.DISCHARGED)
        self.assert_times(history["patient_journey"], {
            "registration": self.at(-1440), "vitals": self.at(-1430), "triage": self.at(-1420),
            "doctor": self.at(-1400), "complete": self.at(-1380),
        })
