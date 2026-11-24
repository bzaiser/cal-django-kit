from decimal import Decimal, InvalidOperation

from django.apps import apps


def get_cal_profile_labels() -> dict:
    """Return industry-profile-specific terminology for cal_kit tabs and menus."""
    profile = ""
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import DomainModuleSettings
            profile = (DomainModuleSettings.get_solo().business_profile or "").strip().lower()
        except Exception:
            profile = ""

    if profile in ("verein", "club", "academy", "association", "kunstverein"):
        return {
            "profile": profile,
            "menu_label": "Kalender & Kurse",
            "calendar_tab": "Kalender",
            "offerings_tab": "Kursvorlagen",
            "offering_singular": "Kursvorlage",
            "offering_plural": "Kursvorlagen",
            "billing_tab": "Kurstermine & Abrechnung",
            "instructor_label": "Dozent / Leitung",
            "responsible_label": "Ansprechpartner (z. B. Kunstbeirat)",
            "program_title": "Jahresprogramm",
            "fee_label": "Kursgebühr",
        }
    if profile in ("practice", "praxis", "health", "medical", "therapy"):
        return {
            "profile": profile,
            "menu_label": "Kalender & Leistungen",
            "calendar_tab": "Kalender",
            "offerings_tab": "Leistungsvorlagen",
            "offering_singular": "Leistungsvorlage",
            "offering_plural": "Leistungsvorlagen",
            "billing_tab": "Termine & Abrechnung",
            "instructor_label": "Therapeut / Leitung",
            "responsible_label": "Praxisleitung / Ansprechpartner",
            "program_title": "Leistungsprogramm",
            "fee_label": "Gebühr / Preis",
        }
    return {
        "profile": profile or "default",
        "menu_label": "Kalender & Angebote",
        "calendar_tab": "Kalender",
        "offerings_tab": "Angebots-Vorlagen",
        "offering_singular": "Angebots-Vorlage",
        "offering_plural": "Angebots-Vorlagen",
        "billing_tab": "Termine & Abrechnung",
        "instructor_label": "Leitung / Durchführender",
        "responsible_label": "Zuständiger / Ansprechpartner",
        "program_title": "Programm & Angebote",
        "fee_label": "Preis / Gebühr",
    }


def get_contract_options() -> list[dict]:
    if not apps.is_installed("domain_kit"):
        return []
    try:
        from domain_kit.models import AssetItem
        items = []
        for c in AssetItem.objects.filter(asset_type="contract").select_related("person").order_by("title")[:200]:
            p_name = c.person.full_name if c.person else ""
            qty = f"{c.quantity} €" if c.quantity else ""
            meta = " · ".join(x for x in (p_name, qty, c.billing_interval) if x)
            items.append({
                "ref": f"contract:{c.id}",
                "label": f"{c.title} ({meta})" if meta else c.title,
                "person_ref": f"person:{c.person_id}" if c.person_id else "",
                "quantity": str(c.quantity) if c.quantity is not None else "",
            })
        return items
    except Exception:
        return []


def parse_decimal_or_none(raw):
    s = (str(raw or "").strip()).replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def resolve_person_name(ref: str) -> str:
    if ref and ref.startswith("person:") and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person
            pid = int(ref.split(":", 1)[1])
            p = Person.objects.filter(pk=pid).first()
            return p.full_name if p else ""
        except Exception:
            return ""
    return ""


def get_event_billing_summary(event) -> dict:
    entries = []
    collection = None
    if apps.is_installed("list_kit"):
        try:
            from list_kit.models import Entry
            qs = (
                Entry.objects.filter(
                    collection__template__slug="kurs-anmeldung",
                    custom_data_json__event_id=str(event.id),
                )
                .select_related("collection")
                .order_by("created_at", "id")
            )
            entries = list(qs)
            if entries:
                collection = entries[0].collection
        except Exception:
            entries = []

    booked = [e for e in entries if (e.status or "").strip().lower() not in ("abgesagt", "abgelehnt")]
    paid = [e for e in booked if bool((e.custom_data_json or {}).get("is_paid"))]
    attended = [e for e in booked if (e.status or "").strip().lower() == "teilgenommen"]
    cancelled = [e for e in entries if (e.status or "").strip().lower() in ("abgesagt", "abgelehnt")]

    tpl = getattr(event, "offering_template", None)
    fee = event.fee_amount if event.fee_amount is not None else (tpl.fee_amount if tpl and tpl.fee_amount is not None else Decimal("0.00"))
    honorarium = Decimal("0.00")
    if tpl and tpl.honorarium_amount is not None:
        honorarium = tpl.honorarium_amount
    elif event.contract_ref and event.contract_ref.startswith("contract:") and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import AssetItem
            cid = int(event.contract_ref.split(":", 1)[1])
            c_obj = AssetItem.objects.filter(pk=cid).first()
            if c_obj and c_obj.quantity:
                honorarium = c_obj.quantity
        except Exception:
            pass

    expected_revenue = fee * len(booked)
    paid_revenue = fee * len(paid)
    open_revenue = expected_revenue - paid_revenue
    net_result = paid_revenue - honorarium

    return {
        "entries": entries,
        "collection": collection,
        "booked_count": len(booked),
        "paid_count": len(paid),
        "attended_count": len(attended),
        "cancelled_count": len(cancelled),
        "fee_per_person": fee,
        "expected_revenue": expected_revenue,
        "paid_revenue": paid_revenue,
        "open_revenue": open_revenue,
        "honorarium": honorarium,
        "net_result": net_result,
    }
