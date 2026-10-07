from uuid import uuid4

from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in
from django.utils import timezone


def update_last_login(sender, user, **kwargs):
    if user and user.pk:
        sender._default_manager.filter(pk=user.pk).update(last_login=timezone.now())


def start_staff_guide_visit(sender, request, **kwargs):
    # Each login gets a new marker so an unchecked "skip" lasts for this visit.
    if request is not None:
        request.session["staff_guide_visit"] = uuid4().hex


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        user_logged_in.disconnect(dispatch_uid='update_last_login')
        user_logged_in.connect(update_last_login, dispatch_uid='accounts.update_last_login', weak=False)
        user_logged_in.connect(start_staff_guide_visit, dispatch_uid='accounts.start_staff_guide_visit', weak=False)
