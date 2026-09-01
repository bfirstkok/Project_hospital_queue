from django.utils import timezone

from .models import StaffDuty


class StaffActivityMiddleware:
    """Record today's attendance and recent activity for signed-in staff."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and user.is_active:
            now = timezone.now()
            duty, created = StaffDuty.objects.get_or_create(
                user=user,
                duty_date=timezone.localdate(now),
                defaults={
                    "is_present": True,
                    "checked_in_at": now,
                    "last_seen_at": now,
                },
            )
            # A manually checked-out member stays off duty until somebody
            # checks them in again from the personnel page.
            if not created and duty.is_present:
                StaffDuty.objects.filter(pk=duty.pk).update(last_seen_at=now)

        return self.get_response(request)
