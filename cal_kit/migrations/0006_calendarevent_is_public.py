from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cal_kit", "0005_calendarcategory_calendareventexception"),
    ]

    operations = [
        migrations.AddField(
            model_name="calendarevent",
            name="is_public",
            field=models.BooleanField(default=False),
        ),
    ]
