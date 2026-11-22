import base64
import urllib.request
from datetime import datetime, timezone

BYDAY_MAP = {"0": "MO", "1": "TU", "2": "WE", "3": "TH", "4": "FR", "5": "SA", "6": "SU"}


def _fmt_ics_dt(dt: datetime, all_day: bool = False) -> str:
    if all_day:
        return dt.strftime("%Y%m%d")
    utc_dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def event_to_vcalendar(ev, prodid="-//cal-django-kit//DE") -> str:
    return events_to_ics([ev], prodid=prodid)


def events_to_ics(events, prodid="-//cal-django-kit//DE", cal_name="Kalender") -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        f"NAME:{_esc(cal_name)}",
        f"X-WR-CALNAME:{_esc(cal_name)}",
    ]
    for ev in events:
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{ev.uid}@cal-django-kit",
            f"DTSTAMP:{_fmt_ics_dt(ev.created_at or ev.start_at)}",
            f"DTSTART{';VALUE=DATE' if ev.all_day else ''}:{_fmt_ics_dt(ev.start_at, ev.all_day)}",
            f"DTEND{';VALUE=DATE' if ev.all_day else ''}:{_fmt_ics_dt(ev.end_at, ev.all_day)}",
            f"SUMMARY:{_esc(ev.title)}",
        ])
        if ev.location:
            lines.append(f"LOCATION:{_esc(ev.location)}")
        if ev.description:
            lines.append(f"DESCRIPTION:{_esc(ev.description)}")
        if getattr(ev, "is_recurring", False):
            freq_map = {
                "daily": "DAILY",
                "weekly": "WEEKLY",
                "monthly": "MONTHLY",
                "yearly": "YEARLY",
            }
            freq = freq_map.get(ev.recurrence_rule)
            if freq:
                rrule_parts = [f"FREQ={freq}"]
                interval = getattr(ev, "recurrence_interval", 1) or 1
                if interval > 1:
                    rrule_parts.append(f"INTERVAL={interval}")
                # BYDAY for weekly with specific weekdays
                weekdays_raw = getattr(ev, "recurrence_weekdays", "") or ""
                if freq == "WEEKLY" and weekdays_raw:
                    byday = ",".join(BYDAY_MAP[d] for d in weekdays_raw.split(",") if d in BYDAY_MAP)
                    if byday:
                        rrule_parts.append(f"BYDAY={byday}")
                if getattr(ev, "recurrence_until", None):
                    until_dt = datetime.combine(ev.recurrence_until, datetime.max.time(), tzinfo=timezone.utc)
                    rrule_parts.append(f"UNTIL={_fmt_ics_dt(until_dt, False)}")
                lines.append(f"RRULE:{';'.join(rrule_parts)}")
            # EXDATE for cancelled exceptions
            try:
                cancelled = [e for e in ev.exceptions.all() if e.cancelled]
                for exc in cancelled:
                    lines.append(f"EXDATE:{_fmt_ics_dt(exc.original_start, ev.all_day)}")
            except Exception:
                pass
        if getattr(ev, "reminder_minutes", None) is not None:
            lines.extend([
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_esc(ev.title)}",
                f"TRIGGER:-PT{int(ev.reminder_minutes)}M",
                "END:VALARM",
            ])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def push_caldav_event(caldav_collection_url: str, username: str, password: str, ev, timeout: int = 10) -> int:
    url = f"{caldav_collection_url.rstrip('/')}/{ev.uid}.ics"
    body = event_to_vcalendar(ev).encode("utf-8")
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "text/calendar; charset=utf-8",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status
