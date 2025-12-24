from django.urls import path
from . import views

urlpatterns = [
    path("", views.opd_list, name="opd_list"),
    path("visit/<int:visit_id>/assessment/", views.visit_assessment, name="visit_assessment"),
    path("monitor/", views.post_opd_monitor, name="post_opd_monitor"),
    path("monitor/api/latest/", views.post_opd_monitor_api, name="post_opd_monitor_api"),

]
