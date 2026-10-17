# Design doc edits for OQ-32

**For:** "Job search inbox agent design" (PDF, 13 pages, 2026-10-17)
**From:** Job search inbox agent PRD v0.3.0, OQ-32
**Status:** **All seven items accepted on 2026-10-17, and applied the same day** to the source Claude Doc, [Job search inbox agent design](https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9) (revision 30 → 51). That means 25 text replacements plus 2 new mode-table rows (d3, f2), each checked by reading the doc back.
**PDF:** re-exported from revision 51 on 2026-10-17, replacing the old PDF in this folder. It is now 14 pages, so the page numbers in the tables below refer to the old 13-page PDF.

The PDF is an export, so apply accepted edits in the source document and re-export. Page numbers refer to the current PDF. "Current text" is quoted from the PDF's text layer, so line breaks may differ slightly.

**If you reject an item:** the PRD requirement it affects goes back to the design doc's behavior, and the research finding stays in the PRD as a risk (PRD §13). The "If rejected" line under each item says what that means in practice.

## (a) Google app publishing status and split permissions (OQ-2, OQ-10)

**Why:** Google issues refresh tokens that expire after 7 days to External apps in "Testing" status, unless they request only basic profile scopes ([Google OAuth 2.0 docs](https://developers.google.com/identity/protocols/oauth2)). Personal use is an exception to restricted-scope verification ([restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)). Also, `gmail.compose` covers both managing drafts and sending email ([Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)).

| # | Location | Current text | Replace with |
|---|---|---|---|
| a1 | p. 12, Risks table, row "Google OAuth for Gmail", Mitigation | "Do it in week 2 as a spike; keep the Google app in testing mode with only your account" | "Do it in week 2 as a spike. Publish the Google app as "In production" without submitting it for verification (personal use is exempt), and use it only with your account. Apps left in "Testing" get refresh tokens that expire after 7 days. Use two grants: gmail.readonly for reading, and gmail.compose only for the draft tool." |
| a2 | p. 12, "Open questions to settle in week 1", second bullet | "Does Google allow the Gmail read scope for a personal app in testing mode without verification?" | "Answered: personal use is exempt from verification, but Testing-mode refresh tokens expire after 7 days. The app therefore runs as an unverified production app, used only by you (Google caps unverified apps at 100 users over their lifetime)." |
| a3 | p. 11, Build plan, week 2 | "Set up Google OAuth (AgentCore Identity), the scheduled poller, SQS and a first Nova Micro classifier." | "Set up Google OAuth (AgentCore Identity) with two grants, read-only for the poller and compose only for the draft tool, then the scheduled poller, SQS and a first Nova Micro classifier." |

**If rejected:** the Google app stays in Testing. You re-consent every 7 days, and ingestion stops silently whenever you forget. ING-4's "works on day 8" criterion is dropped.

## (b) Latency wording (OQ-7)

**Why:** with a 30-minute poll, "within minutes" and "p95 under 5 minutes" from arrival can't be met. The doc defines alert latency from email arrival.

| # | Location | Current text | Replace with |
|---|---|---|---|
| b1 | p. 1, "How you use it on a normal day", first bullet | "Every 30 to 60 minutes it checks for new emails" | "Every 30 minutes it checks for new emails" |
| b2 | p. 1, second bullet | "An interview invite or assessment deadline reaches WhatsApp within minutes, with company, role and any date it found." | "An interview invite or assessment deadline reaches WhatsApp within about 35 minutes of arriving (one 30-minute poll plus processing), with company, role and any date it found." |
| b3 | p. 2, "What happens to one new email", step 4 | "you get a WhatsApp alert within minutes" | "you get a WhatsApp alert within one poll interval plus a few minutes" |
| b4 | p. 3, Components table, EventBridge Scheduler row | "Starts the Gmail poll every 30 to 60 minutes and the daily stale-application check" | "Starts the Gmail poll every 30 minutes and the daily stale-application check" |
| b5 | p. 9, example CV line | "p95 alert latency under 5 minutes" | "p95 alert latency under 35 minutes from arrival" (still a placeholder until measured) |
| b6 | p. 9, metrics table, "Alert latency" row, "What to report" | "p50 and p95 in minutes" | "p50 and p95 in minutes, from arrival (headline) and from detection (pipeline only)" |

**If rejected:** the PRD keeps the split measures (NFR-LAT-1 and NFR-LAT-2). The CV line must still not claim under 5 minutes from arrival unless that is measured.

## (c) KDV is not covered by credits (OQ-5)

**Why:** "Promotional Credit will not be applied against any sales, use, gross receipts" or similar taxes ([AWS Promotional Credit Terms](https://aws.amazon.com/awscredits/), section 7). The 20% rate is the standard KDV rate (general knowledge). Confirm both on the first invoice.

| # | Location | Current text | Replace with |
|---|---|---|---|
| c1 | p. 10, first line | "At that rate, $100 to $200 in credits lasts well past the 12-month expiry, so in practice you would pay close to nothing in year one." | "At that rate, $100 to $200 in credits lasts well past the 12-month expiry. Credits are not applied against taxes, so in year one you pay the KDV on your usage: roughly $0.40 to $1.60 a month at the 20% standard rate (estimate; check the first invoice)." |
| c2 | p. 10, "Tax." paragraph | "Whether KDV applies to credit-covered usage is not confirmed." | "Credits are not applied against taxes (AWS Promotional Credit Terms, section 7), so expect KDV on credit-covered usage too. Confirm on the first invoice." |
| c3 | p. 12, Risks table, row "Credits not covering everything", "Why it matters" | "Marketplace models and KDV coverage are undocumented" | "KDV is not covered by credits; Marketplace model coverage is undocumented" |
| c4 | p. 5, "Credit-saver mode on the MacBook", first paragraph | "AWS then costs about $0 to $1 a month." | "AWS then costs about $0 to $1 a month, plus KDV." |

**If rejected:** nothing changes in the PRD's requirements. NFR-COST-6 stays as a finding, and the doc's year-one figure stays optimistic.

## (d) Approval check in the draft tool, and cedarpy in MacBook mode (OQ-9, OQ-11)

**Why:** Cedar cannot look up DynamoDB, so it can't verify an approval by itself. It can check tool inputs and the caller's identity ([AgentCore Policy getting started](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-getting-started.html)). MacBook mode has no Gateway, so without a local enforcer the brief's "not just the prompt" rule would not hold there.

| # | Location | Current text | Replace with |
|---|---|---|---|
| d1 | p. 7, "Tools", `create_gmail_draft` bullet | "create_gmail_draft: save a follow-up draft. The only write action it has on Gmail, and only after approval." | "create_gmail_draft: save a follow-up draft. The only write action on Gmail. It needs an approval ID, and the tool itself checks that you approved exactly this text before saving. Its Gmail client can only create drafts, because the compose permission would also allow sending." |
| d2 | p. 8, "Safety against prompt injection", second bullet | "An AgentCore Policy written in Cedar blocks any send or forward action at the Gateway, whatever the model decides." | "An AgentCore Policy written in Cedar, in ENFORCE mode, blocks any send or forward action at the Gateway, whatever the model decides. In MacBook mode, the local MCP server checks the same Cedar policy files with cedarpy before every tool call." |
| d3 | p. 6, mode table | (no row) | Add the row: "Never-send enforcement | Cedar policy at AgentCore Gateway | The same Cedar policies, checked by cedarpy in the local MCP server" |

**If rejected:** TOOL-4's in-tool approval check and MODE-4 are removed from the PRD. MacBook mode then relies on the tool set and the prompt alone, which conflicts with the brief.

## (e) The approval workflow, not the model, saves the draft (OQ-36)

**Why:** once you have approved a draft, the exact approved text should be saved with no model in between. A separate caller identity also lets Cedar forbid the draft tool for the agent.

| # | Location | Current text | Replace with |
|---|---|---|---|
| e1 | p. 8, "Approval gate", step 3 | "A durable function waits, at no compute cost, until you tap one." | "A durable function waits, at no compute cost, until you tap one. Your tap goes straight to the waiting function, not to the model." |
| e2 | p. 8, step 4 | "On Approve it saves the draft to Gmail. You review and press Send yourself." | "On Approve, the approval workflow (not the model) saves the exact approved text to Gmail as a draft. It calls through the Gateway under its own identity, so the Cedar policy still applies. You review and press Send yourself." |
| e3 | p. 7, "Tools" heading line | "Tools (served as MCP through AgentCore Gateway)" | "Tools (served as MCP through AgentCore Gateway; create_gmail_draft is called only by the approval workflow)" |

**If rejected:** the agent calls `create_gmail_draft` itself after the tap, as the doc now implies. TOOL-4's `approval_id` check still guards it, but the model handles the approved text, and Cedar can't single out the approval step.

## (f) Optional: fill gaps in the mode table (OQ-21)

These fill rows the doc leaves empty. They don't change any decision in it.

| # | Location | Current text | Replace with |
|---|---|---|---|
| f1 | p. 6, mode table, "Approval wait" row | "Durable function | Pending drafts kept in DynamoDB until you tap Approve" | "Durable function, with a pending-draft record in DynamoDB | Pending-draft record in DynamoDB until you tap Approve" |
| f2 | p. 6, mode table | (no row) | Add the row: "Daily stale check | EventBridge Scheduler | EventBridge Scheduler sends the check to SQS; the Mac handles it when it wakes" |

**If rejected:** no effect on the PRD, which already specifies both (APR-1, APR-3).

## (g) How the private labeled set runs in CI (OQ-8)

**Why:** the doc says to "Keep the raw labeled emails on the Mac only" (p. 5) and also to "Run the labeled set ... in GitHub Actions on every change" (p. 9). On GitHub's runners, the emails would leave the Mac.

| # | Location | Current text | Replace with |
|---|---|---|---|
| g1 | p. 9, first paragraph | "Run the labeled set and the chat suite in GitHub Actions on every change, and fail the build if invite recall or the injection pass rate drops." | "Run the evaluation in GitHub Actions in two tiers, and fail the build if invite recall or the injection pass rate drops. Tier 1 runs on every change on GitHub's runners, with synthetic emails, the injection set and the chat suite. Tier 2 runs the private labeled set on a self-hosted runner on your Mac, on pushes to main and weekly, never on pull requests, and uploads only scores. This keeps the raw emails on the Mac." |

**If rejected:** you must pick one of the two rules in the doc. Either the labeled set may go to GitHub's runners, which drops NFR-PRIV-6, or CI checks only scores you upload by hand from the Mac.

## Additions that change no decision (no edit required)

v0.3 adds these to the PRD. The doc doesn't contradict them, so adding them to it is optional, for completeness:

- a one-off CSV import of current applications (TRK-3);
- calendar invites read for dates (CLS-2);
- a language tag on labels and per-language metrics (H6);
- a dead-letter queue with an alarm (ING-3);
- the webhook signature check (CHT-2);
- deploying from main through GitHub Actions with OIDC (OPS-1);
- DynamoDB TTLs (NFR-PRIV-4).

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Google app publishing status and split permissions | Accept | 2026-10-17 | Verify on day 8 after consent (week 3 exit) |
| (b) Latency wording | Accept | 2026-10-17 | The 35-minute figure is still initial until M5 is measured |
| (c) KDV | Accept | 2026-10-17 | Confirm the rate and the basis on the first AWS Turkey invoice |
| (d) Approval check in the tool; cedarpy | Accept | 2026-10-17 | Pin the cedarpy version; parity tests in week 5 |
| (e) Approval workflow saves the draft | Accept | 2026-10-17 | Week 4 test: the Gateway tells the two identities apart; fallback in PRD §16 |
| (f) Mode-table gaps (optional) | Accept | 2026-10-17 | — |
| (g) CI tiers for the private set | Accept | 2026-10-17 | Self-hosted runner set up in week 6; no `pull_request` trigger on Tier 2 |
