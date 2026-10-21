# Week 4 plan: memory and approval

**Date:** 2026-10-20
**Status:** OQ-44 accepted (all fourteen items, 2026-10-20). Steps 0 to 8 are built and pushed (2026-10-20, `9037dd4` to `e34f6c7`, Cedar fixes `b98861a` and `c74e564`); step 9.1 deployed by run 39483632112 and checked (fix `69eac71`, run 39484941844; the Ask page answered); step 9.2 passed on 2026-10-21 (exit 5: the agent's role and my session both denied by Cedar, run 39592428389), probe off again next; next is step 9.3 (exits 1 to 3). Live state: `Project status.md`. Steps 9 and 10 are mine: the runbook at the end has the commands.
**Sources:** PRD v0.4.3 (§9 APR-1 to APR-4, MEM-1, MEM-2, MODE-3, TOOL-1, TOOL-4, ING-1, ING-3; §10; §12; §14 week 4; §16a; §18 OQ-4, OQ-20, OQ-21, OQ-27, OQ-30, OQ-35, OQ-36, OQ-38), the design doc (Claude Doc, revision 106), `Week 3 plan.md`, the week 3 code as deployed (commit `c2302c4`).

## Checked before planning (2026-10-20, read-only, my `aws login` session)

| Check | Result |
|---|---|
| The managed organization's SCP, policy simulator with `aws:RequestedRegion` = eu-north-1 | **Allowed:** `lambda:CreateFunction`, every durable-execution action (Checkpoint, GetState, SendCallbackSuccess and Failure, List, Get, Stop), `bedrock-agentcore:CreateMemory`, `CreateEvent`, `ListEvents`, `ListMemoryRecords`, `RetrieveMemoryRecords`, `BatchCreateMemoryRecords`, `StartMemoryExtractionJob`, `CreatePolicyEngine`, `CreatePolicy`, `InvokeGateway`, `GetResourceOauth2Token`, `scheduler:CreateSchedule`, `sqs:SendMessage`. Control: `iam:CreateOpenIDConnectProvider` is still `explicitDeny`. Without the Region in the context every action simulates as denied, so the SCP pins the home Region, as OQ-39 said |
| Lambda durable functions in Stockholm (OQ-27) | **Available.** AWS's own announcement of 2025-12 lists Europe (Stockholm) among 14 new Regions (the PRD's §16a still says "Frankfurt only"). `ListDurableExecutionsByFunction` answers in eu-north-1 (empty list on the poll function) |
| AgentCore Memory and Policy in eu-north-1 | `list-memories` and `list-policy-engines` answer, both empty |
| Workload identity `inbox-agent-drafts` (ING-4) | Exists, return URL `http://localhost:9090/oauth2/callback`, as the week 2 spike left it |
| aws-cdk-lib 2.273 (installed) | Has `durable_config` on Lambda functions, `Memory` (event expiry 7 to 365 days), `PolicyEngine`, `Policy` and the Gateway's `policy_engine_configuration`. No new CDK package |
| boto3 1.43.114 (installed) | Has the durable-execution calls and every AgentCore Memory data-plane call |
| The ING-1 query | **Finding:** it doesn't exclude my own mail. Week 4's Gmail drafts and the follow-ups I send would match the subject keywords, be polled, and reset `last_contact`. OQ-44 (l) |

Facts from AWS's durable-function docs that shape the design: an SQS event source mapping limits a durable execution to 15 minutes, so a 7-day wait needs an asynchronous start; durable functions must be invoked by version or alias; the permissions for checkpoints and callbacks name `<function ARN>:*`; the execution name makes a start idempotent; a timed-out callback raises `CallbackError`.

## Week 4 exit criteria (PRD §14)

1. A seeded 10-day-old application produces a draft on the Follow-ups page with Approve and Skip, and the drafts-ready email arrives.
2. Approve puts a draft in Gmail and leaves Sent unchanged.
3. Skip creates no draft, and a 1-minute test timeout creates none.
4. The exported memory record count matches the count listed in AgentCore Memory.
5. A draft call made with the agent's identity is denied.

Also due this week: the day-8 read-token check (around 2026-10-26, week 3 exit 4) and week 3 exit 1 (both US-04 questions on the Ask page, cloud). Carried from week 2: a real reply landing on an imported row, and M8 on my labels.

## Shape of the result

```
EventBridge Scheduler (09:00 Europe/Istanbul) ─┐
poll Lambda ───────────────────────────────────┼─► SQS ─► queue consumer Lambda
Mac app: Approve / Skip (my session) ──────────┘            │
                                                            ├─ new_email ───► Runtime batch path (+ company memory event)
                                                            ├─ stale_check ─► Runtime stale-check path ─► drafts
                                                            │                   (search_gmail, read_email, Nova Lite,
                                                            │                    notify_owner "N drafts ready")
                                                            │   └─► start one approval workflow per draft (async,
                                                            │        execution name = approval_id)
                                                            └─ decision ────► pending-draft record + callback
                                                                              (never the Runtime, never a model)

approval workflow (durable function, cloud only)
  register: pending-draft record (awaiting decision), follow-up state
  wait: callback, 7 days ─► Approve: create_gmail_draft via the Gateway, under its own role
                         ─► Skip / timeout: no draft

Gateway (IAM auth, Cedar policy engine in ENFORCE)
  Runtime role:          read_email, search_gmail, upsert_application, list_applications, notify_owner
  approval role:         create_gmail_draft only (with an approval_id)
create_gmail_draft Lambda: approval check (TOOL-4), compose token from inbox-agent-drafts, drafts.create only

AgentCore Memory (30-day events): Ask-page turns ─► preferences;  company events ─► one summary per company
Mac app: Dashboard, Ask (now with short-term memory), Follow-ups (new)
```

- **InboxAgentAlwaysFree** gains the `search_gmail` and `create_gmail_draft` Lambdas and roles, the daily-check schedule (queue target), and the follow-up fields on tracker rows. It keeps the tables, queues and schedules MacBook mode needs.
- **InboxAgentAgentCore** (cloud only) gains the Memory resource, the policy engine and its policies, the approval workflow (durable function, published version) and its role, and the consumer's new permissions. Deleting it in MacBook mode removes pending waits, which §15 already handles (approve or skip every pending draft first).

## Steps

Each step ends with `uv run pytest` green; steps that touch `infra/` also end with `cdk synth` in both modes. Every new role is hand-built, every new log group keeps 7 days, and the guardrail tests cover them; the only new wildcard is the one OQ-44 (b) names.

### 0. Decisions, dependencies and documents
- I accept or reject each OQ-44 item and approve the dependency in OQ-44 (n).
- Then: PRD v0.4.4 and the design doc revision after 106 get the accepted rows; each design-doc edit is read back. The PDF stays the revision 90 export until I re-export it.
- `uv add` only the approved package.

### 1. Query and tracker groundwork (ING-1, TRK-1, APR-4)
- ING-1: the query excludes my own mail (OQ-44 (l)); the M8 and alert-sender tests extend to it.
- Tracker rows get a `follow_up` map: last proposed, last skipped, last expired, last draft, skips in a row (TRK-1's follow-up state, OQ-20). `rows()`, `list_applications` and the Dashboard ignore it, as `list_applications` already returns no follow-up state.
- `upsert_application` resets "skips in a row" when it applies a new message (APR-4: "since its last new email").
- `eligibility.py`: the APR-4 rules as one pure function of a row and today's date, in Europe/Istanbul. Tests: every rule alone and together, the 10-day boundary, a twice-skipped row coming back only after a new email, the cap and order of OQ-44 (c).

### 2. The two Gmail tools (TOOL-1, TOOL-4, ING-4)
Inputs as drafted in OQ-44 (e).
- `search_gmail` (read grant, `inbox-agent-poller`): the newest matches for one tracker company, ANDed with the job-search query; IDs, thread, sender address, receipt time and the `Message-ID` header only, never subject or snippet. Its own Lambda and role, like `read_email`.
- `create_gmail_draft` (compose grant, `inbox-agent-drafts`): input `approval_id` only. It reads the pending-draft record and creates a draft only if the record exists, is approved by my identity (OQ-44 (d)), its stored text hash matches the text, and it isn't used; then it marks it used. Its Gmail client has one method, `users.drafts.create`; anything else raises before a request is built. It builds the MIME message from the record (To, Subject `Re: …`, `In-Reply-To`, `References`, `threadId`).
- The role gets the token calls for the drafts identity on exact ARNs (as the poll role has for the poller's), `GetItem`/`UpdateItem` on the pending-drafts table, nothing else.
- The Gateway gets both targets; its tool list then equals the PRD's six (TOOL-1 acceptance). The Runtime's tool client maps the new prefixed names.
- Tests: missing, unknown, unapproved, reused and text-altered `approval_id` create no draft; a `messages.send` through the client raises without a request; the search query always contains the job-search part; no subject or snippet in `search_gmail` output; the drafts client never sees the read token and the read client never sees the compose token.

### 3. The stale-check path and drafting (APR-1, APR-2, APR-4)
A fixed sequence in the graph, like the batch path (OQ-42 (b)); the model writes the text and nothing else.
- Select eligible rows (step 1's function, over a direct tracker read, OQ-44 (g)), oldest first, capped (OQ-44 (c)).
- For each: `search_gmail` → pick the newest message whose sender isn't noreply, no-reply, donotreply or mailer-daemon → `read_email` on it → Nova Lite writes the follow-up in that email's language (OQ-44 (m) when there is none), with my tone preference from memory (MEM-2) and the company summary as context. The email text goes in as quoted data (CLS-3).
- Checks in code: at most 120 words; names the role and the last contact date (the date is formatted by code in the draft's language and must appear verbatim); one retry, then the row is left for tomorrow.
- `approval_id` = a hash of the company key, role key and the check's date, so a redelivered check proposes nothing twice.
- `notify_owner` sends "You have N follow-up drafts ready" with the count only (OQ-44 (f)).
- Returns the drafts to the consumer: approval_id, company, role, To or none, thread ID, `In-Reply-To`, subject, language, text.
- Tests with the fake model and fake tools: a hostile email body can't change the sequence or the recipient; noreply senders are skipped; no person email gives an empty To and a note; over-long drafts are rejected; the drafts-ready alert carries only the count.

### 4. The approval workflow (APR-3, OQ-20, OQ-27)
- One durable function (Python 3.13, arm64), published as a version on every deploy; the consumer starts it asynchronously on that version with the execution name = `approval_id` (OQ-44 (a)).
- `register` step: put the pending-draft record (state `awaiting_decision`, text, text hash, To, thread, subject, language, company and role keys, callback ID) with a condition that it doesn't exist; set the row's `follow_up.last_proposed`.
- `wait`: one callback, timeout 7 days. A test start can shorten it to 60 seconds; the consumer never forwards that field, so only my direct invoke can (exit check 3).
- On Approve: call `create_gmail_draft` through the Gateway with SigV4 under the workflow's own role, then set state `draft_saved` and `follow_up.last_draft`. On Skip: state `skipped`, skips in a row + 1. On `CallbackError` (timeout): state `expired`. Each resolved record gets `expires_at` = resolved + 30 days (NFR-PRIV-4).
- Its role: checkpoint and state calls on its own `:*` ARN (OQ-44 (b)), `InvokeGateway`, the pending-drafts table, `UpdateItem` on the tracker limited to the `follow_up` attribute (`dynamodb:Attributes`).
- Tests with the SDK's local test runner: Approve → one draft call; Skip and timeout → none; a replay after the draft call doesn't call it twice; two pending at once resolve only their own.

### 5. The consumer takes checks and decisions (ING-3, APR-3)
- `stale_check` → one `InvokeAgentRuntime` (longer read timeout, within the consumer's 180 s), then one asynchronous start per returned draft. A start whose execution name exists is treated as done.
- `decision` → never the Runtime. Accept it only from my identity (OQ-44 (d)): conditional update `awaiting_decision` → `approved` or `skipped`, recording the approved text hash and who decided; then `SendDurableExecutionCallbackSuccess` with the stored callback ID. Redelivery after the update goes straight to the callback; a decision for an expired or resolved record is dropped and logged by ID.
- Its role gains: `lambda:InvokeFunction` on the workflow's version, the callback calls on `:*` (OQ-44 (b)), `GetItem`/`UpdateItem` on the pending-drafts table, `iam:GetRole` on my one role (OQ-44 (d)).
- The daily schedule: EventBridge Scheduler, `cron(0 9 * * ? *)` in Europe/Istanbul, target the agent queue with `{"type": "stale_check"}` in both modes (OQ-44 (c)). Its own role, `sqs:SendMessage` on the queue only. In MacBook mode the message waits for the Mac worker.
- Tests: a decision never reaches the Runtime client; a decision from another sender is dropped; Approve then Skip for the same ID resolves once; an unknown type is still dropped.

### 6. AgentCore Memory (MEM-1, MEM-2, MODE-3)
As drafted in OQ-44 (h).
- One Memory resource, event expiry 30 days, a user-preference strategy over the Ask page's turns and a summary strategy over the batch path's per-company events.
- Chat path: loads the last turns of its session (MEM-1) and my preferences; records each turn. The Ask page keeps one session ID per browser session.
- Batch path: after `upsert_application`, one event in that company's session with the class, status, dates and next step; never email text (NFR-PRIV-3).
- Runtime role: `CreateEvent`, `ListEvents`, `RetrieveMemoryRecords` on the one Memory ARN.
- `export_memory.py` (run by me): pages `ListMemoryRecords` per namespace into a local SQLite file under `data/` (gitignored) and prints both counts (MODE-3, exit 4).
- Local mode (OQ-44 (i)): a SQLite store behind the same interface; it can load the export.
- Tests: two-turn follow-up ("and Copperleaf?") with a scripted model; a stated tone preference reaches the drafting prompt when the store returns it; company events contain no email text; the Memory resource has 30 days.

### 7. Cedar at the Gateway, first part (TOOL-4, OQ-36)
As drafted in OQ-44 (j) and (k).
- A policy engine attached to the Gateway in ENFORCE. Permits: the Runtime role for its 5 tools; the approval role for `create_gmail_draft`, only with `approval_id` in the input. An explicit forbid on `create_gmail_draft` for every other principal. Default deny covers the rest.
- Week 5 adds the never-send forbid (TOOL-2), the recipient forbid (ALR-3), the injection set and cedarpy.
- Tests: the policy text names the right roles and actions; the Gateway is in ENFORCE.

### 8. The Mac app's Follow-ups page (APR-2, APR-3)
- Lists records in `awaiting_decision`: company, role, To (or "no reply-to address found, To is empty"), language, the full text, Approve and Skip. Nothing else is editable.
- Approve or Skip sends `{"type": "decision", "approval_id", "decision", "text_hash"}` to the agent queue with my session. The page never writes DynamoDB; the button disables after one click and shows "sent".
- The queue URL and table come from the deployed stacks, as for the other pages.
- Tests with AppTest and a fake backend: exactly 2 buttons per draft; the message carries the hash of the shown text; an empty list shows a plain note.

### 9. Deploy (I deploy, through main)
1. Push steps 1 to 8 with the daily schedule **disabled** (`STALE_CHECK_ON`, like `CONSUMER_ON`). Check: Memory ACTIVE with 30 days; the Gateway lists 6 tools; the policy engine is ENFORCE; the workflow has a published version; the new log groups keep 7 days.
2. Policy checks (exit 5) as OQ-44 (k) says.
3. Exit checks 1 to 3 with a seeded fictional row (below), started by a manual `stale_check` message.
4. Turn the schedule on (one-line commit).

### 10. Exit checks
- **1 and 2:** seed a fictional row (`example.com` company, last contact 11 days ago) with `import_applications` or a put; send `{"type": "stale_check"}` with `aws sqs send-message`. The page shows one draft (empty To, as no thread exists) and the drafts-ready email arrives. Approve; Gmail Drafts has it, Sent is unchanged (count before and after).
- **3:** a second seeded row, Skip → no draft. A third: start the workflow directly with the 60-second test timeout → state `expired`, no draft.
- **4:** chat a tone preference on the Ask page, wait for extraction, run `export_memory.py`; the counts match and 5 records show full content.
- **5:** as OQ-44 (k).
- Cleanup (my go): delete the seeded rows, the Gmail draft, and the test records.
- Record results in CLAUDE.md.

## What I do by hand this week
- Accept or reject each OQ-44 item and the dependency.
- Push to main (the runner deploys), and turn the daily schedule on.
- Send the test `stale_check` messages, click Approve and Skip, check Gmail Drafts and Sent.
- Start the 60-second timeout test.
- Run `export_memory.py`.
- The day-8 read-token check (around 2026-10-26). The compose token has never been refreshed after consent either: the first Approve after more than an hour proves it.

## Risks for this week
- **The many stale imported rows.** Imported rows carry the applied date as last contact, so the first live check finds most of them at once. The cap in OQ-44 (c) spreads them over days.
- **Memory extraction on the Free plan.** Built-in strategies may route inference within the EU geography; if the Free plan's no-cross-Region rule blocks that, extraction fails silently. The exit 4 check sees it; the fallback is the built-in strategy with an override model (Nova Lite through the `eu.` profile, which is proven).
- **Cedar with IAM principals.** AWS documents `AgentCore::IamEntity` with the caller's ARN; one third-party report says enforcement on IAM gateways arrived later than the docs. The live denial is the test (OQ-36); the fallback is §16's: the in-tool check alone, recorded as a known gap.
- **A policy engine in ENFORCE denies everything not permitted.** A missing permit breaks the batch path. Deploy step 1 turns it on with the schedule off, and the next poll with mail proves the batch path still works.
- **Durable replay.** Code outside steps runs again on every replay; the draft call must sit inside a step so a replay never repeats it. Tests replay on purpose.
- **Old versions in flight.** A deploy publishes a new version; executions waiting since an older one resume on it. Breaking changes to the record or callback shape need both shapes for 7 days.
- **AgentCore charges.** Memory and the policy engine are the first new charges since week 3. Check Billing → Credits the day after the deploy.

## Runbook for steps 9 and 10 (my commands)

Placeholders in angle brackets come from the stack outputs or the console; none of them is written down in the repo. Run from the repo root after `aws login`.

**Deploy (step 9.1).** Push main; the runner deploys. The daily schedule stays off (`STALE_CHECK_ON = False` in `infra/always_free_stack.py`), and the probe stays off (`POLICY_PROBE_ON = False` in `infra/agentcore_stack.py`). Right after it, ask one question on the Ask page: an answer proves the Runtime role passes the policy engine (the chat path calls `list_applications` through the Gateway). If it fails, the batch path fails the same way, so act before the next poll with mail.

**Policy probe (step 9.2, exit 5).**
1. Set `POLICY_PROBE_ON = True`, commit, push, wait for the deploy.
2. `aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn <runtime ARN> --runtime-session-id inbox-agent-policy-probe-2026-10-20xx --payload '{"type": "policy_probe"}' --cli-binary-format raw-in-base64-out probe.json` and read `probe.json`: `refused` should be true, with a detail that names the policy (not "the tool failed", which would mean the tool refused instead).
3. The same call with my own session must be denied too: it is any identity but the workflow's.
4. Set `POLICY_PROBE_ON = False`, commit, push.

**A draft, Approve, Skip (exits 1 to 3).**
1. Seed two fictional rows with a CSV under `data/` (gitignored), last contact 11 days ago, for example `Örnek Test Ltd,Test Engineer,<date>,applied` and `Örnek Deneme AŞ,QA Engineer,<date>,applied`: `uv run python import_applications.py data/week4-test.csv --table <ApplicationsTableName>`.
2. Count Gmail Sent, then send the check: `aws sqs send-message --queue-url <agent queue URL> --message-body '{"type": "stale_check"}'`.
3. Within a minute or two: the drafts-ready email arrives, and the Follow-ups page shows both drafts (To empty, as no thread exists).
4. Approve one: Gmail Drafts has it, and Sent has the same count. Skip the other: no draft.
5. Timeout: take one draft's JSON from `aws dynamodb get-item` (or write one by hand with a fresh 32-hex `approval_id`), save it as `data/timeout-test.json` in the shape `{"draft": {...}, "test_timeout_seconds": 60}`, then `aws lambda invoke --function-name inbox-agent-approval:<latest version> --invocation-type Event --durable-execution-name <its approval_id> --payload fileb://data/timeout-test.json out.json`. After a minute the record says `expired`, and no draft exists.
6. Cleanup: delete the seeded rows, the Gmail draft, the test records.

**Memory (exit 4).** On the Ask page, state a preference ("I prefer short, formal follow-ups"), wait a few minutes for extraction, then `uv run python export_memory.py --show 5`: it prints both counts and five records.

**Turn the daily check on (step 9.4).** `STALE_CHECK_ON = True`, commit, push. The first live check proposes at most 5, oldest first.
