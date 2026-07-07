from django import forms

from .models import Device, Queue, Visit


PAIRABLE_QUEUE_STATUSES = [
    Queue.Status.MONITORING,
]


class NurseTriageAssessmentForm(forms.Form):
    URGENT_SYMPTOM_CHOICES = [
        ("chest_pain", "เจ็บหน้าอก"),
        ("dyspnea", "หายใจลำบาก / หอบเหนื่อย"),
        ("altered_consciousness", "ซึมลง / หมดสติ"),
        ("seizure", "ชัก"),
        ("major_bleeding", "เลือดออกมาก"),
        ("severe_pain", "ปวดรุนแรง"),
        ("high_fever", "ไข้สูง"),
        ("severe_accident", "อุบัติเหตุรุนแรง"),
    ]
    RISK_FLAG_CHOICES = [
        ("copd_asthma", "COPD / Asthma"),
        ("child_under_5", "เด็กอายุต่ำกว่า 5 ปี"),
        ("elderly_80", "ผู้สูงอายุ ≥ 80 ปี"),
        ("pregnant", "ตั้งครรภ์"),
        ("immunocompromised", "ภูมิคุ้มกันต่ำ"),
    ]
    VITAL_FIELDS = ["rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]

    rr = forms.IntegerField(label="RR *", min_value=0, required=False)
    rr_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    pr = forms.IntegerField(label="PR / BPM *", min_value=0, required=False)
    pr_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    sys_bp = forms.IntegerField(label="Systolic BP *", min_value=0, required=False)
    sys_bp_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    dia_bp = forms.IntegerField(label="Diastolic BP *", min_value=0, required=False)
    dia_bp_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    bt = forms.FloatField(label="BT *", min_value=30, max_value=45, required=False)
    bt_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    o2sat = forms.IntegerField(label="O₂ Sat *", min_value=0, max_value=100, required=False)
    o2sat_unmeasured = forms.BooleanField(label="ยังไม่ได้วัด", required=False)
    pain_score = forms.IntegerField(
        label="Pain Score",
        min_value=0,
        max_value=10,
        required=False,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 10, "step": 1}),
    )
    urgent_symptoms = forms.MultipleChoiceField(
        label="อาการเร่งด่วน",
        choices=URGENT_SYMPTOM_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    risk_flags = forms.MultipleChoiceField(
        label="กลุ่มเสี่ยง",
        choices=RISK_FLAG_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    symptoms = forms.CharField(
        label="รายละเอียดอาการเพิ่มเติม",
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

        return cleaned


class DevicePairingForm(forms.Form):
    visit = forms.ModelChoiceField(
        queryset=Visit.objects.select_related("patient", "queue").filter(
            queue__status__in=["WAITING_VITALS", "WAITING_QUEUE", "MONITORING", "FOLLOWUP"]
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
        self.fields["device"].queryset = Device.objects.filter(is_active=True).order_by("device_id")
        self.fields["visit"].queryset = (
            Visit.objects
            .select_related("patient", "queue")
            .filter(queue__status__in=PAIRABLE_QUEUE_STATUSES)
            .order_by("queue__priority", "-registered_at")
        )
        self.fields["device"].label_from_instance = lambda device: device.device_id
        self.fields["visit"].label_from_instance = self.visit_label

    @staticmethod
    def visit_label(visit):
        patient = visit.patient
        patient_name = f"{patient.first_name} {patient.last_name}".strip()
        severity = visit.final_severity or "-"
        queue_status = getattr(getattr(visit, "queue", None), "status", "-")
        return f"{patient_name} | Visit #{visit.id} | {severity} | {queue_status}"
