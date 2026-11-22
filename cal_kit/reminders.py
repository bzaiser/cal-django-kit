from datetime import timedelta

from django.utils import timezone

from cal_kit.models import CalendarEvent


def dispatch_due_cal_reminders(user=None) -> int:
    """Check due CalendarEvent reminders and send notifications via notify_kit if available."""
    now = timezone.now()
    due_qs = CalendarEvent.objects.filter(
        reminder_sent=False,
        reminder_minutes__isnull=False,
        end_at__gte=now - timedelta(days=1),
    ).select_related("user")
    if user is not None and getattr(user, "is_authenticated", False):
        due_qs = due_qs.filter(user=user)

    notify_fn = None
    try:
        from notify_kit.services import notify
        notify_fn = notify
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
                        link="/calendar/",
                        topic="cal.reminder",
                    )
                except Exception:
                    pass
            ev.reminder_sent = True
            ev.save(update_fields=["reminder_sent"])
            sent_count += 1
    return sent_count
