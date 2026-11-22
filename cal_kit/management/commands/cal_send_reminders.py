from django.core.management.base import BaseCommand

from cal_kit.reminders import dispatch_due_cal_reminders


class Command(BaseCommand):
    help = "Prüft fällige Kalender-Erinnerungen und versendet sie (optional über notify_kit)."

    def handle(self, *args, **options):
        sent_count = dispatch_due_cal_reminders()
        self.stdout.write(self.style.SUCCESS(f"{sent_count} Erinnerung(en) verarbeitet."))
