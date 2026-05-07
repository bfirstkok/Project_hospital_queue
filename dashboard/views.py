from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
import csv
from pathlib import Path
from queues.models import Queue, Visit

@login_required
def dashboard_view(request):
    waiting = Queue.objects.filter(status="WAITING")
    called = Queue.objects.filter(status="CALLED")

    context = {
        "waiting_total": waiting.count(),
        "called_total": called.count(),
        "red_total": waiting.filter(priority=1).count(),
        "yellow_total": waiting.filter(priority=2).count(),
        "green_total": waiting.filter(priority=3).count(),
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

    return render(request, "dashboard/ai_evaluation.html", {
        "metrics": metrics,
        "confusion": confusion,
        "metrics_path": metrics_path,
        "confusion_path": confusion_path,
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
    severity_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}

    for visit in visits:
        triage_wait = _minutes_between(visit.registered_at, visit.triaged_at)
        called_wait = _minutes_between(visit.registered_at, visit.called_at)
        status = getattr(getattr(visit, "queue", None), "status", "-")
        severity_counts[visit.final_severity] = severity_counts.get(visit.final_severity, 0) + 1

        if triage_wait is not None:
            triage_minutes.append(triage_wait)
        if called_wait is not None:
            called_minutes.append(called_wait)

        rows.append({
            "visit": visit,
            "patient": visit.patient,
            "status": status,
            "triage_wait": triage_wait,
            "called_wait": called_wait,
        })

    avg_triage = round(sum(triage_minutes) / len(triage_minutes), 2) if triage_minutes else None
    avg_called = round(sum(called_minutes) / len(called_minutes), 2) if called_minutes else None

    return render(request, "dashboard/waiting_time_report.html", {
        "rows": rows,
        "avg_triage": avg_triage,
        "avg_called": avg_called,
        "severity_counts": severity_counts,
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
            visit.final_severity,
            queue.status if queue else "",
            visit.registered_at,
            visit.triaged_at,
            visit.called_at,
            _minutes_between(visit.registered_at, visit.triaged_at),
            _minutes_between(visit.registered_at, visit.called_at),
        ])

    return response
