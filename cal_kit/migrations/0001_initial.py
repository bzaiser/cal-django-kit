import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CalendarEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.CharField(default=uuid.uuid4, max_length=128, unique=True)),
                ("title", models.CharField(max_length=140)),
                ("start_at", models.DateTimeField(db_index=True)),
                ("end_at", models.DateTimeField()),
                ("all_day", models.BooleanField(default=False)),
                ("location", models.CharField(blank=True, default="", max_length=140)),
                ("description", models.TextField(blank=True, default="")),
                ("color_state", models.PositiveSmallIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cal_kit_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["start_at"]},
        ),
    ]
