from django import forms
from django.db.models import Q

from .models import Device, Queue, TriageResult, Visit


def pairable_visit_query():
    """YELLOW observation candidates or post-OPD/inpatient monitoring visits."""
    return (
        Q(
            final_severity=Visit.Severity.YELLOW,
            queue__status=Queue.Status.WAITING_QUEUE,
        )
        | Q(queue__status=Queue.Status.MONITORING)
    )


class NurseTriageAssessmentForm(forms.Form):
    YES_NO_CHOICES = [
        ("", "ยังไม่ระบุ"),
        ("yes", "ใช่"),
        ("no", "ไม่ใช่"),
    ]
    URGENT_SYMPTOM_CHOICES = [
        ("chest_pain", "เจ็บหน้าอก"),
        ("dyspnea", "หายใจลำบาก / หอบเหนื่อย"),
        ("altered_consciousness", "ซึมลง / สับสน"),
        ("unresponsive", "ไม่รู้สึกตัว / เรียกไม่ตื่น"),
        ("seizure", "ชัก"),
        ("active_seizure", "กำลังชัก"),
        ("major_bleeding", "เลือดออกมาก"),
        ("severe_pain", "ปวดรุนแรง"),
        ("high_fever", "ไข้สูง"),
        ("severe_accident", "อุบัติเหตุรุนแรง"),
        ("stroke_signs", "หน้าเบี้ยว แขนขาอ่อนแรง หรือพูดไม่ชัดเฉียบพลัน"),
    ]
    RISK_FLAG_CHOICES = [
        ("copd_asthma", "COPD / Asthma"),
        ("child_under_5", "เด็กอายุต่ำกว่า 5 ปี"),
        ("elderly_80", "ผู้สูงอายุ ≥ 80 ปี"),
        ("pregnant", "ตั้งครรภ์"),
        ("immunocompromised", "ภูมิคุ้มกันต่ำ"),
    ]
    VITAL_FIELDS = ["rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]

    rr = forms.IntegerField(label="อัตราการหายใจ (RR) *", min_value=0, required=False)
    rr_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    pr = forms.IntegerField(label="ชีพจร (PR) *", min_value=0, required=False)
    pr_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    sys_bp = forms.IntegerField(label="ความดันตัวบน *", min_value=0, required=False)
    sys_bp_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    dia_bp = forms.IntegerField(label="ความดันตัวล่าง *", min_value=0, required=False)
    dia_bp_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    bt = forms.FloatField(label="อุณหภูมิร่างกาย *", min_value=30, max_value=45, required=False)
    bt_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    o2sat = forms.IntegerField(label="ออกซิเจนในเลือด (SpO₂) *", min_value=0, max_value=100, required=False)
    o2sat_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    pain_score = forms.IntegerField(
        label="Pain Score",
        min_value=0,
        max_value=10,
        required=False,
        initial=0,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 10, "step": 1}),
    )
    urgent_symptoms = forms.MultipleChoiceField(
        label="อาการเร่งด่วน",
        choices=URGENT_SYMPTOM_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    risk_flags = forms.MultipleChoiceField(
        label="กลุ่มพิเศษ",
        choices=RISK_FLAG_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    lifesaving_intervention = forms.ChoiceField(
        label="ต้องช่วยชีวิตทันทีหรือไม่",
        choices=YES_NO_CHOICES,
        required=False,
        widget=forms.RadioSelect,
    )
    high_risk_condition = forms.ChoiceField(
        label="เป็นภาวะเสี่ยงสูงที่ไม่ควรรอหรือไม่",
        choices=YES_NO_CHOICES,
        required=False,
        widget=forms.RadioSelect,
    )
    mental_status = forms.ChoiceField(
        label="ระดับการตอบสนองของผู้ป่วย",
        choices=[("", "ยังไม่ระบุ"), *TriageResult.MentalStatus.choices],
        required=False,
        widget=forms.RadioSelect,
    )
    severe_distress = forms.ChoiceField(
        label="มีความทุกข์ทรมานหรืออาการรุนแรงที่ต้องประเมินฉุกเฉินหรือไม่",
        choices=YES_NO_CHOICES,
        required=False,
        widget=forms.RadioSelect,
    )
    expected_resources = forms.ChoiceField(
        label="คาดว่าจะต้องตรวจหรือรักษาเพิ่มเติมกี่ประเภท",
        choices=[
            ("", "ยังประเมินไม่ได้"),
            (TriageResult.ExpectedResources.NONE, "0 ประเภท — ตรวจทั่วไปหรือรับยากินกลับบ้าน"),
            (TriageResult.ExpectedResources.ONE, "1 ประเภท — เช่น ตรวจเลือดอย่างเดียว"),
            (TriageResult.ExpectedResources.MANY, "2 ประเภทขึ้นไป — เช่น ตรวจเลือด + X-ray"),
        ],
        required=False,
        widget=forms.RadioSelect,
    )
    symptoms = forms.CharField(
        label="เหตุผล/รายละเอียดเพิ่มเติม",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, is_draft=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_draft = is_draft

    def clean(self):
        cleaned = super().clean()
        if self.is_draft:
            return cleaned

        for field in self.VITAL_FIELDS:
            if cleaned.get(field) is None and not cleaned.get(f"{field}_unmeasured"):
                self.add_error(field, "กรุณากรอกค่า หรือเลือก 'ยังไม่ได้วัด'")

        decision_fields = [
            "lifesaving_intervention",
            "high_risk_condition",
            "severe_distress",
        ]
        for field in decision_fields:
            if not cleaned.get(field):
                self.add_error(field, "กรุณาเลือก ใช่ หรือ ไม่ใช่")

        if not cleaned.get("mental_status"):
            self.add_error("mental_status", "กรุณาประเมินระดับการตอบสนองของผู้ป่วย")

        has_emergency_decision = (
            any(cleaned.get(field) == "yes" for field in decision_fields)
            or cleaned.get("mental_status") in {
                TriageResult.MentalStatus.VERBAL,
                TriageResult.MentalStatus.PAIN,
                TriageResult.MentalStatus.UNRESPONSIVE,
            }
        )
        if not has_emergency_decision and not cleaned.get("expected_resources"):
            self.add_error(
                "expected_resources",
                "กรุณาประเมินจำนวนทรัพยากรสำหรับแยกระดับเหลือง เขียว และขาว",
            )

        return cleaned


class DevicePairingForm(forms.Form):
    visit = forms.ModelChoiceField(
        queryset=Visit.objects.select_related("patient", "queue").filter(
            pairable_visit_query(),
        ).exclude(device_assignments__is_active=True).order_by("queue__priority", "registered_at"),
        label="Visit",
        required=True,
    )
    device = forms.ModelChoiceField(
        queryset=Device.objects.filter(is_active=True).exclude(assignments__is_active=True).order_by("device_id"),
        label="Device",
        required=True,
    )


class DeviceCreateForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ["device_id", "api_key", "is_active"]
        widgets = {
            "device_id": forms.TextInput(attrs={"placeholder": "WATCH001"}),
            "api_key": forms.TextInput(attrs={"placeholder": "เว้นว่างเพื่อสร้างอัตโนมัติ"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device_id"].required = True
        self.fields["api_key"].required = False
        self.fields["is_active"].required = False
        self.fields["is_active"].initial = True

    def clean_device_id(self):
        device_id = (self.cleaned_data.get("device_id") or "").strip()
        if not device_id:
            raise forms.ValidationError("กรุณากรอก device_id")
        if Device.objects.filter(device_id=device_id).exists():
            raise forms.ValidationError("device_id นี้มีอยู่แล้ว")
        return device_id

    def clean_api_key(self):
        return (self.cleaned_data.get("api_key") or "").strip()


class DeviceManagementPairForm(forms.Form):
    device = forms.ModelChoiceField(
        queryset=Device.objects.none(),
        label="Device",
        required=True,
    )
    visit = forms.ModelChoiceField(
        queryset=Visit.objects.none(),
        label="Visit",
        required=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = Device.objects.filter(is_active=True).exclude(assignments__is_active=True).order_by("device_id")
        self.fields["visit"].queryset = (
            Visit.objects
            .select_related("patient", "queue")
            .filter(pairable_visit_query())
            .exclude(device_assignments__is_active=True)
            .order_by("queue__priority", "-registered_at")
        )
        self.fields["device"].label_from_instance = lambda device: device.device_id
        self.fields["visit"].label_from_instance = self.visit_label
        self.fields["device"].widget.attrs.update({
            "class": "combo-select",
            "data-placeholder": "พิมพ์เพื่อค้นหา device...",
        })
        self.fields["visit"].widget.attrs.update({
            "class": "combo-select",
            "data-placeholder": "พิมพ์เพื่อค้นหาผู้ป่วย/visit...",
        })

    @staticmethod
    def visit_label(visit):
        patient = visit.patient
        patient_name = f"{patient.first_name} {patient.last_name}".strip()
        severity = visit.final_severity or "-"
        queue_status = getattr(getattr(visit, "queue", None), "status", "-")
        return f"{patient_name} | Visit #{visit.id} | {severity} | {queue_status}"
