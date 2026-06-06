from datetime import timedelta
import random

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.db import transaction
from django.http import JsonResponse
from django.db.models import OuterRef, Subquery

from queues.models import Queue, Visit, TelemetryLog, VitalSign
from .models import VisitAssessment
from .forms import VisitAssessmentForm

EXAM_ROOMS = (1, 2, 3)


def _get_related(obj, attr):
    try:
        return getattr(obj, attr)
    except AttributeError:
        return None


def _monitor_alerts(v):
    alerts = []
    if v.last_o2sat is not None and v.last_o2sat < 95:
        alerts.append("SpO2 < 95")
    if v.last_rr is not None and v.last_rr > 30:
        alerts.append("RR > 30")
    if v.last_bt is not None and float(v.last_bt) >= 39:
        alerts.append("Temp >= 39")
    if v.last_sys is not None and v.last_sys < 90:
        alerts.append("Systolic BP < 90")
    if v.last_bpm is not None and v.last_bpm >= 120:
        alerts.append("BPM >= 120")
    return alerts


# -----------------------------
# helpers
# -----------------------------
def _compute_opd(assessment):
    """
    รองรับทั้งชื่อ compute_opd_priority() และ compute_opd_urgency()
    คืนค่า (color, reasons)
    """
    if assessment is None:
        return ("NORMAL", [])
    if hasattr(assessment, "compute_opd_priority"):
        return assessment.compute_opd_priority()
    if hasattr(assessment, "compute_opd_urgency"):
        return assessment.compute_opd_urgency()
    # กันพังสุดท้าย
    return ("GREEN", ["No urgency rule method found on VisitAssessment"])


def _opd_queue_queryset(selected_room):
    return (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result", "visit__vitals")
        .filter(status="CALLED", exam_room=selected_room)
        .order_by("visit__id")
    )


def _opd_queue_payload(q_items):
    rows = []
    counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}

    for index, q in enumerate(q_items, start=1):
        v = q.visit
        severity = v.final_severity or "GREEN"
        if severity in counts:
            counts[severity] += 1

        triage = _get_related(v, "triage_result")
        vitals = _get_related(v, "vitals")

        rows.append({
            "index": index,
            "queue_id": q.id,
            "visit_id": v.id,
            "patient_name": f"{v.patient.first_name} {v.patient.last_name}",
            "severity": severity,
            "ai_severity": triage.ai_severity if triage else None,
            "nurse_severity": triage.nurse_severity if triage else None,
            "ai_reason": triage.ai_reason if triage else "",
            "pain_score": vitals.pain_score if vitals else None,
            "rr": vitals.rr if vitals else None,
            "pr": vitals.pr if vitals else None,
            "o2sat": vitals.o2sat if vitals else None,
            "bt": vitals.bt if vitals else None,
            "called_at": timezone.localtime(v.called_at).strftime("%H:%M") if v.called_at else "-",
        })

    return {
        "rows": rows,
        "counts": counts,
        "total": len(rows),
    }


# -----------------------------
# OPD LIST (เคสที่ถูกเรียกเข้าห้องตรวจ)
# -----------------------------
@login_required
def opd_list(request):
    selected_room = request.session.get("opd_exam_room")
    if selected_room not in EXAM_ROOMS:
        return redirect("opd_room_select")

    q_items = _opd_queue_queryset(selected_room)
    
    # Count by severity
    red_count = sum(1 for q in q_items if q.visit.final_severity == "RED")
    yellow_count = sum(1 for q in q_items if q.visit.final_severity == "YELLOW")
    green_count = sum(1 for q in q_items if q.visit.final_severity == "GREEN")
    
    return render(request, "opd_list.html", {
        "q_items": q_items,
        "red_count": red_count,
        "yellow_count": yellow_count,
        "green_count": green_count,
        "selected_room": selected_room,
        "rooms": EXAM_ROOMS,
    })


@login_required
def opd_list_api(request):
    selected_room = request.session.get("opd_exam_room")
    if selected_room not in EXAM_ROOMS:
        return JsonResponse({"ok": False, "error": "no_exam_room"}, status=400)

    payload = _opd_queue_payload(_opd_queue_queryset(selected_room))
    payload.update({
        "ok": True,
        "selected_room": selected_room,
        "server_time": timezone.now().isoformat(),
    })
    return JsonResponse(payload)


@login_required
def opd_room_select(request):
    if request.method == "POST":
        room = request.POST.get("exam_room")
        if room in {"1", "2", "3"}:
            request.session["opd_exam_room"] = int(room)
            return redirect("opd_list")
        return render(request, "opd_room_select.html", {
            "rooms": EXAM_ROOMS,
            "error": "กรุณาเลือกห้องตรวจ",
        })

    return render(request, "opd_room_select.html", {
        "rooms": EXAM_ROOMS,
        "selected_room": request.session.get("opd_exam_room"),
    })


# -----------------------------
# OPD ASSESSMENT
# -----------------------------
@login_required
@transaction.atomic
def visit_assessment(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), pk=visit_id)

    q = getattr(visit, "queue", None)
    if not q or q.status != "CALLED":
        return redirect("opd_list")

    assessment = VisitAssessment.objects.filter(visit=visit).first()

    if request.method == "POST":
        form = VisitAssessmentForm(request.POST, instance=assessment)
        if form.is_valid():
            assessment = form.save(commit=False)
            assessment.visit = visit

            color, reasons = _compute_opd(assessment)
            assessment.opd_urgency = color
            assessment.save()

            # (ทางเลือก) ปรับสี final ตาม OPD auto
            if color in ["RED", "YELLOW"]:
                visit.final_severity = color
                visit.triaged_at = visit.triaged_at or timezone.now()
                visit.save(update_fields=["final_severity", "triaged_at"])

            # ตรวจสอบว่ามีนัดครั้งต่อไปหรือไม่
            next_appt = getattr(assessment, "next_appointment_at", None)

            if next_appt:
                # ถ้ามีนัด -> เปลี่ยน Queue status เป็น FOLLOWUP (ใช้ Visit เดิม)
                q.status = "FOLLOWUP"
                q.save(update_fields=["status"])
            else:
                # ถ้าไม่มีนัด -> ปิดคิว OPD
                q.status = "OPD_DONE"
                q.save(update_fields=["status"])

            # Redirect ไปหน้ารายละเอียด Visit เพื่อให้เห็น Assessment ที่บันทึกไป
            return redirect("opd_visit_detail", visit_id=visit.id)
    else:
        form = VisitAssessmentForm(instance=assessment)

    color, reasons = _compute_opd(assessment)

    # ✅ จากโครงสร้างโฟลเดอร์ของเธอ: opd/templates/assessment.html
    return render(request, "assessment.html", {
        "visit": visit,
        "q": q,
        "form": form,
        "opd_color": color,
        "opd_reasons": reasons,
        "followup_visit_id": getattr(assessment, "followup_visit_id", None),
    })


# -----------------------------
# OPD VISIT DETAIL (ดูรายละเอียด Visit พร้อม Assessment)
# URL: /opd/visit/<visit_id>/detail/
# -----------------------------
@login_required
def opd_visit_detail(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), pk=visit_id)
    logs = TelemetryLog.objects.filter(visit=visit).select_related("device").order_by("-ts")[:50]

    # ดึงข้อมูล assessment ถ้ามี
    assessment = None
    if hasattr(visit, 'opd_assessment'):
        assessment = visit.opd_assessment

    return render(request, "queues/monitor_visit_detail.html", {
        "visit": visit,
        "logs": logs,
        "assessment": assessment
    })


# -----------------------------
# POST OPD MONITOR (หน้า)
# -----------------------------
@login_required
def post_opd_monitor(request):
    # ✅ จากโครงสร้างโฟลเดอร์ของเธอ: opd/templates/post_opd_monitor.html
    return render(request, "post_opd_monitor.html")


# -----------------------------
# POST OPD MONITOR (API)
# -----------------------------
@login_required
def post_opd_monitor_api(request):
    offline_after = timezone.now() - timedelta(minutes=3)

    monitor_qs = (
        Queue.objects
        .select_related("visit", "visit__patient")
        .filter(status="MONITORING")
        .order_by("priority", "created_at")[:200]
    )

    visit_ids = [q.visit_id for q in monitor_qs]

    latest_log = (
        TelemetryLog.objects
        .filter(visit_id=OuterRef("pk"))
        .order_by("-ts")
    )

    visits = (
        Visit.objects
        .select_related("patient")
        .filter(id__in=visit_ids)
        .annotate(
            last_log_ts=Subquery(latest_log.values("ts")[:1]),
            last_bpm=Subquery(latest_log.values("bpm")[:1]),
            last_o2sat=Subquery(latest_log.values("o2sat")[:1]),
            last_bt=Subquery(latest_log.values("bt")[:1]),
            last_rr=Subquery(latest_log.values("rr")[:1]),
            last_sys=Subquery(latest_log.values("sys_bp")[:1]),
            last_dia=Subquery(latest_log.values("dia_bp")[:1]),
            last_device_id=Subquery(latest_log.values("device__device_id")[:1]),
        )
    )

    visit_map = {v.id: v for v in visits}

    rows = []
    for q in monitor_qs:
        v = visit_map.get(q.visit_id)
        if not v:
            continue

        online = bool(v.last_log_ts and v.last_log_ts >= offline_after)
        alerts = _monitor_alerts(v)

        rows.append({
            "visit_id": v.id,
            "name": f"{v.patient.first_name} {v.patient.last_name}",
            "severity": v.final_severity,
            "device_id": v.last_device_id,
            "online": online,
            "alerts": alerts,
            "alert_level": "critical" if alerts else "normal",
            "vitals": {
                "bpm": v.last_bpm,
                "o2sat": v.last_o2sat,
                "bt": v.last_bt,
                "rr": v.last_rr,
                "sys_bp": v.last_sys,
                "dia_bp": v.last_dia,
            },
            "queue_id": q.id,
            "created_at": q.created_at.isoformat() if q.created_at else None,
        })

    return JsonResponse({"ok": True, "rows": rows, "server_time": timezone.now().isoformat()})


# -----------------------------
# FOLLOWUP VISIT DETAIL (สำหรับกดดูรายละเอียดจาก monitor)
# URL: /queues/monitor/visit/<visit_id>/
# -----------------------------
@login_required
def post_opd_visit_detail(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), pk=visit_id)

    # ต้องเป็นเคส FOLLOWUP เท่านั้น
    q = getattr(visit, "queue", None)
    if not q or q.status != "MONITORING":
        return redirect("monitor_dashboard")

    logs = (
        TelemetryLog.objects
        .select_related("device")
        .filter(visit=visit)
        .order_by("-ts")[:100]
    )

    # ดึงข้อมูล assessment ถ้ามี
    assessment = None
    if hasattr(visit, 'opd_assessment'):
        assessment = visit.opd_assessment

    # ใช้ template เดิมของ queues ได้ถ้ามีอยู่แล้ว
    # ถ้าอยากแยกไฟล์ใหม่ค่อยทำ templates/opd/followup_detail.html ภายหลัง
    return render(request, "queues/monitor_visit_detail.html", {
        "visit": visit,
        "logs": logs,
        "assessment": assessment,
    })


# -----------------------------
# DEMO PUSH TELEMETRY (สุ่มค่า vitals เข้า TelemetryLog)
# URL: /queues/monitor/demo/push/<visit_id>/
# -----------------------------
@login_required
@transaction.atomic
def post_opd_demo_push_telemetry(request, visit_id: int):
    visit = get_object_or_404(Visit, pk=visit_id)

    # ต้องเป็น followup เท่านั้น
    q = getattr(visit, "queue", None)
    if not q or q.status != "MONITORING":
        return JsonResponse({"ok": False, "error": "visit is not MONITORING"}, status=400)

    # สุ่มค่าทั่วไป
    bpm = random.randint(60, 120)
    o2 = random.randint(90, 100)
    bt = round(random.uniform(36.2, 38.8), 1)
    rr = random.randint(14, 26)
    sys_bp = random.randint(100, 170)
    dia_bp = random.randint(60, 100)

    # สร้าง log (device = None ได้ เพราะ model เธออนุญาตไว้ใน update_location แล้ว)
    log = TelemetryLog.objects.create(
        visit=visit,
        device=None,
        ts=timezone.now(),
        bpm=bpm,
        o2sat=o2,
        bt=bt,
        rr=rr,
        sys_bp=sys_bp,
        dia_bp=dia_bp,
    )

    return JsonResponse({
        "ok": True,
        "log_id": log.id,
        "vitals": {
            "bpm": bpm, "o2sat": o2, "bt": bt,
            "rr": rr, "sys_bp": sys_bp, "dia_bp": dia_bp,
        }
    })
