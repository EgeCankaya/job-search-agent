# Privacy policy

Inbox agent is a personal tool. Its only user is the owner of this
repository, and it reads only the owner's own Gmail account. It is not
offered to anyone else.

## What it accesses

- **Gmail, read-only (`gmail.readonly`):** only messages that match a narrow
  job-search query (applicant tracking systems, companies in the owner's
  applications tracker, saved recruiter addresses, and job-related subject
  words). Other mail is never read.
- **Gmail drafts (`gmail.compose`):** used only to save a follow-up draft
  after the owner clicks Approve in a local app. The agent never sends or
  forwards email.

## What it stores, and for how long

All storage is in the owner's own AWS account, in the EU (Stockholm,
eu-north-1).

- An applications tracker: company, role, status, dates, company domains and
  recruiter addresses. It is kept until the owner ends the project, then
  exported and deleted.
- The IDs of processed messages, deleted automatically after 30 days.
- Pending follow-up drafts, deleted automatically 30 days after they are
  resolved.
- Logs keep IDs, counts and timings, never email text, and are deleted after
  7 days.

Before an email reaches a language model, it is cut to its subject, sender,
date and the first 2,000 characters of plain text. Models run on Amazon
Bedrock in the EU.

## What it shares

Nothing. Data is not sold, shared, or used for advertising, and no person
other than the owner sees it. Alerts go only to the owner's own email
address.

The use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Contact

Open an issue in this repository.
