from datetime import datetime, timedelta
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .ical import events_to_ics
from .models import CalendarEvent


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
    return render(request, "cal_kit/calendar.html")


@login_required
def events_json_view(request):
    qs = CalendarEvent.objects.filter(user=request.user)
    start_raw = request.GET.get("start")
    end_raw = request.GET.get("end")
    if start_raw:
        try:
            qs = qs.filter(end_at__gte=_parse_dt(start_raw))
        except ValueError:
            pass
    if end_raw:
        try:
            qs = qs.filter(start_at__lte=_parse_dt(end_raw))
        except ValueError:
            pass

    items = []
    for ev in qs:
        items.append({
            "id": str(ev.id),
            "title": ev.title,
            "start": timezone.localtime(ev.start_at).isoformat() if timezone.is_aware(ev.start_at) else ev.start_at.isoformat(),
            "end": timezone.localtime(ev.end_at).isoformat() if timezone.is_aware(ev.end_at) else ev.end_at.isoformat(),
            "allDay": ev.all_day,
            "backgroundColor": f"var(--theme-state-{ev.color_state}, var(--app-primary, #0d6efd))",
            "borderColor": f"var(--theme-state-{ev.color_state}, var(--app-primary, #0d6efd))",
            "extendedProps": {
                "location": ev.location,
                "description": ev.description,
                "color_state": ev.color_state,
            },
        })
    return JsonResponse(items, safe=False)


@login_required
@require_POST
def save_event_view(request):
    pk = (request.POST.get("pk") or "").strip()
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
    ev.location = (request.POST.get("location") or "").strip()[:140]
    ev.description = (request.POST.get("description") or "").strip()
    try:
        ev.color_state = max(1, min(8, int(request.POST.get("color_state") or 1)))
    except (TypeError, ValueError):
        ev.color_state = 1
    ev.save()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "id": ev.id})
    messages.success(request, _("Termin gespeichert."))
    return redirect("cal_kit:calendar")


@login_required
@require_POST
def move_event_view(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk, user=request.user)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"status": "error"}, status=400)

    if payload.get("start"):
        ev.start_at = _parse_dt(payload["start"])
    if payload.get("end"):
        ev.end_at = _parse_dt(payload["end"])
    elif ev.end_at < ev.start_at:
        ev.end_at = ev.start_at + timedelta(hours=1)
    if "allDay" in payload:
        ev.all_day = bool(payload["allDay"])
    ev.save()
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def delete_event_view(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk, user=request.user)
    ev.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    messages.success(request, _("Termin gelöscht."))
    return redirect("cal_kit:calendar")


@login_required
def ical_feed_view(request):
    events = CalendarEvent.objects.filter(user=request.user)
    ics = events_to_ics(events)
    resp = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = 'inline; filename="calendar.ics"'
    return resp
