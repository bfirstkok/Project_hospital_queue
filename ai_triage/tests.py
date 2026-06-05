from unittest.mock import patch

from django.test import TestCase

from ai_triage.services import apply_ai_triage
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
        self.assertEqual(visit.final_severity, "GREEN")
        self.assertEqual(visit.queue.priority, 3)
        self.assertEqual(triage.ai_severity, "GREEN")
        self.assertIn("Rule guardrail applied", triage.ai_reason)

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_red_rule_trigger_overrides_model(self, _mock_dt):
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

        self.assertEqual(result["severity"], "RED")
        self.assertEqual(visit.final_severity, "RED")
        self.assertEqual(visit.queue.priority, 1)

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
        self.assertEqual(visit.final_severity, "YELLOW")
        self.assertEqual(visit.queue.priority, 2)

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_pain_score_seven_is_red(self, _mock_dt):
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

        self.assertEqual(result["severity"], "RED")

    @patch("ai_triage.services.dt_predict", return_value=("GREEN", 0.9, "model"))
    def test_urgent_symptom_is_red(self, _mock_dt):
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

        self.assertEqual(result["severity"], "RED")

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
