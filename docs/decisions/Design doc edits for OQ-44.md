# Design doc edits for OQ-44: building week 4, memory and approval

**For:** the PRD v0.4.3 and the design doc (Claude Doc, revision 106)
**What triggered it:** week 4 starts on 2026-10-20. The PRD and the design doc name the week 4 parts (AgentCore Memory, the daily stale check, drafting with Nova Lite, the durable-function approval wait, decisions that bypass the model, `search_gmail` and `create_gmail_draft`, the approval workflow's identity in Cedar, the Follow-ups page, the drafts-ready email) but leave the choices below open. Week 3 also changed the ground: the queue consumer exists, deploys run on the self-hosted runner, and the account's managed-organization SCP was found to block one service (OQ-43).
**Status:** **all fourteen items accepted on 2026-10-20** (the (n) dependencies approved), **and applied the same day** to the PRD (v0.4.4) and the source Claude Doc, [Job search inbox agent design](https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9) (revision 106 → 113). Each design-doc edit was checked by reading the doc back. The PDF in this folder is still the revision 90 export; re-export it to match. The (n) packages are added with `uv add` (`approval` group and dev). No code is written yet.
**Depends on:** OQ-20, OQ-27, OQ-30, OQ-35, OQ-36, OQ-42 (a), (b), (c), (d), (e), (f).
**Plan:** `Week 4 plan.md` shows where each item lands.

Every item gives a recommendation and the main alternative. "No change" means the PRD or the design doc already says it.

## Checked on 2026-10-20 (read-only, my `aws login` session)

| Check | Result |
|---|---|
| SCP, policy simulator with `aws:RequestedRegion` = eu-north-1 | Every week 4 action is allowed: Lambda create and all durable-execution calls, AgentCore Memory (create, events, records, extraction), AgentCore Policy (engine, policy), `InvokeGateway`, `GetResourceOauth2Token`, `scheduler:CreateSchedule`, `sqs:SendMessage`. `iam:CreateOpenIDConnectProvider` is still denied. Without the Region every action is denied: the SCP pins eu-north-1 |
| Durable functions in Stockholm | AWS's 2025-12 announcement lists Europe (Stockholm); `ListDurableExecutionsByFunction` answers in eu-north-1 |
| AgentCore Memory and Policy | `list-memories` and `list-policy-engines` answer in eu-north-1 (both empty) |
| `inbox-agent-drafts` workload identity | Exists, as the week 2 spike left it |
| aws-cdk-lib 2.273, boto3 1.43.114 | Durable config, `Memory`, `PolicyEngine`, `Policy` and every API call week 4 needs; no new CDK package |

## (a) The approval wait is a durable function in the AgentCore stack, started asynchronously by the consumer

OQ-27 asked whether durable functions exist in Stockholm. They do (above). Two facts from AWS's docs shape the rest: an SQS event source mapping caps a durable execution at 15 minutes, so a 7-day wait must be started asynchronously; and a durable function must be invoked by a version or alias.

**Recommendation:**
- **One approval workflow function**, Python 3.13, arm64, with durable execution on (execution timeout 8 days, history kept 14 days), published as a new version on every deploy. One execution per draft.
- **In the AgentCore stack.** It calls the Gateway, which lives there, and MacBook mode doesn't use it (the pending-draft record holds the wait, APR-3). Deleting the stack in MacBook mode ends pending waits, which §15 step 3 already handles.
- **The consumer starts it,** asynchronously, on the published version, with the execution name = the `approval_id`, once per draft the stale check returns. A redelivered check can't start a second execution for the same draft.
- **The workflow writes the pending-draft record**, not the Runtime: the Runtime role gets no write access to that table, so the model's side can never mark a record approved. The workflow puts the record with its callback ID, waits on one callback (7 days, `CallbackError` on timeout), and on Approve calls `create_gmail_draft` through the Gateway inside a step, so a replay never repeats it.

**Alternative:** Step Functions with a callback task token (§17). Rejected: the reason to switch (no durable functions in Stockholm) is gone.

| PRD section | Change |
|---|---|
| APR-3 | Add: "The approval workflow is a durable function in cloud mode, started asynchronously by the queue consumer with the `approval_id` as the execution name. It writes the pending-draft record; the agent's role cannot write that table" |
| §16a durable-functions row | Status → **Verified (2026-10-20):** listed for Stockholm by AWS (2025-12) and the API answers in eu-north-1 |
| §17 approval wait row | "Stockholm is checked in week 4" → "available in Stockholm (checked 2026-10-20)"; revisit → No |
| §18 OQ-27 | Open → **Resolved (2026-10-20)** |
| MODE-2 | Add: "The approval workflow is in the AgentCore stack" |

| Design doc place | Change |
|---|---|
| Agent design, Approval gate, step 3 | "A durable function waits, at no compute cost, until you click one." → "A durable function, started by the queue consumer for each draft, waits at no compute cost until you click one." |

## (b) One named wildcard: durable-execution ARNs

The guardrail says no wildcards in any role. AWS authorizes checkpoints and callbacks against the durable execution's ARN, `<function ARN>:<version>/durable-execution/<name>/<id>`, and documents `<function ARN>:*` as the way to grant them, because executions started on older versions resume on those versions after a deploy.

**Recommendation:** allow exactly one wildcard form: the approval workflow's own function ARN followed by `:*`, only for the durable actions (`lambda:CheckpointDurableExecution` and `lambda:GetDurableExecutionState` on the workflow's role; `lambda:SendDurableExecutionCallbackSuccess` and `...Failure` on the consumer's role). The guardrail test names this one exception and keeps rejecting every other `*`. No managed policy (`AWSLambdaBasicDurableExecutionRolePolicy` is not used).

**Alternative:** pin to the current version (`:<n>/durable-execution/*`). Still a wildcard, and every deploy would break the callbacks of waits started before it.

PRD and design doc: no change (a build detail). CLAUDE.md's guardrail line gets the exception.

## (c) The daily check goes through the queue in both modes, capped at 5 drafts, oldest first

APR-1 says Scheduler triggers the check in both modes and, in MacBook mode, puts it on SQS. ING-3 says the daily check goes through the one queue. In cloud mode the target is not stated.

**Recommendation:**
- **One schedule, one target, both modes:** `cron(0 9 * * ? *)` in Europe/Istanbul, target the agent queue with `{"type": "stale_check"}`. The consumer takes it to the Runtime in cloud mode; the Mac worker in MacBook mode. No mode-specific schedule, and MODE-2's "stays on in MacBook mode" holds unchanged.
- **The stale-check path is a fixed sequence** like the batch path (OQ-42 (b)): eligible rows are chosen in code (APR-4); per row, `search_gmail`, then `read_email`, then one Nova Lite call writes the text. The model never picks a tool or a recipient.
- **At most 5 drafts per check, oldest last contact first.** The rest stay eligible and come up the next day. Imported rows carry the applied date as last contact, so the first live check would otherwise propose most of the tracker at once, and one check must fit in the consumer's 180-second limit.

**Alternative:** no cap, and the consumer's timeout raised to 15 minutes (queue visibility then 90 minutes, which delays every retried email by that much).

| PRD section | Change |
|---|---|
| APR-1 | Add: "In both modes, Scheduler puts the check message on the SQS queue. A check proposes at most 5 follow-ups, oldest last contact first; the rest stay eligible for the next day" |
| APR-1 acceptance | Add: "With 7 eligible rows, a check proposes the 5 oldest" |
| §18 OQ-21 | Add: "SQS is the target in both modes" |

| Design doc place | Change |
|---|---|
| Components, EventBridge Scheduler row | "…and the daily stale-application check" → "…and puts the daily stale-application check on the queue" |
| Credit-saver table, Daily stale check, cloud column | "EventBridge Scheduler" → "EventBridge Scheduler sends the check to SQS" |

## (d) A decision counts only if it came from my IAM session

TOOL-4 says the draft tool checks that "I approved it in the app, under my IAM identity". APR-3 says the consumer marks the record approved. Nothing says how the consumer knows the decision came from me: any principal that may send to the queue could put a decision there.

**Recommendation:**
- SQS stamps every message with the sender's IAM identity (`SenderId`: the role's unique ID and the session name). The consumer accepts a `decision` only when the role ID is that of my `aws login` role (`managed/AccountFullAccessRole`, resolved once per cold start with `iam:GetRole` on that one ARN). Anything else is dropped and logged by ID.
- The consumer's conditional update records `approved_text_hash` and `decided_by` (the role ID). `create_gmail_draft` requires both, and the hash to match the record's text.
- The decision message carries the hash of the text the page showed, so an approval binds to what I read.
- No queue policy is added: without one, only same-account principals with `sqs:SendMessage` can send, which are the poll role, the schedule role and my session. The Runtime and the tool roles have no send permission.

**Alternative:** the Mac app writes the approval straight to DynamoDB with my session, and the consumer only resumes the wait. Then the app writes data, and APR-3's path changes.

| PRD section | Change |
|---|---|
| APR-3 step 1 | Add: "The consumer accepts a decision only when SQS reports my IAM role as its sender, and records the decided text hash and the sender" |
| TOOL-4 second bullet | "I approved it in the app, under my IAM identity" → add "(the record shows an Approve recorded by the consumer from a message my role sent, OQ-44 (d))" |
| APR-3 acceptance | Add: "A decision message sent by any other role changes nothing" |

Design doc: no change. "Your decision goes through SQS straight to the waiting function" still holds.

## (e) Inputs of `search_gmail` and `create_gmail_draft`

PRD §9 conventions: "`search_gmail` and `create_gmail_draft`: inputs fixed in week 4". Drafting needs the thread of the latest email from a person at the company (APR-2), its sender for the To field, and its `Message-ID` header for threading. `gmail.compose` can't read messages, so these are read at drafting time with the read grant.

**Recommendation:**

| Tool | Inputs | Returns |
|---|---|---|
| `search_gmail` | `company` (a tracker company name) | Up to 5 newest matches, each with message ID, thread ID, sender address, receipt time and the `Message-ID` header. Never subject, snippet or body. The search is the row's company domains and saved recruiter senders, ANDed with the job-search query (ING-1, without `after:`); a row with neither uses the quoted company name ANDed with the job-search query. Read grant only (`inbox-agent-poller`) |
| `create_gmail_draft` | `approval_id` only | The Gmail draft ID. Everything else (To, subject, thread, `In-Reply-To`, text) comes from the pending-draft record, so no caller can change the recipient or the text at draft time. Compose grant only (`inbox-agent-drafts`); its client allows `users.drafts.create` and nothing else |

**Alternative:** `create_gmail_draft` takes the text, thread and To as inputs and compares the text hash. Then a caller supplies the recipient, which the record already pins.

| PRD section | Change |
|---|---|
| §9 conventions, the `search_gmail` and `create_gmail_draft` line | Replace with the two rows above |
| TOOL-1 acceptance | "The Gmail read tools return only messages that match the job-search query" stays; add "`search_gmail` returns no subject, snippet or body" |

| Design doc place | Change |
|---|---|
| Agent design, tools, `create_gmail_draft` | Add: "It takes only the approval ID; the recipient, thread and text come from the approved record." |

## (f) `notify_owner` sends the drafts-ready email with a count

APR-2 says `notify_owner` sends "You have N follow-up drafts ready". The tool's inputs (OQ-42 (d)) are `kind` (urgent or possible), `email_class`, `company`, `role`, `date`: none carries a count, and `email_class` and `company` are required. A new input needs a PRD revision.

**Recommendation:** `kind` gains a third value, `drafts_ready`, with one new optional input, `count` (an integer from 1 to 5, the cap in (c)). With `drafts_ready`, `count` is required and the class, company, role and date are refused; with `urgent` or `possible`, `count` is refused and the old rules hold. The tool writes the text. Still no recipient and no free text (ALR-3). The Runtime role calls it at the end of the stale check, through the Gateway.

**Alternative:** a separate topic publish from the consumer or the workflow. Then a second role publishes to the topic, against ALR-3's single publisher.

| PRD section | Change |
|---|---|
| §9 conventions, `notify_owner` | Add `drafts_ready` to `kind`, and the optional `count` with the rules above |
| APR-2 | "`notify_owner` SHALL send an email saying…" → add "(`kind` = `drafts_ready`, OQ-44 (f))" |
| ALR-3 acceptance | "The tool schema has no recipient field and no free-text field" stays true; no change |

Design doc: no change. The `notify_owner` sentence still holds.

## (g) Follow-up state is bookkeeping on the tracker row, not a tool

TRK-1 lists follow-up state (last proposed date, skip count, last draft date); APR-4 needs "skipped 2 times in a row since its last new email". The tool list is fixed, `list_applications` returns no follow-up state by design (OQ-42 (d)), and `upsert_application` has no follow-up inputs.

**Recommendation:**
- One `follow_up` map per row: last proposed, last skipped, last expired, last draft, skips in a row.
- **Writers:** the approval workflow, directly (as OQ-42 (b) made the processed-email log graph bookkeeping), with `UpdateItem` limited by IAM to the `follow_up` attribute; and `upsert_application`, which resets "skips in a row" when it applies a new message.
- **Reader:** the stale-check path reads the tracker directly (the Runtime role gains `dynamodb:Scan` on the tracker, read-only). Eligibility is one pure function of the row and the date.

**Alternative:** add follow-up inputs to `upsert_application`. That would let the batch path's model-fed tool touch follow-up state, and adds inputs.

| PRD section | Change |
|---|---|
| §9 conventions, Batch path bullet | Add: "Follow-up state on tracker rows is likewise bookkeeping: the approval workflow writes it, and `upsert_application` only resets the skip streak when it applies a new message" |

Design doc: no change.

## (h) AgentCore Memory: two strategies, one summary per company, built-in models

MEM-1 and MEM-2 name short-term turns, long-term preferences and one summary per company; TRK-1 says the agent updates that company's summary. AgentCore's built-in strategies don't include "per company": the summary strategy summarizes a session.

**Recommendation:**
- **One Memory resource** in the AgentCore stack, event expiry 30 days (MEM-2, OQ-30), actor `owner`.
- **Ask page (MEM-1):** each conversation is one session; the chat path reads its last turns and records each turn. A **user-preference strategy** extracts tone, roles and companies to prioritize into `/preferences/owner`.
- **Company summaries (MEM-2, TRK-1):** after each tracker update, the batch path records one event in the session named after the company's key, holding only the class, the status, the dates and the next step, never email text (NFR-PRIV-3). A **summary strategy** keeps one summary per such session, so per company.
- **Readers:** drafting retrieves my tone preference and the company summary; the chat path retrieves preferences. Memory is graph code, not a tool (the chat path's tools stay `list_applications`).
- **Built-in strategies, no override.** AWS says built-in extraction may run in another Region of the same geography, which is the EU here (NFR-PRIV-5 holds). If the Free plan blocks it, extraction fails visibly at exit 4, and the fallback is the built-in strategies with an override model: Nova Lite through the proven `eu.` profile, with a hand-built memory execution role.
- **Export (MODE-3, OQ-4):** `export_memory.py`, run by me, pages `ListMemoryRecords` per namespace into a SQLite file under `data/` on the Mac (gitignored) and compares counts.

**Alternative:** company summaries as records the code writes itself (`BatchCreateMemoryRecords`), no extraction. Deterministic, but it repeats the tracker and shows no extraction.

| PRD section | Change |
|---|---|
| MEM-2 | Add: "Company summaries come from a summary strategy over one session per company, whose events carry only the class, status, dates and next step. Built-in strategies; their inference stays in the EU geography" |
| NFR-PRIV-5 | Add: "AgentCore Memory's built-in extraction runs within the EU geography" |
| §18 OQ-30 | Provisional → **Resolved** once the deployed resource shows 30 days |

| Design doc place | Change |
|---|---|
| Agent design, Memory, Long-term | Add: "Company summaries are kept from a short event the agent records after each tracker update, which carries no email text." |

## (i) Local memory is SQLite, with no new package

MEM-1 says "local SQLite or Chroma" for MacBook mode.

**Recommendation:** SQLite from Python's standard library, behind the same `Memory` interface: the turns of each session (MEM-1, with the same 30-day expiry applied on read and by a delete on start) and the long-term records loaded from the export (MODE-3). No extraction runs locally; preferences and summaries come from the export. Chroma is not added.

**Alternative:** Chroma for semantic lookup. A new package with embeddings, for a few dozen records.

| PRD section | Change |
|---|---|
| MEM-1 | "local SQLite or Chroma" → "local SQLite (OQ-44 (i))" |

| Design doc place | Change |
|---|---|
| Credit-saver table, Memory row | "Local SQLite or Chroma, filled from AgentCore Memory before the switch" → "Local SQLite, filled from AgentCore Memory before the switch" |

## (j) The Gateway's Cedar policy engine arrives in week 4, permits only

Week 4's exit says "a draft call made with the agent's identity is denied", and §14 week 4 lists the approval workflow's identity in Cedar. Week 5 owns the never-send policy. A policy engine in ENFORCE denies every call it doesn't permit.

**Recommendation:**
- Attach a policy engine to the Gateway in **ENFORCE** in week 4, with permits by principal (`AgentCore::IamEntity`, matched on the assumed-role ARN of each role): the Runtime role for `read_email`, `search_gmail`, `upsert_application`, `list_applications`, `notify_owner`; the approval workflow's role for `create_gmail_draft`, only when the input has `approval_id`. An explicit forbid on `create_gmail_draft` for every other principal.
- **Week 5 adds** the send/forward forbid (TOOL-2), the recipient forbid (ALR-3), the injection set and cedarpy (MODE-4).
- It goes live with the daily schedule off, and the next poll with mail proves the batch path still passes the engine.

**Alternative:** LOG_ONLY in week 4. Then nothing is denied, and exit 5 can't pass until week 5.

| PRD section | Change |
|---|---|
| §14 week 4 | Add: "[v0.4.4] The policy engine in ENFORCE with the per-identity permits (OQ-44 (j)); the forbids follow in week 5" |
| §16a two-identity row | Status after the check: Verified, or the §16 fallback recorded |
| §18 OQ-36 | → Resolved after exit 5 |

| Design doc place | Change |
|---|---|
| Build plan, Week 4 | Add: "Attach the Cedar policy engine with one permit per identity; week 5 adds the rules that block sending." |
| Build plan, Week 5 | "Add the Cedar policy that blocks sending…" stays |

## (k) How exit 5 proves the agent's identity is denied

Only AgentCore can assume the Runtime role, so I can't call the Gateway as the agent from the Mac. The batch, chat and stale-check paths never call `create_gmail_draft`, by design.

**Recommendation:** a one-off probe inside the Runtime: a payload type `policy_probe`, accepted only while the Runtime's environment has `POLICY_PROBE=1`, that calls `create_gmail_draft` through the Gateway with a made-up `approval_id` and returns only the error type. One deploy turns it on, I invoke it, and the next deploy turns it off; a test keeps the flag off in the committed config. Alongside it: the same call with my own session is denied (default deny for any other identity), and the approval workflow's call succeeds at exit 2.

**Alternative:** no probe. Prove it with my session's denial, the policy text test and the explicit forbid only. That shows "every identity but the workflow's" in general, not the agent's role itself.

| PRD section | Change |
|---|---|
| UAC-TOOL-4 or §14 week 4 exit | Add: "(checked with a one-off probe in the Runtime, OQ-44 (k))" |

Design doc: no change.

## (l) The query excludes my own mail

The ING-1 query today is `(parts) after:… -from:no-reply@sns.amazonaws.com`. Gmail's search covers Drafts and Sent too, so from week 4 the agent's own follow-up drafts and the follow-ups I send would match the subject keywords (for example "Re: Başvurunuz"), be polled, classified and written to the row as a new contact. That resets the 10-day clock, contradicts TRK-1 ("the latest inbound email") and APR-4's assumption that "the poller cannot see my sent mail".

**Recommendation:** add `-from:me -in:drafts` to the query, with the alert-sender exclusion's tests extended (the built query contains both; the local matcher drops a message from my address).

**Alternative:** keep polling them and teach the batch path to skip messages from me. That reads my own mail for nothing.

| PRD section | Change |
|---|---|
| ING-1 | "The query SHALL exclude the alert sender…" → add "and my own mail (`-from:me -in:drafts`), so drafts and the follow-ups I send are never read back [OQ-44 (l)]" |
| ING-1 acceptance | Add: "A draft and a sent message of mine are not returned by the built query" |

| Design doc place | Change |
|---|---|
| Interface, No self-reading | Add: "It also excludes your own mail and drafts, so follow-ups are never read back as replies." |

## (m) The draft's language when there is no reply to answer

APR-2: the draft is in the language of the latest email in the thread, replies in the thread of the latest email from a person, and with no such email has an empty To field. With no person's email there is no thread to take the language from.

**Recommendation:** with no person's email, use the language of the latest job-search email from that company of any sender (often the ATS receipt); with none at all, English. The draft then starts a new thread with the subject "Follow-up: <role>" in that language. The Follow-ups page says the To field is empty.

**Alternative:** Turkish as the default, or one configured default language.

| PRD section | Change |
|---|---|
| APR-2 | Add: "Without an email from a person, the language is that of the latest email from the company, or English if there is none, and the draft starts a new thread" |
| §18 OQ-35 | → Resolved for drafts |

Design doc: no change.

## (n) New dependency

| Group | Package | Where it runs | Why |
|---|---|---|---|
| `approval` (new) | `aws-durable-execution-sdk-python` 2.0.1 (AWS, Python ≥ 3.11, needs only boto3) | The approval workflow's ZIP, built by uv for arm64 like the Runtime's; the Mac for tests | The durable-execution SDK. AWS's runtimes include it, but AWS recommends bundling it so a runtime update can't change replay behaviour |
| dev | `aws-durable-execution-sdk-python-testing` 1.2.1 | The Mac and GitHub's runner, tests only | Runs the workflow locally with replay, waits and callbacks |

Not added: Chroma ((i)), the AgentCore Memory SDK client (boto3 has the calls), any Gmail library (the standard library builds the MIME message).

**Alternative:** use the copy in the Lambda runtime and skip the bundle. No install, but the SDK version can change under a waiting execution.

| PRD section | Change |
|---|---|
| §16a | Add a row for the durable-execution SDK and its test runner; update the durable-functions row as in (a) |

## What the code change will be (after acceptance)

The steps in `Week 4 plan.md`: query and tracker groundwork (1), the two Gmail tools (2), the stale-check path and drafting (3), the approval workflow (4), the consumer's checks and decisions and the schedule (5), Memory (6), the Cedar policy engine (7), the Follow-ups page (8), the deploy in stages (9), and the exit checks (10).

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Durable function in the AgentCore stack, started async by the consumer; OQ-27 resolved | Accept | 2026-10-20 | |
| (b) One named wildcard for durable-execution ARNs | Accept | 2026-10-20 | |
| (c) Daily check through the queue in both modes; 5 drafts per check, oldest first | Accept | 2026-10-20 | |
| (d) A decision counts only from my IAM session (SQS SenderId) | Accept | 2026-10-20 | |
| (e) `search_gmail` (`company`) and `create_gmail_draft` (`approval_id` only) | Accept | 2026-10-20 | |
| (f) `notify_owner` `drafts_ready` with `count` | Accept | 2026-10-20 | |
| (g) Follow-up state as bookkeeping on the row | Accept | 2026-10-20 | |
| (h) Memory: preference and per-company summary strategies, built-in models | Accept | 2026-10-20 | |
| (i) Local memory in SQLite | Accept | 2026-10-20 | |
| (j) Cedar policy engine in ENFORCE in week 4, permits only | Accept | 2026-10-20 | |
| (k) A one-off Runtime probe for exit 5 | Accept | 2026-10-20 | |
| (l) The query excludes my own mail and drafts | Accept | 2026-10-20 | |
| (m) Draft language without a reply: company's latest email, else English | Accept | 2026-10-20 | |
| (n) Dependency: the durable-execution SDK and its test runner | Accept | 2026-10-20 | |
