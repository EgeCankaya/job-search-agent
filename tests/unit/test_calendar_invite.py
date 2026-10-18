"""CLS-2 calendar invites (UAC-CLS-2). Fictional invites only."""

from datetime import UTC, date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default
from email import message_from_bytes
from zoneinfo import ZoneInfo

import pytest

from inbox_agent.calendar_invite import CalendarInvite, find_invite, key_date, parse_invite


def ics(*event_lines, before=()):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST", *before, "BEGIN:VEVENT", *event_lines,
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def email_with(invite_text=None, body="Merhaba, davetiye ektedir."):
    message = EmailMessage()
    message["From"] = "İK <ik@kuzey.example.com>"
    message["To"] = "aday@example.com"
    message["Subject"] = "Davet: Teknik mülakat"
    message.set_content(body)
    if invite_text is not None:
        message.add_alternative(invite_text, subtype="calendar", params={"method": "REQUEST"})
    # Round-trip through bytes, the way a raw Gmail message arrives.
    return message_from_bytes(message.as_bytes(), policy=default)


def test_tzid_start_and_summary():
    invite = parse_invite(ics("DTSTART;TZID=Europe/Istanbul:20261103T140000", "SUMMARY:Teknik mülakat"))
    assert invite == CalendarInvite(
        start=datetime(2026, 11, 3, 14, 0, tzinfo=ZoneInfo("Europe/Istanbul")), title="Teknik mülakat"
    )
    assert invite.start.utcoffset() == timedelta(hours=3)


def test_utc_start():
    invite = parse_invite(ics("DTSTART:20261103T110000Z", "SUMMARY:Technical interview"))
    assert invite.start == datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
    assert invite.start.utcoffset() == timedelta(0)


def test_all_day_event_is_a_date():
    invite = parse_invite(ics("DTSTART;VALUE=DATE:20261105", "SUMMARY:Assessment deadline"))
    assert invite.start == date(2026, 11, 5)
    assert not isinstance(invite.start, datetime)


def test_floating_time_stays_naive():
    assert parse_invite(ics("DTSTART:20261103T140000")).start == datetime(2026, 11, 3, 14, 0)


def test_windows_tzid_uses_a_single_offset_vtimezone():
    # Outlook's style: a Windows zone name defined in the same file.
    vtimezone = (
        "BEGIN:VTIMEZONE", "TZID:Turkey Standard Time",
        "BEGIN:STANDARD", "DTSTART:16010101T000000", "TZOFFSETFROM:+0300", "TZOFFSETTO:+0300", "END:STANDARD",
        "END:VTIMEZONE",
    )
    invite = parse_invite(ics('DTSTART;TZID="Turkey Standard Time":20261103T140000', before=vtimezone))
    assert invite.start == datetime(2026, 11, 3, 14, 0, tzinfo=timezone(timedelta(hours=3)))


def test_unknown_zone_with_daylight_saving_stays_naive():
    vtimezone = (
        "BEGIN:VTIMEZONE", "TZID:Example Standard Time",
        "BEGIN:STANDARD", "TZOFFSETTO:+0100", "END:STANDARD",
        "BEGIN:DAYLIGHT", "TZOFFSETTO:+0200", "END:DAYLIGHT",
        "END:VTIMEZONE",
    )
    invite = parse_invite(ics("DTSTART;TZID=Example Standard Time:20261103T140000", before=vtimezone))
    assert invite.start == datetime(2026, 11, 3, 14, 0)
    assert invite.start.tzinfo is None


def test_folded_and_escaped_summary():
    invite = parse_invite(ics("DTSTART:20261103T110000Z", "SUMMARY;LANGUAGE=tr:Teknik mülakat\\, Backend", "  Engineer"))
    assert invite.title == "Teknik mülakat, Backend Engineer"


def test_alarm_inside_the_event_is_ignored():
    invite = parse_invite(
        ics(
            "BEGIN:VALARM", "TRIGGER:-PT15M", "SUMMARY:Reminder", "END:VALARM",
            "DTSTART:20261103T110000Z", "SUMMARY:Technical interview",
        )
    )
    assert invite.title == "Technical interview"


def test_first_event_wins():
    text = ics("DTSTART:20261103T110000Z", "SUMMARY:First").replace(
        "END:VCALENDAR", "BEGIN:VEVENT\r\nDTSTART:20261201T110000Z\r\nSUMMARY:Second\r\nEND:VEVENT\r\nEND:VCALENDAR"
    )
    assert parse_invite(text).title == "First"


def test_event_without_summary_has_an_empty_title():
    assert parse_invite(ics("DTSTART:20261103T110000Z")).title == ""


@pytest.mark.parametrize("dtstart", ["SUMMARY:No start", "DTSTART:next tuesday", "DTSTART:20261303T110000Z"])
def test_no_valid_start_is_no_invite(dtstart):
    assert parse_invite(ics(dtstart)) is None


def test_text_without_an_event_is_no_invite():
    assert parse_invite("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n") is None
    assert parse_invite("not a calendar") is None


# The CLS-2 acceptance criteria


def test_invite_gives_the_key_date_when_the_body_has_none():
    message = email_with(ics("DTSTART;TZID=Europe/Istanbul:20261103T140000", "SUMMARY:Teknik mülakat"))
    invite = find_invite(message)
    assert key_date(invite, extracted=None) == datetime(2026, 11, 3, 14, 0, tzinfo=ZoneInfo("Europe/Istanbul"))


def test_invite_start_wins_over_a_body_date():
    message = email_with(ics("DTSTART:20261103T110000Z"), body="Görüşme 4 Kasım'da.")
    assert key_date(find_invite(message), extracted=date(2026, 11, 4)) == datetime(2026, 11, 3, 11, 0, tzinfo=UTC)


def test_email_with_no_date_has_an_empty_date():
    message = email_with(None, body="Başvurunuz için teşekkür ederiz.")
    assert find_invite(message) is None
    assert key_date(None, extracted=None) is None


def test_body_date_is_used_without_an_invite():
    assert key_date(None, extracted=date(2026, 11, 4)) == date(2026, 11, 4)


def test_invite_as_attachment_part():
    message = email_with()
    message.add_attachment(
        ics("DTSTART:20261103T110000Z").encode(), maintype="text", subtype="calendar", filename="invite.ics"
    )
    assert find_invite(message_from_bytes(message.as_bytes(), policy=default)).start == datetime(
        2026, 11, 3, 11, 0, tzinfo=UTC
    )
