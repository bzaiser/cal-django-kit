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
from cal_kit.models import CalendarEvent, CalendarCategory, CalendarEventException

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
        self.assertIn("calEvRecurrenceRule", cal_html)
        self.assertIn("bi-arrow-repeat", cal_html)
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

    def test_recurring_events_occurrences_and_rrule_export(self):
        # 1. Create a weekly recurring event for 4 weeks
        r = self.client.post(
            "/calendar/save/",
            {
                "title": "Team Standup",
                "start_at": "2026-11-02T09:00",
                "end_at": "2026-11-02T09:30",
                "recurrence_rule": "weekly",
                "recurrence_interval": "1",
                "recurrence_until": "2026-11-25",
                "color_state": "4",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        ev_id = r.json()["id"]
        ev = CalendarEvent.objects.get(pk=ev_id)
        self.assertTrue(ev.is_recurring)
        self.assertEqual(ev.recurrence_rule, "weekly")

        # 2. Check occurrences via JSON view (November 2026 has Nov 2, 9, 16, 23)
        res = self.client.get("/calendar/events.json?start=2026-11-01T00:00:00Z&end=2026-11-30T23:59:59Z")
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertEqual(len(items), 4)
        for it in items:
            self.assertTrue(it["extendedProps"]["is_recurring"])
            self.assertEqual(it["extendedProps"]["master_id"], str(ev_id))
            self.assertEqual(it["extendedProps"]["recurrence_rule"], "weekly")

        # 3. Check iCal RRULE export
        feed = self.client.get("/calendar/feed.ics").content.decode("utf-8")
        self.assertIn("RRULE:FREQ=WEEKLY;UNTIL=20261125T", feed)

        # 4. Delete only one occurrence (scope=this), series stays
        occ_id = items[1]["id"]
        dl = self.client.post(f"/calendar/delete/{occ_id}/",
                              {"scope": "this"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(dl.status_code, 200)
        # Series still exists
        self.assertEqual(CalendarEvent.objects.filter(title="Team Standup").count(), 1)
        # Exception created
        self.assertEqual(CalendarEventException.objects.filter(event__title="Team Standup", cancelled=True).count(), 1)

        # 5. Delete whole series
        dl2 = self.client.post(f"/calendar/delete/{ev_id}/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(dl2.status_code, 200)
        self.assertEqual(CalendarEvent.objects.filter(title="Team Standup").count(), 0)

    def test_calendar_categories_and_byday(self):
        # Create a category
        r = self.client.post("/calendar/categories/save/",
                             {"name": "Arbeit", "color_state": "2"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)
        cat_id = r.json()["id"]

        # Create event with category and BYDAY Mo+Mi
        r2 = self.client.post("/calendar/save/", {
            "title": "Standup MoMi",
            "start_at": "2026-11-02T09:00",
            "end_at": "2026-11-02T09:30",
            "recurrence_rule": "weekly",
            "recurrence_weekdays": "0,2",
            "recurrence_until": "2026-11-26",  # excl. Nov 30 Mo → 4 Mo + 4 Mi = 8
            "category": str(cat_id),
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r2.status_code, 200)
        ev = CalendarEvent.objects.get(title="Standup MoMi")
        self.assertEqual(ev.recurrence_weekdays, "0,2")
        self.assertEqual(ev.category_id, cat_id)

        # Nov 2026: Mondays = 2,9,16,23; Wednesdays = 4,11,18,25 → 8 occurrences
        res = self.client.get("/calendar/events.json?start=2026-11-01T00:00:00Z&end=2026-11-30T23:59:59Z")
        items = res.json()
        self.assertEqual(len(items), 8)

        # iCal BYDAY export
        feed = self.client.get("/calendar/feed.ics").content.decode("utf-8")
        self.assertIn("BYDAY=MO,WE", feed)

        # Category filter
        res_cat = self.client.get(f"/calendar/events.json?start=2026-11-01T00:00:00Z&end=2026-11-30T23:59:59Z&category={cat_id}")
        self.assertEqual(len(res_cat.json()), 8)
        res_no_cat = self.client.get("/calendar/events.json?start=2026-11-01T00:00:00Z&end=2026-11-30T23:59:59Z&category=99999")
        self.assertEqual(len(res_no_cat.json()), 0)

        # Categories management modal rendered & wired
        cal_html = self.client.get("/calendar/").content.decode("utf-8")
        self.assertIn('id="calCategoriesModal"', cal_html)
        self.assertIn('id="calCatForm"', cal_html)
        self.assertIn("cal-cat-edit-btn", cal_html)
        self.assertIn("cal-cat-del-btn", cal_html)


    def test_events_json_handles_plus_timezone_offset_and_providers(self):
        from django.test import override_settings

        cal_html = self.client.get("/calendar/").content.decode("utf-8")
        self.assertIn("encodeURIComponent(info.startStr)", cal_html)
        self.assertIn("encodeURIComponent(info.endStr)", cal_html)

        self.client.post(
            "/calendar/save/",
            {
                "title": "Berlin Event",
                "start_at": "2026-11-15T10:00",
                "end_at": "2026-11-15T11:00",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        # Unencoded '+' in query string becomes space in Django request.GET
        res_unencoded = self.client.get(
            "/calendar/events.json?start=2026-10-26T00:00:00+01:00&end=2026-12-07T00:00:00+01:00"
        )
        self.assertEqual(res_unencoded.status_code, 200)
        self.assertEqual(len(res_unencoded.json()), 1)

        # Encoded '%2B' also works
        res_encoded = self.client.get(
            "/calendar/events.json?start=2026-10-26T00:00:00%2B01:00&end=2026-12-07T00:00:00%2B01:00"
        )
        self.assertEqual(res_encoded.status_code, 200)
        self.assertEqual(len(res_encoded.json()), 1)

        # External provider included on "Alle", excluded when filtering by a specific CalendarCategory
        def _fake_provider(request, window_start, window_end):
            return [{
                "id": "ext-1",
                "title": "External Todo",
                "start": "2026-11-20T12:00:00+01:00",
                "editable": False,
                "url": "/list/1/",
                "extendedProps": {"read_only": True, "completed": False},
            }]

        with override_settings(CAL_KIT_EVENT_PROVIDERS=[_fake_provider]):
            all_res = self.client.get(
                "/calendar/events.json?start=2026-10-26T00:00:00+01:00&end=2026-12-07T00:00:00+01:00"
            )
            self.assertEqual(all_res.status_code, 200)
            titles = [e["title"] for e in all_res.json()]
            self.assertIn("Berlin Event", titles)
            self.assertIn("External Todo", titles)

            cat_res = self.client.get(
                "/calendar/events.json?start=2026-10-26T00:00:00+01:00&end=2026-12-07T00:00:00+01:00&category=99999"
            )
            self.assertEqual(cat_res.status_code, 200)
            self.assertEqual(len(cat_res.json()), 0)

        # External move handler hook
        moved = []

        def _fake_move_handler(request, pk, payload):
            if pk == "ext-1":
                moved.append((pk, payload.get("start")))
                return True
            return False

        with override_settings(CAL_KIT_MOVE_HANDLERS=[_fake_move_handler]):
            mv_ok = self.client.post(
                "/calendar/move/ext-1/",
                json.dumps({"start": "2026-11-22T14:00:00+01:00"}),
                content_type="application/json",
            )
            self.assertEqual(mv_ok.status_code, 200)
            self.assertEqual(moved, [("ext-1", "2026-11-22T14:00:00+01:00")])

            mv_fail = self.client.post(
                "/calendar/move/unknown-99/",
                json.dumps({"start": "2026-11-22T14:00:00+01:00"}),
                content_type="application/json",
            )
            self.assertEqual(mv_fail.status_code, 400)

    def test_shared_categories_public_flag_and_location_conflict_check(self):
        from django.contrib.auth import get_user_model
        from cal_kit.models import CalendarCategory, CalendarEvent

        User = get_user_model()
        other = User.objects.create_user(username="member2", password="pw")

        # 1. Shared category with is_public_default
        rcat = self.client.post("/calendar/categories/save/", {
            "name": "Vereinstermine",
            "color_state": "2",
            "is_shared": "on",
            "is_public_default": "on",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(rcat.status_code, 200)
        cat = CalendarCategory.objects.get(pk=rcat.json()["id"])
        self.assertTrue(cat.is_shared)
        self.assertTrue(cat.is_public_default)

        # 2. Save event with resource_ref + is_public
        rev = self.client.post("/calendar/save/", {
            "title": "Vorstandssitzung",
            "start_at": "2026-12-01T18:00",
            "end_at": "2026-12-01T20:00",
            "location": "Großer Saal",
            "resource_ref": "asset:7",
            "category": str(cat.id),
            "form_has_public_flag": "1",
            "is_public": "on",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(rev.status_code, 200)
        ev = CalendarEvent.objects.get(pk=rev.json()["id"])
        self.assertTrue(ev.is_public)
        self.assertEqual(ev.resource_ref, "asset:7")

        # 3. Other user sees shared event (read_only=True) and gets conflict warning for same room
        c2 = Client()
        c2.force_login(other)
        ev_json = c2.get("/calendar/events.json?start=2026-12-01T00:00:00Z&end=2026-12-02T00:00:00Z").json()
        self.assertEqual(len(ev_json), 1)
        self.assertTrue(ev_json[0]["extendedProps"]["read_only"])

        conf = c2.get("/calendar/check-conflict/?start=2026-12-01T19:00&end=2026-12-01T21:00&resource_ref=asset:7&location=Gro%C3%9Fer%20Saal").json()
        self.assertEqual(len(conf["conflicts"]), 1)
        self.assertEqual(conf["conflicts"][0]["title"], "Vorstandssitzung")

        # Editing the same event with exclude_pk does not conflict with itself
        self_conf = self.client.get(f"/calendar/check-conflict/?start=2026-12-01T19:00&end=2026-12-01T21:00&resource_ref=asset:7&exclude_pk={ev.id}").json()
        self.assertEqual(len(self_conf["conflicts"]), 0)

        # Multi-calendar filter: personal + cat
        self.client.post("/calendar/save/", {
            "title": "Privater Termin",
            "start_at": "2026-12-02T10:00",
            "end_at": "2026-12-02T11:00",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        multi_json = self.client.get(f"/calendar/events.json?start=2026-12-01T00:00:00Z&end=2026-12-03T00:00:00Z&category=personal,{cat.id}").json()
        self.assertEqual(len(multi_json), 2)
        personal_only = self.client.get("/calendar/events.json?start=2026-12-01T00:00:00Z&end=2026-12-03T00:00:00Z&category=personal").json()
        self.assertEqual([e["title"] for e in personal_only], ["Privater Termin"])

    def test_no_missing_migrations(self):
        call_command("makemigrations", "cal_kit", "--check", "--dry-run", verbosity=0)

    def test_offering_template_create_event_and_billing_views(self):
        from cal_kit.models import OfferingTemplate
        r = self.client.post("/calendar/offerings/", {
            "title": "Aquarellkurs",
            "instructor_name": "Ursula Wolke",
            "responsible_name": "Margit Hägebarth",
            "fee_amount": "35.00",
            "honorarium_amount": "120.00",
            "default_duration_minutes": "180",
            "max_participants": "12",
            "show_in_program": "on",
        })
        self.assertEqual(r.status_code, 302)
        tpl = OfferingTemplate.objects.get(title="Aquarellkurs")

        r_ev = self.client.post(f"/calendar/offerings/{tpl.id}/create-event/", {"start_at": "2026-06-10T10:00"})
        self.assertEqual(r_ev.status_code, 302)
        ev = CalendarEvent.objects.get(offering_template=tpl)
        self.assertTrue(ev.allow_registration)
        self.assertEqual(str(ev.fee_amount), "35.00")

        ov = self.client.get("/calendar/billing/")
        self.assertEqual(ov.status_code, 200)
        self.assertContains(ov, "Aquarellkurs")

        det = self.client.get(f"/calendar/events/{ev.id}/billing/")
        self.assertEqual(det.status_code, 200)
        self.assertContains(det, "120")

        csv_r = self.client.get(f"/calendar/events/{ev.id}/billing/csv/")
        self.assertEqual(csv_r.status_code, 200)
        self.assertIn("text/csv", csv_r["Content-Type"])

        # Recurring event from offering: 13x Mondays 16:00-22:00 + free-text location
        r_rec = self.client.post(f"/calendar/offerings/{tpl.id}/create-event/", {
            "start_date": "2026-09-07",
            "start_time": "16:00",
            "end_time": "22:00",
            "location": "Alte Kapelle",
            "recurrence_rule": "weekly",
            "recurrence_weekdays": ["0"],
            "repeat_count": "13",
        })
        self.assertEqual(r_rec.status_code, 302)
        ev_rec = CalendarEvent.objects.filter(offering_template=tpl, location="Alte Kapelle").first()
        self.assertIsNotNone(ev_rec)
        self.assertEqual(ev_rec.recurrence_rule, "weekly")
        self.assertEqual(ev_rec.recurrence_weekdays, "0")
        from datetime import timedelta
        occs = list(ev_rec.get_occurrences(ev_rec.start_at, ev_rec.start_at + timedelta(days=120)))
        self.assertEqual(len(occs), 13)

        # Icon "none" is persisted cleanly
        r_none = self.client.post("/calendar/save/", {
            "title": "Ohne Icon Termin",
            "start_at": "2026-09-10T10:00",
            "end_at": "2026-09-10T11:00",
            "icon": "none",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r_none.status_code, 200)
        self.assertEqual(CalendarEvent.objects.get(title="Ohne Icon Termin").icon, "none")

    def test_offering_edit_form_unlocalizes_decimal_fields_in_de(self):
        from django.test import override_settings

        self.client.post("/calendar/offerings/", {
            "title": "Ölmalerei",
            "fee_amount": "35.00",
            "fee_non_member": "50.00",
            "fee_reduced": "25.00",
            "honorarium_amount": "120.00",
        })
        with override_settings(LANGUAGE_CODE="de"):
            resp = self.client.get("/calendar/offerings/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="35.00"')
        self.assertContains(resp, 'value="50.00"')
        self.assertContains(resp, 'value="25.00"')
        self.assertContains(resp, 'value="120.00"')

    def test_offering_edit_form_shows_uploaded_image_and_remove_option(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        img = SimpleUploadedFile("kurs.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", content_type="image/png")
        self.client.post("/calendar/offerings/", {"title": "Bildkurs", "image": img})
        resp = self.client.get("/calendar/offerings/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="remove_image"')
        self.assertContains(resp, "kurs")

    def test_event_credit_note_pdf_and_partial_cancellation_billing(self):
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        from cal_kit.models import OfferingTemplate
        tpl = OfferingTemplate.objects.create(
            user=self.user, title="Gutschrift-Kurs", fee_amount=Decimal("100.00"), honorarium_amount=Decimal("250.00")
        )
        ev = CalendarEvent.objects.create(
            user=self.user,
            offering_template=tpl,
            title="Gutschrift-Termin",
            instructor_name="Max Dozent",
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=2),
        )
        r = self.client.get(f"/calendar/events/{ev.id}/billing/credit-note.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertIn(r["Content-Type"], ("application/pdf", "text/html; charset=utf-8"))

        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        Group.objects.get_or_create(name="Mitglied / Portal")
        member = get_user_model().objects.create_user(username="plain_member", password="pw", is_staff=False)
        self.client.force_login(member)
        self.assertEqual(self.client.get("/calendar/offerings/").status_code, 403)
        self.assertEqual(self.client.post("/calendar/offerings/", {"title": "Hack"}).status_code, 403)
        self.assertEqual(self.client.get("/calendar/billing/").status_code, 403)

    def test_custom_dates_session_cancel_append_and_notify(self):
        from datetime import timedelta
        from cal_kit.models import CalendarEvent, OfferingTemplate
        self.client.force_login(self.user)
        tpl = OfferingTemplate.objects.create(user=self.user, title="3-Tage-Workshop", fee_amount=90)
        r = self.client.post(f"/calendar/offerings/{tpl.id}/create-event/", {
            "start_date": "2026-06-10",
            "start_time": "16:00",
            "end_time": "19:00",
            "recurrence_rule": "custom",
            "custom_date": ["2026-06-10", "2026-06-24", "2026-07-08"],
            "custom_start": ["16:00", "16:00", "16:00"],
            "custom_end": ["19:00", "19:00", "19:00"],
        })
        self.assertEqual(r.status_code, 302)
        ev = CalendarEvent.objects.get(title="3-Tage-Workshop")
        self.assertEqual(ev.recurrence_rule, "custom")
        self.assertEqual(len(ev.custom_dates_json), 3)
        occs = list(ev.get_occurrences(ev.start_at - timedelta(days=1), ev.start_at + timedelta(days=60)))
        self.assertEqual(len(occs), 3)

        # Cancel first session and append replacement
        r_ca = self.client.post(f"/calendar/events/{ev.id}/billing/session-action/", {
            "action_type": "cancel_append",
            "original_start": occs[0][0].isoformat(),
            "replacement_date": "2026-07-22",
        })
        self.assertEqual(r_ca.status_code, 302)
        ev.refresh_from_db()
        self.assertEqual(len(ev.custom_dates_json), 4)
        occs_after = list(ev.get_occurrences(ev.start_at - timedelta(days=1), ev.start_at + timedelta(days=60)))
        self.assertEqual(len(occs_after), 3)

        # Billing page renders sessions & prorated summary after cancel_only
        self.client.post(f"/calendar/events/{ev.id}/billing/session-action/", {
            "action_type": "cancel_only",
            "original_start": occs_after[0][0].isoformat(),
        })
        b_resp = self.client.get(f"/calendar/events/{ev.id}/billing/")
        self.assertEqual(b_resp.status_code, 200)
        self.assertIn("2 von 4 aktiv", b_resp.content.decode("utf-8"))

    def test_offering_duplicate_toggle_active_sort_and_program_filter(self):
        from cal_kit.models import OfferingTemplate, Program

        prog = Program.objects.create(title="Kinderferienprogramm")
        tpl = OfferingTemplate.objects.create(
            user=self.user,
            title="Keramikwerkstatt",
            instructor_name="Zora",
            program=prog,
            is_active=True,
        )
        # Duplicate
        r_dup = self.client.post(f"/calendar/offerings/{tpl.id}/duplicate/")
        self.assertEqual(r_dup.status_code, 302)
        dup = OfferingTemplate.objects.get(title="Keramikwerkstatt - Kopie")
        self.assertNotEqual(dup.slug, tpl.slug)
        self.assertEqual(dup.program_id, prog.id)
        self.assertTrue(dup.is_active)

        # Toggle inactive
        r_tog = self.client.post(f"/calendar/offerings/{tpl.id}/toggle-active/")
        self.assertEqual(r_tog.status_code, 302)
        tpl.refresh_from_db()
        self.assertFalse(tpl.is_active)

        # Default offering list excludes inactive, status=inactive shows it
        r_act = self.client.get("/calendar/offerings/")
        self.assertNotContains(r_act, f'data-id="{tpl.id}"')
        self.assertContains(r_act, "Keramikwerkstatt - Kopie")

        r_inact = self.client.get("/calendar/offerings/?status=inactive")
        self.assertContains(r_inact, f'data-id="{tpl.id}"')

        # Calendar modal dropdown excludes inactive
        r_cal = self.client.get("/calendar/")
        self.assertNotIn(tpl, list(r_cal.context["offering_templates"]))
        self.assertIn(dup, list(r_cal.context["offering_templates"]))

        # Filter by program & sort
        r_prog = self.client.get(f"/calendar/offerings/?program={prog.id}&sort=title&q=Kopie")
        self.assertEqual(r_prog.status_code, 200)
        self.assertContains(r_prog, "Keramikwerkstatt - Kopie")

        # Filter by category=none (uncategorized) vs category=<id>
        from cal_kit.models import CalendarCategory
        cat_o = CalendarCategory.objects.create(user=self.user, name="Kurse Test", is_shared=True)
        dup.category = cat_o
        dup.save(update_fields=["category"])
        tpl_uncat = OfferingTemplate.objects.create(user=self.user, title="Ohne Kat Kurs", slug="ohne-kat-kurs", is_active=True)

        r_none = self.client.get("/calendar/offerings/?category=none")
        self.assertContains(r_none, "Ohne Kat Kurs")
        self.assertNotContains(r_none, "Keramikwerkstatt - Kopie")

        r_cat = self.client.get(f"/calendar/offerings/?category={cat_o.id}")
        self.assertContains(r_cat, "Keramikwerkstatt - Kopie")
        self.assertNotContains(r_cat, "Ohne Kat Kurs")

    def test_shared_category_edit_and_delete_permissions(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from cal_kit.models import CalendarCategory

        Group.objects.get_or_create(name="Mitglieder")
        User = get_user_model()
        owner = User.objects.create_user(username="owner_cat", password="pw", is_staff=True)
        staff2 = User.objects.create_user(username="staff2", password="pw", is_staff=True)
        readonly = User.objects.create_user(username="readonly", password="pw", is_staff=False)

        shared_cat = CalendarCategory.objects.create(user=owner, name="Kurse & Workshops", is_shared=True)
        private_cat = CalendarCategory.objects.create(user=owner, name="Privat", is_shared=False)

        c_staff = Client()
        c_staff.login(username="staff2", password="pw")
        r_edit_shared = c_staff.post("/calendar/categories/save/", {
            "pk": shared_cat.id,
            "name": "Kurse & Workshops Neu",
            "color_state": "3",
            "is_shared": "on",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r_edit_shared.status_code, 200)
        shared_cat.refresh_from_db()
        self.assertEqual(shared_cat.name, "Kurse & Workshops Neu")

        r_edit_priv = c_staff.post("/calendar/categories/save/", {
            "pk": private_cat.id,
            "name": "Hacked",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r_edit_priv.status_code, 404)

        r_del_priv = c_staff.post(f"/calendar/categories/{private_cat.id}/delete/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r_del_priv.status_code, 404)

        c_ro = Client()
        c_ro.login(username="readonly", password="pw")
        r_ro_page = c_ro.get("/calendar/")
        self.assertEqual(r_ro_page.status_code, 200)
        self.assertNotContains(r_ro_page, 'id="calManageCatsBtn"')
        r_ro_del = c_ro.post(f"/calendar/categories/{shared_cat.id}/delete/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r_ro_del.status_code, 403)
        self.assertTrue(CalendarCategory.objects.filter(pk=shared_cat.id).exists())

        r_del_shared = c_staff.post(f"/calendar/categories/{shared_cat.id}/delete/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r_del_shared.status_code, 200)
        self.assertFalse(CalendarCategory.objects.filter(pk=shared_cat.id).exists())

    def test_billing_overview_year_filter(self):
        from cal_kit.models import CalendarEvent

        CalendarEvent.objects.create(
            user=self.user,
            title="Kurs 2025",
            start_at="2025-05-10T10:00Z",
            end_at="2025-05-10T12:00Z",
            allow_registration=True,
        )
        CalendarEvent.objects.create(
            user=self.user,
            title="Kurs 2026",
            start_at="2026-05-10T10:00Z",
            end_at="2026-05-10T12:00Z",
            allow_registration=True,
        )

        r_2025 = self.client.get("/calendar/billing/?year=2025")
        self.assertEqual(r_2025.status_code, 200)
        self.assertContains(r_2025, "Kurs 2025")
        self.assertNotContains(r_2025, "Kurs 2026")

        r_2026 = self.client.get("/calendar/billing/?year=2026")
        self.assertContains(r_2026, "Kurs 2026")
        self.assertNotContains(r_2026, "Kurs 2025")


if __name__ == "__main__":
    from django.test.runner import DiscoverRunner
    call_command("migrate", verbosity=0)
    runner = DiscoverRunner(verbosity=2)
    sys.exit(bool(runner.run_tests(["tests.test_cal_kit"])))
