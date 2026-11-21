from datetime import datetime, timedelta
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .ical import events_to_ics
from .models import RECURRENCE_CHOICES, CalendarCategory, CalendarEvent, CalendarEventException


def _parse_dt(raw: str, fallback=None):
    if not raw:
        return fallback
    val = raw.strip().replace("Z", "+00:00")
    if len(val) == 10:
        val += "T00:00:00"
    dt = datetime.fromisoformat(val)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


@login_required
def calendar_view(request):
    now = timezone.now()
    upcoming_reminders = (
        CalendarEvent.objects.filter(user=request.user, end_at__gte=now)
        .filter(Q(is_reminder=True) | Q(reminder_minutes__isnull=False))
        .order_by("start_at")[:6]
    )
    categories = CalendarCategory.objects.filter(user=request.user)
    return render(request, "cal_kit/calendar.html", {
        "upcoming_reminders": upcoming_reminders,
        "categories": categories,
    })


@login_required
def events_json_view(request):
    start_raw = request.GET.get("start")
    end_raw = request.GET.get("end")
    window_start = _parse_dt(start_raw, fallback=timezone.now() - timedelta(days=60))
    window_end = _parse_dt(end_raw, fallback=timezone.now() + timedelta(days=90))

    # Optional category filter
    cat_id = request.GET.get("category")

    non_recurring = CalendarEvent.objects.filter(
        user=request.user,
        recurrence_rule="none",
        end_at__gte=window_start,
        start_at__lte=window_end,
    ).select_related("category")
    recurring = CalendarEvent.objects.filter(
        user=request.user,
        start_at__lte=window_end,
    ).exclude(recurrence_rule="none").prefetch_related("exceptions").select_related("category")

    if cat_id:
        non_recurring = non_recurring.filter(category_id=cat_id)
        recurring = recurring.filter(category_id=cat_id)

    items = []

    def _add_event(ev, occ_start, occ_end, occ_title, occ_id):
        color = f"var(--theme-state-{ev.color_state}, var(--app-primary, #0d6efd))"
        items.append({
            "id": str(occ_id),
            "title": occ_title,
            "start": timezone.localtime(occ_start).isoformat() if timezone.is_aware(occ_start) else occ_start.isoformat(),
            "end": timezone.localtime(occ_end).isoformat() if timezone.is_aware(occ_end) else occ_end.isoformat(),
            "allDay": ev.all_day,
            "backgroundColor": color,
            "borderColor": color,
            "extendedProps": {
                "master_id": str(ev.id),
                "location": ev.location,
                "description": ev.description,
                "color_state": ev.color_state,
                "icon": ev.icon,
                "reminder_minutes": ev.reminder_minutes,
                "is_reminder": ev.is_reminder,
                "all_day": ev.all_day,
                "has_alarm": ev.reminder_minutes is not None,
                "has_location": bool(ev.location),
                "is_recurring": ev.is_recurring,
                "recurrence_rule": ev.recurrence_rule,
                "recurrence_interval": ev.recurrence_interval,
                "recurrence_until": ev.recurrence_until.isoformat() if ev.recurrence_until else "",
                "recurrence_weekdays": ev.recurrence_weekdays,
                "category_id": ev.category_id,
                "category_name": ev.category.name if ev.category else "",
            },
        })

    for ev in non_recurring:
        _add_event(ev, ev.start_at, ev.end_at, ev.title, ev.id)

    for ev in recurring:
        for occ_start, occ_end, occ_title in ev.get_occurrences(window_start, window_end):
            occ_id = f"{ev.id}_{occ_start.strftime('%Y%m%d%H%M')}"
            _add_event(ev, occ_start, occ_end, occ_title, occ_id)

    return JsonResponse(items, safe=False)


@login_required
@require_POST
def save_event_view(request):
    pk = (request.POST.get("pk") or "").strip()
    if "_" in pk:
        pk = pk.split("_")[0]
    ev = get_object_or_404(CalendarEvent, pk=pk, user=request.user) if pk else CalendarEvent(user=request.user)
    title = (request.POST.get("title") or "").strip()[:140]
    start_raw = request.POST.get("start_at", "")
    if not title or not start_raw:
        return JsonResponse({"status": "error", "message": "Titel und Start erforderlich."}, status=400)

    start_dt = _parse_dt(start_raw)
    end_dt = _parse_dt(request.POST.get("end_at", ""), fallback=start_dt + timedelta(hours=1))
    if end_dt < start_dt:
        end_dt = start_dt + timedelta(hours=1)

    ev.title = title
    ev.start_at = start_dt
    ev.end_at = end_dt
    ev.all_day = bool(request.POST.get("all_day"))
    ev.is_reminder = bool(request.POST.get("is_reminder"))
    raw_rem = (request.POST.get("reminder_minutes") or "").strip()
    if raw_rem.isdigit():
        ev.reminder_minutes = min(10080, int(raw_rem))
    else:
        ev.reminder_minutes = None
    ev.reminder_sent = False
    ev.location = (request.POST.get("location") or "").strip()[:140]
    ev.description = (request.POST.get("description") or "").strip()
    try:
        ev.color_state = max(1, min(8, int(request.POST.get("color_state") or 1)))
    except (TypeError, ValueError):
        ev.color_state = 1
    raw_icon = (request.POST.get("icon") or "").strip()[:40]
    ev.icon = raw_icon if raw_icon.startswith("bi-") else ""

    rule = (request.POST.get("recurrence_rule") or "none").strip()
    ev.recurrence_rule = rule if rule in dict(RECURRENCE_CHOICES) else "none"
    try:
        ev.recurrence_interval = max(1, min(99, int(request.POST.get("recurrence_interval") or 1)))
    except (TypeError, ValueError):
        ev.recurrence_interval = 1
    until_raw = (request.POST.get("recurrence_until") or "").strip()
    if until_raw:
        try:
            ev.recurrence_until = datetime.fromisoformat(until_raw).date()
        except Exception:
            ev.recurrence_until = None
    else:
        ev.recurrence_until = None

    # BYDAY weekdays (comma-sep digits 0-6)
    raw_wd = (request.POST.get("recurrence_weekdays") or "").strip()
    valid_wd = [d for d in raw_wd.split(",") if d.strip().isdigit() and 0 <= int(d.strip()) <= 6]
    ev.recurrence_weekdays = ",".join(valid_wd) if ev.recurrence_rule == "weekly" else ""

    # Category
    cat_id = (request.POST.get("category") or "").strip()
    if cat_id and cat_id.isdigit():
        try:
            ev.category = CalendarCategory.objects.get(pk=cat_id, user=request.user)
        except CalendarCategory.DoesNotExist:
            ev.category = None
    else:
        ev.category = None

    ev.save()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "id": ev.id})
    messages.success(request, _("Termin gespeichert."))
    return redirect("cal_kit:calendar")


@login_required
@require_POST
def move_event_view(request, pk):
    real_pk = pk.split("_")[0] if "_" in str(pk) else pk
    # Check if this is an occurrence id (has underscore = timestamp suffix)
    is_occurrence = "_" in str(pk)
    ev = get_object_or_404(CalendarEvent, pk=real_pk, user=request.user)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"status": "error"}, status=400)

    scope = payload.get("scope", "all")  # "this" or "all"

    new_start = _parse_dt(payload.get("start")) if payload.get("start") else None
    new_end = _parse_dt(payload.get("end")) if payload.get("end") else None
    all_day = payload.get("allDay")

    if ev.is_recurring and is_occurrence and scope == "this":
        # Create/update an exception for this occurrence
        orig_start_str = str(pk).split("_", 1)[1]  # YYYYmmddHHMM
        try:
            orig_start = timezone.make_aware(datetime.strptime(orig_start_str, "%Y%m%d%H%M"))
        except Exception:
            orig_start = ev.start_at
        exc, _ = CalendarEventException.objects.update_or_create(
            event=ev, original_start=orig_start,
            defaults={"new_start": new_start, "new_end": new_end, "cancelled": False}
        )
    else:
        if new_start:
            ev.start_at = new_start
        if new_end:
            ev.end_at = new_end
        elif ev.end_at < ev.start_at:
            ev.end_at = ev.start_at + timedelta(hours=1)
        if all_day is not None:
            ev.all_day = bool(all_day)
        ev.save()

    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def delete_event_view(request, pk):
    real_pk = pk.split("_")[0] if "_" in str(pk) else pk
    is_occurrence = "_" in str(pk)
    ev = get_object_or_404(CalendarEvent, pk=real_pk, user=request.user)

    scope = request.POST.get("scope", "all")  # "this" or "all"

    if ev.is_recurring and is_occurrence and scope == "this":
        # Cancel only this occurrence via exception
        orig_start_str = str(pk).split("_", 1)[1]
        try:
            orig_start = timezone.make_aware(datetime.strptime(orig_start_str, "%Y%m%d%H%M"))
        except Exception:
            orig_start = ev.start_at
        CalendarEventException.objects.update_or_create(
            event=ev, original_start=orig_start,
            defaults={"cancelled": True}
        )
    else:
        ev.delete()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    messages.success(request, _("Termin gelöscht."))
    return redirect("cal_kit:calendar")


@login_required
def ical_feed_view(request):
    events = CalendarEvent.objects.filter(user=request.user).prefetch_related("exceptions")
    ics = events_to_ics(events)
    resp = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = 'inline; filename="calendar.ics"'
    return resp


# ── Category CRUD ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def save_category_view(request):
    pk = (request.POST.get("pk") or "").strip()
    cat = get_object_or_404(CalendarCategory, pk=pk, user=request.user) if pk else CalendarCategory(user=request.user)
    cat.name = (request.POST.get("name") or "").strip()[:80]
    if not cat.name:
        return JsonResponse({"status": "error", "message": "Name erforderlich."}, status=400)
    try:
        cat.color_state = max(1, min(8, int(request.POST.get("color_state") or 1)))
    except (TypeError, ValueError):
        cat.color_state = 1
    raw_icon = (request.POST.get("icon") or "").strip()[:40]
    cat.icon = raw_icon if raw_icon.startswith("bi-") else ""
    cat.save()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "id": cat.id, "name": cat.name, "color_state": cat.color_state})
    return redirect("cal_kit:calendar")


@login_required
@require_POST
def delete_category_view(request, pk):
    cat = get_object_or_404(CalendarCategory, pk=pk, user=request.user)
    cat.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    return redirect("cal_kit:calendar")
