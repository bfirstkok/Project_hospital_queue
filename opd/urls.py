from django.urls import path
from . import views
from accounts.access import Capability, capability_required

urlpatterns = [
    path("rooms/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_room_select), name="opd_room_select"),
    path("", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_list), name="opd_list"),
    path("api/list/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_list_api), name="opd_list_api"),
    path("visit/<int:visit_id>/select-doctor/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.select_examiner), name="select_examiner"),
    path("visit/<int:visit_id>/assessment/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.visit_assessment), name="visit_assessment"),
    path("visit/<int:visit_id>/detail/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_visit_detail), name="opd_visit_detail"),
    path("monitor/", capability_required(Capability.MONITOR_PATIENT)(views.post_opd_monitor), name="post_opd_monitor"),
    path("monitor/api/latest/", capability_required(Capability.MONITOR_PATIENT)(views.post_opd_monitor_api), name="post_opd_monitor_api"),

]
