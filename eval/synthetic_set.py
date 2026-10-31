"""EV-1s (OQ-47 (d''), (f'')): the 120 synthetic labeled emails.

Each `eval/synthetic/synNNN.json` holds one fictional email (sender,
subject, a text or HTML body, an optional calendar invite, the received
time) and its labels: the class after CLS-1's precedence, the company, the
role and the key date. The rules the labels follow are in
`eval/synthetic/LABELING.md`; a blind second labeling checked them
(`eval/synthetic/agreement.json`).

The emails were written from CLS-1's definitions only: no real mail went
into them. Every address and link is under `example.com`.

`fetched_email` turns a case into the message the real code reads: an RFC
822 message with a text/calendar part when the case has an invite, parsed by
`messages.parse_message`, so the batch path sees what Gmail would give it.
"""

import json
import re
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "eval" / "synthetic"
LANGUAGES = ("tr", "en")
CLASSES = ("offer", "interview_invite", "assessment", "rejection", "recruiter_outreach", "other")
# Only these classes put a key date on the tracker row (tracker_update.KEY_DATE_TYPES).
DATED_CLASSES = ("interview_invite", "assessment")
LABEL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")
CALENDAR_START = re.compile(r"\d{8}T\d{6}")
# A name, not a paragraph (classify.FIELD_LIMIT).
NAME_LIMIT = 100


class CaseError(ValueError):
    pass


def _fictional(text: str) -> bool:
    """Every address and web link in the text is under example.com."""
    hosts = re.findall(r"[\w.+-]+@([\w.-]+)", text) + re.findall(r"https?://([\w.-]+)", text)
    return all(host.lower() == "example.com" or host.lower().endswith(".example.com") for host in hosts)


def check_case(case: dict, stem: str) -> list[str]:
    email, labels = case.get("email", {}), case.get("labels", {})
    calendar = email.get("calendar")
    when = labels.get("date")
    names = [labels.get("company"), labels.get("role")]
    problems = [
        "id" if case.get("id") != stem else "",
        "language" if case.get("language") not in LANGUAGES else "",
        "sender" if not parseaddr(email.get("sender", ""))[1].lower().endswith(".example.com") else "",
        "subject" if not email.get("subject") else "",
        "body" if not (email.get("body") or email.get("html")) else "",
        "received_at" if not _aware(email.get("received_at")) else "",
        "calendar" if calendar is not None and not _calendar_ok(calendar) else "",
        "fictional" if not _fictional(json.dumps(email, ensure_ascii=False)) else "",
        "class" if labels.get("email_class") not in CLASSES else "",
        "names" if any(n is not None and (not isinstance(n, str) or not n.strip() or len(n) > NAME_LIMIT) for n in names) else "",
        "date" if when is not None and not (isinstance(when, str) and LABEL_DATE.fullmatch(when) and _valid_date(when)) else "",
        "date_class" if when is not None and labels.get("email_class") not in DATED_CLASSES else "",
        "hard_case" if case.get("hard_case") is not None and not re.fullmatch(r"[a-z_]+", str(case["hard_case"])) else "",
    ]
    return [p for p in problems if p]


def _aware(value) -> bool:
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except (TypeError, ValueError):
        return False


def _valid_date(value: str) -> bool:
    try:
        (datetime.fromisoformat if "T" in value else date.fromisoformat)(value)
    except ValueError:
        return False
    return True


def _calendar_ok(calendar) -> bool:
    return (
        isinstance(calendar, dict)
        and set(calendar) == {"start", "tzid", "summary"}
        and bool(CALENDAR_START.fullmatch(str(calendar["start"])))
        and bool(calendar["summary"])
    )


def load_cases(directory: Path = CASES_DIR) -> list[dict]:
    """Every case, checked; a malformed one stops the load."""
    cases = []
    for path in sorted(directory.glob("syn*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        problems = check_case(case, path.stem)
        if problems:
            raise CaseError(f"{path.name}: check {', '.join(problems)}")
        cases.append(case)
    return cases


def calendar_part(calendar: dict) -> str:
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//inbox-agent eval//EN",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"DTSTART;TZID={calendar['tzid']}:{calendar['start']}",
            f"SUMMARY:{calendar['summary']}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


def raw_message(case: dict) -> bytes:
    email = case["email"]
    message = EmailMessage()
    message["From"] = email["sender"]
    message["Subject"] = email["subject"]
    if email.get("body"):
        message.set_content(email["body"])
        if email.get("html"):
            message.add_alternative(email["html"], subtype="html")
    else:
        message.set_content(email["html"], subtype="html")
    if email.get("calendar"):
        message.add_attachment(
            calendar_part(email["calendar"]).encode("utf-8"), maintype="text", subtype="calendar", filename="invite.ics"
        )
    return message.as_bytes()


def fetched_email(case: dict, message_id: str):
    from inbox_agent.messages import parse_message

    return parse_message(
        raw_message(case), message_id=message_id, received_at=datetime.fromisoformat(case["email"]["received_at"])
    )
