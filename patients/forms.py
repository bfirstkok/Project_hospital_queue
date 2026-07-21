# patients/forms.py
from django import forms
from .models import Patient

class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "national_id",
            "gender", "age", "phone",
            "blood_type",
            "height_cm", "weight_kg", "bp_sys", "bp_dia",
            "province","district","subdistrict","postal_code",
            "chronic_diseases", "allergies", "medications",
            "emergency_name", "emergency_relationship", "emergency_phone",
            "note",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "chronic_diseases": forms.Textarea(attrs={"rows": 2}),
            "allergies": forms.Textarea(attrs={"rows": 2}),
            "medications": forms.Textarea(attrs={"rows": 2}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if nid and (not nid.isdigit() or len(nid) != 13):
            raise forms.ValidationError("เลขบัตรประชาชนต้องเป็นตัวเลข 13 หลัก")
        return nid


class PublicPatientRegistrationForm(forms.ModelForm):
    consent = forms.BooleanField(required=True)

    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "national_id",
            "gender", "age", "phone", "blood_type",
            "height_cm", "weight_kg",
            "province", "district", "subdistrict", "postal_code",
            "chronic_diseases", "allergies", "medications",
            "emergency_name", "emergency_relationship", "emergency_phone",
            "note",
        ]

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if not nid.isdigit() or len(nid) != 13:
            raise forms.ValidationError("เลขบัตรประชาชนต้องเป็นตัวเลข 13 หลัก")
        return nid

    def clean_age(self):
        age = self.cleaned_data.get("age")
        if age is not None and age > 130:
            raise forms.ValidationError("กรุณาตรวจสอบอายุ")
        return age

    def validate_unique(self):
        # ผู้ป่วยเดิมลงทะเบียนรับบริการครั้งใหม่ได้ โดย view จะอัปเดตข้อมูลเดิม
        pass
