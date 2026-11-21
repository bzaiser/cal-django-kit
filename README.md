# cal-django-kit

Wiederverwendbares Kalender-Paket (FullCalendar Offline-Bundle, Design-Studio-Theme-kompatibel) für Django + `django-design-studio`.

## Features
- **Ansichten**: Jahr, Monat, Woche, Tag & Liste, Drag & Drop, Resize, Outlook-Stil Event-Icons & Hover-Card.
- **Serientermine & BYDAY**: `daily`, `weekly`, `monthly`, `yearly` inkl. Intervall, Enddatum (`recurrence_until`) und Wochentags-Auswahl (`recurrence_weekdays` / `BYDAY=MO,WE,FR`).
- **Einzel-Ausnahmen (`CalendarEventException`)**: Einzelne Vorkommnisse einer Serie gezielt absagen oder verschieben, ohne die ganze Serie zu löschen.
- **Kalender-Kategorien (`CalendarCategory`)**: Mehrere Kalender/Kategorien pro Benutzer mit Farbe, Icon und Filter-Chips über dem Kalender.
- **Erinnerungen**: Vorab-Alarme (`VALARM`), Management-Command `cal_send_reminders` (optional über `notify_kit`).
- **iCalendar & CalDAV**: `/calendar/feed.ics` mit `RRULE`, `BYDAY`, `EXDATE`, `VALARM` sowie CalDAV-PUT-Helper.

## Einbindung
```python
# settings.py
INSTALLED_APPS = [..., "design_studio", "cal_kit"]

# urls.py (empfohlen über ops_kit.runtime, damit per UI de-/aktivierbar)
from ops_kit.runtime import optional_kit_path
urlpatterns += optional_kit_path("cal_kit", "calendar/", "cal_kit.urls")
```
