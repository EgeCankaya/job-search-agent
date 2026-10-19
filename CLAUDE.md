# CLAUDE.md

A personal Job Search Inbox Agent. It watches Gmail for job-search email, keeps
an applications tracker current, emails the owner (through SNS) only for
interview invites, assessments and offers, and saves follow-ups as Gmail drafts
only after the owner clicks Approve in a local Mac app. **It never sends or
forwards email from Gmail.** The owner is the sole builder and sole user. The
repo is **public**.

## Source of truth and order of authority (PRD §13)

1. **Design doc** owns architecture: services, models, modes, data flow. The
   live version is the Claude Doc https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9
   (revision 106). `Job search inbox agent design.pdf` is still the export of revision 90;
   the owner re-exports it.
2. **`Job search inbox agent PRD.md`** (v0.4.3) owns requirements and targets:
   FRs §9, NFRs §10, metrics §11, CI gates §12, milestones §14, dependencies §16a.
3. **The code follows both.** Never add tools, tool inputs, endpoints or
   capabilities beyond PRD §9, or architecture beyond the design doc.
4. **Changes to the design doc go through an OQ-style item** that the owner
   accepts or rejects one by one. Examples: `Design doc edits for OQ-32.md`,
   `Design doc edits for OQ-39.md`, `Design doc edits for OQ-40.md`,
   `Design doc edits for OQ-41.md`, `Design doc edits for OQ-42.md` and
   `Design doc edits for OQ-43.md`, all accepted and applied to the PRD and
   the design doc.

**If the PRD and the code would disagree, stop and ask the owner. Don't invent
scope.**

`Job search inbox agent PRD review.md` is a private working note. It is
gitignored and must stay out of the repo.

## Working rules from the owner

- **Ask before installing anything** (Homebrew, npm, uv packages beyond the project).
- **Never deploy** and never create accounts or enter credentials. The owner does these.
- **Never commit secrets, emails, phone numbers or personal data.** Tests use fictional values (`example.com`, `90555…`).
- Commit and push only on the owner's verbal confirmation in chat (for example "yes, commit" or "push it"). A yes covers the commit or push it answers, not later ones. Commit messages end with the co-author line.
- Stay inside the current milestone. Week 2+ items (Gmail, Bedrock, LangGraph, AgentCore resources, the Mac app, GitHub Actions) wait for their week.
- Research notes go to `/Users/egemencankaya/Vault/OpenCode Research/` (from `~/AGENTS.md`).

## Stack and layout

AWS CDK in Python, region **eu-north-1 (Stockholm)**, Python 3.13, managed with **uv**.
The layout below is the M1 code after the OQ-40 change: no inbound endpoint,
email alerts through SNS.

```
app.py                       CDK entry point (calls infra.app.build)
import_applications.py       TRK-3 import, run by the owner (wraps src/inbox_agent)
infra/config.py              settings: tag, region, AGENT_MODE, alert email
infra/app.py                 builds the stacks for the configured mode
infra/always_free_stack.py   tables, queues, alert topic, budgets, poll Lambda + schedule
infra/agentcore_stack.py     Runtime (CfnRuntime), Gateway + 4 Lambda targets, queue consumer (cloud only)
infra/runtime_bundle.py      the Runtime ZIP: uv installs the agent group for arm64 at synth, no Docker
src/budgets/budgets_resource.py   deploy-time custom resource that creates the budgets
src/inbox_agent/             runtime code (packaged into the poll Lambda):
  settings.py                ATS domains, subject keywords, alert sender, alias list
  tracker.py, matching.py    tracker table; TRK-1 normalization and matching
  import_applications.py     TRK-3 CSV import
  query.py                   ING-1 query builder, local matcher, M8 coverage hook
  gmail.py                   GmailClient interface (search, fetch); real client via AgentCore Identity (ING-4)
  messages.py                headers, and a fetched message's text parts and calendar invite
  processed_log.py, poll.py  ING-2 log, alert claim and handled marks, last-poll time; the poll Lambda
  trim.py, classify.py       CLS-3 trimming and prompt; CLS-1 classes and precedence, CLS-2 extraction
  reader.py                  the temporary week 2 queue reader Lambda (OQ-41 e)
  tracker_update.py, alerts.py  TRK-1 row update; ALR-1 alert text
  classes.py, model.py       the 6 classes; Model interface, Bedrock Converse adapter, chat_model() per AGENT_MODE
  mode.py, memory.py         AGENT_MODE for the runtime; the Memory interface (no-op until week 4)
  tools.py                   the week 3 tool specs, fixed input schemas and validation (OQ-42 d)
  tool_handlers.py           read_email, upsert_application, list_applications, notify_owner; InProcessTools
  tool_lambda.py             one tool per Lambda behind the Gateway (TOOL_NAME pins which)
  gateway_tools.py           the tools through AgentCore Gateway: MCP over HTTPS, SigV4-signed (OQ-42 e)
  batch_path.py, chat_path.py  the LangGraph graphs: fixed batch path (OQ-42 b); chat with list_applications only
  runtime.py                 the Runtime entry point (new_email, chat); local worker on 127.0.0.1:8080
  consumer.py                the queue consumer Lambda: one InvokeAgentRuntime per new email (OQ-42 a)
  dashboard.py, app_backend.py  DASH-1 numbers; the Mac app's AWS calls (stack discovery, scans, Ask)
  local_mcp_server.py, stdio_tools.py  MacBook mode's tools: MCP over stdio (no port), and the worker's client
spikes/                      one-off checks run by the owner (ING-4 AgentCore Identity spike)
mac_app/                     the Streamlit Mac app: app.py (navigation), dashboard_page.py, ask_page.py, common.py
.streamlit/config.toml       the app binds 127.0.0.1:8501 and sends no usage stats (CHT-2)
.github/workflows/           check.yml (tests + synth, GitHub runners, PRs) and deploy.yml (main only, deploy on the Mac's self-hosted runner)
tests/fakes.py               in-memory DynamoDB table, SQS queue and SNS topic
tests/unit/                  handler, import, query, poll, reader, trim and classifier tests
tests/infra/                 CDK assertion tests
```

### Commands

```bash
uv sync                # dev group only; add --all-groups for agent, local and app
uv run pytest
cdk synth -c alert_email=alerts@example.com
cdk synth -c alert_email=alerts@example.com -c agent_mode=local
uv run python import_applications.py data/applications.csv --table <ApplicationsTableName>
uv run streamlit run mac_app/app.py              # the Mac app, from the repo root
AGENT_MODE=local PYTHONPATH=src uv run python -m inbox_agent.runtime   # the local worker on 127.0.0.1:8080
```

The import uses boto3 from the dev group and your `aws login` session.

Set `JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1` to silence the jsii warning
about Node 26 (it works; Node 24 LTS is the supported line).

### Settings

| Context | Env var | Notes |
|---|---|---|
| `alert_email` | `ALERT_EMAIL` | Address for the budget alerts and the SNS alert topic's one subscription. Required. Never commit it |
| `agent_mode` | `AGENT_MODE` | `cloud` (default) or `local`. `local` leaves out the AgentCore stack (MODE-1, MODE-2) and warns that the MacBook-mode local worker isn't built yet |

`cdk.out/` and `cdk.context.json` are gitignored, because they can hold the
account ID and the alert email.

## What M1 built (week 1)

**`InboxAgentAlwaysFree`**
- DynamoDB, on-demand billing:
  - applications tracker: `company_key` + `role_key` (TRK-1 matching), no TTL, `RemovalPolicy.RETAIN`;
  - processed-email log: `message_id`, TTL on `expires_at`;
  - pending drafts: `approval_id`, TTL on `expires_at`.
  
  TTL values (30 days, NFR-PRIV-4) are set by application code later. Table keys are provisional until data lands in week 2.
- SQS agent queue with 14-day retention, plus a dead-letter queue (`maxReceiveCount` 3, 14 days). Both SSE-SQS and HTTPS-only. The DLQ alarm is week 3.
- SNS alert topic (ALR-3) with exactly one email subscription, to the `alert_email` address. `notify_owner` will publish here in week 2; it takes no recipient.
  - Topic policy (owner's pick, 2026-10-17): Allow `sns:Publish` to this account's principals only (`arn:aws:iam::<account>:root`, from `AWS::AccountId`), so the owner's `aws login` session can send the week 1 test alert. It also denies `sns:Publish` from other accounts (`aws:PrincipalAccount`) and without TLS. **Narrowed in week 2 (OQ-41 e, deployed 2026-10-18):** the Allow names only the queue reader's role, and `DenyPublishFromOtherPrincipals` (`ArnNotEquals aws:PrincipalArn`) replaces the other-accounts deny, so the owner's session can no longer publish. Week 3 moves both to the agent's role.
- No inbound HTTP endpoint (CHT-2): no API Gateway API and no Lambda function URL in any stack. The OQ-40 change removed the M1 WhatsApp webhook, its HTTP API and the five Meta and owner SSM parameter names.
- 3 AWS Budgets ($1, $5, $10, monthly, email), with `IncludeCredit: false` (see the Free plan section), created by the custom resource described under Gotchas.

**`InboxAgentAgentCore`**: empty, deployed only in cloud mode.

**Guardrails, all tested:**
- `project=inbox-agent` tag on everything;
- 7-day retention on every log group;
- no EC2/VPC, ELB, OpenSearch Serverless or SES resources;
- no API Gateway API or Lambda function URL, in either mode;
- every role is hand-built: no managed policies, no wildcards (budgets, poll, poll schedule, queue reader).
- resource policies use `Principal: "*"` only on a Deny with a condition (the topic policy), never on an Allow.

### Gotchas learned

- **`AWS::Budgets::Budget` doesn't exist in eu-north-1.** CDK's built-in validator fails the synth once credentials are present. The budgets are made by a deploy-time custom resource instead: `src/budgets/budgets_resource.py`, `Custom::InboxAgentBudgets`. It calls the global Budgets API, replaces the budgets on Create and Update, and deletes them on Delete. Its role has only `budgets:ModifyBudget` and `budgets:TagResource` on the 3 budget ARNs. The `project` tag is passed as a property, because `Tags.of()` can't reach API-made budgets.
- **`cdk synth` only validates against the Region once the CLI is signed in.** Always run it after changes, not just pytest.
- **CloudFormation can't create SecureString parameters.** A secret added later is only referenced by name in `infra/config.py`; the owner sets the values with `aws ssm put-parameter --type SecureString`.
- **Use an explicit `logs.LogGroup` passed to the Function, not `log_retention`.** `log_retention` adds a custom-resource Lambda with its own log group. A test checks that every function has `LoggingConfig.LogGroup`.
- **The `aws/ssm` managed KMS key allows decryption via SSM** for principals in the account, so no `kms:Decrypt` grant is needed.
- **`cdk.json` carries the recommended feature flags** from `cdk init`.
- **An AWS service publishing to the alert topic needs its own Allow and an exception in the Deny.** A service call carries no `aws:PrincipalArn`, so `DenyPublishFromOtherPrincipals` denies it. Week 3 does this for the dead-letter alarm only: an Allow for `cloudwatch.amazonaws.com` with `aws:SourceArn` = the alarm's ARN and `aws:SourceAccount`, and the Deny also requires `StringNotEquals aws:SourceArn` (the alarm). Proven only by the live DLQ exit check.

## Week 2 so far (2026-10-18)

Built behind interfaces with fakes, so Gmail and Nova plug in later:
- **TRK-3 import** and the shared **TRK-1 matching** module. Imported rows use the applied date as `last_contact`, so the stale check can see them.
- **ING-1 query builder**: four OR'd parts, `after:` (last successful poll - 1 h) as epoch seconds, and `-from:no-reply@sns.amazonaws.com`. Tracker domains and addresses must pass strict checks, or they are skipped, so a stored value can't widen the query. `query_coverage` is the M8 hook.
- **ING-2/ING-3 poll Lambda** (`inbox_agent.poll.handler`). The processed-email log has a 30-day TTL. The last-poll time is the `#poll-state` item in the same table. A message is queued before it is logged, so it's never lost. Queue messages carry IDs and times only. The schedule is `rate(30 minutes)` in the `inbox-agent` schedule group, and is **ENABLED** since the owner's go on 2026-10-18. It exists in both modes, but only cloud mode can enable it (OQ-41 c).
- **CLS-1/CLS-3 classifier step**: trimming to 2,000 characters, a quoted-data prompt that the email can't break out of, precedence applied in code, and a yes/no question. `CLASSIFIER_MODEL_ID` has no default.
- **OQ-41, accepted 2026-10-18 (all items, (a) to (f)), commit `387fe3d`, deployed 2026-10-18:**
  - (a) 20 inflected Turkish subject keywords and (b) hackerrank.com, codility.com, testgorilla.com, codesignal.com in `settings.py`.
  - (c) The poll schedule is ENABLED only in cloud mode; in MacBook mode the Mac worker polls.
  - (d) The alias list is the reserved tracker item `#config`/`aliases`; `rows()` skips it. `import_applications --aliases data/aliases.csv` replaces it; without `--aliases` the import uses the stored list. `settings.COMPANY_ALIASES` stays empty.
  - (e) **Queue reader** (`inbox_agent.reader.handler`): skips non-`new_email` messages; fetches the message (`format=raw`) with the poller's read grant; trims; one Nova Micro call returns classes, the yes/no answer, company, role and date (ISO or null, never guessed); a calendar invite's start wins; TRK-1 update unless the matched row already lists the message ID; `claim_alert` (conditional put on the log item) before publishing, released if the publish fails; `mark_handled` last. Partial batch failures, batch 5, max concurrency 2, queue visibility 6 min (6 x the 60 s timeout). Its event source mapping is **enabled** (`QUEUE_READER_ON`, owner's go 2026-10-18), and only ever in cloud mode.
  - (f) `CLASSIFIER_MODEL_ID=eu.amazon.nova-micro-v1:0` lives in `infra/config.py`. Only the reader's role has `bedrock:InvokeModel`: on the profile ARN, plus `amazon.nova-micro-v1:0` in eu-central-1, eu-north-1, eu-west-1 and eu-west-3 with a `bedrock:InferenceProfileArn` condition.
  - A read-only `cdk diff` (2026-10-18) showed no replacements: 5 new reader resources, plus in-place updates to the queue, the topic policy, and the poll role and function.
- **`boto3[crt]` is a dev dependency** (owner approved 2026-10-18), for the import script on the Mac. The `crt` extra is required: boto3 can't read `aws login` credentials without it (`MissingDependencyException`). The Lambda runtime brings its own boto3, and tests use fakes, never AWS.

## Week 3 (started 2026-10-19)

- **Plan:** `Week 3 plan.md` (exit checks, 10 steps, what the owner does by hand).
- **OQ-42, accepted 2026-10-18 (all items, (a) to (n)), applied 2026-10-19** to PRD v0.4.2 and design-doc revision 105:
  - (a) a cloud-only queue consumer Lambda calls `InvokeAgentRuntime` per message (it is APR-3's consumer; OQ-36's path is decided); (b) the batch path is a fixed sequence, the model only classifies, and the processed-email log is graph bookkeeping, not a tool;
  - (c) one Lambda and role per tool, in the always-free stack; week 3 builds `read_email`, `upsert_application`, `list_applications`, `notify_owner`; (d) the tool inputs are fixed in PRD §9 conventions (`notify_owner` has no free-text input);
  - (e) IAM (SigV4) at the Gateway and the Runtime; (f) only the `notify_owner` role publishes to the topic; (g) the DLQ alarm gets an exception scoped to its own ARN;
  - (h) the chat path binds only `list_applications`; (i) the AgentCore constructs in aws-cdk-lib 2.273 with hand-built roles, Runtime ZIP built by uv for arm64, no Docker; (j) local mode uses `qwen3.8:27b` (the owner's pick 2026-10-19, replacing `qwen3:32b`), a stdio MCP server, the worker on 127.0.0.1:8080, and the week 3 local check covers the chat path only;
  - (k) timing as log fields now, OpenTelemetry in week 6; (l) superseded by OQ-43; (m) pytest + synth gate the deploy until Tier 1.
- **OQ-43, accepted and applied 2026-10-19** (PRD v0.4.3, design-doc revision 106): the AWS-managed organization's SCP denies every IAM OIDC provider action (`iam:CreateOpenIDConnectProvider` simulates as `explicitDeny`), so GitHub OIDC is impossible in this account. OPS-1 deploys on a **self-hosted runner on the Mac** with the owner's `aws login` session, only for main, with no AWS credential in GitHub. The runner moves from week 6 to week 3.
- **Dependencies (approved 2026-10-18, added 2026-10-19):** groups `agent` (langgraph, langchain-aws, bedrock-agentcore, mcp, langchain-mcp-adapters), `local` (langchain-ollama), `app` (streamlit). The owner installs Ollama (Homebrew, 0.40.6) and pulls `qwen3.8:27b`.
- **Steps 1 and 2 built (2026-10-19), not deployed:** the mode, model (Bedrock or Ollama through LangChain) and memory interfaces; the 4 tool handlers with strict schemas, and the per-tool Lambda wrapper. `tracker_update.update_for_status`/`manual_update` and `alerts.build_alert` serve the tools; the reader still uses the old entry points. A live local check classified a fictional Turkish invite correctly with `qwen3.8:27b` (14 s).
- **Step 3 built (2026-10-19), not deployed:** the batch and chat graphs, the Gateway MCP client and the entry point. The batch path is cloud-only until the Mac worker; in local mode the chat path runs the tool code in-process until the local MCP server (step 7). A live local run on a fictional seeded tracker answered both US-04 questions (and the Turkish one) correctly in 8.5 to 9.4 s. The Runtime ZIP needs a top-level shim to start `inbox_agent.runtime` (step 4).
- **Step 4 built (2026-10-19), not deployed:** the 4 tool Lambdas (always-free stack, a role each), the Gateway (IAM auth, no semantic search, 4 Lambda targets), the Runtime (raw `CfnRuntime`, hand-built role, its 7-day log group made in CDK), and the consumer (mapping off: `CONSUMER_ON` in `infra/agentcore_stack.py`). Queue visibility is now 18 min (6 x the consumer's 180 s). A read-only `cdk diff` (2026-10-19) shows no replacements: 16 new resources in the free stack, 17 in the AgentCore stack, in-place updates to the queue and the poll and reader functions.
- **Step 5 built (2026-10-19), not deployed:** the dead-letter alarm (`ApproximateNumberOfMessagesVisible` >= 1, 5-minute maximum, alarm action only) on the alert topic, and the topic policy's alarm exception. One switch, `AGENT_CUTOVER` in `infra/config.py` (False), flips the cutover in one deploy: reader mapping off, consumer mapping on, topic publisher from the reader's role to the `notify_owner` role. A test keeps exactly one queue reader on. A read-only `cdk diff` of the free stack shows the new alarm and in-place updates (topic policy, queue, poll and reader functions), no replacements.
- **Stage 1 deployed by the owner (2026-10-19, `cdk deploy --all`, 251 s), steps 4 and 5 live, `AGENT_CUTOVER` still False:** both stacks `UPDATE_COMPLETE`. Checked the same day:
  - Runtime `inbox_agent-<id>` READY (version 1); its workload identity is named after the Runtime ID, as the policy assumes. Gateway READY, `AWS_IAM`, no search type, 4 targets READY.
  - Signed MCP `list_tools` returns exactly the 4 tools (`<target>___<tool>` names) with the PRD inputs.
  - A count-only chat question through `InvokeAgentRuntime`: 9.4 s cold, 6.8 s warm (NFR-LAT-3 target 15 s). It said 2 rows, which is right: the live reader created a second row on 2026-10-19.
  - The batch path given an already-handled message ID returned `duplicate: true` in 5.0 s, and the log entry was unchanged.
  - The log group `/aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT` exists with 7 days, and the Runtime writes there: `chat_done` and `batch_done` lines only, no question text, no errors. No log group in the account lacks retention.
  - Not yet exercised live: the batch path through the Gateway tools (read_email, upsert, notify_owner), which runs at the cutover; the DLQ alarm email.
- **Step 6 built (2026-10-19), not committed:** the Mac app's Dashboard and Ask pages (`mac_app/`), backed by `app_backend.py` (finds the tables and the Runtime from the deployed stacks, so no name is written down) and `dashboard.py`. Alert claims now record their kind (`alert_kind`: urgent or possible) for the weekly counts; claims from before that count as "unrecorded". Tests render both pages with Streamlit's AppTest and a fake backend, and check every Dashboard number against a direct count on a seeded table. Live check: the app answers on 127.0.0.1:8501 and a connection to the Mac's LAN address is refused (week 3 exit 5); the Dashboard loaded from the real tables.
- **Cutover live (2026-10-19, commit `010fec2`, run 39266847321):** `AGENT_CUTOVER = True`. After the deploy: reader mapping Disabled, consumer mapping Enabled, topic policy allows only `NotifyOwnerToolRole` plus the dead-letter alarm; both queues empty. Before it, a read-only pre-check: `read_email` through the Gateway returned a trimmed email, and `notify_owner` was refused by the topic (SNS `AuthorizationError`).
- **Week 3 exit checks on 2026-10-19:**
  - **8 (cutover):** a fictional Turkish invite gave exactly 1 alert: poll 14:22 UTC found and queued it; the consumer invoked the Runtime; the batch path ran `read_email`, Nova Micro, `upsert_application` (new row: interviewing, key date 2026-11-05 14:00) and `notify_owner` in 12.1 s. Receipt → alert 3.4 min (NFR-LAT-2), detection → alert 0.3 min (NFR-LAT-1). The 14:52 and 15:22 polls re-saw it (found 1, skipped 1, enqueued 0).
  - **7 (dead letters):** a `new_email` message with a made-up ID failed at 14:23, 14:41 and 14:59 UTC (Gmail HTTP 400, logged by type only), moved to the DLQ, and the alarm went to ALARM at 15:20 UTC; its email arrived. So the topic policy's CloudWatch exception works.
  - **3 (pipeline deploy)** and **5 (loopback only)** passed earlier; **2 (local graph)** passed for the chat path.
  - Cleanup (owner's go, 2026-10-19): the test row and the dead-letter message were deleted; the tracker holds its 2 real rows. The invite's processed-log entry stays until its TTL, so it is never queued again.
  - Still open: **1** (both US-04 questions on the Ask page, cloud), **4** (day-8 read token, around 2026-10-26), **6** passed on a seeded table in tests.
- **First pipeline deploy (2026-10-19, run 39265425574):** the owner registered the self-hosted runner (`~/actions-runner`, launchd agent, labels `self-hosted, macOS, ARM64`, PATH with `/opt/homebrew/bin`) and pushed `0350f52`. The check passed on GitHub's runner and the deploy job updated both stacks from the Mac in 2 min 24 s (`UPDATE_COMPLETE`). The 759-line public log holds neither the account ID nor the alert address. **Week 3 exit 3 met.** `origin/main` is at `0350f52`.
- **Step 8 built (2026-10-19):** `check.yml` (pytest and `cdk synth` in both modes on `ubuntu-24.04`, no secrets; on pull requests and as the deploy's first job) and `deploy.yml` (push to main and manual runs only; the deploy job runs on `[self-hosted, macOS]` with the owner's `aws login` session, masks the account ID and the alert address, and deploys the two app stacks with `--require-approval never`). Actions are pinned to commit SHAs (checkout v7.0.1, setup-uv v10.3.0, setup-node v7.1.0); aws-cdk 2.1145.0. Tests forbid a `pull_request` trigger, secrets and `id-token` in the deploy workflow.
  - **The owner still has to:** register the self-hosted runner on the Mac (repo Settings → Actions → Runners → New self-hosted runner, macOS ARM64), install it as a launchd service under the owner's user so it sees `~/.aws`, make sure its PATH includes `/opt/homebrew/bin` (uv, aws, cdk), then push. A deploy needs a fresh `aws login`; if the session has expired, the run fails with a hint and can be re-run by hand.
- **Step 7 built (2026-10-19), not committed:** the local MCP server (`local_mcp_server.py`, the tracker tools over stdio, no port; cedarpy in week 5) and the worker's client (`stdio_tools.py`, one long-lived session on a background event loop). In local mode the worker starts the server as a child process, with the tracker table's name (from `TRACKER_TABLE_NAME` or the deployed stack), `PYTHONPATH`, and MCP's safe environment only. Live check: the worker answered count-only questions from the real tracker through Ollama (14 s first, 5 s after); only the worker listens (127.0.0.1:8080), the child has no socket, and the LAN address is refused (week 3 exit 2, chat path).
- **Gotchas from step 7:** the project isn't installed as a package, so the worker needs `PYTHONPATH=src` and passes the same to its child. The MCP SDK's own input check would quote bad values in its error, so the server turns it off (`validate_input=False`) and relies on `tools.validate`.
- **Gotchas from step 4:**
  - The AgentCore `Runtime` construct adds wildcard X-Ray, CloudWatch and S3 grants to the role you pass, so the Runtime is the raw `CfnRuntime`. The `Gateway` construct is fine, but its default semantic search adds a search tool; the stack deletes `SearchType`.
  - numpy has no `aarch64-manylinux2014` wheel: the bundle targets `manylinux_2_28`. Check at the first deploy that the Runtime starts.
  - The CDK forces colour, and `uv export` to stdout then writes escape codes into the requirements; the bundler has uv write the file with `--color never`.
  - Tests set the `aws:cdk:bundling-stacks: []` context, so they never bundle. A real `cdk synth` bundles (about 155 MB unzipped).
  - The Runtime's log group name `/aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT` is AWS's convention, not checked yet: after the first deploy, confirm the Runtime writes there.
- **Nova Lite profile (looked up 2026-10-19):** `eu.amazon.nova-lite-v1:0` is `ACTIVE` and routes to `amazon.nova-lite-v1:0` in the same 4 Regions as Micro (eu-central-1, eu-north-1, eu-west-1, eu-west-3). The Runtime role names both profiles and these 8 model ARNs.

## AWS Free plan (decided 2026-10-17)

The owner is on the **AWS Free plan and will stay on it**. Research findings:
- AWS's current docs list Bedrock and AgentCore as available on the Free plan.
- **No cross-Region features on the Free plan**, so the design's `eu.` Nova inference profile may be blocked. Measurements leave two routes (OQ-39 (b)): route 1, the `eu.` profile from Stockholm (Nova Micro + Nova Lite, design unchanged); route 2, Nova Lite in-Region in Stockholm for everything. Frankfurt adds nothing, because it has no in-Region Nova Micro either. **Route 1 was chosen on 2026-10-18** (OQ-41 (f)): the quotas were raised, and one call to `eu.amazon.nova-micro-v1:0` from eu-north-1 succeeded, so the Free plan doesn't block the `eu.` profile.
- **The account closes 6 months after sign-up, or when the credits run out**, with no extension. Data can be recovered only by upgrading within 90 days. You can't downgrade from Paid back to Free.
- Nothing is ever charged, so no KDV.

### The account: AWS's new sign-up experience

- **Home Region is fixed at eu-north-1.** It was picked from the contact country (Turkey) and can't be changed. Resources can't be created in other Regions. Unlocking Regions needs the Paid plan plus "activate advanced features", which is irreversible, so it's ruled out.
- **No root user and no console IAM users.** A Builder ID owns the account; IAM users work only programmatically. Account settings and MFA live in AWS Settings (settings.aws.com). The account is in an AWS-managed organization automatically.
- **CLI access:** `aws login` (CLI region set to `eu-north-1`). It yields an assumed `AccountFullAccessRole` session. A CLI region of `eu-central-1` made the login fail.
- **Never write the account ID into the repo.** CDK reads it from `CDK_DEFAULT_ACCOUNT`.

**OQ-39, accepted 2026-10-17 (all items, (a) to (f)):**
- **Code:** the budgets exclude credits so they track credit burn, and `REGION` is `eu-north-1`.
- **Docs:** applied to PRD v0.4.0 and design-doc revision 90: Free plan, both model routes, the 6-month horizon (by month 5: export the tracker, then upgrade or end), budgets that count usage before credits, no KDV on the Free plan, and the home Region. The PRD's §16a lists the Nova quotas as a blocker.

## Interface (accepted 2026-10-17, OQ-40)

The owner doesn't want a Meta account. OQ-40 (all items, (a) to (e)) replaced WhatsApp with two things, applied to PRD v0.4.0 and design-doc revision 90:
- **Email alerts** through an SNS topic with one confirmed subscription (the owner's address, supplied at deploy time, never committed). The tool `send_whatsapp` is now `notify_owner`, with no recipient input. The Gmail query excludes the alert sender (`-from:no-reply@sns.amazonaws.com`).
- **A local Python Streamlit app on 127.0.0.1** with three pages: Dashboard (DASH-1, P2), Ask (CHT-1) and Follow-ups (APR-2/3). It uses the owner's `aws login` session; CHT-2 is now owner-only access. The app starts in week 3.

There is no public endpoint. **The code now matches:** the WhatsApp webhook, its HTTP API and the Meta SSM parameter names are removed, and the SNS alert topic is in (see What M1 built). The owner redeployed on 2026-10-18, so the deployed stack matches too: the webhook is gone, and a test alert email arrived (the new week 1 exit).

## Current state (2026-10-18)

- **Commit `e723332` on `main`:** the M1 code plus the PRD, the design PDF and the OQ-32 edits.
- **Commit `73a4f03` on `main`:** the move to eu-north-1, the budgets custom resource and its tests, the README notes, the OQ-39 and OQ-40 proposals.
- **Commit `4796d6a` on `main`:** PRD v0.4.0, the design PDF from revision 90, the decisions in both OQ files, and this file.
- **Commit `6b66431` on `main`:** the OQ-40 code change (webhook removed, SNS alert topic added, tests, README, this file).
- **GitHub:** private repo `EgeCankaya/job-search-agent`, pushed 2026-10-18 (`origin/main`).
- **The owner's CLI login works** (`aws login`, eu-north-1).
- **Deployed 2026-10-17 by the owner:**
  - `cdk bootstrap` and `cdk deploy --all` in eu-north-1. Both stacks are `CREATE_COMPLETE`.
  - The 3 budgets exist with `IncludeCredit: false`, and 15 resources carry the project tag.
- **Redeployed 2026-10-18 by the owner** (`cdk deploy --all` with the real `alert_email`):
  - The webhook and its HTTP API are gone (`aws apigatewayv2 get-apis` returns `[]`).
  - The alert topic has one confirmed email subscription, and the 3 budgets email the owner.
  - A leftover unconfirmed subscription to `you@example.com`, from a first deploy with the placeholder, can't be deleted; SNS removes it after about 3 days.
  - **Week 1 exit met:** a test alert published with `aws sns publish` arrived by email.
- **Nova quota requests filed 2026-10-17:**
  - Service Quotas `L-DC7FF66C` and `L-7C42E72A` at 500,000, both pending.
  - A support case (category "General") for the tokens-per-day and per-minute quotas.
- **Measured 2026-10-17** (details in OQ-39):
  - Nova Micro is profile-only in both eu-north-1 and eu-central-1; Nova Lite runs on demand in eu-north-1.
  - The `eu.` profiles are `ACTIVE`, but calls get throttled because **every Nova inference quota is 0**.
  - AgentCore's control plane (Runtime, Gateway, Memory, Identity, Policy, Evaluations) responds in eu-north-1.
- **Nova quotas raised (seen 2026-10-18):** both requests `CASE_CLOSED`; cross-Region Micro and Lite at 400k tokens and 20 requests per minute, in-Region Lite at 200k and 10, per day 207M (Micro) and 564M (Lite). The route 1 call succeeded (7 in, 2 out tokens), so **route 1 is used**, recorded in PRD v0.4.1 (CLS-1, TOOL-3, §16a, OQ-39). No blocker is left.
- **ING-4 spike passed (2026-10-18)**, with `spikes/agentcore_identity_gmail.py` (run it in a real terminal; it waits for a browser sign-in):
  - Google side, done by the owner: project created, Gmail API enabled, consent screen "In production" (unverified) with `gmail.readonly` and `gmail.compose`, a web client whose redirect URI is the AgentCore callback URL. Homepage and privacy policy point at the repo and `PRIVACY.md`.
  - AWS side, made by the owner with the CLI (not in CDK yet): credential provider `inbox-agent-google` (its client secret sits in a Secrets Manager secret AgentCore manages), workload identities `inbox-agent-poller` and `inbox-agent-drafts`, both allowing the return URL `http://localhost:9090/oauth2/callback`. The fixed user ID is `owner`.
  - Results: a read token ran the ING-1 query, Gmail refused a draft from it (HTTP 403), and after both consents the read token came from the vault with no sign-in. Recorded in PRD §16a.
  - Still open: the day-8 refresh check (around 2026-10-26, run the read command again).
- **Real `GmailClient` in the poll Lambda (deployed 2026-10-18):** `AgentCoreGmailClient` gets the read token as `inbox-agent-poller` for user `owner`. It asks Gmail only for IDs and `internalDate` (`fields=id,internalDate`, so no snippet). It stops above 500 matches. The poll role can call `GetWorkloadAccessTokenForUserId` and `GetResourceOauth2Token` on the exact directory, workload, vault and provider ARNs, plus `GetSecretValue` on the provider's one secret (`infra/config.py`; recreating the provider changes its name).
  - **Dry run:** invoke the Lambda by hand with `{"dry_run": true}`. It searches but queues nothing, logs nothing as processed, and leaves the last-poll time alone. **Passed 2026-10-18** after the deploy: `found 0` (90-minute window), so the AgentCore read token and the Gmail search work.
  - **Tracker:** 1 imported application, no alias item yet (`import_applications` 2026-10-18: `unchanged=1 aliases=0`).
  - **Deploy from Terminal.app, not the inline `!` shell** (it garbles long nested commands and can't answer the IAM prompt): look up `ALERT_EMAIL` from the confirmed subscription, then `ALERT_EMAIL="$ALERT_EMAIL" cdk deploy --all`.
  - **The schedule is ENABLED** (`POLL_SCHEDULE_ON` in `infra/always_free_stack.py`, owner's go 2026-10-18, needs a deploy), and only ever in cloud mode (OQ-41 c). A test keeps it off whenever the reader is off. A live poll marks emails processed and queues them; if nothing reads the queue within its 14-day retention, those emails are lost while the log says they were handled. So turn the reader on first, or together.
- **Live since 2026-10-18 (commits `387fe3d`, `454ff2a`, `c5511ec` on `origin/main`):** the owner deployed with the poll schedule ENABLED and the reader's event source mapping Enabled. The topic still has exactly 1 confirmed subscription.
  - A first deploy attempt failed on a typo (`ALERT_EMAIL="$<address>"` left `@gmail.com`); it rolled back cleanly. `c5511ec` makes the synth reject a partial address.
- **Week 2 exit checks (2026-10-18):**
  - A Turkish test invite (subject matched `mülakatınız`) gave exactly 1 alert, under 30 minutes: poll 18:52:33Z found 1 and queued 1; the reader created 1 row and sent 1 alert. Class, company, role and date were right.
  - The next poll (19:23Z) re-saw the invite in its overlap: `found 1, skipped 1, enqueued 0`, and the reader did nothing. The alert email, inside the same window, was not returned.
  - The test row was deleted afterwards; the tracker holds the 1 imported application.
  - Still waiting: a real reply to an imported application landing on its row, and M8 on the owner's labels.
- **Week 1 items the owner does by hand:**
  - MFA in AWS Settings;
  - a calendar reminder at sign-up + 5 months (OQ-39 (c));
  - the AgentCore test runtime for OQ-1;
  - the time-log baseline for M7.
