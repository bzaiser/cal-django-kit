from django.urls import path

from . import views

app_name = "cal_kit"

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("events.json", views.events_json_view, name="events_json"),
    path("check-conflict/", views.conflict_check_view, name="check_conflict"),
    path("save/", views.save_event_view, name="save_event"),
    path("move/<str:pk>/", views.move_event_view, name="move_event"),
    path("delete/<str:pk>/", views.delete_event_view, name="delete_event"),
    path("feed.ics", views.ical_feed_view, name="ical_feed"),
    path("categories/save/", views.save_category_view, name="save_category"),
    path("categories/<int:pk>/delete/", views.delete_category_view, name="delete_category"),
    path("offerings/", views.offering_list_view, name="offering_list"),
    path("offerings/<int:pk>/create-event/", views.offering_create_event_view, name="offering_create_event"),
    path("offerings/<int:pk>/delete/", views.offering_delete_view, name="offering_delete"),
    path("billing/", views.billing_overview_view, name="billing_overview"),
    path("events/<int:pk>/billing/", views.event_billing_view, name="event_billing"),
    path("events/<int:pk>/billing/csv/", views.event_billing_csv_view, name="event_billing_csv"),
]
