from django.contrib import admin

from .models import (
    Appointment,
    Assessment,
    OtpChallenge,
    Patient,
    PatientAccessToken,
    PatientPin,
)


class ReadOnlySecurityAdmin(admin.ModelAdmin):
    """Expose security-related records for inspection without allowing edits."""

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


@admin.register(PatientAccessToken)
class PatientAccessTokenAdmin(ReadOnlySecurityAdmin):
    list_display = ("id", "patient", "expires_at", "created_at", "last_used_at")
    list_filter = ("expires_at", "created_at")
    search_fields = (
        "patient__hn",
        "patient__national_id",
        "patient__first_name",
        "patient__last_name",
    )
    date_hierarchy = "created_at"


@admin.register(PatientPin)
class PatientPinAdmin(ReadOnlySecurityAdmin):
    list_display = (
        "id",
        "patient",
        "failed_attempts",
        "lockout_level",
        "locked_until",
        "updated_at",
    )
    list_filter = ("lockout_level", "updated_at")
    search_fields = (
        "patient__hn",
        "patient__national_id",
        "patient__first_name",
        "patient__last_name",
    )
    date_hierarchy = "updated_at"


@admin.register(OtpChallenge)
class OtpChallengeAdmin(ReadOnlySecurityAdmin):
    list_display = (
        "id",
        "national_id",
        "channel",
        "purpose",
        "expires_at",
        "consumed_at",
        "attempts",
        "created_at",
    )
    list_filter = ("channel", "purpose", "created_at", "consumed_at")
    search_fields = ("national_id",)
    date_hierarchy = "created_at"
