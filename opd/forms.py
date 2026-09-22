from django import forms
from .models import VisitAssessment


class VisitAssessmentForm(forms.ModelForm):
    send_to_monitoring = forms.BooleanField(
        label="รับไว้รักษา/ติดตามอาการที่ รพ. (Monitoring)",
        required=False,
    )
    has_next_appointment = forms.BooleanField(
        label="มีนัดหมายครั้งต่อไป",
        required=False,
    )

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
            "diagnosis": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "ระบุการวินิจฉัย หรือเลือกข้อความที่ใช้บ่อยด้านล่าง",
            }),
            "treatment": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "ระบุแผนการรักษา หรือเลือกข้อความที่ใช้บ่อยด้านล่าง",
            }),
            "next_appointment_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["has_next_appointment"].initial = bool(self.instance.next_appointment_at)

        # Age is identity/demographic data already known from Patient. Keep it
        # visible in the clinical form, but never ask the doctor to re-enter or
        # override it here.
        self.fields["age"].disabled = True
        self.fields["age"].help_text = "คำนวณอัตโนมัติจากวันเกิด/ข้อมูลผู้ป่วย"
        self.fields["age"].widget.attrs.update({
            "readonly": "readonly",
            "aria-readonly": "true",
            "data-source": "patient",
        })

        for name in (
            "chief_complaint",
            "known_copd_asthma",
            "pain_score",
            "bt",
            "sys_bp",
            "dia_bp",
            "child_under_5",
            "pregnant",
            "low_immunity",
        ):
            self.fields[name].widget.attrs.setdefault("data-prefill-source", "triage")

    def clean(self):
        cleaned_data = super().clean()
        send_to_monitoring = cleaned_data.get("send_to_monitoring")
        has_next_appointment = cleaned_data.get("has_next_appointment")
        next_appointment_at = cleaned_data.get("next_appointment_at")

        if send_to_monitoring:
            cleaned_data["has_next_appointment"] = False
            cleaned_data["next_appointment_at"] = None
            cleaned_data["next_appointment_note"] = ""
            return cleaned_data

        if has_next_appointment and not next_appointment_at:
            self.add_error("next_appointment_at", "กรุณาระบุวันและเวลานัดหมาย")

        if not has_next_appointment:
            cleaned_data["next_appointment_at"] = None
            cleaned_data["next_appointment_note"] = ""

        return cleaned_data
