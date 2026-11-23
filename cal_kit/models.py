import calendar
from datetime import datetime, timedelta
import uuid

from django.conf import settings
from django.db import models


RECURRENCE_CHOICES = [
    ("none", "Keine Wiederholung"),
    ("daily", "Täglich"),
    ("weekly", "Wöchentlich"),
    ("monthly", "Monatlich"),
    ("yearly", "Jährlich"),
]

WEEKDAY_CHOICES = [
    ("0", "Mo"), ("1", "Di"), ("2", "Mi"), ("3", "Do"),
    ("4", "Fr"), ("5", "Sa"), ("6", "So"),
]


class CalendarCategory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cal_kit_categories",
    )
    name = models.CharField(max_length=80)
    color_state = models.PositiveSmallIntegerField(default=1)
    icon = models.CharField(max_length=40, blank=True, default="")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CalendarEvent(models.Model):
    uid = models.CharField(max_length=128, unique=True, default=uuid.uuid4)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cal_kit_events",
    )
    category = models.ForeignKey(
        CalendarCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    title = models.CharField(max_length=140)
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=140, blank=True, default="")
    description = models.TextField(blank=True, default="")
    color_state = models.PositiveSmallIntegerField(default=1)
    icon = models.CharField(max_length=40, blank=True, default="")
    reminder_minutes = models.PositiveIntegerField(null=True, blank=True)
    reminder_sent = models.BooleanField(default=False)
    is_reminder = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)
    recurrence_rule = models.CharField(
        max_length=16,
        choices=RECURRENCE_CHOICES,
        default="none",
        db_index=True,
    )
    recurrence_interval = models.PositiveSmallIntegerField(default=1)
    recurrence_until = models.DateField(null=True, blank=True)
    # Comma-separated weekday numbers 0=Mon…6=Sun, only used when rule=weekly
    recurrence_weekdays = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_at"]

    def __str__(self):
        return f"{self.title} ({self.start_at:%Y-%m-%d})"

    @property
    def is_recurring(self) -> bool:
        return self.recurrence_rule not in ("", "none")

    def _weekday_set(self) -> set[int]:
        """Return set of active weekday integers (0=Mon) from recurrence_weekdays."""
        if not self.recurrence_weekdays:
            return set()
        result = set()
        for part in self.recurrence_weekdays.split(","):
            part = part.strip()
            if part.isdigit():
                result.add(int(part))
        return result

    def get_occurrences(self, window_start, window_end, max_count=730):
        duration = self.end_at - self.start_at
        # Load exceptions once
        try:
            exc_map = {
                e.original_start: e
                for e in self.exceptions.all()
            }
        except Exception:
            exc_map = {}

        if not self.is_recurring:
            if self.end_at >= window_start and self.start_at <= window_end:
                exc = exc_map.get(self.start_at)
                if exc and exc.cancelled:
                    return
                if exc and (exc.new_start or exc.new_end):
                    yield (exc.new_start or self.start_at, exc.new_end or self.end_at, exc.new_title or self.title)
                else:
                    yield (self.start_at, self.end_at, self.title)
            return

        until_dt = None
        if self.recurrence_until:
            until_dt = datetime.combine(self.recurrence_until, datetime.max.time())
            if self.start_at.tzinfo:
                until_dt = until_dt.replace(tzinfo=self.start_at.tzinfo)

        interval = max(1, self.recurrence_interval or 1)
        weekdays = self._weekday_set()
        curr = self.start_at
        count = 0

        while count < max_count:
            if until_dt and curr > until_dt:
                break
            if curr > window_end:
                break

            # BYDAY filter for weekly recurrence
            if self.recurrence_rule == "weekly" and weekdays and curr.weekday() not in weekdays:
                curr = curr + timedelta(days=1)
                continue

            curr_end = curr + duration
            if curr_end >= window_start and curr <= window_end:
                exc = exc_map.get(curr)
                if not (exc and exc.cancelled):
                    if exc and (exc.new_start or exc.new_end):
                        yield (exc.new_start or curr, exc.new_end or curr_end, exc.new_title or self.title)
                    else:
                        yield (curr, curr_end, self.title)

            # Advance
            if self.recurrence_rule == "daily":
                curr = curr + timedelta(days=interval)
            elif self.recurrence_rule == "weekly":
                if weekdays:
                    curr = curr + timedelta(days=1)
                else:
                    curr = curr + timedelta(weeks=interval)
            elif self.recurrence_rule == "monthly":
                new_year = curr.year + (curr.month - 1 + interval) // 12
                new_month = (curr.month - 1 + interval) % 12 + 1
                max_day = calendar.monthrange(new_year, new_month)[1]
                new_day = min(self.start_at.day, max_day)
                curr = curr.replace(year=new_year, month=new_month, day=new_day)
            elif self.recurrence_rule == "yearly":
                new_year = curr.year + interval
                max_day = calendar.monthrange(new_year, self.start_at.month)[1]
                new_day = min(self.start_at.day, max_day)
                curr = curr.replace(year=new_year, day=new_day)
            else:
                break
            count += 1


class CalendarEventException(models.Model):
    """Override or cancel a single occurrence of a recurring event."""
    event = models.ForeignKey(
        CalendarEvent,
        on_delete=models.CASCADE,
        related_name="exceptions",
    )
    original_start = models.DateTimeField(db_index=True)
    cancelled = models.BooleanField(default=False)
    new_start = models.DateTimeField(null=True, blank=True)
    new_end = models.DateTimeField(null=True, blank=True)
    new_title = models.CharField(max_length=140, blank=True, default="")

    class Meta:
        unique_together = [("event", "original_start")]

    def __str__(self):
        status = "abgesagt" if self.cancelled else "verschoben"
        return f"{self.event.title} @ {self.original_start:%Y-%m-%d %H:%M} ({status})"
