from collections import Counter

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
    TriageResult,
    Visit,
)


class FiftyPatientVariedSymptomWorkflowTests(TestCase):
    """Functional volume test: 50 different patients/symptom profiles through the real HTTP flow."""

    def setUp(self):
        self.client = Client()
        self.rows = []
        self.yellow_visits = []
        self.operator = get_user_model().objects.create_user(
            username="fifty_patient_operator",
            password="test-only-password",
            first_name="เจ้าหน้าที่",
            last_name="จำลอง 50 คน",
        )
        self.client.force_login(self.operator)

        self.nurses = []
        for index in range(1, 6):
            nurse = get_user_model().objects.create_user(
                username=f"fifty_nurse_{index}",
                password="test-only-password",
                first_name="พยาบาล",
                last_name=f"จำลอง {index}",
            )
            StaffProfile.objects.create(user=nurse, role=StaffProfile.Role.NURSE)
            StaffDuty.objects.create(
                user=nurse,
                duty_date=timezone.localdate(),
                is_present=True,
                is_available=True,
                last_seen_at=timezone.now(),
            )
            self.nurses.append(nurse)

    @staticmethod
    def _scenario(label, symptoms, expected, **overrides):
        data = {
            "label": label,
            "symptoms": symptoms,
            "expected": expected,
            "rr": 18,
            "pr": 82,
            "sys_bp": 118,
            "dia_bp": 76,
            "bt": 36.8,
            "o2sat": 98,
            "pain_score": 2,
            "lifesaving_intervention": "no",
            "high_risk_condition": "no",
            "mental_status": TriageResult.MentalStatus.ALERT,
            "severe_distress": "no",
            "expected_resources": TriageResult.ExpectedResources.ONE,
            "urgent_symptoms": [],
            "risk_flags": [],
        }
        data.update(overrides)
        return data

    def _scenarios(self):
        s = self._scenario
        return [
            # RED (10)
            s("หมดสติเรียกไม่ตื่น", "หมดสติ เรียกไม่ตื่น", {"RED"}),
            s("กำลังชักต่อเนื่อง", "กำลังชักและชักไม่หยุด", {"RED"}),
            s("เลือดออกมาก", "เลือดออกมาก เลือดไหลไม่หยุด", {"RED"}),
            s("ออกซิเจนต่ำมาก", "อ่อนเพลียมาก", {"RED"}, o2sat=86),
            s("หายใจเร็ววิกฤต", "หายใจเร็วมาก", {"RED"}, rr=40),
            s("หายใจช้าวิกฤต", "หายใจช้ามาก", {"RED"}, rr=5),
            s("ความดันตกวิกฤต", "หน้ามืด ตัวเย็น", {"RED"}, sys_bp=72),
            s("ต้องช่วยชีวิตทันที", "อาการทรุดลงอย่างรวดเร็ว", {"RED"}, lifesaving_intervention="yes"),
            s("ไม่ตอบสนอง", "ไม่ตอบสนองต่อการเรียก", {"RED"}, mental_status=TriageResult.MentalStatus.UNRESPONSIVE),
            s("อุบัติเหตุเสียเลือด", "อุบัติเหตุรุนแรงและเสียเลือด", {"RED"}),

            # PINK (10)
            s("เจ็บหน้าอก", "เจ็บหน้าอก แน่นกลางอก", {"PINK"}),
            s("สับสนเฉียบพลัน", "สับสน ตอบไม่รู้เรื่อง", {"PINK"}),
            s("ชักแล้วหยุด", "ชักหนึ่งครั้งแล้วหยุด ตอนนี้รู้สึกตัว", {"PINK"}),
            s("รถชน", "รถชนแต่รู้สึกตัวดี ไม่มีเลือดออกมาก", {"PINK"}),
            s("สงสัย stroke", "หน้าเบี้ยว พูดไม่ชัด", {"PINK"}),
            s("หอบและออกซิเจนต่ำ", "หายใจลำบาก หอบเหนื่อย", {"PINK"}, o2sat=93),
            s("SpO2 ต่ำ", "เวียนหัวและอ่อนแรง", {"PINK"}, o2sat=94),
            s("RR สูง", "หายใจเร็วผิดปกติ", {"PINK"}, rr=32),
            s("ความดันต่ำ", "หน้ามืดเวลาลุก", {"PINK"}, sys_bp=85),
            s("ภาวะเสี่ยงสูง", "สงสัยภาวะแพ้ยารุนแรงระยะแรก", {"PINK"}, high_risk_condition="yes"),

            # YELLOW (16)
            s("SpO2 เฝ้าระวัง", "เหนื่อยง่ายกว่าปกติ", {"YELLOW"}, o2sat=96, expected_resources=TriageResult.ExpectedResources.MANY),
            s("RR 24", "หายใจเร็วเล็กน้อย", {"YELLOW"}, rr=24, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ชีพจรเร็ว", "ใจสั่น", {"YELLOW"}, pr=125, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ไข้ 38.4", "มีไข้และหนาวสั่น", {"YELLOW"}, bt=38.4, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ไข้ 39.2", "ตัวร้อนมากและเพลีย", {"YELLOW"}, bt=39.2, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ปวด 8/10", "ปวดท้องมากแต่สัญญาณชีพคงที่", {"YELLOW"}, pain_score=8, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ปวดรุนแรง", "ปวดรุนแรงบริเวณหลัง", {"YELLOW"}, expected_resources=TriageResult.ExpectedResources.MANY),
            s("หายใจลำบากเล็กน้อย", "หายใจลำบากเล็กน้อยแต่ SpO2 ปกติ", {"YELLOW"}, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ประวัติไข้สูง", "ไข้สูงเมื่อคืน วันนี้ลดลงแล้ว", {"YELLOW"}, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ความดันสูงมาก", "ปวดศีรษะร่วมกับความดันสูง", {"YELLOW"}, sys_bp=185, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ความดันล่างสูง", "มึนศีรษะ ความดันล่างสูง", {"YELLOW"}, dia_bp=122, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ตั้งครรภ์", "คลื่นไส้และอ่อนเพลียในหญิงตั้งครรภ์", {"YELLOW"}, risk_flags=["pregnant"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("ผู้สูงอายุ 80+", "อ่อนเพลียในผู้สูงอายุ", {"YELLOW"}, risk_flags=["elderly_80"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("เด็กอายุต่ำกว่า 5", "มีไอในเด็กเล็ก", {"YELLOW"}, risk_flags=["child_under_5"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("COPD/Asthma", "แน่นหายใจเล็กน้อยในผู้ป่วย COPD", {"YELLOW"}, risk_flags=["copd_asthma"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("ภูมิคุ้มกันต่ำ", "อ่อนเพลียในผู้มีภูมิคุ้มกันต่ำ", {"YELLOW"}, risk_flags=["immunocompromised"], expected_resources=TriageResult.ExpectedResources.MANY),

            # WHITE (5)
            s("ขอใบรับรองแพทย์", "ขอใบรับรองแพทย์ ไม่มีอาการผิดปกติ", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("คันผิวหนังเล็กน้อย", "คันผิวหนังเล็กน้อย ไม่มีหายใจลำบาก", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("แผลถลอกเล็กน้อย", "แผลถลอกเล็กน้อย ไม่มีเลือดออกมาก", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ปรึกษาการนอน", "ปรึกษาเรื่องการนอน ไม่มีเจ็บหน้าอก", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("น้ำมูกเล็กน้อย", "น้ำมูกเล็กน้อย ไม่มีไข้สูง", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),

            # Normal / non-urgent: ML is allowed to distinguish GREEN from WHITE (9)
            s("ปวดศีรษะเล็กน้อย", "ปวดศีรษะเล็กน้อย", {"GREEN", "WHITE"}),
            s("ปวดคอหลังตื่น", "ปวดคอหลังตื่นนอน", {"GREEN", "WHITE"}),
            s("ไอเล็กน้อย", "ไอเล็กน้อย ไม่มีหอบ", {"GREEN", "WHITE"}),
            s("เจ็บคอ", "เจ็บคอเล็กน้อย", {"GREEN", "WHITE"}),
            s("ปวดท้องเล็กน้อย", "ปวดท้องเล็กน้อย", {"GREEN", "WHITE"}),
            s("คลื่นไส้", "คลื่นไส้เล็กน้อย", {"GREEN", "WHITE"}),
            s("ท้องเสียครั้งเดียว", "ท้องเสียหนึ่งครั้ง", {"GREEN", "WHITE"}),
            s("ผื่นคัน", "ผื่นคันที่แขน", {"GREEN", "WHITE"}),
            s("ปวดข้อเข่า", "ปวดข้อเข่าเวลาเดิน", {"GREEN", "WHITE"}),
        ]

    def _expected_label(self, expected):
        return "/".join(sorted(expected))

    def _route_for(self, severity):
        if severity in {Visit.Severity.RED, Visit.Severity.PINK}:
            return Queue.Status.EMERGENCY_TRANSFER
        if severity == Visit.Severity.YELLOW:
            return Queue.Status.OBSERVATION_MONITORING
        return Queue.Status.WAITING_QUEUE

    def test_fifty_patients_with_different_symptoms(self):
        scenarios = self._scenarios()
        self.assertEqual(len(scenarios), 50)
        actual_severities = []
        failures = []

        for index, scenario in enumerate(scenarios, start=1):
            national_id = str(7000000000000 + index)
            first_name = f"ผู้ป่วย{index:02d}"
            last_name = scenario["label"][:40]

            register_response = self.client.post(reverse("register_patient"), {
                "first_name": first_name,
                "last_name": last_name,
                "national_id": national_id,
                "gender": "M" if index % 2 else "F",
                "age": str(18 + (index % 63)),
                "phone": f"080{index:07d}",
                "blood_type": "UNKNOWN",
                "bp_sys": str(scenario["sys_bp"]),
                "bp_dia": str(scenario["dia_bp"]),
                "note": scenario["symptoms"],
            })

            patient = Patient.objects.filter(national_id=national_id).first()
            if patient is None:
                self.rows.append((index, scenario["label"], self._expected_label(scenario["expected"]), "REGISTER_FAILED", "ไม่ผ่าน"))
                failures.append(f"#{index} registration did not create patient")
                continue

            visit = Visit.objects.select_related("queue").filter(patient=patient).order_by("-registered_at").first()
            if visit is None:
                self.rows.append((index, scenario["label"], self._expected_label(scenario["expected"]), "NO_VISIT", "ไม่ผ่าน"))
                failures.append(f"#{index} registration did not create visit")
                continue

            assessment_payload = {
                "action": "evaluate",
                "rr": str(scenario["rr"]),
                "pr": str(scenario["pr"]),
                "sys_bp": str(scenario["sys_bp"]),
                "dia_bp": str(scenario["dia_bp"]),
                "bt": str(scenario["bt"]),
                "o2sat": str(scenario["o2sat"]),
                "pain_score": str(scenario["pain_score"]),
                "lifesaving_intervention": scenario["lifesaving_intervention"],
                "high_risk_condition": scenario["high_risk_condition"],
                "mental_status": scenario["mental_status"],
                "severe_distress": scenario["severe_distress"],
                "expected_resources": scenario["expected_resources"],
                "symptoms": scenario["symptoms"],
                "urgent_symptoms": scenario["urgent_symptoms"],
                "risk_flags": scenario["risk_flags"],
            }
            assessment_response = self.client.post(
                reverse("nurse_triage_assessment", args=[visit.id]),
                assessment_payload,
            )
            visit.refresh_from_db()
            visit.queue.refresh_from_db()
            triage = TriageResult.objects.filter(visit=visit).first()
            actual_severity = triage.ai_severity if triage else None
            actual_severities.append(actual_severity or "NONE")
            classification_ok = (
                register_response.status_code == 302
                and assessment_response.status_code == 302
                and visit.queue.status == Queue.Status.WAITING_CONFIRMATION
                and actual_severity in scenario["expected"]
            )

            final_status = visit.queue.status
            assignment_ok = True
            confirm_ok = False
            if actual_severity:
                confirm_payload = {
                    "severity": actual_severity,
                    "nurse_note": "ยืนยันตามผลทดสอบอัตโนมัติ 50 ผู้ป่วย",
                    "risk_flags_present": "1" if scenario["risk_flags"] else "0",
                    "risk_flags": scenario["risk_flags"],
                }
                if actual_severity == Visit.Severity.YELLOW:
                    confirm_payload["yellow_assignment_required"] = "1"
                confirmation_response = self.client.post(
                    reverse("triage_visit", args=[visit.id]),
                    confirm_payload,
                )
                visit.refresh_from_db()
                visit.queue.refresh_from_db()
                confirm_ok = confirmation_response.status_code == 302 and visit.final_severity == actual_severity

                if actual_severity == Visit.Severity.YELLOW:
                    assignment = NurseCareAssignment.objects.filter(visit=visit, is_active=True).select_related("nurse").first()
                    assignment_ok = assignment is not None
                    if assignment_ok:
                        device = Device.objects.create(
                            device_id=f"FIFTY-WATCH-{index:02d}",
                            api_key=f"fifty-key-{index:02d}",
                            is_active=True,
                        )
                        pair_response = self.client.post(reverse("device_management"), {
                            "action": "pair_device",
                            "device": str(device.id),
                            "visit": str(visit.id),
                        })
                        visit.queue.refresh_from_db()
                        assignment_ok = (
                            pair_response.status_code == 302
                            and DeviceAssignment.objects.filter(visit=visit, device=device, is_active=True).exists()
                            and visit.queue.status == Queue.Status.OBSERVATION_MONITORING
                        )
                        if assignment_ok:
                            self.yellow_visits.append(visit.id)
                final_status = visit.queue.status

            expected_route = None
            if len(scenario["expected"]) == 1:
                expected_route = self._route_for(next(iter(scenario["expected"])))
            else:
                expected_route = Queue.Status.WAITING_QUEUE

            route_ok = final_status == expected_route
            passed = classification_ok and confirm_ok and assignment_ok and route_ok
            actual_text = f"AI={actual_severity or '-'}, queue={final_status}"
            self.rows.append((index, scenario["label"], f"{self._expected_label(scenario['expected'])} -> {expected_route}", actual_text, "ผ่าน" if passed else "ไม่ผ่าน"))
            if not passed:
                failures.append(
                    f"#{index} {scenario['label']}: expected={scenario['expected']}/{expected_route}, "
                    f"actual={actual_severity}/{final_status}"
                )

        # Functional-volume invariants after all 50 patients have entered the system.
        loads = [
            NurseCareAssignment.objects.filter(nurse=nurse, is_active=True).count()
            for nurse in self.nurses
        ]
        expected_yellow = 16
        yellow_assignment_count = NurseCareAssignment.objects.filter(is_active=True).count()
        monitor_items = self.client.get(reverse("monitor_summary_api")).json().get("items", [])
        monitor_visit_ids = {item.get("visit_id") for item in monitor_items}

        volume_ok = Patient.objects.count() == 50 and len(self.rows) == 50
        capacity_ok = (
            yellow_assignment_count == expected_yellow
            and max(loads, default=0) <= 4
            and (max(loads) - min(loads) <= 1 if loads else True)
        )
        monitor_ok = len(self.yellow_visits) == expected_yellow and all(str(visit_id) in monitor_visit_ids for visit_id in self.yellow_visits)

        if not volume_ok:
            failures.append(f"volume invariant failed: patients={Patient.objects.count()}, rows={len(self.rows)}")
        if not capacity_ok:
            failures.append(f"nurse capacity/balance failed: assignments={yellow_assignment_count}, loads={loads}")
        if not monitor_ok:
            failures.append(f"monitor visibility failed: yellow={len(self.yellow_visits)}, monitor={len(monitor_items)}")

        # Close all YELLOW observation cases and verify capacity/device release.
        for visit_id in self.yellow_visits:
            self.client.post(reverse("discharge_visit", args=[visit_id]))
        cleanup_ok = (
            NurseCareAssignment.objects.filter(is_active=True).count() == 0
            and DeviceAssignment.objects.filter(is_active=True).count() == 0
        )
        if not cleanup_ok:
            failures.append("cleanup failed: active nurse/device assignments remained after discharge")

        # Print a project-friendly expected-vs-actual report.
        print("\n" + "=" * 146)
        print("สรุปผลทดสอบอัตโนมัติ: ผู้ป่วยจำลอง 50 คน อาการแตกต่างกัน")
        print("=" * 146)
        print(f"{'คน':<4} | {'อาการ/สถานการณ์':<30} | {'ผลที่คาดว่าจะได้':<42} | {'ผลที่ได้':<42} | สถานะ")
        print("-" * 146)
        for index, label, expected, actual, status in self.rows:
            print(f"{index:<4} | {label[:30]:<30} | {expected[:42]:<42} | {actual[:42]:<42} | {status}")
        print("-" * 146)
        counts = Counter(actual_severities)
        print(f"จำนวนผู้ป่วย: {Patient.objects.count()}/50")
        print(f"ผลคัดกรองจริง: {dict(counts)}")
        print(f"พยาบาล 5 คน workload ก่อนจำหน่าย: {loads} (กำหนดสูงสุด 4 คน/พยาบาล)")
        print(f"ผู้ป่วยสีเหลืองที่มอบหมายและขึ้น Monitor: {len(self.yellow_visits)}/{expected_yellow}")
        print(f"คืน slot/device หลังจำหน่ายสีเหลือง: {'ผ่าน' if cleanup_ok else 'ไม่ผ่าน'}")
        print(f"สรุป: ผ่าน {50 - len([r for r in self.rows if r[4] != 'ผ่าน'])}/50 ราย")
        print("หมายเหตุ: เป็น functional simulation แบบเรียงลำดับ ไม่ใช่ stress/load test แบบ 50 คนพร้อมกัน")
        print("ใช้ฐานข้อมูล test แยกจาก production และข้อมูลจำลองถูกลบทิ้งเมื่อ test จบ")
        print("=" * 146 + "\n")

        self.assertFalse(failures, "\n".join(failures))
