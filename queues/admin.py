from django.contrib import admin
from .models import CriticalAlert, Visit, VitalSign, Queue, TriageResult, Device, DeviceAssignment, TelemetryLog

admin.site.register(Visit)
admin.site.register(VitalSign)
admin.site.register(Queue)
admin.site.register(TriageResult)
admin.site.register(Device)
admin.site.register(DeviceAssignment)
admin.site.register(TelemetryLog)
admin.site.register(CriticalAlert)
