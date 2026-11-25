import csv
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
from .models import RECURRENCE_CHOICES, CalendarCategory, CalendarEvent, CalendarEventException, OfferingTemplate
from .services import (
    get_cal_profile_labels,
    get_contract_options,
    get_event_billing_summary,
    parse_decimal_or_none,
    resolve_person_name,
)

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
        addr_qs = (
            Address.objects.select_related("category")
            .filter(
                Q(person__isnull=True)
                | Q(category__purpose="location")
                | Q(category__name__icontains="standort")
                | Q(category__name__icontains="veranstaltung")
                | Q(category__name__icontains="verein")
            )
            .exclude(Q(person__isnull=False) & Q(category__name__icontains="privat"))
            .distinct()
            .order_by("label", "city", "street")
        )
        for a in addr_qs:
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
        "offering_templates": OfferingTemplate.objects.order_by("title"),
        "cal_labels": get_cal_profile_labels(),
        "cal_active_tab": "calendar",
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
                "offering_template_id": ev.offering_template_id,
                "image_url": ev.resolved_image_url,
                "has_own_image": bool(ev.image),
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
    if getattr(user, "is_superuser", False):
        return True
    return user.groups.filter(name__in=("Redakteur", "Pressewart", "Leitung / Vorstand", "Vorstand", "Schriftführer")).exists()


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
    if "responsible_ref" in request.POST or "responsible_name" in request.POST:
        rref = (request.POST.get("responsible_ref") or "").strip()[:64]
        ev.responsible_ref = rref
        rname = (request.POST.get("responsible_name") or "").strip()[:140]
        if rref.startswith("person:") and not rname:
            rname = resolve_person_name(rref)
        ev.responsible_name = rname
    if "contract_ref" in request.POST:
        ev.contract_ref = (request.POST.get("contract_ref") or "").strip()[:64]
    if "fee_amount" in request.POST:
        ev.fee_amount = parse_decimal_or_none(request.POST.get("fee_amount"))
    if "offering_template_id" in request.POST:
        otid = (request.POST.get("offering_template_id") or "").strip()
        ev.offering_template = OfferingTemplate.objects.filter(pk=int(otid)).first() if otid.isdigit() else None
    if "allow_registration" in request.POST or "form_has_registration" in request.POST:
        ev.allow_registration = bool(request.POST.get("allow_registration"))
    if "max_participants" in request.POST:
        raw_mp = (request.POST.get("max_participants") or "").strip()
        ev.max_participants = int(raw_mp) if raw_mp.isdigit() and int(raw_mp) > 0 else None
    ev.description = (request.POST.get("description") or "").strip()
    if request.POST.get("remove_image") == "1" and ev.image:
        ev.image.delete(save=False)
        ev.image = None
    if "image" in request.FILES:
        ev.image = request.FILES["image"]
    try:
        ev.color_state = max(1, min(8, int(request.POST.get("color_state") or 1)))
    except (TypeError, ValueError):
        ev.color_state = 1
    raw_icon = (request.POST.get("icon") or "").strip()[:40]
    ev.icon = raw_icon if (raw_icon.startswith("bi-") or raw_icon == "none") else ""

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


# ── Offerings & Billing ────────────────────────────────────────────────────────

@login_required
def offering_list_view(request):
    cal_labels = get_cal_profile_labels()
    can_manage = _has_shared_editor_access(request.user) or getattr(request.user, "is_staff", False)
    if request.method == "POST":
        pk = (request.POST.get("pk") or "").strip()
        if pk:
            tpl = get_object_or_404(OfferingTemplate, pk=pk)
            if tpl.user_id != request.user.id and not can_manage:
                return HttpResponse("Kein Zugriff.", status=403)
        else:
            tpl = OfferingTemplate(user=request.user)

        tpl.title = (request.POST.get("title") or "").strip()[:160] or "Angebot"
        cat_id = (request.POST.get("category") or "").strip()
        tpl.category = CalendarCategory.objects.filter(pk=int(cat_id)).first() if cat_id.isdigit() else None
        tpl.description = (request.POST.get("description") or "").strip()
        tpl.bring_along_note = (request.POST.get("bring_along_note") or "").strip()
        tpl.material_note = (request.POST.get("material_note") or "").strip()[:200]
        tpl.default_location = (request.POST.get("default_location") or "").strip()[:140]
        try:
            tpl.default_duration_minutes = max(15, min(1440, int(request.POST.get("default_duration_minutes") or 120)))
        except (TypeError, ValueError):
            tpl.default_duration_minutes = 120
        raw_min = (request.POST.get("min_participants") or "").strip()
        tpl.min_participants = int(raw_min) if raw_min.isdigit() and int(raw_min) > 0 else None
        raw_max = (request.POST.get("max_participants") or "").strip()
        tpl.max_participants = int(raw_max) if raw_max.isdigit() and int(raw_max) > 0 else None

        iref = (request.POST.get("instructor_ref") or "").strip()[:64]
        tpl.instructor_ref = iref
        tpl.instructor_name = (request.POST.get("instructor_name") or "").strip()[:140] or resolve_person_name(iref)

        rref = (request.POST.get("responsible_ref") or "").strip()[:64]
        tpl.responsible_ref = rref
        tpl.responsible_name = (request.POST.get("responsible_name") or "").strip()[:140] or resolve_person_name(rref)

        tpl.contract_ref = (request.POST.get("contract_ref") or "").strip()[:64]
        tpl.fee_amount = parse_decimal_or_none(request.POST.get("fee_amount"))
        tpl.fee_non_member = parse_decimal_or_none(request.POST.get("fee_non_member"))
        tpl.fee_reduced = parse_decimal_or_none(request.POST.get("fee_reduced"))
        tpl.honorarium_amount = parse_decimal_or_none(request.POST.get("honorarium_amount"))
        if tpl.honorarium_amount is None and tpl.contract_ref.startswith("contract:") and apps.is_installed("domain_kit"):
            try:
                from domain_kit.models import AssetItem
                c_obj = AssetItem.objects.filter(pk=int(tpl.contract_ref.split(":", 1)[1])).first()
                if c_obj and c_obj.quantity:
                    tpl.honorarium_amount = c_obj.quantity
            except Exception:
                pass
        tpl.show_in_program = bool(request.POST.get("show_in_program"))
        if request.FILES.get("image"):
            tpl.image = request.FILES["image"]
        elif request.POST.get("remove_image") == "1":
            tpl.image = None
        if "image_url" in request.POST:
            tpl.image_url = (request.POST.get("image_url") or "").strip()[:200]
        tpl.save()
        messages.success(request, f"{cal_labels['offering_singular']} „{tpl.title}“ gespeichert.")
        return redirect("cal_kit:offering_list")

    qs = OfferingTemplate.objects.select_related("category", "user").prefetch_related("events")
    if not can_manage:
        qs = qs.filter(user=request.user)
    categories = CalendarCategory.objects.filter(Q(user=request.user) | Q(is_shared=True)).distinct()
    return render(request, "cal_kit/offerings.html", {
        "offerings": qs,
        "categories": categories,
        "instructors": _get_domain_instructors(),
        "location_options": _get_domain_location_options(),
        "contract_options": get_contract_options(),
        "cal_labels": cal_labels,
        "cal_active_tab": "offerings",
    })


@login_required
@require_POST
def offering_create_event_view(request, pk):
    tpl = get_object_or_404(OfferingTemplate, pk=pk)
    start_date_raw = (request.POST.get("start_date") or "").strip()
    start_time_raw = (request.POST.get("start_time") or "").strip()
    end_time_raw = (request.POST.get("end_time") or "").strip()

    if start_date_raw:
        st_time = start_time_raw or "10:00"
        start_dt = _parse_dt(f"{start_date_raw}T{st_time}")
        if start_dt and end_time_raw:
            end_dt = _parse_dt(f"{start_date_raw}T{end_time_raw}")
        else:
            end_dt = None
    else:
        start_dt = _parse_dt(request.POST.get("start_at") or "")
        end_dt = _parse_dt(request.POST.get("end_at") or "")

    if not start_dt:
        start_dt = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=7)
    if not end_dt or end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=tpl.default_duration_minutes or 120)

    r_ref = (request.POST.get("resource_ref") or "").strip()[:64]
    loc = (request.POST.get("location") or "").strip()[:140]
    if r_ref and not loc:
        for opt in _get_domain_location_options():
            if opt["ref"] == r_ref:
                loc = opt["label"][:140]
                break
    if not loc:
        loc = tpl.default_location[:140]

    rule = (request.POST.get("recurrence_rule") or "none").strip()
    if rule not in dict(RECURRENCE_CHOICES):
        rule = "none"
    try:
        interval = max(1, min(99, int(request.POST.get("recurrence_interval") or 1)))
    except (TypeError, ValueError):
        interval = 1

    wd_list = [d.strip() for d in request.POST.getlist("recurrence_weekdays") if d.strip().isdigit() and 0 <= int(d.strip()) <= 6]
    if not wd_list:
        raw_wd = (request.POST.get("recurrence_weekdays") or "").strip()
        wd_list = [d.strip() for d in raw_wd.split(",") if d.strip().isdigit() and 0 <= int(d.strip()) <= 6]
    if rule == "weekly" and not wd_list:
        wd_list = [str(timezone.localtime(start_dt).weekday())]
    weekdays_str = ",".join(wd_list) if rule == "weekly" else ""

    until_date = None
    until_raw = (request.POST.get("recurrence_until") or "").strip()
    repeat_raw = (request.POST.get("repeat_count") or "").strip()
    if until_raw:
        try:
            until_date = datetime.fromisoformat(until_raw).date()
        except Exception:
            until_date = None
    elif rule != "none" and repeat_raw.isdigit() and int(repeat_raw) > 1:
        cnt = min(104, int(repeat_raw))
        base_d = timezone.localtime(start_dt).date()
        if rule == "daily":
            until_date = base_d + timedelta(days=(cnt - 1) * interval)
        elif rule == "weekly":
            per_week = max(1, len(wd_list))
            weeks_needed = max(0, (cnt - 1) // per_week)
            until_date = base_d + timedelta(weeks=weeks_needed * interval, days=6)
        elif rule == "monthly":
            until_date = base_d + timedelta(days=31 * (cnt - 1) * interval)
        elif rule == "yearly":
            until_date = base_d + timedelta(days=366 * (cnt - 1) * interval)

    ev = CalendarEvent.objects.create(
        user=request.user,
        category=tpl.category,
        offering_template=tpl,
        title=(request.POST.get("title") or tpl.title)[:140],
        start_at=start_dt,
        end_at=end_dt,
        location=loc,
        resource_ref=r_ref,
        instructor_ref=tpl.instructor_ref,
        instructor_name=tpl.instructor_name,
        responsible_ref=tpl.responsible_ref,
        responsible_name=tpl.responsible_name,
        contract_ref=tpl.contract_ref,
        fee_amount=tpl.fee_amount,
        max_participants=tpl.max_participants,
        allow_registration=True,
        is_public=True,
        description=tpl.description,
        color_state=tpl.category.color_state if tpl.category else 1,
        recurrence_rule=rule,
        recurrence_interval=interval,
        recurrence_until=until_date,
        recurrence_weekdays=weekdays_str,
    )
    messages.success(request, f"Termin „{ev.title}“ aus Vorlage erstellt.")
    return redirect("cal_kit:event_billing", pk=ev.id)


@login_required
@require_POST
def offering_delete_view(request, pk):
    tpl = get_object_or_404(OfferingTemplate, pk=pk)
    if tpl.user_id != request.user.id and not _has_shared_editor_access(request.user):
        return HttpResponse("Kein Zugriff.", status=403)
    tpl.delete()
    messages.success(request, "Vorlage gelöscht.")
    return redirect("cal_kit:offering_list")


@login_required
def billing_overview_view(request):
    cal_labels = get_cal_profile_labels()
    can_manage = _has_shared_editor_access(request.user) or getattr(request.user, "is_staff", False)
    qs = CalendarEvent.objects.select_related("category", "offering_template").filter(
        Q(offering_template__isnull=False) | Q(allow_registration=True)
    )
    if not can_manage:
        irefs = _user_instructor_refs(request.user)
        q_own = Q(user=request.user)
        if irefs:
            q_own |= Q(instructor_ref__in=irefs) | Q(responsible_ref__in=irefs)
        qs = qs.filter(q_own)
    rows = []
    total_expected = 0
    total_paid = 0
    total_net = 0
    for ev in qs.order_by("-start_at")[:100]:
        summary = get_event_billing_summary(ev)
        rows.append({"event": ev, "summary": summary})
        total_expected += summary["expected_revenue"]
        total_paid += summary["paid_revenue"]
        total_net += summary["net_result"]

    return render(request, "cal_kit/billing_overview.html", {
        "rows": rows,
        "total_expected": total_expected,
        "total_paid": total_paid,
        "total_net": total_net,
        "cal_labels": cal_labels,
        "cal_active_tab": "billing",
    })


@login_required
def event_billing_view(request, pk):
    ev = get_object_or_404(CalendarEvent.objects.select_related("category", "offering_template"), pk=pk)
    can_manage = _has_shared_editor_access(request.user) or ev.user_id == request.user.id
    if not can_manage and ev.instructor_ref not in _user_instructor_refs(request.user):
        return HttpResponse("Kein Zugriff.", status=403)
    summary = get_event_billing_summary(ev)
    return render(request, "cal_kit/event_billing.html", {
        "event": ev,
        "summary": summary,
        "has_finance_kit": apps.is_installed("finance_kit"),
        "cal_labels": get_cal_profile_labels(),
        "cal_active_tab": "billing",
    })


@login_required
def event_billing_csv_view(request, pk):
    ev = get_object_or_404(CalendarEvent.objects.select_related("category", "offering_template"), pk=pk)
    can_manage = _has_shared_editor_access(request.user) or ev.user_id == request.user.id
    if not can_manage and ev.instructor_ref not in _user_instructor_refs(request.user):
        return HttpResponse("Kein Zugriff.", status=403)
    summary = get_event_billing_summary(ev)
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="abrechnung-{ev.id}.csv"'
    resp.write("\ufeff")
    writer = csv.writer(resp, delimiter=";")
    writer.writerow(["Name", "E-Mail", "Status", "Bezahlt", "Gebühr (€)"])
    for e in summary["entries"]:
        data = e.custom_data_json or {}
        is_paid = bool(data.get("is_paid"))
        email = data.get("absender_email") or data.get("email") or ""
        writer.writerow([
            data.get("absender_name") or e.title,
            email,
            e.status,
            "Ja" if is_paid else "Nein",
            f"{summary['fee_per_person']:.2f}" if (e.status or "").lower() not in ("abgesagt", "abgelehnt") else "0.00",
        ])
    return resp


@login_required
def event_credit_note_pdf_view(request, pk):
    ev = get_object_or_404(CalendarEvent.objects.select_related("category", "offering_template"), pk=pk)
    can_manage = _has_shared_editor_access(request.user) or ev.user_id == request.user.id
    if not can_manage and ev.instructor_ref not in _user_instructor_refs(request.user):
        return HttpResponse("Kein Zugriff.", status=403)
    summary = get_event_billing_summary(ev)

    instructor_address = ""
    instructor_tax_id = ""
    if ev.instructor_ref and ev.instructor_ref.startswith("person:") and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person
            pid = int(ev.instructor_ref.split(":", 1)[1])
            p = Person.objects.prefetch_related("addresses").filter(pk=pid).first()
            if p:
                instructor_tax_id = p.tax_id
                addr = p.addresses.first()
                if addr:
                    instructor_address = f"{addr.street}, {addr.postal_code} {addr.city}".strip(", ")
        except Exception:
            pass

    org_name = getattr(settings, "DESIGN_STUDIO_APP_NAME", "Verein / Akademie")
    if apps.is_installed("site_kit"):
        try:
            from site_kit.models import SiteConfig
            org_name = SiteConfig.get_solo().site_name or org_name
        except Exception:
            pass

    from django.template.loader import render_to_string
    html = render_to_string("cal_kit/event_credit_note_pdf.html", {
        "event": ev,
        "summary": summary,
        "org_name": org_name,
        "instructor_address": instructor_address,
        "instructor_tax_id": instructor_tax_id,
        "credit_note_no": f"GS-{ev.start_at:%Y}-{ev.id:04d}",
        "issued_date": timezone.localdate(),
    })
    try:
        from io import BytesIO
        from xhtml2pdf import pisa
        buf = BytesIO()
        status = pisa.CreatePDF(html, dest=buf, encoding="utf-8")
        if not status.err:
            resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
            resp["Content-Disposition"] = f'inline; filename="gutschrift-{ev.id}.pdf"'
            return resp
    except Exception:
        pass
    return HttpResponse(html)


@login_required
@require_POST
def event_billing_to_finance_view(request, pk):
    ev = get_object_or_404(CalendarEvent.objects.select_related("offering_template"), pk=pk)
    if not (_has_shared_editor_access(request.user) or ev.user_id == request.user.id):
        return HttpResponse("Kein Zugriff.", status=403)
    if not apps.is_installed("finance_kit"):
        messages.warning(request, "finance_kit ist nicht installiert.")
        return redirect("cal_kit:event_billing", pk=ev.id)
    from finance_kit.models import LedgerEntry
    from finance_kit.services import next_doc_number

    summary = get_event_billing_summary(ev)
    created = 0
    if summary["paid_revenue"] > 0:
        _, was_created = LedgerEntry.objects.get_or_create(
            source_ref=f"cal_event:{ev.id}:income",
            defaults={
                "date": timezone.localdate(),
                "entry_type": "income",
                "sphere": "zweckbetrieb",
                "amount": summary["paid_revenue"],
                "description": f"Kurs-Einnahmen: {ev.title}"[:220],
                "category_name": "Kurs- & Teilnahmegebühren",
                "receipt_number": next_doc_number("BEL", LedgerEntry, "receipt_number"),
                "created_by": request.user,
            },
        )
        if was_created:
            created += 1
    if summary["honorarium"] > 0:
        _, was_created = LedgerEntry.objects.get_or_create(
            source_ref=f"cal_event:{ev.id}:honorarium",
            defaults={
                "date": timezone.localdate(),
                "entry_type": "expense",
                "sphere": "zweckbetrieb",
                "amount": summary["honorarium"],
                "description": f"Dozenten-Honorar: {ev.title}"[:220],
                "category_name": "Dozenten-Honorar",
                "receipt_number": next_doc_number("BEL", LedgerEntry, "receipt_number"),
                "person_ref": ev.instructor_ref[:64],
                "person_name": ev.instructor_name[:140],
                "tax_exempt_paragraph": "3_26" if request.POST.get("use_3_26") == "on" else "",
                "created_by": request.user,
            },
        )
        if was_created:
            created += 1
    messages.success(request, f"{created} Buchung(en) ins Finanzjournal übernommen.")
    return redirect("cal_kit:event_billing", pk=ev.id)
