from unittest.mock import patch

from django.test import TestCase

from ai_triage.services import apply_ai_triage, localize_ai_reason
from ai_triage.ml.predictor import dt_predict
from patients.models import Patient
from queues.models import Queue, TriageResult, Visit, VitalSign


class AiTriageGuardrailTests(TestCase):
    def make_visit(self, **vitals):
        patient = Patient.objects.create(
            first_name="Demo",
            last_name="Patient",
            national_id=f"{Patient.objects.count() + 1:013d}",
        )
        visit = Visit.objects.create(patient=patient)
        Queue.objects.create(visit=visit)
        VitalSign.objects.create(visit=visit, **vitals)
        return visit

    @patch("ai_triage.services.dt_predict", return_value=("RED", 1.0, "model"))
    def test_normal_vitals_stay_green_even_if_model_predicts_red(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
        )

        result = apply_ai_triage(visit)
        visit.refresh_from_db()
        triage = TriageResult.objects.get(visit=visit)

        self.assertEqual(result["severity"], "GREEN")
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.queue.priority, 5)
        self.assertEqual(triage.ai_severity, "GREEN")
        self.assertIn("ระบบกฎความปลอดภัยปรับระดับคำแนะนำ", triage.ai_reason)

    def test_legacy_english_reason_is_localized_for_display(self):
        reason = (
            "No critical vital-sign trigger detected; Rule guardrail applied "
            "(model suggested YELLOW, rule result RED)"
        )

        localized = localize_ai_reason(reason)

        self.assertIn("ไม่พบค่าสัญญาณชีพที่เข้าเกณฑ์วิกฤต", localized)
        self.assertIn("ระบบตรวจพบเงื่อนไขความปลอดภัย", localized)
        self.assertIn("ปรับคำแนะนำจากสีเหลืองเป็นสีแดง", localized)
        self.assertNotIn("โมเดลแนะนำ", localized)

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_low_o2_below_95_is_pink(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=92,
        )

        result = apply_ai_triage(visit)
        visit.refresh_from_db()

        self.assertEqual(result["severity"], "PINK")
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.queue.priority, 5)

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_critical_low_o2_is_red(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=88,
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "RED")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_multiple_danger_vitals_are_pink(self, _mock_dt):
        visit = self.make_visit(
            rr=31,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=92,
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "PINK")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_yellow_rule_trigger_overrides_model(self, _mock_dt):
        visit = self.make_visit(
            rr=24,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
        )

        result = apply_ai_triage(visit)
        visit.refresh_from_db()

        self.assertEqual(result["severity"], "YELLOW")
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.queue.priority, 5)

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_pain_score_seven_is_yellow(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
            pain_score=7,
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "YELLOW")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_negated_danger_symptoms_do_not_trigger_red(self, _mock_dt):
        visit = self.make_visit(
            rr=20,
            pr=105,
            sys_bp=121,
            dia_bp=78,
            bt=36.8,
            o2sat=98,
            pain_score=8,
        )
        visit.note = "ปวดแขนรุนแรง ไม่มีอาการเจ็บหน้าอก ไม่หอบ ไม่มีชัก ไม่หมดสติ"
        visit.save(update_fields=["note"])

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "YELLOW")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_high_risk_symptom_is_pink(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
            urgent_symptoms=["chest_pain"],
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "PINK")

    @patch("ai_triage.services.dt_predict", return_value=("WHITE", 0.9, "model"))
    def test_normal_vitals_can_be_classified_white(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "WHITE")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_risk_flag_is_yellow(self, _mock_dt):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
            risk_flags=["elderly_80"],
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "YELLOW")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_lifesaving_assessment_is_red(self, _mock_dt):
        visit = self.make_visit(rr=18, pr=82, sys_bp=114, dia_bp=76, bt=36.8, o2sat=98)
        TriageResult.objects.create(
            visit=visit,
            lifesaving_intervention=True,
            high_risk_condition=False,
            altered_mental_status=False,
            mental_status="ALERT",
            severe_distress=False,
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "RED")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_high_risk_assessment_is_pink(self, _mock_dt):
        visit = self.make_visit(rr=18, pr=82, sys_bp=114, dia_bp=76, bt=36.8, o2sat=98)
        TriageResult.objects.create(
            visit=visit,
            lifesaving_intervention=False,
            high_risk_condition=True,
            altered_mental_status=False,
            mental_status="ALERT",
            severe_distress=False,
        )

        result = apply_ai_triage(visit)

        self.assertEqual(result["severity"], "PINK")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_expected_resources_distinguish_levels_three_to_five(self, _mock_dt):
        expected = {"2_PLUS": "YELLOW", "1": "GREEN", "0": "WHITE"}
        for resources, severity in expected.items():
            with self.subTest(resources=resources):
                visit = self.make_visit(rr=18, pr=82, sys_bp=114, dia_bp=76, bt=36.8, o2sat=98)
                TriageResult.objects.create(
                    visit=visit,
                    lifesaving_intervention=False,
                    high_risk_condition=False,
                    altered_mental_status=False,
                    mental_status="ALERT",
                    severe_distress=False,
                    expected_resources=resources,
                )

                result = apply_ai_triage(visit)

                self.assertEqual(result["severity"], severity)

    @patch("ai_triage.ml.predictor.load_model")
    def test_predictor_receives_visit_age_pain_and_chief_complaint(self, mock_load_model):
        visit = self.make_visit(
            rr=18,
            pr=82,
            sys_bp=114,
            dia_bp=76,
            bt=36.8,
            o2sat=98,
            pain_score=6,
        )
        visit.patient.age = 47
        visit.patient.save(update_fields=["age"])
        visit.note = "ปวดแขนด้านซ้าย"
        visit.save(update_fields=["note"])
        TriageResult.objects.create(
            visit=visit,
            lifesaving_intervention=False,
            high_risk_condition=True,
            altered_mental_status=False,
            mental_status="VERBAL",
            severe_distress=True,
            expected_resources="2_PLUS",
        )
        model = mock_load_model.return_value
        model.predict.return_value = ["GREEN"]
        model.predict_proba.return_value = [[0.1, 0.8, 0.1]]

        dt_predict(visit.vitals, visit=visit)

        frame = model.predict.call_args.args[0]
        self.assertEqual(frame.iloc[0]["age"], 47)
        self.assertEqual(frame.iloc[0]["nrs_pain"], 6)
        self.assertEqual(frame.iloc[0]["chief_complain"], "ปวดแขนด้านซ้าย")
        self.assertEqual(frame.iloc[0]["lifesaving_intervention"], 0)
        self.assertEqual(frame.iloc[0]["high_risk_condition"], 1)
        self.assertEqual(frame.iloc[0]["severe_distress"], 1)
        self.assertEqual(frame.iloc[0]["mental_status"], 2)
        self.assertEqual(frame.iloc[0]["expected_resources"], "2_PLUS")
