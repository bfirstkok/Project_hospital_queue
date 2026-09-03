from django.apps import AppConfig


class QueuesConfig(AppConfig):
    name = "queues"

    def ready(self):
        # Register lifecycle hooks that release nurse capacity when a visit ends.
        from . import signals  # noqa: F401
