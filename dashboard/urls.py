from django.urls import path
from .views import ai_evaluation_view, dashboard_view, waiting_time_report, waiting_time_report_csv

app_name = "dashboard"

urlpatterns = [
    path("", dashboard_view, name="home"),
    path("ai-evaluation/", ai_evaluation_view, name="ai_evaluation"),
    path("reports/waiting-time/", waiting_time_report, name="waiting_time_report"),
    path("reports/waiting-time.csv", waiting_time_report_csv, name="waiting_time_report_csv"),
]
