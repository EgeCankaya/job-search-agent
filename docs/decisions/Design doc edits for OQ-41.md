# Design doc edits for OQ-41: finishing week 2

**For:** the PRD v0.4.0 and the design doc (Claude Doc, revision 90)
**Decisions that triggered it:** on 2026-10-18 I answered the five week 2 questions (keywords, assessment domains, local-mode polling, the alias list, who reads the queue). The same day, the route 1 test call succeeded.
**Status:** **all six items accepted on 2026-10-18, and applied the same day** to the PRD (v0.4.1) and the source Claude Doc, [Job search inbox agent design](https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9) (revision 90 → 98). Each design-doc edit was checked by reading the doc back. The PDF in this folder is still the revision 90 export; re-export it to match.
**Depends on:** OQ-39 (model routes), OQ-40 (SNS alerts).

## The route 1 test call (2026-10-18)

The two Service Quotas requests show `CASE_CLOSED`, and the applied Nova quotas are no longer 0:

| Quota | Value |
|---|---|
| Cross-Region tokens per minute, Nova Micro and Nova Lite | 400,000 each |
| Cross-Region requests per minute, Nova Micro and Nova Lite | 20 each |
| In-Region tokens per minute, Nova Lite | 200,000 |
| In-Region requests per minute, Nova Lite | 10 |
| Tokens per day, Nova Micro / Nova Lite | 207,360,000 / 564,480,001 |

One Converse call to `eu.amazon.nova-micro-v1:0` from eu-north-1 succeeded: 7 input tokens, 2 output tokens, stop reason `end_turn`. The Free plan's no-cross-Region rule does not block the `eu.` profile. The profile routes to `amazon.nova-micro-v1:0` in eu-central-1, eu-north-1, eu-west-1 and eu-west-3, so data stays in the EU.

## (a) Inflected Turkish subject keywords

Gmail matches whole words, so `subject:mülakat` misses "mülakatınız" and `subject:başvuru` misses "başvurunuz". That is an M8 coverage gap.

| PRD section | Change |
|---|---|
| ING-1 part 4 | Keep the 9 base words and add the common forms: **başvurunuz, başvurunuzu, başvurunuza, başvurunuzla, başvurunuzun, başvurusu, mülakatı, mülakata, mülakatınız, mülakatınızı, görüşmesi, görüşmeye, görüşmeniz, görüşmenizi, teklifi, teklifimiz, teklifiniz, değerlendirmesi, değerlendirmeye, değerlendirmeniz**. "görüşmek" is left out, because "görüşmek üzere" is a common sign-off |

Design doc: no change. It doesn't list the keywords.

## (b) Assessment-platform sender domains

| PRD section | Change |
|---|---|
| ING-1 part 1 | "Senders from the ATS domain list in config" → "Senders from the ATS and assessment-platform domain list in config". The list gains hackerrank.com, codility.com, testgorilla.com and codesignal.com |

Their invitations often have subjects like "You're invited to a test", which no keyword matches. Their marketing mail is classified "other" and filed silently.

Design doc: no change.

## (c) Local mode: only the Mac worker polls

The design doc's mode table and PRD ING-1 and §15 already say the Mac worker polls in MacBook mode. MODE-2's "the EventBridge schedules stay on" contradicts them, because the Lambda's read token lives in AgentCore Identity, which MacBook mode replaces with the Keychain (ING-4). Two pollers would also share one last-poll time.

| PRD section | Change |
|---|---|
| MODE-2 | "The always-free stack includes the EventBridge schedules, which stay on in MacBook mode (APR-1)" → "The always-free stack includes the EventBridge schedules. The daily stale-check schedule stays on in MacBook mode (APR-1). The Gmail poll schedule is turned off, because the Mac worker polls (ING-1)" |
| UAC-MODE-2 and MODE-2 acceptance | "…and leaves the DynamoDB tables, SQS queues and schedules unchanged" → "…leaves the DynamoDB tables, the SQS queues and the stale-check schedule unchanged, and turns the poll schedule off" |

Design doc: no change. It already says this.

## (d) The alias list lives in the tracker table

| PRD section | Change |
|---|---|
| TRK-1 normalization bullet | Add: "The alias list is one reserved item in the tracker table (it can't collide with a company key), never in the repo. Every reader of tracker rows skips it" |
| TRK-3 | Add an optional input: `import_applications --aliases <csv>`, with the columns variant and canonical name. It replaces the alias item before the rows are imported, so the import uses the same aliases. The CSV stays on the Mac, like the applications CSV |

Cost: none to speak of. The poll's existing scan returns the item in the same page, and an edit is one write. It works in both modes and with GitHub Actions deploys (week 3), and needs no redeploy.

Design doc: no change. Table contents are a build detail (PRD §13).

## (e) A temporary queue reader for week 2

Daily use starts in week 2, but the agent arrives in week 3. Until then, a small Lambda reads the queue so the week 2 exit ("a test invite produces exactly 1 alert email") can pass now.

**What it does:** it is SQS-triggered and skips any message that isn't `new_email`. It then:
1. fetches the message with the read grant (the poller's workload identity);
2. trims it (CLS-3);
3. classifies it and extracts company, role and date in the same Nova Micro call (CLS-1, CLS-2), using the calendar invite's start when there is one;
4. applies the TRK-1 update;
5. publishes the ALR-1 alert to the alert topic.

ING-2: a second delivery of the same message is skipped through the processed-email log. A conditional tracker write records the message ID, so it lands once, and a conditional "alert sent" mark allows at most 1 alert.

**What it is not:** it is not the agent. It has no tools and no model-chosen actions, and it never touches the compose grant. It writes the tracker and publishes to SNS directly, because the Gateway tools (TOOL-1) and Cedar (TOOL-2) arrive in weeks 3 and 5. Its steps become the agent's nodes in week 3, and then it is removed.

| PRD section | Change |
|---|---|
| §14 week 2 deliverables | Add "[v0.4.1] A temporary queue reader Lambda (fetch, trim, classify and extract, TRK-1 update, ALR-1 alert) until the week 3 agent replaces it [OQ-41]" |
| §14 week 3 deliverables | Add "[v0.4.1] The agent replaces the temporary queue reader; the topic policy moves to the agent's role [OQ-41]" |
| ALR-3 | "The topic policy SHALL let only the agent's role publish" → add "(in week 2, the temporary queue reader's role)". The policy also denies every other principal, so my `aws login` session can no longer publish test alerts |

| Design doc place | Change |
|---|---|
| Components table, AWS Lambda row | "Gmail poller, tracker tools" → "Gmail poller, tracker tools, and in week 2 only a temporary queue reader" |
| Build plan, week 2 | Add: "Until the week 3 agent exists, a temporary Lambda reads the queue: it classifies and extracts, updates the tracker and sends the alert email" |

## (f) Model route 1 chosen

| PRD section | Change |
|---|---|
| CLS-1 | "(Nova Micro on route 1, Nova Lite on route 2; OQ-39 (b), route not yet chosen)" → "Nova Micro through the `eu.` profile (route 1, chosen 2026-10-18; OQ-39 (b))" |
| TOOL-3 | Route 1 chosen. The role names the profile and the 4 Regional model ARNs it routes to (eu-central-1, eu-north-1, eu-west-1, eu-west-3), only through that profile (`bedrock:InferenceProfileArn` condition). Route 2 stays as the fallback if cross-Region calls ever stop working |
| §16a Nova quotas row | Blocker → **Verified (2026-10-18)**: the quota values above, and the route 1 call succeeded |
| §18 OQ-39 row, §16 Bedrock quota risk | The route in (b) is decided: route 1 |
| Header, §16a intro | "currently one: the Nova quotas" → no blocker |

| Design doc place | Change |
|---|---|
| Models paragraph | "The route is picked once the account's Nova quotas are raised; every Nova inference quota was 0 on 2026-10-17" → "Route 1 was chosen on 2026-10-18, after the quotas were raised and its test call succeeded". Keep the route 2 sentence as the fallback |
| Flow step 3, build plan week 2, cost table Bedrock row | "(Micro or Lite, depending on the model route)" → "Nova Micro" (chat and drafting stay on Nova Lite) |

## What the code change will be (after acceptance)

1. `settings.py`: the keyword forms (a) and the 4 domains (b), with tests that the query contains them.
2. `infra`: the poll schedule is ENABLED only in cloud mode (c), and stays DISABLED in code until I say so. A test covers both modes.
3. `tracker.py`: the alias item. `rows()` skips it, and `import_applications --aliases` sets it (d). The poll and the reader read it.
4. The reader (e): `gmail.py` gains a fetch method; `classify.py` returns company, role and date; `reader.py` is the handler; and the CDK gets the reader Lambda with:
   - an explicit 7-day log group and a hand-built role with exact ARNs;
   - the queue's visibility timeout raised above the reader's timeout;
   - an event source mapping with partial batch failures and a concurrency cap, kept **disabled** until I say so;
   - the topic policy narrowed to the reader's role.
5. Route 1 (f): `CLASSIFIER_MODEL_ID=eu.amazon.nova-micro-v1:0` as config, and `bedrock:InvokeModel` for the reader's role only.

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Turkish keyword forms | Accept | 2026-10-18 | |
| (b) Assessment-platform domains | Accept | 2026-10-18 | |
| (c) Only the Mac worker polls in local mode | Accept | 2026-10-18 | |
| (d) Alias list in the tracker table | Accept | 2026-10-18 | |
| (e) Temporary queue reader | Accept | 2026-10-18 | |
| (f) Route 1 | Accept | 2026-10-18 | |
