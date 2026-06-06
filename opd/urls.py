from django.urls import path
from . import views

urlpatterns = [
    path("rooms/", views.opd_room_select, name="opd_room_select"),
    path("", views.opd_list, name="opd_list"),
    path("api/list/", views.opd_list_api, name="opd_list_api"),
    path("visit/<int:visit_id>/assessment/", views.visit_assessment, name="visit_assessment"),
    path("visit/<int:visit_id>/detail/", views.opd_visit_detail, name="opd_visit_detail"),
    path("monitor/", views.post_opd_monitor, name="post_opd_monitor"),
    path("monitor/api/latest/", views.post_opd_monitor_api, name="post_opd_monitor_api"),

]
