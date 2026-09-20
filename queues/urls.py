from django.urls import path
from django.views.generic import RedirectView
from . import views
from . import personnel_views
from . import shift_views
from . import triage_confirmation
from . import transfer_views

# ✅ FOLLOWUP monitor อยู่ที่ opd
from opd import views as opd_views
from accounts.access import Capability, capability_required, superuser_required

urlpatterns = [
    path("", capability_required(Capability.VIEW_QUEUE)(views.queue_list), name="queue_list"),
    path("display/", views.queue_display, name="queue_display"),
    path("waiting-vitals/", capability_required(Capability.RECORD_VITALS)(views.waiting_vitals), name="waiting_vitals"),
    path("waiting-confirmation/", capability_required(Capability.CONFIRM_TRIAGE)(triage_confirmation.waiting_confirmation), name="waiting_confirmation"),
    path("emergency-transfers/", capability_required(Capability.VIEW_EMERGENCY)(views.emergency_transfers), name="emergency_transfers"),
    path("personnel/", capability_required(Capability.VIEW_PERSONNEL)(personnel_views.personnel_dashboard), name="personnel_dashboard"),
    path("shifts/", capability_required(Capability.VIEW_SHIFT_SCHEDULE)(shift_views.shift_schedule), name="shift_schedule"),
    path("personnel/heartbeat/", personnel_views.staff_heartbeat, name="staff_heartbeat"),
    path("personnel/photo/<int:profile_id>/", personnel_views.staff_photo, name="staff_photo"),

    # queue actions
    path("assessment/<int:visit_id>/", capability_required(Capability.RECORD_VITALS)(views.nurse_triage_assessment), name="nurse_triage_assessment"),
    path("return-to-vitals/<int:visit_id>/", capability_required(Capability.CONFIRM_TRIAGE)(views.return_to_waiting_vitals), name="return_to_waiting_vitals"),
    path("triage/<int:visit_id>/", capability_required(Capability.CONFIRM_TRIAGE)(triage_confirmation.triage_visit), name="triage_visit"),
    path("call/<int:visit_id>/", capability_required(Capability.MANAGE_QUEUE)(views.call_visit), name="call_visit"),
    path("adjust/<int:visit_id>/", capability_required(Capability.MANAGE_QUEUE)(views.adjust_queue), name="adjust_queue"),
    path("transfer/<int:visit_id>/", capability_required(Capability.TRANSFER_PATIENT)(transfer_views.transfer_patient), name="transfer_patient"),
    path("monitoring/<int:visit_id>/", capability_required(Capability.MONITOR_PATIENT)(views.send_to_monitoring), name="send_to_monitoring"),
    path("discharge/<int:visit_id>/", capability_required(Capability.END_MONITORING)(views.discharge_visit), name="discharge_visit"),
    path("cancel/<int:visit_id>/", capability_required(Capability.MANAGE_QUEUE)(views.cancel_queue), name="cancel_queue"),
    path("api/update-severity/<int:visit_id>/", capability_required(Capability.CONFIRM_TRIAGE)(views.update_severity_api), name="update_severity_api"),
    path("api/alerts/<int:alert_id>/ack/", capability_required(Capability.ACKNOWLEDGE_ALERT)(views.acknowledge_alert), name="acknowledge_alert"),
    path("api/alerts/<int:alert_id>/review/", capability_required(Capability.ACKNOWLEDGE_ALERT)(views.start_alert_review), name="start_alert_review"),
    path("api/alerts/<int:alert_id>/resolve/", capability_required(Capability.ACKNOWLEDGE_ALERT)(views.resolve_alert), name="resolve_alert"),
    path("api/alerts/<int:alert_id>/false-alarm/", capability_required(Capability.ACKNOWLEDGE_ALERT)(views.false_alarm_alert), name="false_alarm_alert"),
    path("api/alerts/<int:alert_id>/escalate/", capability_required(Capability.ACKNOWLEDGE_ALERT)(views.escalate_alert), name="escalate_alert"),
    path("api/alerts/mine/", capability_required(Capability.ACKNOWLEDGE_ALERT)(views.my_critical_alerts), name="my_critical_alerts"),

    # /queues/monitor/ is the waiting-area wearable monitor. Post-OPD
    # monitoring remains available under /opd/monitor/.
    path("monitor/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_dashboard), name="monitor_dashboard"),
    path("monitor/api/latest/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_latest_api), name="monitor_latest_api"),
    path("monitor/api/summary/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_summary_api), name="monitor_summary_api"),
    path("monitor/visit/<int:visit_id>/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_visit_detail), name="followup_visit_detail"),
    path("monitor/demo/push/<int:visit_id>/", superuser_required(opd_views.post_opd_demo_push_telemetry), name="followup_demo_push"),

    # ✅ monitor เดิม (WAITING) ย้ายไป /queues/monitor/waiting/
    path("monitor/waiting/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_dashboard), name="waiting_monitor_dashboard"),
    path("monitor/waiting/api/latest/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_latest_api), name="waiting_monitor_latest_api"),
    path("monitor/waiting/api/summary/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_summary_api), name="waiting_monitor_summary_api"),
    path("monitor/waiting/visit/<int:visit_id>/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_visit_detail), name="waiting_monitor_visit_detail"),
    path("monitor/waiting/api/sparklines/", capability_required(Capability.MONITOR_PATIENT)(views.monitor_sparklines_api), name="waiting_monitor_sparklines_api"),
    path("devices/pairing/", capability_required(Capability.MANAGE_DEVICE)(views.device_pairing), name="device_pairing"),

    # iot api
    path("api/iot/telemetry/", views.iot_telemetry, name="iot_telemetry"),

    # demo
    path("demo/create/", superuser_required(views.demo_create_visit_queue), name="demo_create_visit_queue"),
    path("dashboard/api/demo-create/", superuser_required(views.dashboard_demo_create), name="dashboard_demo_create"),

    path("patients/", RedirectView.as_view(url="/patients/register/", permanent=False)),
]
