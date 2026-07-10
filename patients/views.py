# patients/views.py
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import PatientForm
from .models import Appointment, Patient
from queues.models import Visit, Queue, VitalSign


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
                note=patient.note,
            )

            # สร้าง VitalSign จากข้อมูลที่กรอกในฟอร์ม
            VitalSign.objects.create(
                visit=visit,
                sys_bp=patient.bp_sys,
                dia_bp=patient.bp_dia,
            )

            # Queue starts outside the prioritized examination queue.
            Queue.objects.create(
                visit=visit,
                status=Queue.Status.WAITING_VITALS,
            )

        return redirect("waiting_vitals")

    # GET
    return render(request, "patients/register.html", {"form": PatientForm()})


@login_required
def patient_search(request):
    query = request.GET.get("q", "").strip()
    patients = Patient.objects.none()

    if query:
        patients = (
            Patient.objects
            .filter(
                Q(hn__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(national_id__icontains=query)
                | Q(phone__icontains=query)
            )
            .order_by("hn", "first_name")[:80]
        )

    return render(request, "patients/search.html", {
        "query": query,
        "patients": patients,
    })


@login_required
def patient_history(request, patient_id: int):
    patient = get_object_or_404(Patient, id=patient_id)
    visits = (
        Visit.objects
        .filter(patient=patient)
        .select_related("queue", "triage_result", "opd_assessment", "vitals")
        .prefetch_related("critical_alerts")
        .order_by("-registered_at")
    )
    appointments = patient.appointments.order_by("-date", "-time", "-created_at")

    return render(request, "patients/history.html", {
        "patient": patient,
        "visits": visits,
        "appointments": appointments,
        "appointment_statuses": Appointment.Status.choices,
    })


@login_required
@require_POST
def create_appointment(request, patient_id: int):
    patient = get_object_or_404(Patient, id=patient_id)
    date = request.POST.get("date")
    time = request.POST.get("time") or None
    note = request.POST.get("note", "").strip()
    if date:
        Appointment.objects.create(patient=patient, date=date, time=time, note=note)
    return redirect("patient_history", patient_id=patient.id)


@login_required
@require_POST
def update_appointment_status(request, appointment_id: int):
    appointment = get_object_or_404(Appointment, id=appointment_id)
    status = request.POST.get("status")
    if status in Appointment.Status.values:
        appointment.status = status
        appointment.attended_at = timezone.now() if status == Appointment.Status.ATTENDED else None
        appointment.save(update_fields=["status", "attended_at", "updated_at"])
    return redirect("patient_history", patient_id=appointment.patient_id)
