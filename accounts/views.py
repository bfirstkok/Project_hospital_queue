from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone


from queues.models import Queue

@login_required
def dashboard(request):
    now = timezone.now()

    waiting_total = Queue.objects.filter(status="WAITING").count()
    called_total  = Queue.objects.filter(status="CALLED").count()

    # แยกสีจาก priority (RED=1, YELLOW=2, GREEN=3)
    red_total    = Queue.objects.filter(status="WAITING", priority=1).count()
    yellow_total = Queue.objects.filter(status="WAITING", priority=2).count()
    green_total  = Queue.objects.filter(status="WAITING", priority=3).count()

    return render(request, "dashboard/dashboard.html", {
        "now": now,
        "waiting_total": waiting_total,
        "called_total": called_total,
        "red_total": red_total,
        "yellow_total": yellow_total,
        "green_total": green_total,
    })
    
