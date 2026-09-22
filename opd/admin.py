from django.contrib import admin

from .models import (
    Bill,
    MedicalCertificate,
    PatientCoverage,
    Prescription,
    PrescriptionItem,
    VisitAssessment,
)


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


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "visit", "prescribed_by", "status", "sent_at", "dispensed_at")
    list_filter = ("status",)
    search_fields = ("visit__patient__hn", "visit__patient__first_name", "visit__patient__last_name")


@admin.register(PrescriptionItem)
class PrescriptionItemAdmin(admin.ModelAdmin):
    list_display = ("id", "prescription", "medication_name", "strength", "quantity", "unit", "unit_price")
    search_fields = ("medication_name", "prescription__visit__patient__hn")


@admin.register(PatientCoverage)
class PatientCoverageAdmin(admin.ModelAdmin):
    list_display = ("patient", "coverage_type", "coverage_percent", "member_no", "is_active")
    list_filter = ("coverage_type", "is_active")
    search_fields = ("patient__hn", "patient__first_name", "patient__last_name", "member_no")


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("id", "visit", "status", "subtotal", "covered_amount", "patient_due", "paid_at")
    list_filter = ("status",)
    search_fields = ("visit__patient__hn", "visit__patient__first_name", "visit__patient__last_name")


@admin.register(MedicalCertificate)
class MedicalCertificateAdmin(admin.ModelAdmin):
    list_display = ("id", "visit", "issued_by", "issued_at", "rest_from", "rest_to")
    search_fields = ("visit__patient__hn", "visit__patient__first_name", "visit__patient__last_name")
