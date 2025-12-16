from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from .models import Queue, Visit
from django.views.decorators.http import require_POST


@login_required
def queue_list(request):
    # เรียงตาม priority (แดงก่อน) แล้วตามเวลาเข้าคิว
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
        q.save()

        visit.called_at = timezone.now()
        visit.save()

    return redirect("queue_list")


@login_required
@require_POST
def triage_visit(request, visit_id):
    visit = get_object_or_404(Visit, id=visit_id)
    new_sev = request.POST.get("severity")

    if new_sev in ["RED", "YELLOW", "GREEN"]:
        visit.final_severity = new_sev
        visit.triaged_at = timezone.now()
        visit.save()

        q = visit.queue
        q.priority = {"RED": 1, "YELLOW": 2, "GREEN": 3}[new_sev]
        q.save()

    return redirect("queue_list")

q_items = (
    Queue.objects
    .select_related("visit", "visit__patient", "visit__triage_result")
    .filter(status="WAITING")
    .order_by("priority", "created_at")
)
