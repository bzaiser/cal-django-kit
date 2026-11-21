from django.urls import path

from . import views

app_name = "cal_kit"

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("events.json", views.events_json_view, name="events_json"),
    path("save/", views.save_event_view, name="save_event"),
    path("move/<str:pk>/", views.move_event_view, name="move_event"),
    path("delete/<str:pk>/", views.delete_event_view, name="delete_event"),
    path("feed.ics", views.ical_feed_view, name="ical_feed"),
]
