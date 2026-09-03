import copy
import io
from contextlib import redirect_stdout

from queues import test_hundred_patient_scenarios as hundred_module


class FiveHundredPatientVariedSymptomWorkflowTests(
    hundred_module.HundredPatientVariedSymptomWorkflowTests
):
    """Functional-volume simulation: 500 unique patient symptom profiles through the real HTTP flow."""

    PATIENT_COUNT = 500
    NURSE_COUNT = 25
    NURSE_CAPACITY = 4

    def _scenarios(self):
        base_scenarios = super()._scenarios()
        contexts = [
            ("ช่วงเช้า", "อาการเริ่มในช่วงเช้าก่อนมารับบริการ"),
            ("หลังอาหาร", "อาการเกิดหลังรับประทานอาหารก่อนมาโรงพยาบาล"),
            ("ระหว่างทำงาน", "อาการเกิดระหว่างทำงานและยังคงมีอาการต่อเนื่อง"),
            ("ขณะพัก", "อาการเกิดขณะพักอยู่ที่บ้านก่อนเดินทางมาโรงพยาบาล"),
            ("หลังเดินทาง", "อาการเกิดหลังเดินทางและผู้ป่วยมารับการประเมิน"),
        ]

        scenarios = []
        for context_index, (context_label, context_text) in enumerate(contexts, start=1):
            for base_index, base in enumerate(base_scenarios, start=1):
                scenario = copy.deepcopy(base)
                scenario["label"] = f"{base['label']} [{context_label}-{context_index}]"
                scenario["symptoms"] = f"{base['symptoms']} โดย{context_text} (กรณี {base_index:03d}-{context_index})"
                scenarios.append(scenario)

        return scenarios

    def test_hundred_patients_with_different_symptoms(self):
        # Reuse the proven 100-patient HTTP workflow while scaling its data set,
        # nurse pool, monitoring assignments, device pairing, and cleanup to 500.
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                super().test_hundred_patients_with_different_symptoms()
        finally:
            report = output.getvalue()
            report = report.replace(
                "สรุปผลทดสอบอัตโนมัติ: ผู้ป่วยจำลอง 100 คน อาการแตกต่างกัน",
                "สรุปผลทดสอบอัตโนมัติ: ผู้ป่วยจำลอง 500 คน อาการแตกต่างกัน",
            )
            report = report.replace(
                "แบบ 100 คนพร้อมกัน",
                "แบบ 500 คนพร้อมกัน",
            )
            report = report.replace(
                "ยืนยันตามผลทดสอบอัตโนมัติ 100 ผู้ป่วย",
                "ยืนยันตามผลทดสอบอัตโนมัติ 500 ผู้ป่วย",
            )
            print(report, end="")
