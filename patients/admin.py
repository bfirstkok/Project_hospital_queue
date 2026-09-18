from django.contrib import admin

from .models import Appointment, Assessment, Patient


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
        "email",
    )
    search_fields = ("hn", "national_id", "first_name", "last_name", "phone", "email")
    list_filter = ("gender",)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "date", "time", "status", "created_at")
    list_filter = ("status", "date")
    search_fields = (
        "patient__hn",
        "patient__national_id",
        "patient__first_name",
        "patient__last_name",
        "note",
    )
    date_hierarchy = "date"


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "assessor", "assessed_at")
    list_filter = ("assessed_at",)
    search_fields = (
        "patient__hn",
        "patient__national_id",
        "patient__first_name",
        "patient__last_name",
        "detail",
        "assessor__username",
        "assessor__first_name",
        "assessor__last_name",
    )
    readonly_fields = ("assessed_at",)
    date_hierarchy = "assessed_at"


# Security-sensitive patient authentication models are intentionally not
# registered here: PatientAccessToken, PatientPin, and OtpChallenge.
# They remain in PostgreSQL but are not exposed for manual editing in Admin.
