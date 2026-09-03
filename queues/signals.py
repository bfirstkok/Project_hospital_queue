from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .care_workload import TERMINAL_CARE_STATUSES
from .models import NurseCareAssignment, Queue


@receiver(post_save, sender=Queue)
def close_nurse_assignment_for_terminal_queue(sender, instance, **kwargs):
    """Return nurse capacity automatically when a care episode reaches a terminal state."""
    if instance.status not in TERMINAL_CARE_STATUSES:
        return
    NurseCareAssignment.objects.filter(
        visit=instance.visit,
        is_active=True,
    ).update(
        is_active=False,
        ended_at=timezone.now(),
    )
