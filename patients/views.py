from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .forms import PatientForm
from .models import Patient
from queues.models import Visit, Queue
from queues.forms import VitalSignForm
from ai_triage.services import apply_ai_triage

def severity_to_priority(sev: str) -> int:
    # priority เลขน้อยมาก่อน
    return {"RED": 1, "YELLOW": 2, "GREEN": 3}.get(sev, 3)

@login_required
def register_patient(request):
    patient_form = PatientForm(request.POST or None)
    vital_form = VitalSignForm(request.POST or None)

    if request.method == "POST" and patient_form.is_valid() and vital_form.is_valid():
        patient, _created = Patient.objects.get_or_create(
            cid=patient_form.cleaned_data["cid"],
            defaults=patient_form.cleaned_data
        )
        # ถ้าผู้ป่วยมีอยู่แล้ว อัปเดตข้อมูลพื้นฐานให้ล่าสุด
        for k, v in patient_form.cleaned_data.items():
            setattr(patient, k, v)
        patient.save()

        visit = Visit.objects.create(patient=patient)  # registered_at auto
        vitals = vital_form.save(commit=False)
        vitals.visit = visit
        vitals.save()

        # ตอนนี้ยังไม่ทำ AI ให้ใช้ default GREEN ไปก่อน
        visit.final_severity = "GREEN"
        visit.save()

        Queue.objects.create(visit=visit, priority=3)  # ตั้งต้น GREEN
        apply_ai_triage(visit)  # <-- ให้ AI ประเมิน แล้วปรับสี+priority ให้อัตโนมัติ

            
        

        return redirect("queue_list")

    return render(request, "patients/register.html", {
        "patient_form": patient_form,
        "vital_form": vital_form
    })
