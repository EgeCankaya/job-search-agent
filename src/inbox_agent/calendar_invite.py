"""CLS-2: the start time and title of a calendar invite (OQ-34).

Reads DTSTART and SUMMARY from the first VEVENT of a `text/calendar` part,
with the event's own TZID or UTC. Recurrence, other time zones and updates
to the same event are out of scope for v1 (PRD §15). Stdlib only.

The title is email text: it goes to extraction, never into logs or alerts
(NFR-PRIV-3).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from email.message import Message
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DATE_TIME = re.compile(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)")
_DATE = re.compile(r"(\d{4})(\d{2})(\d{2})")
_OFFSET = re.compile(r"([+-])(\d{2})(\d{2})(\d{2})?")
_ESCAPE = re.compile(r"\\([\\;,nN])")


@dataclass(frozen=True)
class CalendarInvite:
    # Aware for UTC or a known TZID; naive (local wall time) for a floating
    # time or an unknown zone; a date for an all-day event.
    start: date | datetime
    title: str


def _content_line(line: str) -> tuple[str, dict[str, str], str] | None:
    """NAME;PARAM=VALUE:value, with ":" allowed inside quoted parameters."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1 :]
            break
    else:
        return None
    name, *params = head.split(";")
    parameters = {}
    for param in params:
        key, _, val = param.partition("=")
        parameters[key.strip().upper()] = val.strip().strip('"')
    return name.strip().upper(), parameters, value


def _offset(value: str) -> timedelta | None:
    match = _OFFSET.fullmatch(value.strip())
    if not match:
        return None
    sign, hours, minutes, seconds = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    return -delta if sign == "-" else delta


def _zone(tzid: str, vtimezones: dict[str, set[timedelta]]) -> tzinfo | None:
    try:
        return ZoneInfo(tzid.lstrip("/"))
    except (ZoneInfoNotFoundError, ValueError):
        pass
    # Outlook writes Windows names ("Turkey Standard Time") and defines them
    # in a VTIMEZONE. One with a single offset (no daylight saving) is exact.
    offsets = vtimezones.get(tzid, set())
    if len(offsets) == 1:
        return timezone(next(iter(offsets)))
    return None


def _start(value: str, parameters: dict[str, str], vtimezones: dict[str, set[timedelta]]) -> date | datetime | None:
    value = value.strip()
    if parameters.get("VALUE", "").upper() == "DATE" or _DATE.fullmatch(value):
        match = _DATE.fullmatch(value)
        try:
            return date(*map(int, match.groups())) if match else None
        except ValueError:
            return None
    match = _DATE_TIME.fullmatch(value)
    if not match:
        return None
    *parts, utc = match.groups()
    try:
        start = datetime(*map(int, parts))
    except ValueError:
        return None
    if utc:
        return start.replace(tzinfo=timezone.utc)
    if "TZID" in parameters:
        zone = _zone(parameters["TZID"], vtimezones)
        if zone is not None:
            return start.replace(tzinfo=zone)
    return start


def _text(value: str) -> str:
    return _ESCAPE.sub(lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def parse_invite(text: str) -> CalendarInvite | None:
    """The first event's start and title, or None if it has no valid DTSTART."""
    lines = re.sub(r"\r?\n[ \t]", "", text).splitlines()  # unfold (RFC 5545 §3.1)
    stack: list[str] = []
    vtimezones: dict[str, set[timedelta]] = {}
    tzid = None
    event: dict[str, tuple[dict[str, str], str]] | None = None
    in_first_event = False
    for line in lines:
        parsed = _content_line(line)
        if parsed is None:
            continue
        name, parameters, value = parsed
        if name == "BEGIN":
            stack.append(value.strip().upper())
            if stack[-1] == "VEVENT" and event is None:
                event, in_first_event = {}, True
        elif name == "END":
            if stack and stack.pop() == "VEVENT":
                in_first_event = False
        elif stack and stack[-1] == "VTIMEZONE" and name == "TZID":
            tzid = value.strip()
        elif stack and stack[-1] in ("STANDARD", "DAYLIGHT") and name == "TZOFFSETTO" and tzid:
            offset = _offset(value)
            if offset is not None:
                vtimezones.setdefault(tzid, set()).add(offset)
        elif in_first_event and stack[-1] == "VEVENT" and name in ("DTSTART", "SUMMARY"):
            # The event's own properties, not those of an alarm inside it.
            event.setdefault(name, (parameters, value))
    if not event or "DTSTART" not in event:
        return None
    start = _start(event["DTSTART"][1], event["DTSTART"][0], vtimezones)
    if start is None:
        return None
    title = _text(event["SUMMARY"][1]) if "SUMMARY" in event else ""
    return CalendarInvite(start=start, title=title)


def find_invite(message: Message) -> CalendarInvite | None:
    """The invite in the first `text/calendar` part that has one."""
    for part in message.walk():
        if part.get_content_type() != "text/calendar":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        invite = parse_invite(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        if invite is not None:
            return invite
    return None


def key_date(invite: CalendarInvite | None, extracted: date | datetime | None) -> date | datetime | None:
    """The invite's start wins over a date extracted from the body (CLS-2).
    No date anywhere gives None, never a guess."""
    if invite is not None:
        return invite.start
    return extracted
