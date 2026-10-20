"""ALR-1 and NFR-PRIV-3: the subject and body of an alert email.

An alert holds the class, company, role and date, and nothing else from the
email. The date is a date object, never text, so no email wording can ride
along in it. Company and role are flattened to one short line each, so they
can't add lines to the body either.

`notify_owner` publishes it to the SNS topic (tool_handlers.py).
"""

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from inbox_agent.classes import EmailClass
from inbox_agent.classify import Classification

CLASS_NAMES = {
    EmailClass.INTERVIEW_INVITE: "Interview invite",
    EmailClass.ASSESSMENT: "Assessment",
    EmailClass.OFFER: "Offer",
    EmailClass.REJECTION: "Rejection",
    EmailClass.RECRUITER_OUTREACH: "Recruiter outreach",
    EmailClass.OTHER: "Other",
}

POSSIBLE_PREFIX = "Possible: "
# SNS email subjects: under 100 characters, no line breaks or control
# characters. Kept ASCII too, which every SNS version of the rule accepts.
SUBJECT_LIMIT = 99
FIELD_LIMIT = 100


@dataclass(frozen=True)
class Alert:
    subject: str
    body: str


@dataclass(frozen=True)
class AlertTopic:
    """`notify_owner`'s one destination: one topic, no recipient (ALR-3)."""

    client: object  # boto3 SNS client
    arn: str

    def publish(self, alert: Alert) -> None:
        self.client.publish(TopicArn=self.arn, Subject=alert.subject, Message=alert.body)


def _field(value: str | None) -> str:
    """One line, no control or invisible characters, at most FIELD_LIMIT."""
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in value or "")
    return " ".join(text.split())[:FIELD_LIMIT].rstrip()


def _ascii(value: str) -> str:
    # "ı" and "İ" don't decompose; the other Turkish letters do under NFKD.
    folded = unicodedata.normalize("NFKD", value.translate(str.maketrans({"ı": "i", "İ": "I"})))
    return " ".join(folded.encode("ascii", "ignore").decode().split())


def format_date(value: date | datetime) -> str:
    """3 Nov 2026, 3 Nov 2026 14:00, or 3 Nov 2026 14:00 UTC+03:00."""
    text = f"{value.day} {value:%b %Y}"
    if not isinstance(value, datetime):
        return text
    text += f" {value:%H:%M}"
    offset = value.utcoffset()
    if offset is None:
        return text
    sign = "-" if offset.total_seconds() < 0 else "+"
    minutes = abs(int(offset.total_seconds())) // 60
    return f"{text} UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def alert_for(
    classification: Classification,
    *,
    company: str,
    role: str | None,
    key_date: date | datetime | None,
) -> Alert | None:
    """The alert for one email, or None when it is filed silently (ALR-1).

    `company` and `role` are the tracker row's, so an email without a role
    names the row it was matched to (TRK-1).
    """
    if not (classification.urgent or classification.possible):
        return None
    return build_alert(
        classification.email_class, possible=classification.possible, company=company, role=role, key_date=key_date
    )


def build_alert(
    email_class: EmailClass,
    *,
    possible: bool,
    company: str,
    role: str | None,
    key_date: date | datetime | None,
) -> Alert:
    """An urgent alert, or with `possible` a "Possible:" one (ALR-1)."""
    if key_date is not None and not isinstance(key_date, date):
        raise TypeError("key_date must be a date or datetime")
    name = CLASS_NAMES[email_class]
    company, role = _field(company), _field(role)
    prefix = POSSIBLE_PREFIX if possible else ""

    subject = f"{prefix}{name}: {company}" + (f", {role}" if role else "")
    body = "\n".join(
        [
            f"Class: {name}",
            f"Company: {company}",
            f"Role: {role or 'not named'}",
            f"Date: {format_date(key_date) if key_date is not None else 'none found'}",
        ]
    )
    return Alert(subject=_ascii(subject)[:SUBJECT_LIMIT].rstrip(), body=body)
