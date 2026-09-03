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


class HundredPatientVariedSymptomWorkflowTests(TestCase):
    """Functional-volume simulation: 100 distinct patient symptom profiles through the real HTTP flow."""

    PATIENT_COUNT = 100
    NURSE_COUNT = 5
    NURSE_CAPACITY = 4

    def setUp(self):
        self.client = Client()
        self.rows = []
        self.yellow_visits = []

        self.operator = get_user_model().objects.create_user(
            username="hundred_patient_operator",
            password="test-only-password",
            first_name="เจ้าหน้าที่",
            last_name="จำลอง 100 คน",
        )
        self.client.force_login(self.operator)

        self.nurses = []
        for index in range(1, self.NURSE_COUNT + 1):
            nurse = get_user_model().objects.create_user(
                username=f"hundred_nurse_{index}",
                password="test-only-password",
                first_name="พยาบาล",
                last_name=f"จำลอง 100-{index}",
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
            # RED / level 1: 20 distinct presentations
            s("หมดสติหลังล้ม", "หมดสติ เรียกไม่ตื่นหลังล้ม", {"RED"}),
            s("กำลังชักต่อเนื่อง", "กำลังชักและชักไม่หยุด", {"RED"}),
            s("เลือดออกมากจากแขน", "เลือดออกมากจากแผลแขน เลือดไหลไม่หยุด", {"RED"}),
            s("ออกซิเจน 86%", "อ่อนเพลียมากและตัวเขียว", {"RED"}, o2sat=86),
            s("ออกซิเจน 88%", "หายใจตื้นและซึมลงเล็กน้อย", {"RED"}, o2sat=88),
            s("หายใจ 40 ครั้ง", "หายใจเร็วมากผิดปกติ", {"RED"}, rr=40),
            s("หายใจ 5 ครั้ง", "หายใจช้ามากผิดปกติ", {"RED"}, rr=5),
            s("ความดัน 72", "หน้ามืด ตัวเย็น ความดันตก", {"RED"}, sys_bp=72),
            s("ความดัน 78", "อ่อนแรงมากและความดันต่ำรุนแรง", {"RED"}, sys_bp=78),
            s("ต้องช่วยชีวิตทันที", "อาการทรุดลงอย่างรวดเร็ว", {"RED"}, lifesaving_intervention="yes"),
            s("ไม่ตอบสนองต่อเสียง", "ผู้ป่วยนิ่งและไม่ตอบสนอง", {"RED"}, mental_status=TriageResult.MentalStatus.UNRESPONSIVE),
            s("ชักไม่หยุดหลังไข้", "ชักไม่หยุดหลังมีไข้", {"RED"}),
            s("เลือดออกศีรษะไม่หยุด", "เลือดไหลไม่หยุดจากบาดแผลศีรษะ", {"RED"}),
            s("เป็นลมแล้วเรียกไม่ตื่น", "เป็นลมและเรียกไม่ตื่น", {"RED"}),
            s("ออกซิเจน 89%", "เหนื่อยมากและริมฝีปากคล้ำ", {"RED"}, o2sat=89),
            s("หายใจ 38 ครั้ง", "หายใจถี่มากต่อเนื่อง", {"RED"}, rr=38),
            s("หายใจ 6 ครั้ง", "หายใจช้ามากเพียงหกครั้งต่อนาที", {"RED"}, rr=6),
            s("ความดัน 75", "ชีพจรเบาและหน้าซีด", {"RED"}, sys_bp=75),
            s("ต้องกู้ชีพจากอาการทรุด", "การไหลเวียนไม่คงที่และอาการทรุด", {"RED"}, lifesaving_intervention="yes"),
            s("ออกซิเจน 82%", "เขียวคล้ำและอ่อนแรงรุนแรง", {"RED"}, o2sat=82),

            # PINK / level 2: 20 distinct presentations
            s("เจ็บหน้าอกกดทับ", "เจ็บหน้าอกเหมือนถูกกดทับ", {"PINK"}),
            s("แน่นกลางอก", "แน่นหน้าอกบริเวณกลางอก", {"PINK"}),
            s("สับสนเฉียบพลัน", "สับสน ตอบไม่รู้เรื่องเฉียบพลัน", {"PINK"}),
            s("ชักหนึ่งครั้งแล้วหยุด", "ชักหนึ่งครั้งแล้วหยุด ตอนนี้รู้สึกตัว", {"PINK"}),
            s("รถชนรู้สึกตัว", "รถชนแต่ยังรู้สึกตัวดี", {"PINK"}),
            s("สงสัยหลอดเลือดสมอง", "หน้าเบี้ยว พูดไม่ชัด", {"PINK"}),
            s("หอบ SpO2 93", "หายใจลำบากและหอบเหนื่อย", {"PINK"}, o2sat=93),
            s("หอบ RR 32", "หอบเหนื่อยและหายใจลำบาก", {"PINK"}, rr=32),
            s("SpO2 94 เวียนหัว", "เวียนหัวและอ่อนแรง", {"PINK"}, o2sat=94),
            s("SpO2 92 อ่อนเพลีย", "อ่อนเพลียมากผิดปกติ", {"PINK"}, o2sat=92),
            s("RR 32 ไม่มีหอบ", "หายใจเร็วผิดปกติ", {"PINK"}, rr=32),
            s("RR 34 ใจสั่น", "หายใจเร็วร่วมกับใจสั่น", {"PINK"}, rr=34),
            s("ความดัน 85", "หน้ามืดเวลาลุกขึ้น", {"PINK"}, sys_bp=85),
            s("ความดัน 88", "อ่อนเพลียและมือเย็น", {"PINK"}, sys_bp=88),
            s("ภาวะเสี่ยงสูงจากแพ้ยา", "สงสัยภาวะแพ้ยารุนแรงระยะแรก", {"PINK"}, high_risk_condition="yes"),
            s("ตอบสนองต่อเสียงเท่านั้น", "ง่วงซึมและตอบเมื่อเรียก", {"PINK"}, mental_status=TriageResult.MentalStatus.VERBAL),
            s("ตอบสนองเมื่อเจ็บ", "ซึมมากและตอบสนองเมื่อกระตุ้น", {"PINK"}, mental_status=TriageResult.MentalStatus.PAIN),
            s("ทุกข์ทรมานรุนแรง", "มีอาการทรมานมากและกระสับกระส่าย", {"PINK"}, severe_distress="yes"),
            s("ปวดรุนแรงชีพจร 125", "ปวดรุนแรงบริเวณท้อง", {"PINK"}, pain_score=9, pr=125),
            s("ปวดรุนแรง SpO2 94", "ปวดมากร่วมกับอ่อนเพลีย", {"PINK"}, pain_score=8, o2sat=94),

            # YELLOW / level 3: exactly 20, intentionally fills 5 nurses to 4/4 each
            s("SpO2 96", "เหนื่อยง่ายกว่าปกติ", {"YELLOW"}, o2sat=96, expected_resources=TriageResult.ExpectedResources.MANY),
            s("SpO2 95", "อ่อนเพลียเวลาเดิน", {"YELLOW"}, o2sat=95, expected_resources=TriageResult.ExpectedResources.MANY),
            s("RR 21", "หายใจเร็วกว่าปกติเล็กน้อย", {"YELLOW"}, rr=21, expected_resources=TriageResult.ExpectedResources.MANY),
            s("RR 24", "หายใจเร็วเล็กน้อย", {"YELLOW"}, rr=24, expected_resources=TriageResult.ExpectedResources.MANY),
            s("RR 30", "หายใจเร็วต่อเนื่องแต่รู้สึกตัวดี", {"YELLOW"}, rr=30, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ชีพจร 120", "ใจสั่นเป็นพัก ๆ", {"YELLOW"}, pr=120, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ชีพจร 135", "ใจเต้นเร็วและอ่อนเพลีย", {"YELLOW"}, pr=135, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ไข้ 38.2", "มีไข้ต่ำและหนาวสั่น", {"YELLOW"}, bt=38.2, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ไข้ 38.8", "ตัวร้อนและปวดเมื่อย", {"YELLOW"}, bt=38.8, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ไข้ 39.3", "ตัวร้อนมากและเพลีย", {"YELLOW"}, bt=39.3, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ปวด 7/10", "ปวดท้องมากแต่สัญญาณชีพคงที่", {"YELLOW"}, pain_score=7, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ปวด 9/10", "ปวดหลังมากแต่ยังรู้สึกตัวดี", {"YELLOW"}, pain_score=9, expected_resources=TriageResult.ExpectedResources.MANY),
            s("หายใจลำบากแต่ค่าออกซิเจนปกติ", "หายใจลำบากเล็กน้อยแต่ยังพูดเป็นประโยค", {"YELLOW"}, expected_resources=TriageResult.ExpectedResources.MANY),
            s("มีอาการไข้สูงเมื่อคืน", "ไข้สูงเมื่อคืน วันนี้อุณหภูมิลดลงแล้ว", {"YELLOW"}, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ความดันบน 180", "ปวดศีรษะร่วมกับความดันสูง", {"YELLOW"}, sys_bp=180, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ความดันล่าง 120", "มึนศีรษะและความดันล่างสูง", {"YELLOW"}, dia_bp=120, expected_resources=TriageResult.ExpectedResources.MANY),
            s("ตั้งครรภ์อ่อนเพลีย", "คลื่นไส้และอ่อนเพลียในหญิงตั้งครรภ์", {"YELLOW"}, risk_flags=["pregnant"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("ผู้สูงอายุ 80+ อ่อนแรง", "อ่อนเพลียในผู้สูงอายุ", {"YELLOW"}, risk_flags=["elderly_80"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("เด็กเล็กมีไอ", "มีไอในเด็กอายุต่ำกว่า 5 ปี", {"YELLOW"}, risk_flags=["child_under_5"], expected_resources=TriageResult.ExpectedResources.MANY),
            s("COPD แน่นหายใจ", "แน่นหายใจเล็กน้อยในผู้ป่วย COPD", {"YELLOW"}, risk_flags=["copd_asthma"], expected_resources=TriageResult.ExpectedResources.MANY),

            # WHITE / level 5: 20 normal-vital, zero-resource presentations
            s("ขอใบรับรองแพทย์", "ขอใบรับรองแพทย์สำหรับยื่นงาน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ตรวจสุขภาพประจำปี", "มาตรวจสุขภาพประจำปี ไม่มีอาการผิดปกติ", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ผิวแห้งเล็กน้อย", "ผิวแห้งเล็กน้อยบริเวณแขน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("คันหนังศีรษะ", "คันหนังศีรษะเป็นบางครั้ง", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ตาแห้ง", "ตาแห้งหลังใช้คอมพิวเตอร์นาน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ผมร่วง", "ผมร่วงมากขึ้นช่วงหนึ่งเดือน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("เล็บเปราะ", "เล็บเปราะแตกง่าย", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("สิวเล็กน้อย", "มีสิวเล็กน้อยบริเวณใบหน้า", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("รังแค", "มีรังแคและหนังศีรษะแห้ง", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("แผลถลอกเล็กน้อย", "แผลถลอกตื้นที่ข้อศอก", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ปวดฟันเล็กน้อย", "เสียวฟันและปวดฟันเล็กน้อย", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("หูอื้อชั่วคราว", "หูอื้อเป็นบางครั้งหลังตื่นนอน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ขี้หูอุดตัน", "รู้สึกหูตันคล้ายมีขี้หู", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ริมฝีปากแห้ง", "ริมฝีปากแห้งและลอก", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("เหงือกบวมเล็กน้อย", "เหงือกบวมเล็กน้อยเวลาแปรงฟัน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ปรึกษาโภชนาการ", "ต้องการคำปรึกษาเรื่องอาหารและน้ำหนัก", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("นอนไม่พอ", "พักผ่อนน้อยจากการทำงาน", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ตรวจสายตา", "ต้องการตรวจสายตาเพราะมองไกลไม่ชัด", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("ขอคำแนะนำออกกำลัง", "ต้องการคำแนะนำการออกกำลังกาย", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),
            s("นัดติดตามทั่วไป", "มาติดตามอาการทั่วไปตามนัด ไม่มีอาการใหม่", {"WHITE"}, expected_resources=TriageResult.ExpectedResources.NONE),

            # Non-urgent / level 4-5: 20 distinct mild presentations; model may choose GREEN or WHITE
            s("ปวดศีรษะเล็กน้อย", "ปวดศีรษะเล็กน้อยช่วงบ่าย", {"GREEN", "WHITE"}),
            s("ปวดคอหลังตื่น", "ปวดคอหลังตื่นนอน", {"GREEN", "WHITE"}),
            s("ไอเล็กน้อย", "ไอแห้งเล็กน้อย", {"GREEN", "WHITE"}),
            s("เจ็บคอเล็กน้อย", "เจ็บคอเล็กน้อยเวลากลืน", {"GREEN", "WHITE"}),
            s("ปวดท้องเล็กน้อย", "ปวดท้องเล็กน้อยหลังอาหาร", {"GREEN", "WHITE"}),
            s("คลื่นไส้เล็กน้อย", "คลื่นไส้เล็กน้อยตอนเช้า", {"GREEN", "WHITE"}),
            s("ท้องเสียหนึ่งครั้ง", "ท้องเสียหนึ่งครั้งหลังรับประทานอาหาร", {"GREEN", "WHITE"}),
            s("ผื่นคันที่แขน", "ผื่นคันเล็กน้อยที่แขน", {"GREEN", "WHITE"}),
            s("ปวดข้อเข่า", "ปวดข้อเข่าเวลาเดินขึ้นบันได", {"GREEN", "WHITE"}),
            s("ปวดเอวเล็กน้อย", "ปวดเอวเล็กน้อยหลังนั่งนาน", {"GREEN", "WHITE"}),
            s("เวียนหัวเล็กน้อย", "เวียนหัวเล็กน้อยหลังลุกเร็ว", {"GREEN", "WHITE"}),
            s("อ่อนเพลียจากพักผ่อนน้อย", "รู้สึกอ่อนเพลียหลังนอนดึก", {"GREEN", "WHITE"}),
            s("จุกแน่นท้องหลังอาหาร", "จุกแน่นท้องเล็กน้อยหลังอาหาร", {"GREEN", "WHITE"}),
            s("ปวดข้อมือจากใช้งาน", "ปวดข้อมือเล็กน้อยหลังพิมพ์งาน", {"GREEN", "WHITE"}),
            s("ตึงไหล่", "ตึงไหล่จากนั่งทำงานนาน", {"GREEN", "WHITE"}),
            s("คัดจมูก", "คัดจมูกเล็กน้อยช่วงเช้า", {"GREEN", "WHITE"}),
            s("เสียงแหบ", "เสียงแหบเล็กน้อยหลังใช้เสียงมาก", {"GREEN", "WHITE"}),
            s("ปวดส้นเท้า", "ปวดส้นเท้าเล็กน้อยเวลาเดิน", {"GREEN", "WHITE"}),
            s("ตากระตุก", "หนังตากระตุกเป็นบางครั้ง", {"GREEN", "WHITE"}),
            s("ปวดกล้ามเนื้อหลังออกกำลัง", "ปวดกล้ามเนื้อเล็กน้อยหลังออกกำลังกาย", {"GREEN", "WHITE"}),
        ]

    @staticmethod
    def _expected_label(expected):
        return "/".join(sorted(expected))

    @staticmethod
    def _route_for(severity):
        if severity in {Visit.Severity.RED, Visit.Severity.PINK}:
            return Queue.Status.EMERGENCY_TRANSFER
        if severity == Visit.Severity.YELLOW:
            return Queue.Status.OBSERVATION_MONITORING
        return Queue.Status.WAITING_QUEUE

    def test_hundred_patients_with_different_symptoms(self):
        scenarios = self._scenarios()
        self.assertEqual(len(scenarios), self.PATIENT_COUNT)

        actual_severities = []
        failures = []

        for index, scenario in enumerate(scenarios, start=1):
            national_id = str(8000000000000 + index)
            first_name = f"ผู้ป่วย{index:03d}"
            last_name = scenario["label"][:40]

            register_response = self.client.post(reverse("register_patient"), {
                "first_name": first_name,
                "last_name": last_name,
                "national_id": national_id,
                "gender": "M" if index % 2 else "F",
                "age": str(18 + (index % 63)),
                "phone": f"081{index:07d}",
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

            assessment_response = self.client.post(
                reverse("nurse_triage_assessment", args=[visit.id]),
                {
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
                },
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
                    "nurse_note": "ยืนยันตามผลทดสอบอัตโนมัติ 100 ผู้ป่วย",
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
                    assignment = NurseCareAssignment.objects.filter(visit=visit, is_active=True).first()
                    assignment_ok = assignment is not None
                    if assignment_ok:
                        device = Device.objects.create(
                            device_id=f"HUNDRED-WATCH-{index:03d}",
                            api_key=f"hundred-key-{index:03d}",
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

            expected_route = (
                self._route_for(next(iter(scenario["expected"])))
                if len(scenario["expected"]) == 1
                else Queue.Status.WAITING_QUEUE
            )
            route_ok = final_status == expected_route
            passed = classification_ok and confirm_ok and assignment_ok and route_ok

            actual_text = f"AI={actual_severity or '-'}, queue={final_status}"
            self.rows.append((
                index,
                scenario["label"],
                f"{self._expected_label(scenario['expected'])} -> {expected_route}",
                actual_text,
                "ผ่าน" if passed else "ไม่ผ่าน",
            ))
            if not passed:
                failures.append(
                    f"#{index} {scenario['label']}: expected={scenario['expected']}/{expected_route}, "
                    f"actual={actual_severity}/{final_status}"
                )

        expected_yellow = sum(1 for scenario in scenarios if scenario["expected"] == {"YELLOW"})
        loads = [
            NurseCareAssignment.objects.filter(nurse=nurse, is_active=True).count()
            for nurse in self.nurses
        ]
        yellow_assignment_count = NurseCareAssignment.objects.filter(is_active=True).count()
        monitor_items = self.client.get(reverse("monitor_summary_api")).json().get("items", [])
        monitor_visit_ids = {item.get("visit_id") for item in monitor_items}

        volume_ok = Patient.objects.count() == self.PATIENT_COUNT and len(self.rows) == self.PATIENT_COUNT
        capacity_ok = (
            yellow_assignment_count == expected_yellow
            and max(loads, default=0) <= self.NURSE_CAPACITY
            and (max(loads) - min(loads) <= 1 if loads else True)
        )
        monitor_ok = (
            len(self.yellow_visits) == expected_yellow
            and all(str(visit_id) in monitor_visit_ids for visit_id in self.yellow_visits)
        )

        if not volume_ok:
            failures.append(f"volume invariant failed: patients={Patient.objects.count()}, rows={len(self.rows)}")
        if not capacity_ok:
            failures.append(f"nurse capacity/balance failed: assignments={yellow_assignment_count}, loads={loads}")
        if not monitor_ok:
            failures.append(f"monitor visibility failed: yellow={len(self.yellow_visits)}, monitor={len(monitor_items)}")

        for visit_id in self.yellow_visits:
            self.client.post(reverse("discharge_visit", args=[visit_id]))

        cleanup_ok = (
            NurseCareAssignment.objects.filter(is_active=True).count() == 0
            and DeviceAssignment.objects.filter(is_active=True).count() == 0
        )
        if not cleanup_ok:
            failures.append("cleanup failed: active nurse/device assignments remained after discharge")

        print("\n" + "=" * 150)
        print("สรุปผลทดสอบอัตโนมัติ: ผู้ป่วยจำลอง 100 คน อาการแตกต่างกัน")
        print("=" * 150)
        print(f"{'คน':<4} | {'อาการ/สถานการณ์':<32} | {'ผลที่คาดว่าจะได้':<42} | {'ผลที่ได้':<42} | สถานะ")
        print("-" * 150)
        for index, label, expected, actual, status in self.rows:
            print(f"{index:<4} | {label[:32]:<32} | {expected[:42]:<42} | {actual[:42]:<42} | {status}")
        print("-" * 150)

        counts = Counter(actual_severities)
        passed_count = self.PATIENT_COUNT - len([row for row in self.rows if row[4] != "ผ่าน"])
        print(f"จำนวนผู้ป่วย: {Patient.objects.count()}/{self.PATIENT_COUNT}")
        print(f"ผลคัดกรองจริง: {dict(counts)}")
        print(
            f"พยาบาล {self.NURSE_COUNT} คน workload ก่อนจำหน่าย: {loads} "
            f"(กำหนดสูงสุด {self.NURSE_CAPACITY} คน/พยาบาล)"
        )
        print(f"ผู้ป่วยสีเหลืองที่มอบหมายและขึ้น Monitor: {len(self.yellow_visits)}/{expected_yellow}")
        print(f"คืน slot/device หลังจำหน่ายสีเหลือง: {'ผ่าน' if cleanup_ok else 'ไม่ผ่าน'}")
        print(f"สรุป: ผ่าน {passed_count}/{self.PATIENT_COUNT} ราย")
        print("หมายเหตุ: เป็น functional simulation แบบเรียงลำดับ ไม่ใช่ stress/load test แบบ 100 คนพร้อมกัน")
        print("ใช้ฐานข้อมูล test แยกจาก production และข้อมูลจำลองถูกลบทิ้งเมื่อ test จบ")
        print("=" * 150 + "\n")

        self.assertFalse(failures, "\n".join(failures))
