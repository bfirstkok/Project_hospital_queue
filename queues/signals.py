from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .care_workload import TERMINAL_CARE_STATUSES, end_assignment_for_visit
from .models import CriticalAlert, Queue


@receiver(post_save, sender=Queue)
def close_nurse_assignment_for_terminal_queue(sender, instance, **kwargs):
    """Return nurse capacity automatically when a care episode reaches a terminal state."""
    if instance.status not in TERMINAL_CARE_STATUSES:
        return
    end_assignment_for_visit(
        instance.visit,
        when=timezone.now(),
        reason=f"ปิดการมอบหมายอัตโนมัติเมื่อสถานะคิวเป็น {instance.status}",
    )


@receiver(post_save, sender=CriticalAlert)
def suppress_non_wearable_critical_alert(sender, instance, created, **kwargs):
    """Keep the live critical-alert feed for wearable/IoT readings only.

    Manual triage vitals are still stored in VitalSign and the workflow audit log,
    but they must not create an unresolved CriticalAlert notification.  The two
    current non-wearable producers identify themselves as ``triage`` and
    ``system_test``.  Wearable sources such as ``iot``, ``iot_vitals`` and
    ``system_test_iot`` remain NEW and are shown to the responsible nurse.
    """
    if not created or instance.status != CriticalAlert.Status.NEW:
        return

    source = (instance.source or "").strip().lower()
    if source not in {"triage", "system_test"}:
        return

    CriticalAlert.objects.filter(
        pk=instance.pk,
        status=CriticalAlert.Status.NEW,
    ).update(
        status=CriticalAlert.Status.ACKNOWLEDGED,
        acknowledged_at=timezone.now(),
    )
