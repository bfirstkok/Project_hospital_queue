from queues.models import StaffProfile

from .access import SIMULATED_ROLE_SESSION_KEY


class SuperuserRoleSimulationMiddleware:
    """Attach a session-scoped simulated hospital role to real superusers only."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False):
            role = request.session.get(SIMULATED_ROLE_SESSION_KEY)
            if role in StaffProfile.Role.values:
                user._simulated_hospital_role = role
            else:
                request.session.pop(SIMULATED_ROLE_SESSION_KEY, None)
                user._simulated_hospital_role = None
        return self.get_response(request)
