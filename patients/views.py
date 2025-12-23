# patients/views.py
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone

from .forms import PatientForm
from .models import Patient
from queues.models import Visit, Queue
from ai_triage.services import apply_ai_triage


def severity_to_priority(sev: str) -> int:
    return {"RED": 1, "YELLOW": 2, "GREEN": 3}.get(sev, 3)


@login_required
def register_patient(request):
    if request.method == "POST":
        form = PatientForm(request.POST)

        if not form.is_valid():
            return render(request, "patients/register.html", {"form": form})

        national_id = form.cleaned_data["national_id"]

        with transaction.atomic():
            patient, created = Patient.objects.get_or_create(
                national_id=national_id,
                defaults=form.cleaned_data,  # ตอนสร้างใหม่ ใส่ทุก field ได้เลย
            )

            # ถ้ามีอยู่แล้ว → อัปเดตข้อมูลจากฟอร์ม (ให้หน้า register ใช้แก้ข้อมูลคนเดิมได้)
            if not created:
                for field, value in form.cleaned_data.items():
                    setattr(patient, field, value)
                patient.save()

            # สร้าง Visit ใหม่ทุกครั้ง
            visit = Visit.objects.create(
                patient=patient,
                registered_at=timezone.now(),
            )

            # AI Triage
            triage_result = apply_ai_triage(visit)

            severity = (
                triage_result.get("ai_severity")
                if isinstance(triage_result, dict)
                else getattr(triage_result, "ai_severity", None)
            ) or "GREEN"

            visit.final_severity = severity
            visit.save()

            # Queue
            Queue.objects.create(
                visit=visit,
                priority=severity_to_priority(severity),
            )

        return redirect("queue_list")

    # GET
    return render(request, "patients/register.html", {"form": PatientForm()})
