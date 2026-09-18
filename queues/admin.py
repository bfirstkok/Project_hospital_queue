from django.contrib import admin

from .models import (
    CriticalAlert,
    Device,
    DeviceAssignment,
    IoTVital,
    NurseCareAssignment,
    Queue,
    ShiftSchedule,
    StaffDuty,
    StaffProfile,
    TelemetryLog,
    TriageResult,
    Visit,
    VisitWorkflowLog,
    VitalSign,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Base admin for operational/audit data that should not be edited manually."""

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "patient",
        "final_severity",
        "registered_at",
        "triaged_at",
        "confirmed_at",
        "called_at",
    )
    list_filter = ("final_severity", "registered_at")
    search_fields = (
        "patient__hn",
        "patient__national_id",
        "patient__first_name",
        "patient__last_name",
        "tracking_token",
    )
    date_hierarchy = "registered_at"


@admin.register(VitalSign)
class VitalSignAdmin(admin.ModelAdmin):
    list_display = (
        "visit",
        "pr",
        "rr",
        "sys_bp",
        "dia_bp",
        "bt",
        "o2sat",
        "pain_score",
        "updated_at",
    )
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "visit__patient__first_name",
        "visit__patient__last_name",
    )


@admin.register(Queue)
class QueueAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "visit",
        "status",
        "priority",
        "exam_room",
        "manual_sequence",
        "is_expedited",
        "created_at",
    )
    list_filter = ("status", "is_expedited", "exam_room")
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "visit__patient__first_name",
        "visit__patient__last_name",
    )


@admin.register(TriageResult)
class TriageResultAdmin(admin.ModelAdmin):
    list_display = (
        "visit",
        "ai_severity",
        "nurse_severity",
        "confidence",
        "model_name",
        "created_at",
    )
    list_filter = ("ai_severity", "nurse_severity", "mental_status")
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "visit__patient__first_name",
        "visit__patient__last_name",
        "model_name",
    )


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "is_active", "last_seen")
    list_filter = ("is_active",)
    search_fields = ("device_id",)


@admin.register(DeviceAssignment)
class DeviceAssignmentAdmin(admin.ModelAdmin):
    list_display = ("device", "visit", "paired_at", "unpaired_at", "is_active")
    list_filter = ("is_active", "paired_at")
    search_fields = (
        "device__device_id",
        "visit__patient__hn",
        "visit__patient__national_id",
    )


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "photo")
    list_filter = ("role",)
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
    )


@admin.register(StaffDuty)
class StaffDutyAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "duty_date",
        "is_present",
        "is_available",
        "checked_in_at",
        "checked_out_at",
        "last_seen_at",
    )
    list_filter = ("duty_date", "is_present", "is_available")
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(ShiftSchedule)
class ShiftScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "shift_date",
        "start_time",
        "end_time",
        "status",
        "note",
        "created_by",
    )
    list_filter = ("status", "shift_date")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "note",
    )
    date_hierarchy = "shift_date"


@admin.register(NurseCareAssignment)
class NurseCareAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "nurse",
        "visit",
        "assigned_by",
        "assigned_at",
        "ended_at",
        "is_active",
    )
    list_filter = ("is_active", "assigned_at")
    search_fields = (
        "nurse__username",
        "nurse__first_name",
        "nurse__last_name",
        "visit__patient__hn",
        "visit__patient__national_id",
    )


@admin.register(TelemetryLog)
class TelemetryLogAdmin(ReadOnlyAdmin):
    list_display = (
        "ts",
        "visit",
        "device",
        "bpm",
        "o2sat",
        "bt",
        "rr",
        "sys_bp",
        "dia_bp",
    )
    list_filter = ("device", "ts")
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "device__device_id",
    )
    date_hierarchy = "ts"


@admin.register(IoTVital)
class IoTVitalAdmin(ReadOnlyAdmin):
    list_display = (
        "created_at",
        "patient_identifier",
        "device_identifier",
        "heart_rate",
        "spo2",
        "temperature",
        "respiratory_rate",
        "blood_pressure_sys",
        "blood_pressure_dia",
    )
    list_filter = ("created_at",)
    search_fields = ("patient_identifier", "device_identifier")
    date_hierarchy = "created_at"


@admin.register(CriticalAlert)
class CriticalAlertAdmin(ReadOnlyAdmin):
    list_display = (
        "created_at",
        "visit",
        "alert_type",
        "severity",
        "value",
        "source",
        "status",
        "acknowledged_by",
        "acknowledged_at",
    )
    list_filter = ("status", "alert_type", "severity", "source")
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "message",
        "source",
    )
    date_hierarchy = "created_at"


@admin.register(VisitWorkflowLog)
class VisitWorkflowLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "visit", "event_type", "actor_name", "actor_role")
    list_filter = ("event_type", "actor_role")
    search_fields = (
        "visit__patient__hn",
        "visit__patient__national_id",
        "actor_name",
        "description",
    )
    date_hierarchy = "created_at"
