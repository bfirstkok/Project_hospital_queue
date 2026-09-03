import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from patients.models import Patient
from queues.models import (
    Device,
    DeviceAssignment,
    NurseCareAssignment,
    Queue,
    StaffDuty,
    StaffProfile,
    TelemetryLog,
    TriageResult,
    Visit,
    VitalSign,
)


class FullPatientWorkflowReportTests(TestCase):
    """
    End-to-end regression scenario for the real staff workflow.

    The test intentionally walks through the same HTTP endpoints used by the UI:
    register -> triage assessment -> nurse confirmation -> automatic nurse
    ownership -> wearable pairing -> monitor API -> telemetry -> discharge.

    A Thai expected-vs-actual report is printed even when a later assertion fails,
    making this suitable both for CI and a manual project demonstration.
    """

    maxDiff = None

    def setUp(self):
        self.client = Client()
        self.report_rows = []

        self.operator = get_user_model().objects.create_user(
            username="workflow_operator",
            password="test-only-password",
            first_name="เจ้าหน้าที่",
            last_name="ทดสอบระบบ",
        )
        self.client.force_login(self.operator)

        self.nurse = get_user_model().objects.create_user(
            username="workflow_nurse",
            password="test-only-password",
            first_name="พยาบาล",
            last_name="ทดสอบ",
        )
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        StaffDuty.objects.create(
            user=self.nurse,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
            last_seen_at=timezone.now(),
        )

        self.device = Device.objects.create(
            device_id="E2E-WATCH-001",
            api_key="e2e-test-key",
            is_active=True,
        )

    def tearDown(self):
        print("\n" + "=" * 104)
        print("สรุปผลทดสอบอัตโนมัติ: Full Patient Workflow")
        print("=" * 104)
        print(f"{'ขั้นตอน':<27} | {'ผลที่คาดว่าจะได้':<34} | {'ผลที่ได้':<34} | สถานะ")
        print("-" * 104)
        for row in self.report_rows:
            status = "ผ่าน" if row["passed"] else "ไม่ผ่าน"
            print(
                f"{self._clip(row['step'], 27):<27} | "
                f"{self._clip(row['expected'], 34):<34} | "
                f"{self._clip(row['actual'], 34):<34} | {status}"
            )
        passed = sum(row["passed"] for row in self.report_rows)
        total = len(self.report_rows)
        print("-" * 104)
        print(f"รวม: ผ่าน {passed}/{total} ขั้นตอน")
        print("หมายเหตุ: ใช้ฐานข้อมูลทดสอบแยกจากข้อมูลจริง และถูกลบทิ้งเมื่อ test จบ")
        print("=" * 104 + "\n")
        super().tearDown()

    @staticmethod
    def _clip(value, length):
        text = str(value).replace("\n", " ")
        if len(text) <= length:
            return text
        return text[: length - 1] + "…"

    def _check(self, step, expected, actual, passed):
        self.report_rows.append({
            "step": step,
            "expected": expected,
            "actual": actual,
            "passed": bool(passed),
        })
        self.assertTrue(passed, f"{step}: expected {expected}; actual {actual}")

    def test_full_yellow_patient_workflow(self):
        # 1) Register a new patient through the same staff registration endpoint.
        register_response = self.client.post(reverse("register_patient"), {
            "first_name": "สมชาย",
            "last_name": "ทดสอบระบบ",
            "national_id": "1234567890999",
            "gender": "M",
            "age": "31",
            "phone": "0812345678",
            "blood_type": "UNKNOWN",
            "bp_sys": "118",
            "bp_dia": "76",
            "note": "ผู้ป่วยจำลองสำหรับทดสอบ workflow อัตโนมัติ",
        })
        patient = Patient.objects.get(national_id="1234567890999")
        visit = Visit.objects.select_related("queue").get(patient=patient)
        self._check(
            "1. ลงทะเบียนผู้ป่วย",
            "redirect รอวัดค่า + WAITING_VITALS",
            f"HTTP {register_response.status_code}, {visit.queue.status}",
            register_response.status_code == 302
            and visit.queue.status == Queue.Status.WAITING_VITALS
            and visit.final_severity is None,
        )

        # 2) Submit a complete triage assessment. The exact AI level is recorded,
        # but the workflow assertion is that the case proceeds to confirmation.
        assessment_response = self.client.post(
            reverse("nurse_triage_assessment", args=[visit.id]),
            {
                "action": "evaluate",
                "rr": "20",
                "pr": "90",
                "sys_bp": "118",
                "dia_bp": "76",
                "bt": "37.0",
                "o2sat": "97",
                "pain_score": "2",
                "lifesaving_intervention": "no",
                "high_risk_condition": "no",
                "mental_status": TriageResult.MentalStatus.ALERT,
                "severe_distress": "no",
                "expected_resources": TriageResult.ExpectedResources.MANY,
                "symptoms": "เวียนศีรษะ ต้องประเมินเพิ่มเติม",
            },
        )
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        triage_result = TriageResult.objects.get(visit=visit)
        vitals = VitalSign.objects.get(visit=visit)
        self._check(
            "2. ประเมินคัดกรอง",
            "บันทึก vitals/AI + WAITING_CONFIRMATION",
            f"HTTP {assessment_response.status_code}, {visit.queue.status}, AI={triage_result.ai_severity}",
            assessment_response.status_code == 302
            and visit.queue.status == Queue.Status.WAITING_CONFIRMATION
            and vitals.rr == 20
            and vitals.pr == 90
            and vitals.o2sat == 97
            and triage_result.ai_severity is not None,
        )

        # 3) Confirm YELLOW and allow the workload engine to choose the least busy
        # available nurse. Only one nurse exists in this isolated scenario.
        confirmation_response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            {
                "severity": Visit.Severity.YELLOW,
                "yellow_assignment_required": "1",
                "nurse_note": "ยืนยันสีเหลืองสำหรับการทดสอบระบบอัตโนมัติ",
                "risk_flags_present": "0",
            },
        )
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        care_assignment = NurseCareAssignment.objects.filter(
            visit=visit,
            is_active=True,
        ).select_related("nurse").first()
        active_load = NurseCareAssignment.objects.filter(
            nurse=self.nurse,
            is_active=True,
        ).count()
        self._check(
            "3. ยืนยันสีเหลือง",
            "WAITING_QUEUE + auto assign พยาบาล 1/4",
            (
                f"HTTP {confirmation_response.status_code}, {visit.queue.status}, "
                f"nurse={care_assignment.nurse.username if care_assignment else '-'}, load={active_load}/4"
            ),
            confirmation_response.status_code == 302
            and visit.final_severity == Visit.Severity.YELLOW
            and visit.queue.status == Queue.Status.WAITING_QUEUE
            and care_assignment is not None
            and care_assignment.nurse_id == self.nurse.id
            and active_load == 1,
        )

        # 4) Pair a wearable using the device-management endpoint.
        pairing_response = self.client.post(reverse("device_management"), {
            "action": "pair_device",
            "device": str(self.device.id),
            "visit": str(visit.id),
        })
        visit.queue.refresh_from_db()
        device_assignment = DeviceAssignment.objects.filter(
            visit=visit,
            device=self.device,
            is_active=True,
        ).first()
        self._check(
            "4. จับคู่อุปกรณ์",
            "อุปกรณ์ active + OBSERVATION_MONITORING",
            f"HTTP {pairing_response.status_code}, {visit.queue.status}, paired={bool(device_assignment)}",
            pairing_response.status_code == 302
            and device_assignment is not None
            and visit.queue.status == Queue.Status.OBSERVATION_MONITORING,
        )

        # 5) Verify that the monitor API exposes the patient and responsible nurse.
        monitor_response = self.client.get(reverse("monitor_summary_api"))
        monitor_payload = monitor_response.json()
        monitor_item = next(
            (
                item for item in monitor_payload.get("items", [])
                if item.get("visit_id") == str(visit.id)
            ),
            None,
        )
        responsible_name = (
            monitor_item.get("responsible_nurse", {}).get("name")
            if monitor_item and monitor_item.get("responsible_nurse")
            else None
        )
        self._check(
            "5. ตรวจหน้า Monitor",
            "พบผู้ป่วย + ชื่อพยาบาลผู้ดูแล",
            f"HTTP {monitor_response.status_code}, found={bool(monitor_item)}, nurse={responsible_name}",
            monitor_response.status_code == 200
            and monitor_item is not None
            and responsible_name == self.nurse.get_full_name(),
        )

        # 6) Simulate the wearable sending live telemetry.
        telemetry_response = self.client.post(
            "/api/iot/telemetry/",
            data=json.dumps({
                "visit_id": visit.id,
                "vitals": {
                    "bpm": 92,
                    "o2sat": 98,
                    "bt": 36.9,
                    "rr": 18,
                },
            }),
            content_type="application/json",
            HTTP_X_DEVICE_ID=self.device.device_id,
            HTTP_X_API_KEY=self.device.api_key,
        )
        telemetry_payload = telemetry_response.json()
        vitals.refresh_from_db()
        telemetry_exists = TelemetryLog.objects.filter(
            visit=visit,
            device=self.device,
            bpm=92,
            o2sat=98,
        ).exists()
        self._check(
            "6. รับข้อมูล IoT",
            "HTTP 200 + บันทึก telemetry/vitals",
            (
                f"HTTP {telemetry_response.status_code}, ok={telemetry_payload.get('ok')}, "
                f"BPM={vitals.pr}, O2={vitals.o2sat}"
            ),
            telemetry_response.status_code == 200
            and telemetry_payload.get("ok") is True
            and telemetry_exists
            and vitals.pr == 92
            and vitals.o2sat == 98,
        )

        # 7) Finish the care episode. Terminal status must free both device and
        # nurse capacity so the next patient can be assigned safely.
        discharge_response = self.client.post(reverse("discharge_visit", args=[visit.id]))
        visit.queue.refresh_from_db()
        care_assignment.refresh_from_db()
        active_device_exists = DeviceAssignment.objects.filter(
            visit=visit,
            is_active=True,
        ).exists()
        remaining_load = NurseCareAssignment.objects.filter(
            nurse=self.nurse,
            is_active=True,
        ).count()
        self._check(
            "7. จำหน่าย/จบการดูแล",
            "DISCHARGED + คืน device + คืน slot พยาบาล",
            (
                f"HTTP {discharge_response.status_code}, {visit.queue.status}, "
                f"device_active={active_device_exists}, load={remaining_load}/4"
            ),
            discharge_response.status_code == 302
            and visit.queue.status == Queue.Status.DISCHARGED
            and not active_device_exists
            and not care_assignment.is_active
            and care_assignment.ended_at is not None
            and remaining_load == 0,
        )
