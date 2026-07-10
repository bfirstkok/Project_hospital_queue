from django.urls import path
from . import views

urlpatterns = [
    path("register/", views.register_patient, name="register_patient"),
    path("search/", views.patient_search, name="patient_search"),
    path("<int:patient_id>/history/", views.patient_history, name="patient_history"),
    path("<int:patient_id>/appointments/create/", views.create_appointment, name="create_appointment"),
    path("appointments/<int:appointment_id>/status/", views.update_appointment_status, name="update_appointment_status"),
]
