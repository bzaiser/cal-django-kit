from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cal_kit", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="calendarevent",
            name="is_reminder",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="calendarevent",
            name="reminder_minutes",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="calendarevent",
            name="reminder_sent",
            field=models.BooleanField(default=False),
        ),
    ]
