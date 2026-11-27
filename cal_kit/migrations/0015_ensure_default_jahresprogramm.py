from django.db import migrations


def ensure_default_jahresprogramm(apps, schema_editor):
    Program = apps.get_model("cal_kit", "Program")
    OfferingTemplate = apps.get_model("cal_kit", "OfferingTemplate")
    prog, _ = Program.objects.get_or_create(
        slug="jahresprogramm",
        defaults={
            "title": "Jahresprogramm",
            "description": "Alle laufenden Kurse, Workshops und Angebote im Überblick.",
            "card_mode": "equal_max",
            "show_in_nav": True,
            "is_public": True,
            "is_featured": True,
            "position": 0,
        },
    )
    OfferingTemplate.objects.filter(program__isnull=True).update(program=prog)


class Migration(migrations.Migration):

    dependencies = [
        ("cal_kit", "0014_program_is_featured"),
    ]

    operations = [
        migrations.RunPython(ensure_default_jahresprogramm, migrations.RunPython.noop),
    ]
