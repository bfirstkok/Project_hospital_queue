from django.conf import settings
from django.db import models


class AccountStatusLog(models.Model):
    """Immutable audit trail for staff account activation/suspension."""

    class Action(models.TextChoices):
        SUSPEND = "SUSPEND", "ระงับบัญชี"
        ACTIVATE = "ACTIVATE", "เปิดใช้งานบัญชี"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="account_status_history",
    )
    action = models.CharField(max_length=16, choices=Action.choices, db_index=True)
    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_status_changes_made",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="acct_status_user_created_idx"),
        ]

    def __str__(self):
        return f"{self.user} {self.action} {self.created_at:%Y-%m-%d %H:%M}"
