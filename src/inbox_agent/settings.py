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
