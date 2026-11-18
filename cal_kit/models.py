import uuid

from django.conf import settings
from django.db import models


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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_at"]

    def __str__(self):
        return f"{self.title} ({self.start_at:%Y-%m-%d})"
