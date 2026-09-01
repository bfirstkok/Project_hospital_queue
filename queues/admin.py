from django.contrib import admin
from .models import (
    CriticalAlert,
    Device,
    DeviceAssignment,
    NurseCareAssignment,
    Queue,
    StaffDuty,
    TelemetryLog,
    TriageResult,
    Visit,
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
admin.site.register(NurseCareAssignment)
