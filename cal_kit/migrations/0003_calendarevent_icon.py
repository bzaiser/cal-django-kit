from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cal_kit", "0002_calendarevent_reminders"),
    ]

    operations = [
        migrations.AddField(
            model_name="calendarevent",
            name="icon",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
