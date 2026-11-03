# Design doc edits for OQ-40: a Mac app and email alerts instead of WhatsApp

**For:** the PRD v0.3.2 and the design doc (Claude Doc, revision 51)
**Decision that triggered it:** on 2026-10-17 I chose not to create a Meta account. WhatsApp is replaced by a local Python app on my Mac, plus email alerts.
**Status:** **all five items accepted on 2026-10-17, and applied the same day** to the PRD (v0.4.0) and the source Claude Doc, [Job search inbox agent design](https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9) (revision 51 → 90, together with OQ-39). Each edit was checked by reading the doc back, and the flow diagram was redrawn. The PDF in this folder was re-exported from revision 90. **The code still has the WhatsApp webhook**; the code change below is a separate task.
**Depends on:** OQ-39 (Free plan, Stockholm).

## The new interface in one paragraph

**Urgent alerts** (interview invite, assessment, offer, and "Possible:") arrive as **email**. AWS sends them through an SNS topic that has a single confirmed subscription: my address. Everything else happens in a **Mac app**: a Python [Streamlit](https://streamlit.io) app that listens only on `127.0.0.1`, started with `uv run`.

The app has three pages:
- **Dashboard:** totals, open applications, counts by status, silent 10+ days, upcoming dates, and this week's alerts.
- **Ask:** chat questions to the agent.
- **Follow-ups:** each pending draft, with Approve and Skip.

The app signs in with my `aws login` session, so IAM is the owner check. **There is no public inbound endpoint any more.** API Gateway, the webhook Lambda and the Meta SSM parameters are removed.

## (a) Interface and scope

| PRD section | Change |
|---|---|
| One-liner, §2, §7 | "messages me on WhatsApp" → "emails me for interview invites, assessments and offers; a Mac app answers questions and takes approvals". §7: "I check email on my phone every day and work on the Mac" |
| §3 Non-goals | "Any web or mobile frontend. WhatsApp is the only interface" → "No hosted or public frontend. The only UI is a local Mac app on 127.0.0.1" |
| §4 Out of scope | The "Web or mobile UI" row becomes "Hosted web or mobile UI". Add a row: "WhatsApp, Telegram and other chat apps: out of scope (OQ-40), they need a third-party account". Remove the "AWS End User Messaging Social" row |
| §9 Conventions | Rename `send_whatsapp` to **`notify_owner`**: publish to the alert SNS topic, with no recipient input. The HTTP endpoints bullet becomes "There is no inbound HTTP endpoint. The Mac app reaches AWS with the owner's IAM session" |
| §17 Decisions | Add a row. Chosen: a local Python app (Streamlit) plus SNS email. Rejected: WhatsApp (needs a Meta account, templates, a SIM), Telegram (another account), a native SwiftUI app (a new language). Revisit only if I miss alerts |

## (b) Alerts (ALR)

| PRD section | Change |
|---|---|
| ALR-1, US-01, US-02 | "WhatsApp alert" → "alert email". The content rule is unchanged: class, company, role and date only (NFR-PRIV-3) |
| **ALR-2** | **Delete.** Email has no 24-hour window and no templates. Remove US-03 and UAC-ALR-2 too |
| ALR-3 | Alerts go only to me through the SNS topic's single confirmed subscription. The topic policy lets only the agent's role publish. Keep the Cedar forbid on recipient-like keys |
| ING-1, M8 | The Gmail query excludes the alert sender (`-from:no-reply@sns.amazonaws.com`), so the agent never reads its own alerts, whose subjects contain words like "interview". Add a test: an alert email is not returned by the query |

## (c) Chat, approvals and the new dashboard

| PRD section | Change |
|---|---|
| CHT-1, US-04 | Questions are asked on the Mac app's **Ask** page. The app calls the agent's chat path directly: AgentCore Runtime in cloud mode, the local worker in MacBook mode. The p95 target of 15 s is unchanged (NFR-LAT-3) |
| **CHT-2** | **Replace** with "Owner-only access". The app binds to 127.0.0.1 only, and every AWS call uses my IAM session, so there is no signature check, verify token or sender filter. Remove UAC-CHT-2 and US-07's "forged webhook call" criterion |
| APR-2, US-05 | Drafts appear on the **Follow-ups** page with Approve and Skip, and an email says "You have N follow-up drafts ready". The window and template branch is removed |
| APR-3, TOOL-4 | "My button reply carries the `approval_id`" → "Approve or Skip in the app sends the decision with the `approval_id` on the SQS queue". "Approved with a tap from my number" → "approved in the app by my IAM identity". Every other APR-3 and TOOL-4 check is unchanged |
| MEM-1 | Short-term memory is the Ask page's conversation |
| **New DASH-1 (P2)** | The **Dashboard** page shows totals, open applications, counts by status, open rows silent for 10 or more days, the next 7 days of key dates, and this week's alert counts, including "Possible:" alerts. It reads DynamoDB only and never writes. *Acceptance:* on a seeded table, every number matches a direct count |

## (d) NFRs, milestones and risks

| PRD section | Change |
|---|---|
| NFR-PRIV-7 | **Delete.** Meta no longer sees alert or chat content |
| NFR-SEC-2 (c), NFR-SEC-3, trust boundaries, threat table | (c) "a WhatsApp message to a number other than mine" → "a notification to anyone but me". Remove the Meta token and app secret. The boundary "Meta → webhook" → "Mac app → AWS (owner IAM session)". The "forged webhook call" threat becomes "No public endpoint exists" |
| NFR-LAT | LAT-1 and LAT-2 end at "SNS accepts the publish". Delete LAT-4, the webhook acknowledgement |
| §14 week 1 | Exit: "ping gets a reply" and "both templates submitted" → "**a test alert email arrives**" |
| §14 weeks 2 to 5 | Week 2: urgent emails go out by email. Week 3: the Mac app with the Dashboard and Ask pages. The signature check is dropped. Week 4: the Follow-ups page; the template, then tap, then drafts criterion is dropped. Week 5: no prepaid SIM |
| §15 mode table | Remove the "WhatsApp inbound" row. Add "Mac app: the same app in both modes; only its agent endpoint changes". Remove the "point Meta's webhook" switch step |
| §16 and §16a | Remove the WhatsApp-number and template-button risks and the Meta dependency. Add two risks. "Email alerts are easier to miss than a chat push", mitigated by a Gmail filter that marks the alert sender as important with phone notifications on. "The agent reads its own alerts", mitigated by the query exclusion and its test. Add SNS email (always free, 1,000 emails a month) and Streamlit as dependencies |
| §18 | Close OQ-3, OQ-12, OQ-28 and OQ-31 as no longer applicable |
| G5 (CV) | Agentic evidence: add "an approval UI and dashboard". README screenshots replace WhatsApp screenshots |

## (e) Design doc

Overview bullets 2 to 4, the "WhatsApp setup" section (it becomes "Interface: Mac app and email alerts"), the Components table (remove API Gateway; add SNS and the Mac app), the mode table, the cost table (the WhatsApp row becomes SNS email at $0), the build plan weeks 1 to 5, and the "WhatsApp sender number" risk row. The flow keeps one queue. New email, the daily stale check and approval decisions go through SQS, and chat questions go straight from the app to the agent.

## What the code change will be (after acceptance)

1. Remove the webhook Lambda, API Gateway and the five Meta and owner SSM parameter names, along with their tests.
2. Add an SNS alert topic with one email subscription (the owner's address, supplied at deploy time like `alert_email`), and a topic policy. Update the tests.
3. Redeploy. Confirm the subscription email, then send a test alert. That meets the new week 1 exit.
4. The Mac app (`ui/`, Streamlit) starts in week 3. The Dashboard page can come first, because it only reads data.

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Interface and scope | Accept | 2026-10-17 | |
| (b) Alerts by email | Accept | 2026-10-17 | |
| (c) Chat, approvals, dashboard | Accept | 2026-10-17 | |
| (d) NFRs, milestones, risks | Accept | 2026-10-17 | |
| (e) Design doc | Accept | 2026-10-17 | |
