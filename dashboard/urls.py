from django.urls import path
from .views import (
    ai_evaluation_view,
    dashboard_view,
    live_summary_api,
    waiting_time_report,
    waiting_time_report_csv,
    waiting_time_report_pdf,
    waiting_time_report_xls,
)
from accounts.access import Capability, capability_required

app_name = "dashboard"

urlpatterns = [
    path("", capability_required(Capability.VIEW_DASHBOARD)(dashboard_view), name="home"),
    path("ai-evaluation/", capability_required(Capability.VIEW_REPORT)(ai_evaluation_view), name="ai_evaluation"),
    path("api/live-summary/", capability_required(Capability.VIEW_DASHBOARD)(live_summary_api), name="live_summary_api"),
    path("reports/waiting-time/", capability_required(Capability.VIEW_REPORT)(waiting_time_report), name="waiting_time_report"),
    path("reports/waiting-time.csv", capability_required(Capability.VIEW_REPORT)(waiting_time_report_csv), name="waiting_time_report_csv"),
    path("reports/waiting-time.xls", capability_required(Capability.VIEW_REPORT)(waiting_time_report_xls), name="waiting_time_report_xls"),
    path("reports/waiting-time.pdf", capability_required(Capability.VIEW_REPORT)(waiting_time_report_pdf), name="waiting_time_report_pdf"),
]
