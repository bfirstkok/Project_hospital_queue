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




from .models import Queue, Visit, Device, TelemetryLog, VitalSign


# -----------------------------
# QUEUE
# -----------------------------
@login_required
def queue_list(request):
    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient")
        .filter(status="WAITING")
        .order_by("priority", "created_at")
    )
    return render(request, "queues/queue_list.html", {"q_items": q_items})


@login_required
def call_visit(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    q = getattr(visit, "queue", None)
    if q and q.status == "WAITING":
        q.status = "CALLED"
        q.save(update_fields=["status"])

        visit.called_at = timezone.now()
        visit.save(update_fields=["called_at"])

<<<<<<< HEAD
    return redirect("queue_list")
=======
    return redirect("opd_list")
>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16


@login_required
@require_POST
def triage_visit(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    new_sev = request.POST.get("severity")

    if new_sev in ["RED", "YELLOW", "GREEN"]:
        visit.final_severity = new_sev
        visit.triaged_at = timezone.now()
        visit.save(update_fields=["final_severity", "triaged_at"])

        q = visit.queue
        q.priority = {"RED": 1, "YELLOW": 2, "GREEN": 3}[new_sev]
        q.save(update_fields=["priority"])

    return redirect("queue_list")


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
        "vitals": {"bpm": 90, "o2sat": 97, "bt": 37.1, "rr": 18, "sys_bp": 120, "dia_bp": 80},
        "gps": {"lat": 16.44, "lng": 102.83}
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

    visit_id = data.get("visit_id")
    if not visit_id:
        return JsonResponse({"ok": False, "error": "visit_id required"}, status=400)

    try:
        visit = Visit.objects.select_related("patient").get(id=visit_id)
    except Visit.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Visit not found"}, status=404)

    vitals = data.get("vitals") or {}
    gps = data.get("gps") or {}

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
        lat=gps.get("lat"),
        lng=gps.get("lng"),
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

    return JsonResponse({"ok": True, "log_id": log.id})


# -----------------------------
# helpers: ดึง "ล่าสุด" ด้วย Subquery
# -----------------------------
def _visit_queryset_with_latest_vitals_and_gps():
    latest_vs = VitalSign.objects.filter(visit=OuterRef("pk")).order_by("-updated_at")

    latest_gps_log = (
        TelemetryLog.objects
        .filter(visit=OuterRef("pk"), lat__isnull=False, lng__isnull=False)
        .order_by("-ts")
    )

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

            last_lat=Subquery(latest_gps_log.values("lat")[:1]),
            last_lng=Subquery(latest_gps_log.values("lng")[:1]),
            last_gps_ts=Subquery(latest_gps_log.values("ts")[:1]),

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
def monitor_latest_api(request):
    """
    ส่งข้อมูลล่าสุดให้หน้า monitor (รีเฟรชทุก 5 วิ)
    ONLINE = มี log ภายใน 3 นาที
    """
    offline_after = timezone.now() - timedelta(minutes=3)

    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result")
        .filter(status="WAITING")
        .order_by("priority", "created_at")[:200]
    )

    rows = []
    for q in q_items:
        visit = q.visit

        last_log = (
            TelemetryLog.objects
            .select_related("device")
            .filter(visit=visit)
            .order_by("-ts")
            .first()
        )

        online = bool(last_log and last_log.ts and last_log.ts >= offline_after)

        rows.append({
            "visit_id": visit.id,
            "name": f"{visit.patient.first_name} {visit.patient.last_name}",
            "severity": visit.final_severity,
            "ai": _get_ai_severity(visit),
            "device_id": last_log.device.device_id if last_log and last_log.device else None,
            "online": online,

            "bpm": last_log.bpm if last_log else None,
            "o2sat": last_log.o2sat if last_log else None,
            "bt": last_log.bt if last_log else None,
            "rr": last_log.rr if last_log else None,
            "sys_bp": last_log.sys_bp if last_log else None,
            "dia_bp": last_log.dia_bp if last_log else None,

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
        .filter(status="WAITING")
        .order_by("priority", "created_at")[:200]
    )

    visit_ids = [q.visit_id for q in q_items]

    visits = {
        v.id: v
        for v in _visit_queryset_with_latest_vitals_and_gps()
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
            },
            "gps": {
                "lat": float(v.last_lat) if v.last_lat is not None else None,
                "lng": float(v.last_lng) if v.last_lng is not None else None,
                "updated_at": v.last_gps_ts.isoformat() if v.last_gps_ts else None,
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
    return render(request, "queues/monitor_visit_detail.html", {"visit": visit, "logs": logs})


# -----------------------------
# MAP
# -----------------------------
@login_required
def map_view(request):
    visits = (
        _visit_queryset_with_latest_vitals_and_gps()
        .exclude(last_lat__isnull=True)
        .exclude(last_lng__isnull=True)[:200]
    )
    now = timezone.now()
    return render(request, "queues/map.html", {"visits": visits, "now": now})


@login_required
@csrf_exempt
@require_POST
def update_location(request, visit_id: int):
    """
    อัปเดตพิกัด โดยบันทึกเป็น TelemetryLog (ไม่แตะ Visit.lat/lng)
    Body: {"lat": 16.44, "lng": 102.83}
    """
    visit = get_object_or_404(Visit, id=visit_id)

    try:
        data = json.loads(request.body.decode("utf-8"))
        lat = data.get("lat")
        lng = data.get("lng")
        if lat is None or lng is None:
            return JsonResponse({"ok": False, "error": "lat/lng required"}, status=400)

        # ✅ กันพัง: ผูก device ล่าสุดถ้ามี (ไม่งั้นค่อยเป็น None)
        last_log = (
            TelemetryLog.objects
            .select_related("device")
            .filter(visit=visit)
            .order_by("-ts")
            .first()
        )
        device = last_log.device if last_log and last_log.device else None

        TelemetryLog.objects.create(
            visit=visit,
            device=device,
            ts=timezone.now(),
            lat=lat,
            lng=lng,
        )

        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    
@login_required
@require_GET
def monitor_sparklines_api(request):
    """
<<<<<<< HEAD
    GET /monitor/api/sparklines/?visit_ids=1,2,3
=======
    GET /queues/monitor/api/sparklines/?visit_ids=1,2,3
>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16
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
    )

    # 3) สร้าง Queue
    priority_map = {"RED": 1, "YELLOW": 2, "GREEN": 3}
    Queue.objects.create(
        visit=visit,
        status="WAITING",
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
        citizen_id=cid,   # ถ้า field ไม่ชื่อ citizen_id ให้แก้
        hn=hn,            # ถ้าไม่มี hn ให้ลบ
    )

    # --- 3) สร้าง Visit ---
    sev_choices = ["RED", "YELLOW", "GREEN"]
    sev = random.choices(sev_choices, weights=[1, 3, 6], k=1)[0]  # GREEN เจอบ่อยกว่า

    visit = Visit.objects.create(
        patient=patient,
        final_severity=sev,
        triaged_at=timezone.now(),  # ถ้าไม่อยากให้เหมือนคัดกรองแล้ว ลบบรรทัดนี้ได้
    )

    # --- 4) สร้าง Queue (WAITING) ---
    priority_map = {"RED": 1, "YELLOW": 2, "GREEN": 3}
    q = Queue.objects.create(
        visit=visit,
        status="WAITING",
        priority=priority_map.get(sev, 3),
    )

    return JsonResponse({
        "ok": True,
        "patient_id": patient.id,
        "visit_id": visit.id,
        "queue_id": q.id,
        "severity": sev,
    })

