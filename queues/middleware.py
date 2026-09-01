from django.utils import timezone

from .models import StaffDuty


class StaffActivityMiddleware:
    """Refresh activity only; login is not treated as attendance."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and user.is_active:
            now = timezone.now()
            duty = StaffDuty.objects.filter(
                user=user,
                duty_date=timezone.localdate(now),
                is_present=True,
            ).first()
            if duty:
                StaffDuty.objects.filter(pk=duty.pk).update(last_seen_at=now)

        return self.get_response(request)
