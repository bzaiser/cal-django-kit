import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cal_kit', '0004_calendarevent_recurrence_interval_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CalendarCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('color_state', models.PositiveSmallIntegerField(default=1)),
                ('icon', models.CharField(blank=True, default='', max_length=40)),
                ('is_default', models.BooleanField(default=False)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cal_kit_categories', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='calendarevent',
            name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='events', to='cal_kit.calendarcategory'),
        ),
        migrations.AddField(
            model_name='calendarevent',
            name='recurrence_weekdays',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.CreateModel(
            name='CalendarEventException',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_start', models.DateTimeField(db_index=True)),
                ('cancelled', models.BooleanField(default=False)),
                ('new_start', models.DateTimeField(blank=True, null=True)),
                ('new_end', models.DateTimeField(blank=True, null=True)),
                ('new_title', models.CharField(blank=True, default='', max_length=140)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='exceptions', to='cal_kit.calendarevent')),
            ],
            options={'unique_together': {('event', 'original_start')}},
        ),
    ]
