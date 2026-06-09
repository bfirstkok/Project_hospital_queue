from datetime import timedelta
import json

from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import random , string
from django.db import transaction
from django.apps import apps




from ai_triage.services import apply_ai_triage
from patients.models import Patient
from .forms import DevicePairingForm, NurseTriageAssessmentForm
from .models import Queue, Visit, Device, DeviceAssignment, TelemetryLog, VitalSign, TriageResult

SEVERITY_PRIORITY = {"RED": 1, "YELLOW": 2, "GREEN": 3}
QUEUE_READY_STATUSES = [Queue.Status.WAITING_QUEUE, Queue.Status.CALLED]
REQUIRED_VITAL_FIELDS = ["rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]


def has_required_vitals(vitals):
    return bool(vitals and all(getattr(vitals, field) is not None for field in REQUIRED_VITAL_FIELDS))


def evaluate_visit_if_vitals_complete(visit):
    vitals = getattr(visit, "vitals", None)
    if not has_required_vitals(vitals):
        return None

    result = apply_ai_triage(visit)
    q = getattr(visit, "queue", None)
    if q and q.status == Queue.Status.WAITING_VITALS:
        q.status = Queue.Status.WAITING_CONFIRMATION
        q.save(update_fields=["status"])
    return result


# -----------------------------
# QUEUE
# -----------------------------
@login_required
def queue_list(request):
    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result")
        .filter(status__in=QUEUE_READY_STATUSES)
        .order_by("priority", "visit__confirmed_at", "created_at")
    )
    
    # Count by severity
    red_count = sum(1 for q in q_items if q.visit.final_severity == "RED")
    yellow_count = sum(1 for q in q_items if q.visit.final_severity == "YELLOW")
    green_count = sum(1 for q in q_items if q.visit.final_severity == "GREEN")
    
    return render(request, "queues/queue_list.html", {
        "q_items": q_items,
        "red_count": red_count,
        "yellow_count": yellow_count,
        "green_count": green_count,
    })


def _vitals_payload(vitals):
    if not vitals:
        return {"hr": None, "o2sat": None, "bt": None, "rr": None, "sys_bp": None, "dia_bp": None}
    return {
        "hr": vitals.pr,
        "o2sat": vitals.o2sat,
        "bt": vitals.bt,
        "rr": vitals.rr,
        "sys_bp": vitals.sys_bp,
        "dia_bp": vitals.dia_bp,
    }


@login_required
def waiting_vitals(request):
    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__vitals")
        .filter(status=Queue.Status.WAITING_VITALS)
        .order_by("created_at")
    )
    return render(request, "queues/waiting_vitals.html", {"q_items": q_items})


@login_required
def waiting_confirmation(request):
    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result", "visit__vitals")
        .filter(status=Queue.Status.WAITING_CONFIRMATION)
        .order_by("visit__triaged_at", "created_at")
    )
    return render(request, "queues/waiting_confirmation.html", {"q_items": q_items})


@login_required
def call_visit(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    q = getattr(visit, "queue", None)
    if not q:
        return redirect("queue_list")

    if request.method == "POST":
        room = request.POST.get("exam_room")
        if room not in {"1", "2", "3"}:
            return render(request, "queues/select_exam_room.html", {
                "visit": visit,
                "queue": q,
                "rooms": [1, 2, 3],
                "error": "กรุณาเลือกห้องตรวจ",
            })

        if q.status == Queue.Status.WAITING_QUEUE:
            q.status = Queue.Status.CALLED
        q.exam_room = int(room)
        q.save(update_fields=["status", "exam_room"])

        visit.called_at = timezone.now()
        visit.save(update_fields=["called_at"])

        return redirect("opd_list")

    if q and q.status == Queue.Status.WAITING_QUEUE:
        return render(request, "queues/select_exam_room.html", {
            "visit": visit,
            "queue": q,
            "rooms": [1, 2, 3],
        })

    return redirect("opd_list")


@login_required
@transaction.atomic
def nurse_triage_assessment(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    q = getattr(visit, "queue", None)

    initial = {}
    vitals = getattr(visit, "vitals", None)
    if vitals:
        initial = {
            "rr": vitals.rr,
            "pr": vitals.pr,
            "sys_bp": vitals.sys_bp,
            "dia_bp": vitals.dia_bp,
            "bt": vitals.bt,
            "o2sat": vitals.o2sat,
            "pain_score": vitals.pain_score,
            "urgent_symptoms": vitals.urgent_symptoms,
            "risk_flags": vitals.risk_flags,
        }
        for field in ["rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]:
            if initial[field] is None:
                initial[f"{field}_unmeasured"] = True
    if visit.note:
        initial["symptoms"] = visit.note

    ai_result = None
    triage_result = getattr(visit, "triage_result", None)

    if request.method == "POST":
        action = request.POST.get("action", "evaluate")
        is_draft = action == "draft"
        form = NurseTriageAssessmentForm(request.POST, is_draft=is_draft)
        if form.is_valid():
            vitals, _ = VitalSign.objects.get_or_create(visit=visit)
            vitals.rr = form.cleaned_data["rr"]
            vitals.pr = form.cleaned_data["pr"]
            vitals.sys_bp = form.cleaned_data["sys_bp"]
            vitals.dia_bp = form.cleaned_data["dia_bp"]
            vitals.bt = form.cleaned_data["bt"]
            vitals.o2sat = form.cleaned_data["o2sat"]
            vitals.pain_score = form.cleaned_data.get("pain_score")
            vitals.urgent_symptoms = form.cleaned_data.get("urgent_symptoms") or []
            vitals.risk_flags = form.cleaned_data.get("risk_flags") or []
            vitals.save()

            visit.note = form.cleaned_data.get("symptoms", "")
            visit.save(update_fields=["note"])

            if not is_draft:
                ai_result = apply_ai_triage(visit)
                triage_result = getattr(visit, "triage_result", None)
                if q:
                    q.status = Queue.Status.WAITING_CONFIRMATION
                    q.save(update_fields=["status"])
                    return redirect("waiting_confirmation")
            else:
                ai_result = {"draft": True}
    else:
        form = NurseTriageAssessmentForm(initial=initial)

    return render(request, "queues/nurse_triage_assessment.html", {
        "visit": visit,
        "queue": q,
        "form": form,
        "ai_result": ai_result,
        "triage_result": triage_result,
    })


@login_required
@require_POST
def triage_visit(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    new_sev = request.POST.get("severity")
    nurse_note = request.POST.get("nurse_note", "").strip()

    if new_sev in ["RED", "YELLOW", "GREEN"]:
        visit.final_severity = new_sev
        visit.confirmed_at = timezone.now()
        visit.save(update_fields=["final_severity", "confirmed_at"])

        q = visit.queue
        q.priority = SEVERITY_PRIORITY[new_sev]
        q.status = Queue.Status.WAITING_QUEUE
        q.save(update_fields=["priority", "status"])

        triage_result, _ = TriageResult.objects.get_or_create(visit=visit)
        triage_result.nurse_severity = new_sev
        triage_result.nurse_note = nurse_note
        triage_result.save(update_fields=["nurse_severity", "nurse_note"])

    return redirect("queue_list")


@login_required
@require_POST
def send_to_monitoring(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    q = getattr(visit, "queue", None)
    if q:
        q.status = "MONITORING"
        q.save(update_fields=["status"])
    return redirect("monitor_dashboard")


@login_required
@require_POST
def discharge_visit(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    q = getattr(visit, "queue", None)
    if q:
        q.status = "DISCHARGED"
        q.save(update_fields=["status"])
    return redirect("monitor_dashboard")


@login_required
@require_POST
def cancel_queue(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    q = getattr(visit, "queue", None)
    if q and q.status in {
        Queue.Status.WAITING_VITALS,
        Queue.Status.WAITING_CONFIRMATION,
        Queue.Status.WAITING_QUEUE,
    }:
        q.status = Queue.Status.CANCELLED
        q.save(update_fields=["status"])
    return redirect("queue_list")


@login_required
@require_POST
def update_severity_api(request, visit_id: int):
    """
    API สำหรับ Dashboard เปลี่ยนสี severity
    POST /queues/api/update-severity/<visit_id>/
    Body: {"severity": "RED"|"YELLOW"|"GREEN"}
    """
    visit = get_object_or_404(Visit, id=visit_id)

    try:
        data = json.loads(request.body.decode("utf-8"))
        new_sev = data.get("severity")

        if new_sev not in ["RED", "YELLOW", "GREEN"]:
            return JsonResponse({"ok": False, "error": "Invalid severity"}, status=400)

        visit.final_severity = new_sev
        visit.confirmed_at = timezone.now()
        visit.save(update_fields=["final_severity", "confirmed_at"])

        q = visit.queue
        q.priority = SEVERITY_PRIORITY[new_sev]
        q.status = Queue.Status.WAITING_QUEUE
        q.save(update_fields=["priority", "status"])

        triage_result, _ = TriageResult.objects.get_or_create(visit=visit)
        triage_result.nurse_severity = new_sev
        triage_result.save(update_fields=["nurse_severity"])

        return JsonResponse({"ok": True, "visit_id": visit.id, "severity": new_sev})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# -----------------------------
# IoT API
# -----------------------------
@csrf_exempt
@require_POST
def iot_telemetry(request):
    """
    POST /api/iot/telemetry/
    Headers:
      X-DEVICE-ID
      X-API-KEY
    Body:
      {
        "visit_id": 1,
        "ts": "2025-12-17T08:30:00Z",
        "vitals": {"bpm": 90, "o2sat": 97, "bt": 37.1, "rr": 18, "sys_bp": 120, "dia_bp": 80}
      }
    """
    device_id = request.headers.get("X-DEVICE-ID")
    api_key = request.headers.get("X-API-KEY")
    if not device_id or not api_key:
        return JsonResponse({"ok": False, "error": "Missing X-DEVICE-ID or X-API-KEY"}, status=401)

    try:
        device = Device.objects.get(device_id=device_id, api_key=api_key, is_active=True)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Invalid device credentials"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    assignment = (
        DeviceAssignment.objects
        .select_related("visit", "visit__patient")
        .filter(device=device, is_active=True)
        .first()
    )
    if not assignment:
        return JsonResponse({"ok": False, "error": "Device is not paired to an active visit"}, status=409)

    visit = assignment.visit
    posted_visit_id = data.get("visit_id")
    if posted_visit_id:
        try:
            posted_visit_id = int(posted_visit_id)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "visit_id must be an integer"}, status=400)
        if posted_visit_id != visit.id:
            return JsonResponse({
                "ok": False,
                "error": "Posted visit_id does not match active device assignment",
            }, status=409)

    vitals = data.get("vitals") or {}
    # parse ts (ถ้าไม่ส่งมา ใช้เวลาปัจจุบัน)
    ts_str = data.get("ts")
    ts = timezone.now()
    if ts_str:
        try:
            ts = timezone.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts, timezone.utc)
            ts = ts.astimezone(timezone.get_current_timezone())
        except Exception:
            ts = timezone.now()

    # 1) บันทึก log ทุกครั้ง
    log = TelemetryLog.objects.create(
        visit=visit,
        device=device,
        ts=ts,
        bpm=vitals.get("bpm"),
        o2sat=vitals.get("o2sat"),
        bt=vitals.get("bt"),
        rr=vitals.get("rr"),
        sys_bp=vitals.get("sys_bp"),
        dia_bp=vitals.get("dia_bp"),
    )

    # 2) update device last_seen
    device.last_seen = timezone.now()
    device.save(update_fields=["last_seen"])

    # 3) update VitalSign ล่าสุด (อ่านง่ายใน monitor)
    vs, _ = VitalSign.objects.get_or_create(visit=visit)
    if vitals.get("rr") is not None:
        vs.rr = vitals.get("rr")
    if vitals.get("bpm") is not None:
        vs.pr = vitals.get("bpm")  # pr = bpm
    if vitals.get("sys_bp") is not None:
        vs.sys_bp = vitals.get("sys_bp")
    if vitals.get("dia_bp") is not None:
        vs.dia_bp = vitals.get("dia_bp")
    if vitals.get("bt") is not None:
        vs.bt = vitals.get("bt")
    if vitals.get("o2sat") is not None:
        vs.o2sat = vitals.get("o2sat")
    vs.save()

    triage_result = evaluate_visit_if_vitals_complete(visit)
    queue_status = getattr(getattr(visit, "queue", None), "status", None)

    return JsonResponse({
        "ok": True,
        "log_id": log.id,
        "vitals_complete": has_required_vitals(vs),
        "queue_status": queue_status,
        "ai": triage_result,
    })


# -----------------------------
# helpers: ดึง "ล่าสุด" ด้วย Subquery
# -----------------------------
def _visit_queryset_with_latest_vitals():
    latest_vs = VitalSign.objects.filter(visit=OuterRef("pk")).order_by("-updated_at")

    latest_any_log = TelemetryLog.objects.filter(visit=OuterRef("pk")).order_by("-ts")

    return (
        Visit.objects
        .select_related("patient")
        .annotate(
            last_ts=Subquery(latest_vs.values("updated_at")[:1]),
            last_bpm=Subquery(latest_vs.values("pr")[:1]),
            last_o2=Subquery(latest_vs.values("o2sat")[:1]),
            last_bt=Subquery(latest_vs.values("bt")[:1]),
            last_rr=Subquery(latest_vs.values("rr")[:1]),
            last_sys=Subquery(latest_vs.values("sys_bp")[:1]),
            last_dia=Subquery(latest_vs.values("dia_bp")[:1]),

            last_log_ts=Subquery(latest_any_log.values("ts")[:1]),
            last_device_id=Subquery(latest_any_log.values("device__device_id")[:1]),
        )
    )



def _get_ai_severity(visit):
    # กันพัง: บางที relation อาจชื่อ triage_result หรือ triage
    obj = getattr(visit, "triage_result", None) or getattr(visit, "triage", None)
    return getattr(obj, "ai_severity", None)


# -----------------------------
# MONITOR (หน้า + API)
# -----------------------------
@login_required
def monitor_dashboard(request):
    return render(request, "queues/monitor_dashboard.html")


@login_required
@transaction.atomic
def device_pairing(request):
    if request.method == "POST":
        form = DevicePairingForm(request.POST)
        if form.is_valid():
            visit = form.cleaned_data["visit"]
            device = form.cleaned_data["device"]
            now = timezone.now()
            DeviceAssignment.objects.filter(device=device, is_active=True).update(
                is_active=False,
                unpaired_at=now,
            )
            DeviceAssignment.objects.filter(visit=visit, is_active=True).update(
                is_active=False,
                unpaired_at=now,
            )
            DeviceAssignment.objects.create(visit=visit, device=device)
            device.last_seen = timezone.now()
            device.save(update_fields=["last_seen"])
            return redirect("device_pairing")
    else:
        form = DevicePairingForm()

    latest_pairings = (
        DeviceAssignment.objects
        .select_related("visit", "visit__patient", "device")
        .order_by("-paired_at")[:30]
    )
    devices = Device.objects.order_by("device_id")

    return render(request, "queues/device_pairing.html", {
        "form": form,
        "latest_pairings": latest_pairings,
        "devices": devices,
    })


@login_required
def monitor_latest_api(request):
    """
    ส่งข้อมูลล่าสุดให้หน้า monitor (รีเฟรชทุก 5 วิ)
    ONLINE = มี log ภายใน 3 นาที
    """
    offline_after = timezone.now() - timedelta(minutes=3)

    latest_log = (
        TelemetryLog.objects
        .filter(visit=OuterRef("visit"))
        .order_by("-ts")
    )

    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result")
        .filter(status=Queue.Status.WAITING_QUEUE)
        .annotate(
            last_log_ts=Subquery(latest_log.values("ts")[:1]),
            last_device_id=Subquery(latest_log.values("device__device_id")[:1]),
            last_bpm=Subquery(latest_log.values("bpm")[:1]),
            last_o2sat=Subquery(latest_log.values("o2sat")[:1]),
            last_bt=Subquery(latest_log.values("bt")[:1]),
            last_rr=Subquery(latest_log.values("rr")[:1]),
            last_sys_bp=Subquery(latest_log.values("sys_bp")[:1]),
            last_dia_bp=Subquery(latest_log.values("dia_bp")[:1]),
        )
        .order_by("priority", "created_at")[:200]
    )

    rows = []
    for q in q_items:
        visit = q.visit

        online = bool(q.last_log_ts and q.last_log_ts >= offline_after)

        rows.append({
            "visit_id": visit.id,
            "name": f"{visit.patient.first_name} {visit.patient.last_name}",
            "severity": visit.final_severity,
            "ai": _get_ai_severity(visit),
            "device_id": q.last_device_id,
            "online": online,

            "bpm": q.last_bpm,
            "o2sat": q.last_o2sat,
            "bt": q.last_bt,
            "rr": q.last_rr,
            "sys_bp": q.last_sys_bp,
            "dia_bp": q.last_dia_bp,

            "registered_at": visit.registered_at.isoformat() if visit.registered_at else None,
        })

    return JsonResponse({"ok": True, "rows": rows})


@login_required
def monitor_summary_api(request):
    """
    API ให้หน้า dashboard / monitor
    เรียงตาม: RED → YELLOW → GREEN → มาก่อนก่อน
    """
    now = timezone.now()

    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient")
        .filter(status=Queue.Status.WAITING_QUEUE)
        .order_by("priority", "visit__confirmed_at", "created_at")[:200]
    )

    visit_ids = [q.visit_id for q in q_items]

    visits = {
        v.id: v
        for v in _visit_queryset_with_latest_vitals()
        .filter(id__in=visit_ids)
    }

    items = []
    for q in q_items:
        v = visits.get(q.visit_id)
        if not v:
            continue

        online = False
        if v.last_log_ts:
            online = (now - v.last_log_ts).total_seconds() <= 60

        items.append({
            "visit_id": v.id,
            "patient_name": f"{v.patient.first_name} {v.patient.last_name}",
            "severity": v.final_severity,
            "registered_at": v.registered_at.isoformat() if v.registered_at else None,
            "online": online,
            "device_id": v.last_device_id,
            "vitals": {
                "bpm": v.last_bpm,
                "o2sat": v.last_o2,
                "bt": v.last_bt,
                "rr": v.last_rr,
                "sys_bp": v.last_sys,
                "dia_bp": v.last_dia,
            }
        })

    return JsonResponse({
        "ok": True,
        "items": items,
        "server_time": now.isoformat()
    })


@login_required
def monitor_visit_detail(request, visit_id: int):
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


@login_required
@require_GET
def monitor_sparklines_api(request):
    """
    GET /queues/monitor/api/sparklines/?visit_ids=1,2,3
    return: { ok:true, series: { "1": {"bpm":[...], "o2":[...]}, ... } }
    """
    ids_raw = request.GET.get("visit_ids", "").strip()
    if not ids_raw:
        return JsonResponse({"ok": True, "series": {}})

    try:
        visit_ids = [int(x) for x in ids_raw.split(",") if x.strip().isdigit()]
    except Exception:
        return JsonResponse({"ok": False, "error": "bad visit_ids"}, status=400)

    N = 20  # จำนวนจุดในกราฟเล็ก (ปรับได้)

    logs = (
        TelemetryLog.objects
        .filter(visit_id__in=visit_ids)
        .order_by("visit_id", "-ts")
        .values("visit_id", "bpm", "o2sat")
    )

    series = {}
    for row in logs:
        vid = str(row["visit_id"])
        series.setdefault(vid, {"bpm": [], "o2": []})

        if len(series[vid]["bpm"]) < N and row["bpm"] is not None:
            series[vid]["bpm"].append(row["bpm"])
        if len(series[vid]["o2"]) < N and row["o2sat"] is not None:
            series[vid]["o2"].append(row["o2sat"])

        # ถ้าทั้งสองครบแล้ว จะไม่ต้องเติมเพิ่ม (กันวนเยอะ)
        if len(series[vid]["bpm"]) >= N and len(series[vid]["o2"]) >= N:
            pass

    # reverse ให้เก่า -> ใหม่ (กราฟวิ่งซ้ายไปขวา)
    for vid in series:
        series[vid]["bpm"] = list(reversed(series[vid]["bpm"]))
        series[vid]["o2"]  = list(reversed(series[vid]["o2"]))

    return JsonResponse({"ok": True, "series": series})

@login_required
@require_POST
def demo_create_visit_queue(request):
    """
    POST /demo/create/
    สร้าง Visit+Queue จำลอง 1 รายการ (WAITING)
    """
    # 1) เลือกคนไข้ที่มีอยู่ (ชัวร์สุด)
    patient = Patient.objects.order_by("?").first()
    if not patient:
        return JsonResponse({"ok": False, "error": "No patients in DB. Create a Patient first."}, status=400)

    # 2) สร้าง Visit
    visit = Visit.objects.create(
        patient=patient,
        final_severity=random.choice(["GREEN", "YELLOW", "RED"]),
        confirmed_at=timezone.now(),
    )

    # 3) สร้าง Queue
    priority_map = {"RED": 1, "YELLOW": 2, "GREEN": 3}
    Queue.objects.create(
        visit=visit,
        status=Queue.Status.WAITING_QUEUE,
        priority=priority_map.get(visit.final_severity, 3),
    )

    return JsonResponse({"ok": True, "visit_id": visit.id})

@login_required
@require_POST
@transaction.atomic
def dashboard_demo_create(request):
    """
    คลิกเดียวสร้าง Patient + Visit + Queue(WAITING) สำหรับเดโม
    """
    # --- 1) สุ่มข้อมูลผู้ป่วย ---
    first_names = ["สมชาย", "สมหญิง", "ธนกฤต", "ณัฐ", "กิตติ", "วราภรณ์", "พิมพ์", "กานต์"]
    last_names  = ["ใจดี", "ศรีสุข", "ทองดี", "มีสุข", "บุญช่วย", "ประเสริฐ", "เจริญพร", "วงศ์ดี"]

    fn = random.choice(first_names)
    ln = random.choice(last_names)

    # สุ่มเลขบัตร/hn แบบง่าย ๆ (ปรับ field ให้ตรงของจริง)
    cid = "".join(random.choice(string.digits) for _ in range(13))
    hn  = "HN" + "".join(random.choice(string.digits) for _ in range(6))

    # --- 2) หาโมเดล Patient ของจริง ---
    Patient = apps.get_model("patients", "Patient")  # ถ้า app/model ไม่ใช่ชื่อนี้ให้แก้ตรงนี้

    # ถ้าในโมเดล Patient ไม่มี field บางตัว ให้ลบออกให้ตรงของเธอ
    patient = Patient.objects.create(
        first_name=fn,
        last_name=ln,
        national_id=cid,
        hn=hn,
    )

    # --- 3) สร้าง Visit ---
    sev_choices = ["RED", "YELLOW", "GREEN"]
    sev = random.choices(sev_choices, weights=[1, 3, 6], k=1)[0]  # GREEN เจอบ่อยกว่า

    visit = Visit.objects.create(
        patient=patient,
        final_severity=sev,
        triaged_at=timezone.now(),  # ถ้าไม่อยากให้เหมือนคัดกรองแล้ว ลบบรรทัดนี้ได้
        confirmed_at=timezone.now(),
    )

    # --- 4) สร้าง Queue (WAITING) ---
    priority_map = {"RED": 1, "YELLOW": 2, "GREEN": 3}
    q = Queue.objects.create(
        visit=visit,
        status=Queue.Status.WAITING_QUEUE,
        priority=priority_map.get(sev, 3),
    )

    return JsonResponse({
        "ok": True,
        "patient_id": patient.id,
        "visit_id": visit.id,
        "queue_id": q.id,
        "severity": sev,
    })

