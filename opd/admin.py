from django.contrib import admin

from .models import VisitAssessment


@admin.register(VisitAssessment)
class VisitAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "visit",
        "examiner",
        "opd_urgency",
        "next_appointment_at",
        "created_at",
        "updated_at",
    )
    list_filter = ("opd_urgency", "created_at")
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "visit__patient__first_name",
        "visit__patient__last_name",
        "examiner__username",
        "examiner__first_name",
        "examiner__last_name",
        "chief_complaint",
        "diagnosis",
        "treatment",
    )
    readonly_fields = ("created_at", "updated_at", "opd_urgency", "opd_reason")
    date_hierarchy = "created_at"
