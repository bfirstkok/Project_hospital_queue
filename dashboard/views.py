from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
import csv
from pathlib import Path
from queues.models import ConfirmedTriageCase, CriticalAlert, Queue, TriageResult, Visit
from queues.triage import SEVERITY_LEVELS
from ai_triage.services import localize_ai_reason


SEVERITY_LABELS = {
    "RED": "แดง · วิกฤต",
    "PINK": "ชมพู · ฉุกเฉิน",
    "YELLOW": "เหลือง · เร่งด่วน",
    "GREEN": "เขียว · เร่งด่วนน้อย",
    "WHITE": "ขาว · ทั่วไป",
}

STATUS_LABELS = {
    "WAITING_VITALS": "รอวัดสัญญาณชีพ",
    "WAITING_CONFIRMATION": "รอพยาบาลยืนยัน",
    "WAITING_QUEUE": "รอเรียกคิว",
    "CALLED": "เรียกแล้ว",
    "MONITORING": "กำลังเฝ้าระวัง",
    "OBSERVATION_MONITORING": "เฝ้าระวังระหว่างรอ",
    "REASSESSMENT_REQUIRED": "ตรวจอาการทางคลินิก (สถานะเดิม)",
    "EMERGENCY_TRANSFER": "ส่งต่อฉุกเฉิน",
    "IN_ROOM": "อยู่ในห้องตรวจ",
    "OPD_DONE": "ตรวจเสร็จ",
    "DISCHARGED": "เสร็จสิ้น",
    "CANCELLED": "ยกเลิก",
}


def _severity_detail_groups(visits):
    """Build auditable patient details behind each severity summary card."""
    grouped = {severity: [] for severity in SEVERITY_LEVELS}
    for visit in visits:
        severity = visit.final_severity
        if severity not in grouped:
            continue
        triage = getattr(visit, "triage_result", None)
        queue = getattr(visit, "queue", None)
        ai_reason = localize_ai_reason(getattr(triage, "ai_reason", "") or "")
        grouped[severity].append({
            "visit": visit,
            "patient": visit.patient,
            "queue_number": queue.display_number if queue else "-",
            "status_label": STATUS_LABELS.get(
                getattr(queue, "status", ""),
                getattr(queue, "status", "-").replace("_", " ").title(),
            ),
            "ai_reason": ai_reason or "ไม่ได้บันทึกเหตุผลจากระบบ",
            "nurse_note": getattr(triage, "nurse_note", "") or "ยืนยันตามผลเดิม/ไม่ได้ระบุหมายเหตุ",
            "symptoms": visit.note or "ไม่ได้บันทึกอาการสำคัญ",
        })
    return [
        {
            "severity": severity,
            "label": SEVERITY_LABELS[severity],
            "rows": grouped[severity],
            "count": len(grouped[severity]),
        }
        for severity in SEVERITY_LEVELS
    ]

@login_required
def dashboard_view(request):
    waiting = Queue.objects.filter(status="WAITING_QUEUE")
    called = Queue.objects.filter(status="CALLED")
    active = Queue.objects.exclude(status__in=["OPD_DONE", "DISCHARGED", "CANCELLED"])
    active_visits = list(
        Visit.objects
        .select_related("patient", "queue", "triage_result")
        .filter(queue__in=active, final_severity__in=SEVERITY_LEVELS)
        .order_by("queue__priority", "registered_at")
    )
    alerts = (
        CriticalAlert.objects
        .filter(status__in=CriticalAlert.ACTIVE_STATUSES)
        .exclude(visit__queue__status=Queue.Status.EMERGENCY_TRANSFER)
    )
    severity_totals = {
        severity: active.filter(visit__final_severity=severity).count()
        for severity in SEVERITY_LEVELS
    }

    context = {
        "waiting_total": waiting.count(),
        "called_total": called.count(),
        "severity_totals": severity_totals,
        "red_total": severity_totals["RED"],
        "pink_total": severity_totals["PINK"],
        "yellow_total": severity_totals["YELLOW"],
        "green_total": severity_totals["GREEN"],
        "white_total": severity_totals["WHITE"],
        "new_alert_total": alerts.count(),
        "latest_alerts": alerts.select_related("visit", "visit__patient")[:8],
        "severity_groups": _severity_detail_groups(active_visits),
        "severity_context_label": "ผู้ป่วยที่ยังอยู่ในกระบวนการบริการ",
        "now": timezone.now(),
    }
    return render(request, "dashboard/dashboard.html", context)


@login_required
def ai_evaluation_view(request):
    base_dir = Path(__file__).resolve().parent.parent
    metrics_path = base_dir / "ai_triage" / "reports" / "metrics.txt"
    confusion_path = base_dir / "ai_triage" / "reports" / "confusion_matrix.csv"

    metrics = metrics_path.read_text(encoding="utf-8") if metrics_path.exists() else "No metrics file found."
    confusion = confusion_path.read_text(encoding="utf-8") if confusion_path.exists() else "No confusion matrix found."

    cases = list(
        ConfirmedTriageCase.objects
        .select_related("confirmed_by")
        .order_by("-confirmed_at", "-id")
    )
    evaluated = [
        case for case in cases
        if case.ai_severity in SEVERITY_LEVELS
        and case.nurse_severity in SEVERITY_LEVELS
    ]
    total_actual = len(evaluated)
    matches = sum(1 for case in evaluated if case.is_ai_match)
    override_count = total_actual - matches
    accuracy = round((matches / total_actual) * 100, 2) if total_actual else None
    override_rate = round((override_count / total_actual) * 100, 2) if total_actual else None

    eligible_cases = [case for case in cases if case.is_training_eligible]
    eligible_total = len(eligible_cases)
    eligibility_rate = (
        round((eligible_total / len(cases)) * 100, 2)
        if cases else None
    )
    confidence_values = [
        case.confidence for case in evaluated if case.confidence is not None
    ]
    avg_confidence = (
        round((sum(confidence_values) / len(confidence_values)) * 100, 2)
        if confidence_values else None
    )

    severity_rows = []
    for severity in SEVERITY_LEVELS:
        severity_cases = [
            case for case in evaluated if case.nurse_severity == severity
        ]
        severity_matches = sum(1 for case in severity_cases if case.is_ai_match)
        severity_total = len(severity_cases)
        severity_rows.append({
            "severity": severity,
            "label": SEVERITY_LABELS[severity],
            "total": severity_total,
            "matches": severity_matches,
            "overrides": severity_total - severity_matches,
            "accuracy": (
                round((severity_matches / severity_total) * 100, 2)
                if severity_total else None
            ),
            "training_eligible": sum(
                1
                for case in eligible_cases
                if case.nurse_severity == severity
            ),
            "bar_width": (
                round((severity_matches / severity_total) * 100, 2)
                if severity_total else 0
            ),
        })

    confusion_rows = []
    for actual in SEVERITY_LEVELS:
        cells = []
        for predicted in SEVERITY_LEVELS:
            count = sum(
                1
                for case in evaluated
                if case.nurse_severity == actual
                and case.ai_severity == predicted
            )
            cells.append({"predicted": predicted, "count": count})
        confusion_rows.append({
            "actual": actual,
            "label": SEVERITY_LABELS[actual],
            "cells": cells,
        })

    by_month = {}
    for case in evaluated:
        captured = case.confirmed_at or case.captured_at
        key = timezone.localtime(captured).strftime("%Y-%m") if captured else "-"
        bucket = by_month.setdefault(
            key,
            {"total": 0, "matches": 0, "overrides": 0},
        )
        bucket["total"] += 1
        if case.is_ai_match:
            bucket["matches"] += 1
        else:
            bucket["overrides"] += 1
    monthly_rows = [
        {
            "month": month,
            "total": data["total"],
            "matches": data["matches"],
            "overrides": data["overrides"],
            "accuracy": (
                round((data["matches"] / data["total"]) * 100, 2)
                if data["total"] else None
            ),
        }
        for month, data in sorted(by_month.items(), reverse=True)
    ]

    model_buckets = {}
    for case in evaluated:
        model_name = case.model_name or "ไม่ระบุโมเดล"
        bucket = model_buckets.setdefault(
            model_name,
            {"total": 0, "matches": 0, "confidence": []},
        )
        bucket["total"] += 1
        bucket["matches"] += int(case.is_ai_match)
        if case.confidence is not None:
            bucket["confidence"].append(case.confidence)
    model_rows = []
    for model_name, data in sorted(
        model_buckets.items(),
        key=lambda item: item[1]["total"],
        reverse=True,
    ):
        model_rows.append({
            "name": model_name,
            "total": data["total"],
            "accuracy": round((data["matches"] / data["total"]) * 100, 2),
            "avg_confidence": (
                round((sum(data["confidence"]) / len(data["confidence"])) * 100, 2)
                if data["confidence"] else None
            ),
        })

    recent_cases = cases[:40]

    return render(request, "dashboard/ai_evaluation.html", {
        "metrics": metrics,
        "confusion": confusion,
        "actual_total": total_actual,
        "actual_matches": matches,
        "actual_overrides": override_count,
        "actual_accuracy": accuracy,
        "override_rate": override_rate,
        "confirmed_total": len(cases),
        "training_eligible_total": eligible_total,
        "training_not_ready_total": len(cases) - eligible_total,
        "training_eligibility_rate": eligibility_rate,
        "avg_confidence": avg_confidence,
        "severity_rows": severity_rows,
        "confusion_rows": confusion_rows,
        "monthly_rows": monthly_rows,
        "model_rows": model_rows,
        "recent_cases": recent_cases,
    })


def _minutes_between(start, end):
    if not start or not end:
        return None
    minutes = (end - start).total_seconds() / 60
    # Timestamps imported from old/demo data can be out of order.  A negative
    # duration is a data-quality issue, not a waiting time, so keep it out of
    # report averages and exported data.
    if minutes < 0:
        return None
    return round(minutes, 2)


def _format_wait_minutes(minutes):
    if minutes is None:
        return "-"
    total_minutes = max(0, int(round(minutes)))
    days, remainder = divmod(total_minutes, 1440)
    hours, mins = divmod(remainder, 60)
    if days:
        return f"{days} วัน {hours} ชม."
    if hours:
        return f"{hours} ชม. {mins} นาที"
    return f"{mins} นาที"


def _waiting_report_summary_data(limit=500):
    visits = list(
        Visit.objects
        .select_related("patient", "queue", "triage_result")
        .order_by("-registered_at")[:limit]
    )

    severity_counts = {severity: 0 for severity in SEVERITY_LEVELS}
    triage_minutes = []
    called_minutes = []
    confirmation_minutes = []
    bottleneck_totals = {
        "registration_to_triage": [],
        "triage_to_confirmation": [],
        "confirmation_to_call": [],
        "call_to_now_or_done": [],
    }
    monthly = {}
    invalid_intervals = 0

    for visit in visits:
        for start, end in (
            (visit.registered_at, visit.triaged_at),
            (visit.triaged_at, visit.confirmed_at),
            (visit.confirmed_at, visit.called_at),
            (visit.registered_at, visit.called_at),
        ):
            if start and end and end < start:
                invalid_intervals += 1

        triage_wait = _minutes_between(visit.registered_at, visit.triaged_at)
        called_wait = _minutes_between(visit.registered_at, visit.called_at)
        confirmation_wait = _minutes_between(visit.triaged_at, visit.confirmed_at)
        call_wait = _minutes_between(visit.confirmed_at, visit.called_at)

        if visit.final_severity:
            severity_counts[visit.final_severity] = severity_counts.get(visit.final_severity, 0) + 1
        if triage_wait is not None:
            triage_minutes.append(triage_wait)
            bottleneck_totals["registration_to_triage"].append(triage_wait)
        if called_wait is not None:
            called_minutes.append(called_wait)
        if confirmation_wait is not None:
            confirmation_minutes.append(confirmation_wait)
            bottleneck_totals["triage_to_confirmation"].append(confirmation_wait)
        if call_wait is not None:
            bottleneck_totals["confirmation_to_call"].append(call_wait)

        queue = getattr(visit, "queue", None)
        if visit.called_at and getattr(queue, "status", "") not in {"OPD_DONE", "DISCHARGED", "CANCELLED"}:
            call_to_now = _minutes_between(visit.called_at, timezone.now())
            if call_to_now is not None:
                bottleneck_totals["call_to_now_or_done"].append(call_to_now)

        if called_wait is not None:
            month_key = visit.registered_at.strftime("%Y-%m")
            monthly.setdefault(month_key, []).append(called_wait)

    avg_triage = round(sum(triage_minutes) / len(triage_minutes), 2) if triage_minutes else None
    avg_called = round(sum(called_minutes) / len(called_minutes), 2) if called_minutes else None
    avg_confirmation = round(sum(confirmation_minutes) / len(confirmation_minutes), 2) if confirmation_minutes else None

    bottleneck_labels = {
        "registration_to_triage": "ลงทะเบียน → คัดกรอง",
        "triage_to_confirmation": "คัดกรอง → พยาบาลยืนยัน",
        "confirmation_to_call": "ยืนยัน → เรียกคิว",
        "call_to_now_or_done": "เรียกคิว → ขั้นตอนปัจจุบัน",
    }
    bottlenecks = []
    for key, values in bottleneck_totals.items():
        avg = round(sum(values) / len(values), 2) if values else None
        bottlenecks.append({
            "name": key,
            "label": bottleneck_labels[key],
            "avg": avg,
            "avg_display": _format_wait_minutes(avg),
            "count": len(values),
        })
    bottlenecks.sort(key=lambda row: row["avg"] or 0, reverse=True)

    monthly_rows = [
        {
            "period": key,
            "avg_called": round(sum(values) / len(values), 2),
            "avg_called_display": _format_wait_minutes(round(sum(values) / len(values), 2)),
            "count": len(values),
        }
        for key, values in sorted(monthly.items(), reverse=True)
        if values
    ][:12]

    return {
        "generated_at": timezone.now(),
        "total": len(visits),
        "avg_triage": avg_triage,
        "avg_triage_display": _format_wait_minutes(avg_triage),
        "avg_called": avg_called,
        "avg_called_display": _format_wait_minutes(avg_called),
        "avg_confirmation": avg_confirmation,
        "avg_confirmation_display": _format_wait_minutes(avg_confirmation),
        "severity_counts": severity_counts,
        "bottlenecks": bottlenecks,
        "monthly_rows": monthly_rows,
        "invalid_intervals": invalid_intervals,
    }


@login_required
def waiting_time_report(request):
    visits = list(
        Visit.objects
        .select_related("patient", "queue", "triage_result")
        .order_by("-registered_at")[:500]
    )

    rows = []
    triage_minutes = []
    called_minutes = []
    confirmation_minutes = []
    opd_minutes = []
    severity_counts = {severity: 0 for severity in SEVERITY_LEVELS}
    daily = {}
    monthly = {}
    bottleneck_totals = {
        "registration_to_triage": [],
        "triage_to_confirmation": [],
        "confirmation_to_call": [],
        "call_to_now_or_done": [],
    }
    invalid_intervals = 0

    for visit in visits:
        for start, end in (
            (visit.registered_at, visit.triaged_at),
            (visit.triaged_at, visit.confirmed_at),
            (visit.confirmed_at, visit.called_at),
            (visit.registered_at, visit.called_at),
        ):
            if start and end and end < start:
                invalid_intervals += 1

        triage_wait = _minutes_between(visit.registered_at, visit.triaged_at)
        called_wait = _minutes_between(visit.registered_at, visit.called_at)
        confirmation_wait = _minutes_between(visit.triaged_at, visit.confirmed_at)
        call_wait = _minutes_between(visit.confirmed_at, visit.called_at)
        status = getattr(getattr(visit, "queue", None), "status", "-") or "-"
        if visit.final_severity:
            severity_counts[visit.final_severity] = severity_counts.get(visit.final_severity, 0) + 1

        if triage_wait is not None:
            triage_minutes.append(triage_wait)
            bottleneck_totals["registration_to_triage"].append(triage_wait)
        if called_wait is not None:
            called_minutes.append(called_wait)
        if confirmation_wait is not None:
            confirmation_minutes.append(confirmation_wait)
            bottleneck_totals["triage_to_confirmation"].append(confirmation_wait)
        if call_wait is not None:
            bottleneck_totals["confirmation_to_call"].append(call_wait)
        if visit.called_at:
            open_end = timezone.now()
            call_to_end = _minutes_between(visit.called_at, open_end)
            if call_to_end is not None and status not in {"OPD_DONE", "DISCHARGED", "CANCELLED"}:
                opd_minutes.append(call_to_end)
                bottleneck_totals["call_to_now_or_done"].append(call_to_end)

        day_key = visit.registered_at.strftime("%Y-%m-%d")
        month_key = visit.registered_at.strftime("%Y-%m")
        for bucket in (daily.setdefault(day_key, []), monthly.setdefault(month_key, [])):
            if called_wait is not None:
                bucket.append(called_wait)

        rows.append({
            "visit": visit,
            "patient": visit.patient,
            "status": status,
            "status_label": STATUS_LABELS.get(status, status.replace("_", " ").title()),
            "triage_wait": triage_wait,
            "triage_wait_display": _format_wait_minutes(triage_wait),
            "called_wait": called_wait,
            "called_wait_display": _format_wait_minutes(called_wait),
            "confirmation_wait": confirmation_wait,
            "confirmation_wait_display": _format_wait_minutes(confirmation_wait),
        })

    avg_triage = round(sum(triage_minutes) / len(triage_minutes), 2) if triage_minutes else None
    avg_called = round(sum(called_minutes) / len(called_minutes), 2) if called_minutes else None
    avg_confirmation = round(sum(confirmation_minutes) / len(confirmation_minutes), 2) if confirmation_minutes else None
    bottlenecks = []
    bottleneck_labels = {
        "registration_to_triage": "ลงทะเบียน → คัดกรอง",
        "triage_to_confirmation": "คัดกรอง → พยาบาลยืนยัน",
        "confirmation_to_call": "ยืนยัน → เรียกคิว",
        "call_to_now_or_done": "เรียกคิว → ขั้นตอนปัจจุบัน",
    }
    for key, values in bottleneck_totals.items():
        bottlenecks.append({
            "name": key,
            "label": bottleneck_labels[key],
            "avg": round(sum(values) / len(values), 2) if values else None,
            "avg_display": _format_wait_minutes(round(sum(values) / len(values), 2)) if values else "-",
            "count": len(values),
        })
    bottlenecks.sort(key=lambda row: row["avg"] or 0, reverse=True)
    daily_rows = [
        {
            "period": key,
            "avg_called": round(sum(values) / len(values), 2),
            "avg_called_display": _format_wait_minutes(round(sum(values) / len(values), 2)),
            "count": len(values),
        }
        for key, values in sorted(daily.items(), reverse=True)
        if values
    ][:31]
    monthly_rows = [
        {
            "period": key,
            "avg_called": round(sum(values) / len(values), 2),
            "avg_called_display": _format_wait_minutes(round(sum(values) / len(values), 2)),
            "count": len(values),
        }
        for key, values in sorted(monthly.items(), reverse=True)
        if values
    ][:12]

    severity_max = max(severity_counts.values()) if severity_counts else 0
    severity_chart_rows = [
        {
            "code": severity,
            "label": SEVERITY_LABELS.get(severity, severity),
            "count": severity_counts.get(severity, 0),
            "width": round((severity_counts.get(severity, 0) / severity_max) * 100, 1) if severity_max else 0,
        }
        for severity in SEVERITY_LEVELS
    ]
    bottleneck_max = max((row["avg"] or 0 for row in bottlenecks), default=0)
    bottleneck_chart_rows = [
        {
            **row,
            "width": round(((row["avg"] or 0) / bottleneck_max) * 100, 1) if bottleneck_max else 0,
        }
        for row in bottlenecks
    ]
    monthly_chart_source = list(reversed(monthly_rows[:6]))
    monthly_max = max((row["avg_called"] or 0 for row in monthly_chart_source), default=0)
    monthly_chart_rows = [
        {
            **row,
            "width": round(((row["avg_called"] or 0) / monthly_max) * 100, 1) if monthly_max else 0,
        }
        for row in monthly_chart_source
    ]

    return render(request, "dashboard/waiting_time_report.html", {
        "rows": rows,
        "avg_triage": avg_triage,
        "avg_triage_display": _format_wait_minutes(avg_triage),
        "avg_called": avg_called,
        "avg_called_display": _format_wait_minutes(avg_called),
        "avg_confirmation": avg_confirmation,
        "avg_confirmation_display": _format_wait_minutes(avg_confirmation),
        "severity_counts": severity_counts,
        "bottlenecks": bottlenecks,
        "daily_rows": daily_rows,
        "monthly_rows": monthly_rows,
        "total": len(rows),
        "invalid_intervals": invalid_intervals,
        "severity_chart_rows": severity_chart_rows,
        "bottleneck_chart_rows": bottleneck_chart_rows,
        "monthly_chart_rows": monthly_chart_rows,
        "severity_groups": _severity_detail_groups(visits),
        "severity_context_label": "ผู้ป่วยในรายงานล่าสุดสูงสุด 500 Visit",
    })


@login_required
def waiting_time_report_csv(request):
    summary = _waiting_report_summary_data()

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="hospital_summary_report.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)

    writer.writerow(["รายงานสรุปประสิทธิภาพบริการ"])
    writer.writerow(["สร้างเมื่อ", timezone.localtime(summary["generated_at"]).strftime("%Y-%m-%d %H:%M")])
    writer.writerow([])
    writer.writerow(["ตัวชี้วัด", "ค่า"])
    writer.writerow(["จำนวน Visit", summary["total"]])
    writer.writerow(["เฉลี่ย ลงทะเบียน → คัดกรอง (นาที)", summary["avg_triage"] if summary["avg_triage"] is not None else ""])
    writer.writerow(["เฉลี่ย คัดกรอง → ยืนยันผล (นาที)", summary["avg_confirmation"] if summary["avg_confirmation"] is not None else ""])
    writer.writerow(["เฉลี่ย ลงทะเบียน → เรียกคิว (นาที)", summary["avg_called"] if summary["avg_called"] is not None else ""])
    writer.writerow(["จุดข้อมูลเวลาไม่ถูกลำดับ", summary["invalid_intervals"]])

    writer.writerow([])
    writer.writerow(["จำนวนผู้ป่วยแยกตามระดับ", "จำนวน"])
    for severity in SEVERITY_LEVELS:
        writer.writerow([SEVERITY_LABELS.get(severity, severity), summary["severity_counts"].get(severity, 0)])

    writer.writerow([])
    writer.writerow(["ช่วงบริการ", "เวลาเฉลี่ย (นาที)", "จำนวนข้อมูล"])
    for row in summary["bottlenecks"]:
        writer.writerow([row["label"], row["avg"] if row["avg"] is not None else "", row["count"]])

    writer.writerow([])
    writer.writerow(["เดือน", "เวลารอรวมเฉลี่ย (นาที)", "จำนวน Visit"])
    for row in summary["monthly_rows"]:
        writer.writerow([row["period"], row["avg_called"], row["count"]])

    return response


@login_required
def waiting_time_report_xls(request):
    summary = _waiting_report_summary_data()
    response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="hospital_summary_report.xls"'
    response.write("\ufeff")
    response.write("<html><head><meta charset='utf-8'><style>")
    response.write("body{font-family:Arial,sans-serif}table{border-collapse:collapse;margin-bottom:18px}th,td{border:1px solid #b8c8cc;padding:7px 10px}th{background:#eaf4f2}.title{font-size:20px;font-weight:bold}")
    response.write("</style></head><body>")
    response.write("<div class='title'>รายงานสรุปประสิทธิภาพบริการ</div>")
    response.write(f"<p>สร้างเมื่อ {timezone.localtime(summary['generated_at']):%Y-%m-%d %H:%M}</p>")

    response.write("<table><tr><th>ตัวชี้วัด</th><th>ค่า</th></tr>")
    overview = [
        ("จำนวน Visit", summary["total"]),
        ("ลงทะเบียน → คัดกรอง เฉลี่ย", summary["avg_triage_display"]),
        ("คัดกรอง → ยืนยันผล เฉลี่ย", summary["avg_confirmation_display"]),
        ("ลงทะเบียน → เรียกคิว เฉลี่ย", summary["avg_called_display"]),
        ("จุดข้อมูลเวลาไม่ถูกลำดับ", summary["invalid_intervals"]),
    ]
    for label, value in overview:
        response.write(f"<tr><td>{label}</td><td>{value}</td></tr>")
    response.write("</table>")

    response.write("<table><tr><th>ระดับผู้ป่วย</th><th>จำนวน</th></tr>")
    for severity in SEVERITY_LEVELS:
        response.write(f"<tr><td>{SEVERITY_LABELS.get(severity, severity)}</td><td>{summary['severity_counts'].get(severity, 0)}</td></tr>")
    response.write("</table>")

    response.write("<table><tr><th>ช่วงบริการ</th><th>เวลาเฉลี่ย</th><th>จำนวนข้อมูล</th></tr>")
    for row in summary["bottlenecks"]:
        response.write(f"<tr><td>{row['label']}</td><td>{row['avg_display']}</td><td>{row['count']}</td></tr>")
    response.write("</table>")

    response.write("<table><tr><th>เดือน</th><th>เวลารอรวมเฉลี่ย</th><th>Visit</th></tr>")
    for row in summary["monthly_rows"]:
        response.write(f"<tr><td>{row['period']}</td><td>{row['avg_called_display']}</td><td>{row['count']}</td></tr>")
    response.write("</table></body></html>")
    return response


def _simple_pdf(lines):
    escaped = []
    for line in lines:
        safe = str(line).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        escaped.append(safe.encode("latin-1", "replace").decode("latin-1"))
    text = ["BT", "/F1 12 Tf", "50 790 Td"]
    for idx, line in enumerate(escaped[:45]):
        if idx:
            text.append("0 -16 Td")
        text.append(f"({line}) Tj")
    text.append("ET")
    stream = "\n".join(text).encode("latin-1", "replace")
    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
    return bytes(pdf)


@login_required
def waiting_time_report_pdf(request):
    summary = _waiting_report_summary_data()
    severity_line = " / ".join(
        f"{severity} {summary['severity_counts'].get(severity, 0)}"
        for severity in SEVERITY_LEVELS
    )
    bottleneck_english = {
        "registration_to_triage": "Registration to triage",
        "triage_to_confirmation": "Triage to nurse confirmation",
        "confirmation_to_call": "Confirmation to queue call",
        "call_to_now_or_done": "Queue call to current stage",
    }
    def minutes_text(value):
        return f"{value:.2f} min" if value is not None else "-"

    lines = [
        "Hospital Service Summary Report",
        f"Generated: {timezone.localtime(summary['generated_at']):%Y-%m-%d %H:%M}",
        "",
        "OVERVIEW",
        f"Total visits: {summary['total']}",
        f"Average registration-to-triage: {minutes_text(summary['avg_triage'])}",
        f"Average triage-to-confirmation: {minutes_text(summary['avg_confirmation'])}",
        f"Average registration-to-call: {minutes_text(summary['avg_called'])}",
        f"Invalid timestamp intervals excluded: {summary['invalid_intervals']}",
        "",
        "PATIENT SEVERITY TOTALS",
        severity_line,
        "",
        "SERVICE BOTTLENECKS",
    ]
    for row in summary["bottlenecks"]:
        lines.append(
            f"{bottleneck_english.get(row['name'], row['name'])}: "
            f"{minutes_text(row['avg'])} ({row['count']} samples)"
        )
    lines.extend(["", "MONTHLY WAITING-TIME TREND"])
    for row in summary["monthly_rows"][:6]:
        lines.append(
            f"{row['period']}: {minutes_text(row['avg_called'])} "
            f"({row['count']} visits)"
        )

    response = HttpResponse(_simple_pdf(lines), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="hospital_summary_report.pdf"'
    return response


@login_required
def live_summary_api(request):
    waiting = Queue.objects.filter(status=Queue.Status.WAITING_QUEUE)
    called = Queue.objects.filter(status=Queue.Status.CALLED)
    alerts = (
        CriticalAlert.objects
        .filter(status__in=CriticalAlert.ACTIVE_STATUSES)
        .exclude(visit__queue__status=Queue.Status.EMERGENCY_TRANSFER)
        .select_related("visit", "visit__patient", "visit__queue")
        .order_by("-created_at")[:10]
    )
    return JsonResponse({
        "ok": True,
        "server_time": timezone.now().isoformat(),
        "waiting_total": waiting.count(),
        "called_total": called.count(),
        "severity": {
            severity: Queue.objects.filter(
                visit__final_severity=severity,
            ).exclude(status__in=["OPD_DONE", "DISCHARGED", "CANCELLED"]).count()
            for severity in SEVERITY_LEVELS
        },
        "new_alert_total": (
            CriticalAlert.objects
            .filter(status__in=CriticalAlert.ACTIVE_STATUSES)
            .exclude(visit__queue__status=Queue.Status.EMERGENCY_TRANSFER)
            .count()
        ),
        "alerts": [
            {
                "id": alert.id,
                "visit_id": alert.visit_id,
                "patient": f"{alert.visit.patient.first_name} {alert.visit.patient.last_name}",
                "queue_number": alert.visit.queue.display_number,
                "queue_status": alert.visit.queue.status,
                "status": alert.status,
                "type": alert.alert_type,
                "message": alert.message,
                "value": alert.value,
                "threshold": alert.threshold,
                "created_at": alert.created_at.isoformat(),
            }
            for alert in alerts
        ],
    })
