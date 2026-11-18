import base64
import urllib.request
from datetime import datetime, timezone


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
