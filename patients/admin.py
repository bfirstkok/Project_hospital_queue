# patients/admin.py
from django.contrib import admin
from .models import Appointment, Patient

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "hn",
        "national_id",
        "first_name",
        "last_name",
        "gender",
        "age",
        "phone",
    )
    search_fields = ("hn", "national_id", "first_name", "last_name", "phone")
    list_filter = ("gender",)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "date", "time", "status", "created_at")
    list_filter = ("status", "date")
    search_fields = ("patient__hn", "patient__national_id", "patient__first_name", "patient__last_name", "note")
