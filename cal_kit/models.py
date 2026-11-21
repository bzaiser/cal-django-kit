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


class CalendarEvent(models.Model):
    uid = models.CharField(max_length=128, unique=True, default=uuid.uuid4)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cal_kit_events",
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
    recurrence_rule = models.CharField(
        max_length=16,
        choices=RECURRENCE_CHOICES,
        default="none",
        db_index=True,
    )
    recurrence_interval = models.PositiveSmallIntegerField(default=1)
    recurrence_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_at"]

    def __str__(self):
        return f"{self.title} ({self.start_at:%Y-%m-%d})"

    @property
    def is_recurring(self) -> bool:
        return self.recurrence_rule not in ("", "none")

    def get_occurrences(self, window_start, window_end, max_count=730):
        duration = self.end_at - self.start_at
        if not self.is_recurring:
            if self.end_at >= window_start and self.start_at <= window_end:
                yield (self.start_at, self.end_at)
            return

        until_dt = None
        if self.recurrence_until:
            until_dt = datetime.combine(self.recurrence_until, datetime.max.time())
            if self.start_at.tzinfo:
                until_dt = until_dt.replace(tzinfo=self.start_at.tzinfo)

        interval = max(1, self.recurrence_interval or 1)
        curr = self.start_at
        count = 0

        while count < max_count:
            if until_dt and curr > until_dt:
                break
            if curr > window_end:
                break

            curr_end = curr + duration
            if curr_end >= window_start and curr <= window_end:
                yield (curr, curr_end)

            if self.recurrence_rule == "daily":
                curr = curr + timedelta(days=interval)
            elif self.recurrence_rule == "weekly":
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
