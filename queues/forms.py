from django import forms

from .models import Device, Visit


class NurseTriageAssessmentForm(forms.Form):
    rr = forms.IntegerField(label="RR", min_value=0, required=True)
    pr = forms.IntegerField(label="PR / BPM", min_value=0, required=True)
    sys_bp = forms.IntegerField(label="Systolic BP", min_value=0, required=True)
    dia_bp = forms.IntegerField(label="Diastolic BP", min_value=0, required=True)
    bt = forms.FloatField(label="BT", min_value=30, max_value=45, required=True)
    o2sat = forms.IntegerField(label="O2 Sat", min_value=0, max_value=100, required=True)
    symptoms = forms.CharField(
        label="Symptoms",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class DevicePairingForm(forms.Form):
    visit = forms.ModelChoiceField(
        queryset=Visit.objects.select_related("patient", "queue").filter(
            queue__status__in=["WAITING", "MONITORING", "FOLLOWUP"]
        ).exclude(device_assignments__is_active=True).order_by("queue__priority", "registered_at"),
        label="Visit",
        required=True,
    )
    device = forms.ModelChoiceField(
        queryset=Device.objects.filter(is_active=True).exclude(assignments__is_active=True).order_by("device_id"),
        label="Device",
        required=True,
    )
