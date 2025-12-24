from django.urls import path
from django.views.generic import RedirectView
from . import views

<<<<<<< HEAD
=======
# ✅ FOLLOWUP monitor อยู่ที่ opd
from opd import views as opd_views

>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16
urlpatterns = [
    path("", views.queue_list, name="queue_list"),

    # queue actions
    path("triage/<int:visit_id>/", views.triage_visit, name="triage_visit"),
    path("call/<int:visit_id>/", views.call_visit, name="call_visit"),
<<<<<<< HEAD

    # optional manual location update (ทางเลือก B)
    path("location/<int:visit_id>/", views.update_location, name="update_location"),

    # monitor
    path("monitor/", views.monitor_dashboard, name="monitor_dashboard"),
    path("monitor/api/latest/", views.monitor_latest_api, name="monitor_latest_api"),
    path("monitor/visit/<int:visit_id>/", views.monitor_visit_detail, name="monitor_visit_detail"),
    path("monitor/api/summary/", views.monitor_summary_api, name="monitor_summary_api"),
=======
    path("location/<int:visit_id>/", views.update_location, name="update_location"),

    # ✅ /queues/monitor/ = FOLLOWUP monitor (หลัง OPD)
    path("monitor/", opd_views.post_opd_monitor, name="monitor_dashboard"),
    path("monitor/api/latest/", opd_views.post_opd_monitor_api, name="monitor_latest_api"),
    path("monitor/visit/<int:visit_id>/", opd_views.post_opd_visit_detail, name="followup_visit_detail"),
    path("monitor/demo/push/<int:visit_id>/", opd_views.post_opd_demo_push_telemetry, name="followup_demo_push"),

    # ✅ monitor เดิม (WAITING) ย้ายไป /queues/monitor/waiting/
    path("monitor/waiting/", views.monitor_dashboard, name="waiting_monitor_dashboard"),
    path("monitor/waiting/api/latest/", views.monitor_latest_api, name="waiting_monitor_latest_api"),
    path("monitor/waiting/api/summary/", views.monitor_summary_api, name="waiting_monitor_summary_api"),
    path("monitor/waiting/visit/<int:visit_id>/", views.monitor_visit_detail, name="waiting_monitor_visit_detail"),
    path("monitor/waiting/api/sparklines/", views.monitor_sparklines_api, name="waiting_monitor_sparklines_api"),
>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16

    # map
    path("map/", views.map_view, name="map_view"),

    # iot api
    path("api/iot/telemetry/", views.iot_telemetry, name="iot_telemetry"),

<<<<<<< HEAD
    path("monitor/api/sparklines/", views.monitor_sparklines_api, name="monitor_sparklines_api"),

    path("demo/create/", views.demo_create_visit_queue, name="demo_create_visit_queue"),

    path("dashboard/api/demo-create/", views.dashboard_demo_create, name="dashboard_demo_create"),

    path("patients/", RedirectView.as_view(url="/patients/register/", permanent=False)),
    
    
    
=======
    # demo
    path("demo/create/", views.demo_create_visit_queue, name="demo_create_visit_queue"),
    path("dashboard/api/demo-create/", views.dashboard_demo_create, name="dashboard_demo_create"),

    path("patients/", RedirectView.as_view(url="/patients/register/", permanent=False)),
>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16
]
