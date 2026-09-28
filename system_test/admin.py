from django.apps import apps
from django.contrib import admin

from .models import TestScenarioRun


@admin.register(TestScenarioRun)
class TestScenarioRunAdmin(admin.ModelAdmin):
    list_display = ("id", "scenario", "patient", "visit", "device", "created_by", "created_at")
    list_filter = ("scenario", "created_at")


class ReadOnlyDatabaseModelAdmin(admin.ModelAdmin):
    """Expose Django models that lack a custom admin without allowing edits."""
    list_per_page = 50

    def get_list_display(self, request):
        fields = [
            field.name
            for field in self.model._meta.concrete_fields
            if field.name.lower() not in {"password", "password_hash", "session_data"}
        ]
        return tuple(fields[:6]) or ("__str__",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.concrete_fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Make Django's unregistered built-in and auto-created relation models visible
# in the admin. Existing, explicitly configured ModelAdmins keep their behavior.
for database_model in apps.get_models(include_auto_created=True):
    if database_model._meta.proxy or database_model in admin.site._registry:
        continue
    admin.site.register(database_model, ReadOnlyDatabaseModelAdmin)

admin.site.index_template = "admin/hospital_index.html"
