from datetime import timedelta
import random

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.db import transaction
from django.http import JsonResponse
from django.db.models import OuterRef, Subquery

from queues.models import Queue, Visit, TelemetryLog
from .models import VisitAssessment
from .forms import VisitAssessmentForm


# -----------------------------
# helpers
# -----------------------------
def _compute_opd(assessment):
    """
    รองรับทั้งชื่อ compute_opd_priority() และ compute_opd_urgency()
    คืนค่า (color, reasons)
    """
    if hasattr(assessment, "compute_opd_priority"):
        return assessment.compute_opd_priority()
    if hasattr(assessment, "compute_opd_urgency"):
        return assessment.compute_opd_urgency()
    # กันพังสุดท้าย
    return ("GREEN", ["No urgency rule method found on VisitAssessment"])


# -----------------------------
# OPD LIST (เคสที่ถูกเรียกเข้าห้องตรวจ)
# -----------------------------
@login_required
def opd_list(request):
    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient")
        .filter(status="CALLED")
        .order_by("priority", "created_at")
    )
    # ✅ จากโครงสร้างโฟลเดอร์ของเธอ: opd/templates/opd_list.html
    return render(request, "opd_list.html", {"q_items": q_items})


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

    assessment, _ = VisitAssessment.objects.get_or_create(visit=visit)

    if request.method == "POST":
        form = VisitAssessmentForm(request.POST, instance=assessment)
        if form.is_valid():
            assessment = form.save()

            # ✅ คำนวณเร่งด่วนแบบปลอดภัย
            color, reasons = _compute_opd(assessment)

            # (ทางเลือก) ปรับสี final ตาม OPD auto
            if color in ["RED", "YELLOW"]:
                visit.final_severity = color
                visit.triaged_at = visit.triaged_at or timezone.now()
                visit.save(update_fields=["final_severity", "triaged_at"])

            # 1) ปิดคิว OPD
            q.status = "OPD_DONE"
            q.save(update_fields=["status"])

            # 2) ถ้ามีนัดครั้งต่อไป -> สร้าง followup visit + queue(FOLLOWUP) (กันซ้ำ)
            next_appt = getattr(assessment, "next_appointment_at", None)

            # ต้องมี field followup_visit จริง (ForeignKey ไป Visit) ใน VisitAssessment
            followup_id = getattr(assessment, "followup_visit_id", None)

            if next_appt and followup_id is None:
                followup_visit = Visit.objects.create(
                    patient=visit.patient,
                    final_severity="GREEN",
                )

                Queue.objects.create(
                    visit=followup_visit,
                    status="FOLLOWUP",
                    priority=3,
                )

                assessment.followup_visit = followup_visit
                assessment.save(update_fields=["followup_visit"])

            return redirect("opd_list")
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

    followup_qs = (
        Queue.objects
        .select_related("visit", "visit__patient")
        .filter(status="FOLLOWUP")
        .order_by("-created_at")[:200]
    )

    visit_ids = [q.visit_id for q in followup_qs]

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
    for q in followup_qs:
        v = visit_map.get(q.visit_id)
        if not v:
            continue

        online = bool(v.last_log_ts and v.last_log_ts >= offline_after)

        rows.append({
            "visit_id": v.id,
            "name": f"{v.patient.first_name} {v.patient.last_name}",
            "severity": v.final_severity,
            "device_id": v.last_device_id,
            "online": online,
            "vitals": {
                "bpm": v.last_bpm,
                "o2sat": v.last_o2sat,
                "bt": v.last_bt,
                "rr": v.last_rr,
                "sys_bp": v.last_sys,
                "dia_bp": v.last_dia,
            },
            "followup_queue_id": q.id,
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
    if not q or q.status != "FOLLOWUP":
        return redirect("monitor_dashboard")

    logs = (
        TelemetryLog.objects
        .select_related("device")
        .filter(visit=visit)
        .order_by("-ts")[:100]
    )

    # ใช้ template เดิมของ queues ได้ถ้ามีอยู่แล้ว
    # ถ้าอยากแยกไฟล์ใหม่ค่อยทำ templates/opd/followup_detail.html ภายหลัง
    return render(request, "queues/monitor_visit_detail.html", {
        "visit": visit,
        "logs": logs,
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
    if not q or q.status != "FOLLOWUP":
        return JsonResponse({"ok": False, "error": "visit is not FOLLOWUP"}, status=400)

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
