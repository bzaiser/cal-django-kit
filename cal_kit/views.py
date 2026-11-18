import calendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .ical import events_to_ics
from .models import CalendarEvent


@login_required
def calendar_view(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        if not (1 <= month <= 12):
            month = today.month
    except (TypeError, ValueError):
        year, month = today.year, today.month

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        start_raw = request.POST.get("start_at", "")
        end_raw = request.POST.get("end_at", "")
        if title and start_raw:
            start_dt = timezone.make_aware(datetime.fromisoformat(start_raw)) if timezone.is_naive(datetime.fromisoformat(start_raw)) else datetime.fromisoformat(start_raw)
            end_dt = timezone.make_aware(datetime.fromisoformat(end_raw)) if end_raw else start_dt + timedelta(hours=1)
            CalendarEvent.objects.create(
                user=request.user,
                title=title[:140],
                start_at=start_dt,
                end_at=end_dt,
                all_day=bool(request.POST.get("all_day")),
                location=(request.POST.get("location") or "").strip()[:140],
                description=(request.POST.get("description") or "").strip(),
            )
            messages.success(request, _("Termin gespeichert."))
            return redirect(f"{request.path}?year={year}&month={month}")

    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(year, month)
    events = CalendarEvent.objects.filter(
        user=request.user,
        start_at__date__gte=weeks[0][0],
        start_at__date__lte=weeks[-1][-1],
    )
    by_day = {}
    for ev in events:
        by_day.setdefault(timezone.localdate(ev.start_at) if timezone.is_aware(ev.start_at) else ev.start_at.date(), []).append(ev)

    prev_month = (date(year, month, 1) - timedelta(days=1))
    next_month = (date(year, month, 28) + timedelta(days=4))
    return render(
        request,
        "cal_kit/calendar.html",
        {
            "year": year,
            "month": month,
            "month_name": date(year, month, 1).strftime("%B %Y"),
            "weeks": [[(d, by_day.get(d, [])) for d in w] for w in weeks],
            "today": today,
            "prev_year": prev_month.year,
            "prev_month": prev_month.month,
            "next_year": next_month.year,
            "next_month": next_month.month,
            "events": events,
        },
    )


@login_required
@require_POST
def delete_event_view(request, pk):
    ev = get_object_or_404(CalendarEvent, pk=pk, user=request.user)
    ev.delete()
    messages.success(request, _("Termin gelöscht."))
    return redirect("cal_kit:calendar")


@login_required
def ical_feed_view(request):
    events = CalendarEvent.objects.filter(user=request.user)
    ics = events_to_ics(events)
    resp = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = 'inline; filename="calendar.ics"'
    return resp
