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

app_name = "dashboard"

urlpatterns = [
    path("", dashboard_view, name="home"),
    path("ai-evaluation/", ai_evaluation_view, name="ai_evaluation"),
    path("api/live-summary/", live_summary_api, name="live_summary_api"),
    path("reports/waiting-time/", waiting_time_report, name="waiting_time_report"),
    path("reports/waiting-time.csv", waiting_time_report_csv, name="waiting_time_report_csv"),
    path("reports/waiting-time.xls", waiting_time_report_xls, name="waiting_time_report_xls"),
    path("reports/waiting-time.pdf", waiting_time_report_pdf, name="waiting_time_report_pdf"),
]
