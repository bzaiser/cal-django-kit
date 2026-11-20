import json
import os
from pathlib import Path
import sys

import django
from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS_DIR = os.path.join(os.path.dirname(BASE_DIR), "django-design-studio")
for p in (BASE_DIR, DS_DIR):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-cal-kit",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "design_studio",
            "cal_kit",
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        ROOT_URLCONF="tests.test_cal_kit",
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                    "design_studio.context_processors.theme_context",
                ],
            },
        }],
        STATIC_URL="/static/",
        USE_TZ=True,
        TIME_ZONE="Europe/Berlin",
    )
    django.setup()

from django.contrib.auth import get_user_model
from django.urls import include, path
from cal_kit.models import CalendarEvent

urlpatterns = [
    path("design-studio/", include("design_studio.urls")),
    path("calendar/", include("cal_kit.urls")),
]


class CalKitTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="clara", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def test_fullcalendar_assets_crud_move_and_ics_feed(self):
        pkg = Path(BASE_DIR) / "cal_kit"
        self.assertTrue((pkg / "static" / "cal_kit" / "vendor" / "fullcalendar" / "index.global.min.js").exists())
        self.assertTrue((pkg / "static" / "cal_kit" / "vendor" / "fullcalendar" / "de.global.min.js").exists())

        cal_html = self.client.get("/calendar/").content.decode("utf-8")
        self.assertIn("FullCalendar.Calendar", cal_html)
        self.assertIn("cal-legend", cal_html)
        self.assertIn("cal-ev-outlook", cal_html)
        self.assertIn("calHoverCard", cal_html)
        self.assertIn("eventMouseEnter", cal_html)
        self.assertIn('data-card-surface="liquid"', cal_html)
        self.assertIn("--fc-event-text-color", cal_html)
        self.assertIn("calEvIconSwatches", cal_html)
        self.assertIn("bi-alarm-fill", cal_html)
        self.assertIn("eventContent", cal_html)
        self.assertNotIn("cdn.jsdelivr.net", cal_html)

        # Create
        r = self.client.post(
            "/calendar/save/",
            {
                "title": "NAS Wartung",
                "start_at": "2026-11-18T14:00",
                "end_at": "2026-11-18T15:30",
                "location": "Serverraum",
                "color_state": "3",
                "icon": "bi-people-fill",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        ev_id = r.json()["id"]

        # JSON feed
        events = self.client.get("/calendar/events.json").json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "NAS Wartung")
        self.assertEqual(events[0]["extendedProps"]["color_state"], 3)
        self.assertEqual(events[0]["extendedProps"]["icon"], "bi-people-fill")
        self.assertTrue(events[0]["extendedProps"]["has_location"])
        self.assertFalse(events[0]["extendedProps"]["is_reminder"])
        self.assertFalse(events[0]["extendedProps"]["all_day"])
        self.assertFalse(events[0]["extendedProps"]["has_alarm"])

        # Drag & drop move
        mv = self.client.post(
            f"/calendar/move/{ev_id}/",
            json.dumps({"start": "2026-11-19T10:00:00+01:00", "end": "2026-11-19T11:30:00+01:00"}),
            content_type="application/json",
        )
        self.assertEqual(mv.status_code, 200)
        ev = CalendarEvent.objects.get(pk=ev_id)
        self.assertEqual(ev.start_at.day, 19)

        # iCal feed
        feed = self.client.get("/calendar/feed.ics")
        self.assertEqual(feed.status_code, 200)
        self.assertIn("SUMMARY:NAS Wartung", feed.content.decode("utf-8"))

        # Delete
        dl = self.client.post(f"/calendar/delete/{ev_id}/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(CalendarEvent.objects.count(), 0)

    def test_calendar_reminders_valarm_and_command(self):
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.localtime(timezone.now())
        start_str = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        end_str = (now + timedelta(minutes=65)).strftime("%Y-%m-%dT%H:%M")
        r = self.client.post(
            "/calendar/save/",
            {
                "title": "Backup prüfen",
                "start_at": start_str,
                "end_at": end_str,
                "reminder_minutes": "15",
                "is_reminder": "on",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        feed = self.client.get("/calendar/feed.ics").content.decode("utf-8")
        self.assertIn("BEGIN:VALARM", feed)
        self.assertIn("TRIGGER:-PT15M", feed)

        call_command("cal_send_reminders")
        ev = CalendarEvent.objects.get(title="Backup prüfen")
        self.assertTrue(ev.reminder_sent)

        events = self.client.get("/calendar/events.json").json()
        self.assertTrue(events[0]["extendedProps"]["is_reminder"])
        self.assertTrue(events[0]["extendedProps"]["has_alarm"])


if __name__ == "__main__":
    from django.test.runner import DiscoverRunner
    call_command("migrate", verbosity=0)
    runner = DiscoverRunner(verbosity=2)
    sys.exit(bool(runner.run_tests(["tests.test_cal_kit"])))
