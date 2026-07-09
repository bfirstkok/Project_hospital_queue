from django.apps import AppConfig
from django.contrib.auth.signals import user_logged_in
from django.utils import timezone


def update_last_login(sender, user, **kwargs):
    if user and user.pk:
        sender._default_manager.filter(pk=user.pk).update(last_login=timezone.now())


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        user_logged_in.disconnect(dispatch_uid='update_last_login')
        user_logged_in.connect(update_last_login, dispatch_uid='accounts.update_last_login', weak=False)
