from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from cal_kit.models import CalendarEvent


class Command(BaseCommand):
    help = "Prüft fällige Kalender-Erinnerungen und versendet sie (optional über notify_kit)."

    def handle(self, *args, **options):
        now = timezone.now()
        due_qs = CalendarEvent.objects.filter(
            reminder_sent=False,
            reminder_minutes__isnull=False,
            end_at__gte=now - timedelta(days=1),
        ).select_related("user")

        notify_fn = None
        try:
            from notify_kit.services import send_notification
            notify_fn = send_notification
        except Exception:
            notify_fn = None

        sent_count = 0
        for ev in due_qs:
            trigger_at = ev.start_at - timedelta(minutes=int(ev.reminder_minutes))
            if now >= trigger_at:
                if notify_fn is not None:
                    try:
                        notify_fn(
                            user=ev.user,
                            title=f"Erinnerung: {ev.title}",
                            message=f"Termin am {timezone.localtime(ev.start_at):%d.%m.%Y %H:%M}" + (f" ({ev.location})" if ev.location else ""),
                            level="info",
                        )
                    except Exception:
                        pass
                ev.reminder_sent = True
                ev.save(update_fields=["reminder_sent"])
                sent_count += 1

        self.stdout.write(self.style.SUCCESS(f"{sent_count} Erinnerung(en) verarbeitet."))
