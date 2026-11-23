from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from seed_kit.registry import register_seeder

from .models import CalendarCategory, CalendarEvent


def seed_cal_demo(count: int = 5):
    User = get_user_model()
    owner = User.objects.filter(is_staff=True).first() or User.objects.first()
    if owner is None:
        return 0

    cat_courses, _ = CalendarCategory.objects.get_or_create(
        user=owner,
        name="Kurse",
        defaults={"color_state": 2, "is_shared": True, "is_public_default": True},
    )
    cat_events, _ = CalendarCategory.objects.get_or_create(
        user=owner,
        name="Veranstaltungen",
        defaults={"color_state": 1, "is_shared": True, "is_public_default": True},
    )

    inst_ref = ""
    inst_name = ""
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person
            doz = Person.objects.filter(is_public=True).first()
            if doz:
                inst_ref = f"person:{doz.id}"
                inst_name = doz.full_name
        except Exception:
            pass

    now = timezone.now()
    created = 0
    for i in range(max(1, count)):
        title = f"Demo-Kurs {i + 1}" if i % 2 == 0 else f"Vereinstreff {i + 1}"
        if CalendarEvent.objects.filter(title=title).exists():
            continue
        start = now + timedelta(days=(i + 1) * 3)
        CalendarEvent.objects.create(
            user=owner,
            category=cat_courses if i % 2 == 0 else cat_events,
            title=title,
            start_at=start,
            end_at=start + timedelta(hours=2),
            location="Schulungsraum 1",
            instructor_ref=inst_ref,
            instructor_name=inst_name,
            is_public=True,
            allow_registration=(i % 2 == 0),
            max_participants=12 if i % 2 == 0 else None,
            description="Automatisch erzeugter Beispiel-Termin.",
        )
        created += 1
    return created


def reset_cal_demo():
    CalendarEvent.objects.filter(description="Automatisch erzeugter Beispiel-Termin.").delete()


register_seeder("cal_kit", seed_cal_demo, order=30, reset_fn=reset_cal_demo)
