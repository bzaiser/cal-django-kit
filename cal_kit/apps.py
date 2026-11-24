import os
from django.apps import AppConfig


class CalKitConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cal_kit"
    path = os.path.dirname(os.path.abspath(__file__))
    verbose_name = "Calendar & Sync Kit"

    def ready(self):
        try:
            from notify_kit.registry import register_notification_topic
        except Exception:
            return
        register_notification_topic(
            key="cal.reminder",
            label="Kalender-Erinnerungen",
            description="Benachrichtigungen für fällige Kalender-Termine und Erinnerungen.",
            icon="bi-alarm",
            default_enabled=True,
            staff_only=False,
        )
