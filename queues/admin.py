from django.contrib import admin
from .models import (
    CriticalAlert,
    Device,
    DeviceAssignment,
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

admin.site.register(Visit)
admin.site.register(VitalSign)
admin.site.register(Queue)
admin.site.register(TriageResult)
admin.site.register(Device)
admin.site.register(DeviceAssignment)
admin.site.register(TelemetryLog)
admin.site.register(CriticalAlert)
admin.site.register(StaffDuty)
admin.site.register(StaffProfile)
admin.site.register(NurseCareAssignment)
admin.site.register(ShiftSchedule)

@admin.register(VisitWorkflowLog)
class VisitWorkflowLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "visit", "event_type", "actor_name", "actor_role")
    list_filter = ("event_type", "actor_role")
    search_fields = ("visit__patient__hn", "actor_name", "description")
    readonly_fields = (
        "visit", "event_type", "actor", "actor_name", "actor_role",
        "description", "details", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False
