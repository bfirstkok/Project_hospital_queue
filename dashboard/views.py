from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
import csv
from pathlib import Path
from queues.models import CriticalAlert, Queue, TriageResult, Visit

@login_required
def dashboard_view(request):
    waiting = Queue.objects.filter(status="WAITING_QUEUE")
    called = Queue.objects.filter(status="CALLED")
    alerts = CriticalAlert.objects.filter(status=CriticalAlert.Status.NEW)

    context = {
        "waiting_total": waiting.count(),
        "called_total": called.count(),
        "red_total": waiting.filter(priority=1).count(),
        "yellow_total": waiting.filter(priority=2).count(),
        "green_total": waiting.filter(priority=3).count(),
        "new_alert_total": alerts.count(),
        "latest_alerts": alerts.select_related("visit", "visit__patient")[:8],
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

    actual_results = (
        TriageResult.objects
        .exclude(ai_severity__isnull=True)
        .exclude(ai_severity="")
        .exclude(nurse_severity__isnull=True)
        .exclude(nurse_severity="")
        .select_related("visit", "visit__patient")
        .order_by("-created_at")
    )
    total_actual = actual_results.count()
    matches = actual_results.filter(ai_severity=F("nurse_severity")).count() if total_actual else 0
    accuracy = round((matches / total_actual) * 100, 2) if total_actual else None
    override_count = total_actual - matches

    by_month = {}
    for result in actual_results:
        key = result.created_at.strftime("%Y-%m")
        bucket = by_month.setdefault(key, {"total": 0, "matches": 0, "overrides": 0})
        bucket["total"] += 1
        if result.ai_severity == result.nurse_severity:
            bucket["matches"] += 1
        else:
            bucket["overrides"] += 1
    monthly_rows = [
        {
            "month": month,
            "total": data["total"],
            "matches": data["matches"],
            "overrides": data["overrides"],
            "accuracy": round((data["matches"] / data["total"]) * 100, 2) if data["total"] else None,
        }
        for month, data in sorted(by_month.items(), reverse=True)
    ]

    return render(request, "dashboard/ai_evaluation.html", {
        "metrics": metrics,
        "confusion": confusion,
        "metrics_path": metrics_path,
        "confusion_path": confusion_path,
        "actual_total": total_actual,
        "actual_matches": matches,
        "actual_overrides": override_count,
        "actual_accuracy": accuracy,
        "monthly_rows": monthly_rows,
        "recent_overrides": actual_results.exclude(ai_severity=F("nurse_severity"))[:30],
    })


def _minutes_between(start, end):
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 60, 2)


@login_required
def waiting_time_report(request):
    visits = (
        Visit.objects
        .select_related("patient", "queue")
        .order_by("-registered_at")[:500]
    )

    rows = []
    triage_minutes = []
    called_minutes = []
    confirmation_minutes = []
    opd_minutes = []
    severity_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    daily = {}
    monthly = {}
    bottleneck_totals = {
        "registration_to_triage": [],
        "triage_to_confirmation": [],
        "confirmation_to_call": [],
        "call_to_now_or_done": [],
    }

    for visit in visits:
        triage_wait = _minutes_between(visit.registered_at, visit.triaged_at)
        called_wait = _minutes_between(visit.registered_at, visit.called_at)
        confirmation_wait = _minutes_between(visit.triaged_at, visit.confirmed_at)
        call_wait = _minutes_between(visit.confirmed_at, visit.called_at)
        status = getattr(getattr(visit, "queue", None), "status", "-")
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
            "triage_wait": triage_wait,
            "called_wait": called_wait,
            "confirmation_wait": confirmation_wait,
        })

    avg_triage = round(sum(triage_minutes) / len(triage_minutes), 2) if triage_minutes else None
    avg_called = round(sum(called_minutes) / len(called_minutes), 2) if called_minutes else None
    avg_confirmation = round(sum(confirmation_minutes) / len(confirmation_minutes), 2) if confirmation_minutes else None
    bottlenecks = []
    for key, values in bottleneck_totals.items():
        bottlenecks.append({
            "name": key,
            "avg": round(sum(values) / len(values), 2) if values else None,
            "count": len(values),
        })
    bottlenecks.sort(key=lambda row: row["avg"] or 0, reverse=True)
    daily_rows = [
        {"period": key, "avg_called": round(sum(values) / len(values), 2), "count": len(values)}
        for key, values in sorted(daily.items(), reverse=True)
        if values
    ][:31]
    monthly_rows = [
        {"period": key, "avg_called": round(sum(values) / len(values), 2), "count": len(values)}
        for key, values in sorted(monthly.items(), reverse=True)
        if values
    ][:12]

    return render(request, "dashboard/waiting_time_report.html", {
        "rows": rows,
        "avg_triage": avg_triage,
        "avg_called": avg_called,
        "avg_confirmation": avg_confirmation,
        "severity_counts": severity_counts,
        "bottlenecks": bottlenecks,
        "daily_rows": daily_rows,
        "monthly_rows": monthly_rows,
        "total": len(rows),
    })


@login_required
def waiting_time_report_csv(request):
    visits = Visit.objects.select_related("patient", "queue").order_by("-registered_at")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="waiting_time_report.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow([
        "visit_id", "patient_name", "severity", "queue_status",
        "registered_at", "triaged_at", "called_at",
        "registered_to_triaged_min", "registered_to_called_min",
    ])

    for visit in visits:
        queue = getattr(visit, "queue", None)
        writer.writerow([
            visit.id,
            f"{visit.patient.first_name} {visit.patient.last_name}",
            visit.final_severity or "",
            queue.status if queue else "",
            visit.registered_at,
            visit.triaged_at,
            visit.called_at,
            _minutes_between(visit.registered_at, visit.triaged_at),
            _minutes_between(visit.registered_at, visit.called_at),
        ])

    return response


@login_required
def waiting_time_report_xls(request):
    visits = Visit.objects.select_related("patient", "queue").order_by("-registered_at")
    response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="waiting_time_report.xls"'
    response.write("\ufeff")
    response.write("<table><thead><tr>")
    headers = [
        "visit_id", "patient_name", "severity", "queue_status",
        "registered_at", "triaged_at", "confirmed_at", "called_at",
        "registered_to_triaged_min", "registered_to_called_min",
    ]
    for header in headers:
        response.write(f"<th>{header}</th>")
    response.write("</tr></thead><tbody>")
    for visit in visits:
        queue = getattr(visit, "queue", None)
        cells = [
            visit.id,
            f"{visit.patient.first_name} {visit.patient.last_name}",
            visit.final_severity or "",
            queue.status if queue else "",
            visit.registered_at,
            visit.triaged_at or "",
            visit.confirmed_at or "",
            visit.called_at or "",
            _minutes_between(visit.registered_at, visit.triaged_at) or "",
            _minutes_between(visit.registered_at, visit.called_at) or "",
        ]
        response.write("<tr>")
        for cell in cells:
            response.write(f"<td>{cell}</td>")
        response.write("</tr>")
    response.write("</tbody></table>")
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
    visits = Visit.objects.select_related("patient", "queue").order_by("-registered_at")[:40]
    lines = ["Hospital Queue Executive Report", f"Generated: {timezone.now():%Y-%m-%d %H:%M}"]
    severity_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    waits = []
    for visit in visits:
        if visit.final_severity:
            severity_counts[visit.final_severity] = severity_counts.get(visit.final_severity, 0) + 1
        wait = _minutes_between(visit.registered_at, visit.called_at)
        if wait is not None:
            waits.append(wait)
    avg_wait = round(sum(waits) / len(waits), 2) if waits else "-"
    lines.extend([
        f"Total rows: {visits.count()}",
        f"Average registered-to-called: {avg_wait} minutes",
        f"Severity RED/YELLOW/GREEN: {severity_counts['RED']}/{severity_counts['YELLOW']}/{severity_counts['GREEN']}",
        "",
        "Recent visits:",
    ])
    for visit in visits[:30]:
        queue = getattr(visit, "queue", None)
        lines.append(
            f"#{visit.id} {visit.patient.first_name} {visit.patient.last_name} "
            f"{visit.final_severity or '-'} {queue.status if queue else '-'}"
        )
    response = HttpResponse(_simple_pdf(lines), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="waiting_time_report.pdf"'
    return response


@login_required
def live_summary_api(request):
    waiting = Queue.objects.filter(status=Queue.Status.WAITING_QUEUE)
    called = Queue.objects.filter(status=Queue.Status.CALLED)
    alerts = (
        CriticalAlert.objects
        .filter(status=CriticalAlert.Status.NEW)
        .select_related("visit", "visit__patient")
        .order_by("-created_at")[:10]
    )
    return JsonResponse({
        "ok": True,
        "server_time": timezone.now().isoformat(),
        "waiting_total": waiting.count(),
        "called_total": called.count(),
        "severity": {
            "RED": waiting.filter(priority=1).count(),
            "YELLOW": waiting.filter(priority=2).count(),
            "GREEN": waiting.filter(priority=3).count(),
        },
        "new_alert_total": CriticalAlert.objects.filter(status=CriticalAlert.Status.NEW).count(),
        "alerts": [
            {
                "id": alert.id,
                "visit_id": alert.visit_id,
                "patient": f"{alert.visit.patient.first_name} {alert.visit.patient.last_name}",
                "type": alert.alert_type,
                "message": alert.message,
                "value": alert.value,
                "threshold": alert.threshold,
                "created_at": alert.created_at.isoformat(),
            }
            for alert in alerts
        ],
    })
