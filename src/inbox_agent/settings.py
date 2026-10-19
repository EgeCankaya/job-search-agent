"""Runtime settings for the agent code. Nothing personal lives here.

The owner's own data (companies, recruiter addresses) lives in the tracker
table, never in this file.
"""

from datetime import timedelta, timezone

AWS_REGION = "eu-north-1"

# MacBook mode's model (OQ-42 j): Ollama on the Mac, loopback only
# (NFR-REL-4). The owner picked Qwen 3.8 27B on 2026-10-19.
LOCAL_MODEL_ID = "qwen3.8:27b"
OLLAMA_URL = "http://127.0.0.1:11434"

# The day an email counts for in the tracker (TRK-1 last contact). Turkey has
# kept UTC+3 all year since 2016; a fixed offset needs no tz database.
OWNER_TIMEZONE = timezone(timedelta(hours=3), "Europe/Istanbul")

# TRK-1 alias list: normalized variant -> normalized canonical name. Empty in
# the repo, because entries would name the companies the owner applied to.
COMPANY_ALIASES: dict[str, str] = {}

# ING-1. Alerts arrive from this sender, and their subjects contain words like
# "interview", so the query excludes it (OQ-40).
ALERT_SENDER = "no-reply@sns.amazonaws.com"

# ING-1 part 1: applicant tracking systems that send on a company's behalf,
# and assessment platforms, whose test invites often match no keyword (OQ-41 b).
# Gmail's from:domain also matches subdomains (hire.lever.co).
ATS_DOMAINS = (
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "codesignal.com",
    "codility.com",
    "greenhouse.io",
    "greenhouse-mail.io",
    "hackerrank.com",
    "icims.com",
    "jobvite.com",
    "kariyer.net",
    "lever.co",
    "myworkday.com",
    "myworkdayjobs.com",
    "personio.com",
    "personio.de",
    "recruitee.com",
    "smartrecruiters.com",
    "successfactors.com",
    "successfactors.eu",
    "taleo.net",
    "teamtailor.com",
    "testgorilla.com",
    "workable.com",
)

# ING-1 part 4: subject keywords, English and Turkish (PRD §9.1). Gmail
# matches whole words, so the common Turkish inflections are listed too
# (OQ-41 a). "görüşmek" is left out: "görüşmek üzere" is a sign-off.
SUBJECT_KEYWORDS = (
    "interview",
    "mülakat",
    "görüşme",
    "assessment",
    "değerlendirme",
    "offer",
    "teklif",
    "başvuru",
    "application",
    # başvuru
    "başvurunuz",
    "başvurunuzu",
    "başvurunuza",
    "başvurunuzla",
    "başvurunuzun",
    "başvurusu",
    # mülakat
    "mülakatı",
    "mülakata",
    "mülakatınız",
    "mülakatınızı",
    # görüşme
    "görüşmesi",
    "görüşmeye",
    "görüşmeniz",
    "görüşmenizi",
    # teklif
    "teklifi",
    "teklifimiz",
    "teklifiniz",
    # değerlendirme
    "değerlendirmesi",
    "değerlendirmeye",
    "değerlendirmeniz",
)

# ING-4: Gmail tokens come from AgentCore Identity. The owner created these by
# hand with the CLI (2026-10-18); the names must match.
GOOGLE_CREDENTIAL_PROVIDER = "inbox-agent-google"
POLLER_WORKLOAD_IDENTITY = "inbox-agent-poller"  # gmail.readonly only
DRAFTS_WORKLOAD_IDENTITY = "inbox-agent-drafts"  # gmail.compose only
# The token vault keys tokens by workload identity and user. One user: the owner.
OWNER_USER_ID = "owner"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
# Where AgentCore sends the browser after consent; only the owner's Mac listens.
OAUTH_RETURN_URL = "http://localhost:9090/oauth2/callback"
# Google returns a refresh token only for offline access; "consent" makes it
# return one again on a repeated sign-in.
GOOGLE_OAUTH_PARAMETERS = {"access_type": "offline", "prompt": "consent"}
