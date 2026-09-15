from django.urls import path
from . import views
from accounts.access import Capability, capability_required

urlpatterns = [
    path("register/", capability_required(Capability.REGISTER_PATIENT)(views.register_patient), name="register_patient"),
    path("<int:patient_id>/edit/", capability_required(Capability.EDIT_PATIENT)(views.edit_patient), name="edit_patient"),
    path("<int:patient_id>/birth-date/", capability_required(Capability.EDIT_PATIENT)(views.update_patient_birth_date), name="update_patient_birth_date"),
    path("search/", capability_required(Capability.VIEW_PATIENT)(views.patient_search), name="patient_search"),
    path("<int:patient_id>/history/", capability_required(Capability.VIEW_PATIENT)(views.patient_history), name="patient_history"),
    path("<int:patient_id>/appointments/create/", capability_required(Capability.EDIT_PATIENT)(views.create_appointment), name="create_appointment"),
    path("appointments/<int:appointment_id>/status/", capability_required(Capability.EDIT_PATIENT)(views.update_appointment_status), name="update_appointment_status"),
]
