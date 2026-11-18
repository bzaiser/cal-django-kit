from django.urls import path

from . import views

app_name = "cal_kit"

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("feed.ics", views.ical_feed_view, name="ical_feed"),
    path("delete/<int:pk>/", views.delete_event_view, name="delete_event"),
]
