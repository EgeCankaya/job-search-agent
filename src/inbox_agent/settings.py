"""Runtime settings for the agent code. Nothing personal lives here.

The owner's own data (companies, recruiter addresses) lives in the tracker
table, never in this file.
"""

AWS_REGION = "eu-north-1"

# TRK-1 alias list: normalized variant -> normalized canonical name. Empty in
# the repo, because entries would name the companies the owner applied to.
COMPANY_ALIASES: dict[str, str] = {}

# ING-1. Alerts arrive from this sender, and their subjects contain words like
# "interview", so the query excludes it (OQ-40).
ALERT_SENDER = "no-reply@sns.amazonaws.com"

# ING-1 part 1: applicant tracking systems that send on a company's behalf.
# Gmail's from:domain also matches subdomains (hire.lever.co).
ATS_DOMAINS = (
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "greenhouse.io",
    "greenhouse-mail.io",
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
    "workable.com",
)

# ING-1 part 4: subject keywords, English and Turkish (PRD §9.1).
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
