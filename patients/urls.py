from django.urls import path
from . import views

urlpatterns = [
    path("register/", views.register_patient, name="register_patient"),
    path("search/", views.register_patient, name="patient_search"),
]
