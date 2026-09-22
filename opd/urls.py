from django.urls import path
from . import views, workflow_views
from accounts.access import Capability, capability_required

urlpatterns = [
    path("rooms/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_room_select), name="opd_room_select"),
    path("", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_list), name="opd_list"),
    path("api/list/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_list_api), name="opd_list_api"),
    path("visit/<int:visit_id>/select-doctor/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.select_examiner), name="select_examiner"),
    path("visit/<int:visit_id>/assessment/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.visit_assessment), name="visit_assessment"),
    path("visit/<int:visit_id>/detail/", capability_required(Capability.DOCTOR_ASSESSMENT)(views.opd_visit_detail), name="opd_visit_detail"),
    path("visit/<int:visit_id>/care/", capability_required(Capability.CREATE_PRESCRIPTION)(workflow_views.opd_care_plan), name="opd_care_plan"),
    path("prescription/item/<int:item_id>/delete/", capability_required(Capability.CREATE_PRESCRIPTION)(workflow_views.delete_prescription_item), name="delete_prescription_item"),
    path("pharmacy/", capability_required(Capability.VIEW_PHARMACY)(workflow_views.pharmacy_worklist), name="pharmacy_worklist"),
    path("pharmacy/<int:prescription_id>/status/", capability_required(Capability.MANAGE_PHARMACY)(workflow_views.pharmacy_update_status), name="pharmacy_update_status"),
    path("billing/", capability_required(Capability.VIEW_BILLING)(workflow_views.billing_worklist), name="billing_worklist"),
    path("billing/<int:bill_id>/", capability_required(Capability.MANAGE_BILLING)(workflow_views.billing_detail), name="billing_detail"),
    path("billing/<int:bill_id>/pay/", capability_required(Capability.MANAGE_BILLING)(workflow_views.billing_pay), name="billing_pay"),
    path("billing/<int:bill_id>/receipt/", capability_required(Capability.VIEW_BILLING)(workflow_views.billing_receipt), name="billing_receipt"),
    path("certificate/<int:certificate_id>/", capability_required(Capability.ISSUE_MEDICAL_CERTIFICATE)(workflow_views.medical_certificate_print), name="medical_certificate_print"),
    path("monitor/", capability_required(Capability.MONITOR_PATIENT)(views.post_opd_monitor), name="post_opd_monitor"),
    path("monitor/api/latest/", capability_required(Capability.MONITOR_PATIENT)(views.post_opd_monitor_api), name="post_opd_monitor_api"),

]
