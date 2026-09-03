from django import template

from queues.models import NurseCareAssignment

register = template.Library()


@register.simple_tag
def active_responsible_nurse(visit):
    """Return the currently assigned nurse for a visit, if any."""
    visit_id = getattr(visit, "pk", None)
    if not visit_id:
        return None

    assignment = (
        NurseCareAssignment.objects
        .select_related("nurse")
        .filter(visit_id=visit_id, is_active=True)
        .order_by("-assigned_at")
        .first()
    )
    return assignment.nurse if assignment else None
