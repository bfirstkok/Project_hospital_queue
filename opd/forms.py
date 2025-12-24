from django import forms
from .models import VisitAssessment


class VisitAssessmentForm(forms.ModelForm):
    class Meta:
        model = VisitAssessment
        fields = [
            "chief_complaint",

            "known_copd_asthma",
            "pain_score",
            "bt", "sys_bp", "dia_bp",
            "fbs", "lab_k", "lab_mg", "lab_hct",

            "anxious_family", "non_toxic_bite", "very_fatigue", "blood_receive",

            "monk", "age", "child_under_5",
            "pregnant", "ga_weeks",
            "epilepsy", "pulmonary_tb_mplus",
            "low_immunity", "low_immunity_detail",

            "diagnosis", "treatment",

            "next_appointment_at", "next_appointment_note",
        ]
        widgets = {
            "chief_complaint": forms.Textarea(attrs={"rows": 3}),
            "diagnosis": forms.Textarea(attrs={"rows": 3}),
            "treatment": forms.Textarea(attrs={"rows": 3}),
            "next_appointment_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
