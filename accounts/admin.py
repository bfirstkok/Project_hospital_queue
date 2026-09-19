from django.contrib import admin

from .models import AccountStatusLog


@admin.register(AccountStatusLog)
class AccountStatusLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "actor", "reason")
    list_filter = ("action", "created_at")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "actor__username",
        "reason",
    )
    date_hierarchy = "created_at"

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
