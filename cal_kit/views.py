from datetime import datetime, timedelta
import json
import logging
import re

from django.apps import apps
from django.conf import settings
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

logger = logging.getLogger(__name__)


def _get_domain_location_options() -> list[dict]:
    """Return structured location options from domain_kit Addresses and Resource AssetItems."""
    if not apps.is_installed("domain_kit"):
        return []
    options = []
    try:
        AssetItem = apps.get_model("domain_kit", "AssetItem")
        for r in AssetItem.objects.filter(asset_type="resource").select_related("address").order_by("title"):
            loc_str = r.title
            if r.address:
                loc_str = f"{r.title} ({r.address.city})" if r.address.city else r.title
            options.append({
                "ref": f"asset:{r.pk}",
                "kind": "resource",
                "group": "Räume & Ressourcen",
                "label": loc_str[:140],
            })
    except Exception:
        pass
    try:
        Address = apps.get_model("domain_kit", "Address")
        for a in Address.objects.all().order_by("label", "city", "street"):
            addr_parts = [p for p in [a.street, f"{a.postal_code} {a.city}".strip()] if p]
            addr_txt = ", ".join(addr_parts)
            lbl = f"{a.label}: {addr_txt}" if a.label and addr_txt else (addr_txt or a.label)
            if lbl:
                options.append({
                    "ref": f"addr:{a.pk}",
                    "kind": "address",
                    "group": "Adressen & Standorte",
                    "label": lbl[:140],
                })
    except Exception:
        pass
    return options


def _find_location_conflicts(start_dt, end_dt, location: str = "", resource_ref: str = "", exclude_pk=None) -> list[dict]:
    """Detect overlapping events at the same resource_ref or location across all users."""
    loc = (location or "").strip()
    ref = (resource_ref or "").strip()
    if not ref and not loc:
        return []
    if not start_dt or not end_dt:
        return []

    loc_filter = Q()
    if ref:
        loc_filter |= Q(resource_ref=ref)
    if loc:
        loc_filter |= Q(location__iexact=loc)

    qs = CalendarEvent.objects.filter(loc_filter).select_related("user")
    if exclude_pk and str(exclude_pk).isdigit():
        qs = qs.exclude(pk=int(exclude_pk))

    conflicts = []
    non_rec = qs.filter(recurrence_rule="none", start_at__lt=end_dt, end_at__gt=start_dt)
    for ev in non_rec[:10]:
        s_local = timezone.localtime(ev.start_at) if timezone.is_aware(ev.start_at) else ev.start_at
        e_local = timezone.localtime(ev.end_at) if timezone.is_aware(ev.end_at) else ev.end_at
        conflicts.append({
            "id": ev.id,
            "title": ev.title,
            "location": ev.location,
            "start": s_local.strftime("%d.%m.%Y %H:%M"),
            "end": e_local.strftime("%H:%M"),
            "owner": ev.user.get_username(),
        })

    win_s = start_dt - timedelta(days=1)
    win_e = end_dt + timedelta(days=1)
    rec = qs.exclude(recurrence_rule="none").filter(start_at__lte=win_e).prefetch_related("exceptions")
    for ev in rec[:30]:
        for occ_s, occ_e, occ_t in ev.get_occurrences(win_s, win_e):
            if occ_s < end_dt and occ_e > start_dt:
                s_local = timezone.localtime(occ_s) if timezone.is_aware(occ_s) else occ_s
                e_local = timezone.localtime(occ_e) if timezone.is_aware(occ_e) else occ_e
                conflicts.append({
                    "id": ev.id,
                    "title": occ_t,
                    "location": ev.location,
                    "start": s_local.strftime("%d.%m.%Y %H:%M"),
                    "end": e_local.strftime("%H:%M"),
                    "owner": ev.user.get_username(),
                })
                break
    return conflicts[:10]


def _parse_dt(raw: str, fallback=None):
    if not raw:
        return fallback
    val = raw.strip().replace("Z", "+00:00")
    # Unencoded '+' in URL query strings (e.g. +01:00) is decoded as space by Django
    val = re.sub(r" (\d{2}:\d{2})$", r"+\1", val)
    if len(val) == 10:
        val += "T00:00:00"
    try:
        dt = datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return fallback
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


@login_required
def calendar_view(request):
    from .reminders import dispatch_due_cal_reminders
    try:
        dispatch_due_cal_reminders(user=request.user)
    except Exception:
        pass
    now = timezone.now()
    upcoming_reminders = (
        CalendarEvent.objects.filter(user=request.user, end_at__gte=now)
        .filter(Q(is_reminder=True) | Q(reminder_minutes__isnull=False))
        .order_by("start_at")[:6]
    )
    categories = CalendarCategory.objects.filter(Q(user=request.user) | Q(is_shared=True)).distinct()
    location_options = _get_domain_location_options()
    instructors = _get_domain_instructors()
    return render(request, "cal_kit/calendar.html", {
        "upcoming_reminders": upcoming_reminders,
        "categories": categories,
        "location_options": location_options,
        "instructors": instructors,
    })


@login_required
def conflict_check_view(request):
    start_dt = _parse_dt(request.GET.get("start", ""))
    end_dt = _parse_dt(request.GET.get("end", ""))
    if start_dt and not end_dt:
        end_dt = start_dt + timedelta(hours=1)
    location = (request.GET.get("location") or "").strip()
    resource_ref = (request.GET.get("resource_ref") or "").strip()
    exclude_pk = (request.GET.get("exclude_pk") or "").strip()
    if "_" in exclude_pk:
        exclude_pk = exclude_pk.split("_")[0]
    conflicts = _find_location_conflicts(start_dt, end_dt, location=location, resource_ref=resource_ref, exclude_pk=exclude_pk)
    return JsonResponse({"conflicts": conflicts})


@login_required
def events_json_view(request):
    from .reminders import dispatch_due_cal_reminders
    try:
        dispatch_due_cal_reminders(user=request.user)
    except Exception:
        pass
    start_raw = request.GET.get("start")
    end_raw = request.GET.get("end")
    window_start = _parse_dt(start_raw, fallback=timezone.now() - timedelta(days=60))
    window_end = _parse_dt(end_raw, fallback=timezone.now() + timedelta(days=90))

    # Optional multi-calendar filter: comma-separated category IDs and/or "personal"
    cat_raw = (request.GET.get("category") or "").strip()
    cat_filter = None
    if cat_raw:
        parts = [p.strip() for p in cat_raw.split(",") if p.strip()]
        cat_ids = [int(p) for p in parts if p.isdigit()]
        include_personal = "personal" in parts
        cat_q = Q()
        if cat_ids:
            cat_q |= Q(category_id__in=cat_ids)
        if include_personal:
            cat_q |= Q(category__isnull=True)
        if cat_q:
            cat_filter = cat_q
        elif parts:
            cat_filter = Q(pk__in=[])

    visible_filter = Q(user=request.user) | Q(category__is_shared=True)
    non_recurring = CalendarEvent.objects.filter(
        visible_filter,
        recurrence_rule="none",
        end_at__gte=window_start,
        start_at__lte=window_end,
    ).select_related("category").distinct()
    recurring = CalendarEvent.objects.filter(
        visible_filter,
        start_at__lte=window_end,
    ).exclude(recurrence_rule="none").prefetch_related("exceptions").select_related("category").distinct()

    if cat_filter is not None:
        non_recurring = non_recurring.filter(cat_filter)
        recurring = recurring.filter(cat_filter)

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
                "resource_ref": ev.resource_ref,
                "instructor_ref": ev.instructor_ref,
                "instructor_name": ev.instructor_name,
                "allow_registration": ev.allow_registration,
                "max_participants": ev.max_participants,
                "registration_count": _event_registration_count(ev.id) if ev.allow_registration else 0,
                "description": ev.description,
                "color_state": ev.color_state,
                "icon": ev.icon,
                "reminder_minutes": ev.reminder_minutes,
                "is_reminder": ev.is_reminder,
                "is_public": ev.is_public,
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
                "read_only": (
                    ev.user_id != request.user.id
                    and not _has_shared_editor_access(request.user)
                    and ev.instructor_ref not in _user_instructor_refs(request.user)
                ),
            },
        })

    for ev in non_recurring:
        _add_event(ev, ev.start_at, ev.end_at, ev.title, ev.id)

    for ev in recurring:
        for occ_start, occ_end, occ_title in ev.get_occurrences(window_start, window_end):
            occ_id = f"{ev.id}_{occ_start.strftime('%Y%m%d%H%M')}"
            _add_event(ev, occ_start, occ_end, occ_title, occ_id)

    # Invoke configured external event providers (e.g. Buckezz list items) when no specific category filter is active
    if not cat_raw:
        providers = getattr(settings, "CAL_KIT_EVENT_PROVIDERS", [])
        for prov in providers:
            try:
                if isinstance(prov, str):
                    import importlib
                    mod_name, func_name = prov.rsplit(".", 1)
                    prov_fn = getattr(importlib.import_module(mod_name), func_name)
                else:
                    prov_fn = prov
                extra = prov_fn(request, window_start, window_end)
                if extra:
                    items.extend(extra)
            except Exception:
                logger.exception("CAL_KIT_EVENT_PROVIDERS error in %r", prov)

    return JsonResponse(items, safe=False)


def _get_domain_instructors():
    if not apps.is_installed("domain_kit"):
        return {"lead": [], "other": []}
    try:
        from domain_kit.models import Person

        lead_keywords = ("dozent", "referent", "kursleiter", "trainer", "vorstand", "leitung")
        lead = []
        other = []
        qs = Person.objects.select_related("category").prefetch_related("roles").order_by("last_name", "first_name")[:300]
        for p in qs:
            role_names = [r.name for r in p.roles.all()]
            meta = []
            if role_names:
                meta.append(", ".join(role_names))
            elif p.category:
                meta.append(p.category.name)
            if p.email:
                meta.append(p.email)
            elif p.company:
                meta.append(p.company)
            label = f"{p.full_name} – {' · '.join(meta)}" if meta else p.full_name
            entry = {"ref": f"person:{p.id}", "label": label}
            is_lead = any(any(kw in rn.lower() for kw in lead_keywords) for rn in role_names) or bool(p.is_public)
            if is_lead:
                lead.append(entry)
            else:
                other.append(entry)
        return {"lead": lead, "other": other}
    except Exception:
        return {"lead": [], "other": []}


def _user_instructor_refs(user) -> set[str]:
    if not user or not getattr(user, "is_authenticated", False) or not apps.is_installed("domain_kit"):
        return set()
    try:
        from domain_kit.models import Person
        return {f"person:{pid}" for pid in Person.objects.filter(user=user).values_list("id", flat=True)}
    except Exception:
        return set()


def _event_registration_count(event_id: int) -> int:
    if not apps.is_installed("list_kit"):
        return 0
    try:
        from list_kit.models import Entry
        return (
            Entry.objects.filter(
                collection__template__slug="kurs-anmeldung",
                custom_data_json__event_id=str(event_id),
            )
            .exclude(status__iexact="Abgelehnt")
            .exclude(status__iexact="Abgesagt")
            .count()
        )
    except Exception:
        return 0


def _has_shared_editor_access(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return user.groups.filter(name__in=("Redakteur", "Leitung / Vorstand", "Vorstand")).exists()


@login_required
@require_POST
def save_event_view(request):
    pk = (request.POST.get("pk") or "").strip()
    if "_" in pk:
        pk = pk.split("_")[0]
    if pk:
        inst_refs = _user_instructor_refs(request.user)
        if _has_shared_editor_access(request.user):
            ev = get_object_or_404(CalendarEvent, Q(pk=pk) & (Q(user=request.user) | Q(category__is_shared=True)))
        elif inst_refs:
            ev = get_object_or_404(CalendarEvent, Q(pk=pk) & (Q(user=request.user) | Q(instructor_ref__in=inst_refs)))
        else:
            ev = get_object_or_404(CalendarEvent, pk=pk, user=request.user)
    else:
        ev = CalendarEvent(user=request.user)
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
    if "is_public" in request.POST or "form_has_public_flag" in request.POST:
        ev.is_public = bool(request.POST.get("is_public"))
    raw_rem = (request.POST.get("reminder_minutes") or "").strip()
    if raw_rem.isdigit():
        ev.reminder_minutes = min(10080, int(raw_rem))
    else:
        ev.reminder_minutes = None
    ev.reminder_sent = False
    ev.location = (request.POST.get("location") or "").strip()[:140]
    ev.resource_ref = (request.POST.get("resource_ref") or "").strip()[:64]
    if "instructor_ref" in request.POST or "form_has_instructor" in request.POST:
        iref = (request.POST.get("instructor_ref") or "").strip()[:64]
        ev.instructor_ref = iref
        iname = (request.POST.get("instructor_name") or "").strip()[:140]
        if iref.startswith("person:") and not iname and apps.is_installed("domain_kit"):
            try:
                from domain_kit.models import Person
                p_obj = Person.objects.filter(pk=int(iref.split(":", 1)[1])).first()
                iname = p_obj.full_name if p_obj else ""
            except Exception:
                pass
        ev.instructor_name = iname
    if "allow_registration" in request.POST or "form_has_registration" in request.POST:
        ev.allow_registration = bool(request.POST.get("allow_registration"))
    if "max_participants" in request.POST:
        raw_mp = (request.POST.get("max_participants") or "").strip()
        ev.max_participants = int(raw_mp) if raw_mp.isdigit() and int(raw_mp) > 0 else None
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
            ev.category = CalendarCategory.objects.filter(Q(pk=cat_id) & (Q(user=request.user) | Q(is_shared=True))).first()
            if ev.category and ev.category.is_public_default and "is_public" not in request.POST and "form_has_public_flag" not in request.POST:
                ev.is_public = True
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
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"status": "error"}, status=400)

    real_pk = str(pk).split("_")[0] if "_" in str(pk) else str(pk)
    if not real_pk.isdigit():
        handlers = getattr(settings, "CAL_KIT_MOVE_HANDLERS", [])
        for handler in handlers:
            try:
                if isinstance(handler, str):
                    import importlib
                    mod_name, func_name = handler.rsplit(".", 1)
                    handler_fn = getattr(importlib.import_module(mod_name), func_name)
                else:
                    handler_fn = handler
                res = handler_fn(request, str(pk), payload)
                if res:
                    return JsonResponse({"status": "ok"})
            except Exception:
                logger.exception("CAL_KIT_MOVE_HANDLERS error in %r", handler)
        return JsonResponse({"status": "error", "message": "Event cannot be moved."}, status=400)

    # Check if this is an occurrence id (has underscore = timestamp suffix)
    is_occurrence = "_" in str(pk)
    ev = get_object_or_404(CalendarEvent, pk=real_pk, user=request.user)

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


def ical_feed_view(request):
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    token = (request.GET.get("token") or "").strip()
    if not user and token:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            from core.models import Person
            p = Person.objects.filter(access_token=token).select_related("user").first()
            if p and p.user:
                user = p.user
        except Exception:
            pass
        if not user:
            try:
                from django.core.signing import TimestampSigner
                signer = TimestampSigner()
                user_id = signer.unsign(token, max_age=365 * 86400)
                user = User.objects.filter(pk=user_id).first()
            except Exception:
                pass

    if not user:
        return HttpResponse("Unauthorized (bitte anmelden oder gültigen Feed-Token angeben)", status=401)

    cal_name = (
        getattr(settings, "CAL_KIT_CALENDAR_NAME", "")
        or getattr(settings, "DESIGN_STUDIO_APP_NAME", "")
        or "Kalender"
    )
    events = CalendarEvent.objects.filter(user=user).prefetch_related("exceptions")
    ics = events_to_ics(events, cal_name=cal_name)

    # Optional: append provider ICS events (e.g. Buckezz list deadlines)
    try:
        from core.calendar_provider import get_buckezz_ical_events
        extra_lines = get_buckezz_ical_events(user)
        if extra_lines:
            idx = ics.rfind("END:VCALENDAR")
            if idx != -1:
                ics = ics[:idx] + extra_lines + "\r\n" + ics[idx:]
    except Exception:
        pass

    from django.utils.text import slugify
    fname = slugify(cal_name) or "calendar"
    resp = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = f'inline; filename="{fname}.ics"'
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
    cat.is_shared = bool(request.POST.get("is_shared"))
    cat.is_public_default = bool(request.POST.get("is_public_default"))
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
