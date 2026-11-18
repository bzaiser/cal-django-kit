# cal-django-kit

Wiederverwendbares Kalender-Paket mit Monatsansicht, `.ics`-Abo-Feed und CalDAV-Sync für Django + `django-design-studio`.

## Installation
```bash
pip install --no-user --upgrade --no-cache-dir git+https://github.com/bzaiser/cal-django-kit.git@main
```

```python
INSTALLED_APPS = [..., "design_studio", "cal_kit"]
# urls.py
path("calendar/", include("cal_kit.urls")),
```
