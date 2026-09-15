from django.contrib import admin

from .models import TestScenarioRun


@admin.register(TestScenarioRun)
class TestScenarioRunAdmin(admin.ModelAdmin):
    list_display = ("id", "scenario", "patient", "visit", "device", "created_by", "created_at")
    list_filter = ("scenario", "created_at")
