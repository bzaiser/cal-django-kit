import os
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

urlpatterns = [
    path("design-studio/", include("design_studio.urls")),
    path("calendar/", include("cal_kit.urls")),
]


class CalKitTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="clara", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def test_create_event_render_calendar_and_ics_feed(self):
        resp = self.client.post(
            "/calendar/",
            {
                "title": "NAS Wartung",
                "start_at": "2026-11-18T14:00",
                "end_at": "2026-11-18T15:30",
                "location": "Serverraum",
            },
        )
        self.assertEqual(resp.status_code, 302)

        cal_page = self.client.get("/calendar/?year=2026&month=11")
        self.assertEqual(cal_page.status_code, 200)
        self.assertIn("NAS Wartung", cal_page.content.decode("utf-8"))

        feed = self.client.get("/calendar/feed.ics")
        self.assertEqual(feed.status_code, 200)
        self.assertIn("text/calendar", feed["Content-Type"])
        ics_text = feed.content.decode("utf-8")
        self.assertIn("BEGIN:VCALENDAR", ics_text)
        self.assertIn("SUMMARY:NAS Wartung", ics_text)


if __name__ == "__main__":
    from django.test.runner import DiscoverRunner
    call_command("migrate", verbosity=0)
    runner = DiscoverRunner(verbosity=2)
    sys.exit(bool(runner.run_tests(["tests.test_cal_kit"])))
