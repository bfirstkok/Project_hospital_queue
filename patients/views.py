from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone

from .models import Patient
from queues.models import Visit, Queue
from ai_triage.services import apply_ai_triage


def severity_to_priority(sev: str) -> int:
    return {"RED": 1, "YELLOW": 2, "GREEN": 3}.get(sev, 3)


@login_required
def register_patient(request):
    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        national_id = request.POST.get("national_id", "").strip()

        if not (first_name and last_name and national_id):
            return render(
                request,
                "patients/register.html",
                {"error": "กรุณากรอกข้อมูลให้ครบ"},
            )

        # 1️⃣ Patient (กันซ้ำด้วย national_id)
        patient, _ = Patient.objects.get_or_create(
            national_id=national_id,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
            },
        )

        # 2️⃣ Visit
        visit = Visit.objects.create(
            patient=patient,
            registered_at=timezone.now(),
        )

        # 3️⃣ AI Triage
        triage_result = apply_ai_triage(visit)

        severity = (
            triage_result.get("ai_severity")
            if isinstance(triage_result, dict)
            else getattr(triage_result, "ai_severity", None)
        ) or "GREEN"

        visit.final_severity = severity
        visit.save()

        # 4️⃣ Queue
        Queue.objects.create(
            visit=visit,
            priority=severity_to_priority(severity),
        )

        # 5️⃣ กลับหน้าคิว
        return redirect("queue_list")

    return render(request, "patients/register.html")
