from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path("", views.queue_list, name="queue_list"),

    # queue actions
    path("triage/<int:visit_id>/", views.triage_visit, name="triage_visit"),
    path("call/<int:visit_id>/", views.call_visit, name="call_visit"),
    path("monitor/", views.monitor_dashboard, name="monitor_dashboard"),
    path("location/<int:visit_id>/", views.update_location, name="update_location"),

    # monitor
    path("monitor/", views.monitor_dashboard, name="monitor_dashboard"),
    path("monitor/api/latest/", views.monitor_latest_api, name="monitor_latest_api"),
    path("monitor/visit/<int:visit_id>/", views.monitor_visit_detail, name="monitor_visit_detail"),
    path("monitor/api/summary/", views.monitor_summary_api, name="monitor_summary_api"),

    # map
    path("map/", views.map_view, name="map_view"),

    # iot api
    path("api/iot/telemetry/", views.iot_telemetry, name="iot_telemetry"),

    path("monitor/api/sparklines/", views.monitor_sparklines_api, name="monitor_sparklines_api"),

    path("demo/create/", views.demo_create_visit_queue, name="demo_create_visit_queue"),

    path("dashboard/api/demo-create/", views.dashboard_demo_create, name="dashboard_demo_create"),

    path("patients/", RedirectView.as_view(url="/patients/register/", permanent=False)),
    
    
    
]
