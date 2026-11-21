from django.contrib import admin

from .models import CalendarEvent


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "start_at", "end_at", "all_day", "color_state")
    list_filter = ("all_day", "color_state", "start_at")
    search_fields = ("title", "description", "location")
    ordering = ("-start_at",)
