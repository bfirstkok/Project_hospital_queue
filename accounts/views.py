from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout as auth_logout
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache


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


@never_cache
def custom_logout(request):
    """
    Custom logout view ที่เคลียร์ session และป้องกัน cache
    """
    auth_logout(request)
    response = redirect('login')
    # เพิ่ม headers เพื่อป้องกัน browser cache
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response

