# EV-1s labeling rules

These rules are the same for the drafting session, which labeled each email
as it wrote it, and the blind second labeling, which saw the emails without
their labels (OQ-47 (d'')). They come from CLS-1's definitions and precedence
(PRD §9.2, `src/inbox_agent/classify.py`) and from what the tracker stores.

"Me" is the owner of the inbox, a job seeker.

## Class

One class per email. If several fit, the first in this order wins:
**offer > interview_invite > assessment > rejection > recruiter_outreach > other**.

| Class | Definition (CLS-1) |
|---|---|
| `interview_invite` | sets or changes an interview of any kind (screen, technical, HR): invitations, scheduling requests, confirmations, reschedules |
| `assessment` | an online test, coding challenge or take-home task, with or without a deadline |
| `offer` | a job offer or offer details |
| `rejection` | the application was declined or the position closed, including "we will keep your CV on file" |
| `recruiter_outreach` | a recruiter or hiring manager contacting me about a role |
| `other` | fits none of the above: automated "application received" receipts, job recommendation digests |

Only the sender's new message counts. A quoted earlier message in a reply
thread (below "On … wrote:" or "… şunu yazdı:") is history, not the email's
content.

## Company

The hiring company's name as the email gives it, with its legal suffix if the
email writes one (`A.Ş.`, `Ltd. Şti.`). Not the job board, the ATS, the test
platform or a recruitment agency: when an agency writes on behalf of a named
client, the client is the company. `null` when the email names no hiring
company, or lists jobs at several companies.

## Role

The job title the email is about, as the email writes it. `null` when it names
none, or lists several jobs. When an email turns down one role and invites me
for another, the role is the one I'm invited for.

## Date

The key date the tracker would store:
- `interview_invite`: the interview's date and time;
- `assessment`: the deadline;
- every other class: `null`.

Written as `YYYY-MM-DD`, or `YYYY-MM-DDTHH:MM` when the email gives a time, in
the wall-clock time the email states (no offset). A calendar invite's start
is the date when there is one. `null` when the email gives no date (a
scheduling link, "our coordinator will contact you", "whenever suits you").
Never computed from a relative phrase.

## Language

`tr` or `en`: the language the email is written in.
