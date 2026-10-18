"""The six email classes (CLS-1) and their precedence."""

from enum import StrEnum


class EmailClass(StrEnum):
    OFFER = "offer"
    INTERVIEW_INVITE = "interview_invite"
    ASSESSMENT = "assessment"
    REJECTION = "rejection"
    RECRUITER_OUTREACH = "recruiter_outreach"
    OTHER = "other"


# When an email fits more than one class, the first one here wins (CLS-1).
PRECEDENCE = (
    EmailClass.OFFER,
    EmailClass.INTERVIEW_INVITE,
    EmailClass.ASSESSMENT,
    EmailClass.REJECTION,
    EmailClass.RECRUITER_OUTREACH,
    EmailClass.OTHER,
)

# These produce an alert email (ALR-1); the rest are filed silently.
URGENT_CLASSES = frozenset({EmailClass.OFFER, EmailClass.INTERVIEW_INVITE, EmailClass.ASSESSMENT})
