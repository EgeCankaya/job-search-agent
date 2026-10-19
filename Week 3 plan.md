# Week 3 plan: the agent

**Date:** 2026-10-18
**Status:** OQ-42 accepted (2026-10-18). Step 0 is done (2026-10-19): PRD v0.4.2, design-doc revision 105, and the Python packages added with uv. The Nova Lite `eu.` profile routes to the same 4 Regions as Micro (looked up 2026-10-19). Still open from step 0: Ollama with its model, which I install. Nothing is built, deployed or committed.
**Sources:** PRD v0.4.1 (§9 TOOL, CHT, MODE, MEM, ALR-3, ING-3, OPS-1; §10; §12; §14 week 3), the design doc (Claude Doc, revision 98), `Design doc edits for OQ-42.md` (the open choices, drafted alongside this plan).

## Week 3 exit criteria (PRD §14, plus OQ-41 (e))

1. Both US-04 example questions are answered correctly on the Ask page ("What is still open with Brightpath?", "Which applications have been silent for 10+ days?").
2. The same graph runs locally against Ollama with `AGENT_MODE=local`.
3. A push to main deploys through GitHub Actions.
4. The read token still works on day 8 after consent (around 2026-10-26).
5. The Mac app refuses a connection on a non-loopback address.
6. Dashboard numbers match a direct count on a seeded table.
7. A failing message reaches the dead-letter queue, and the alarm email arrives within 10 minutes (ING-3).
8. The agent has replaced the temporary queue reader: a test invite gives exactly 1 alert, a re-poll gives none, and the topic policy names only the new publisher (OQ-41 (e), OQ-42 (f)).

Carried over from week 2, not week 3 work: a real reply to an imported application landing on its row, and M8 on my labels.

## Shape of the result

```
Gmail ─ poll Lambda (unchanged) ─ SQS ─ queue consumer Lambda ──► AgentCore Runtime (LangGraph)
                                     │      (cloud only)              │ batch path: fixed nodes
                                     └─ DLQ ─ CloudWatch alarm ─┐     │ chat path: Nova Lite + list_applications
                                                                │     ▼ SigV4
Mac app (127.0.0.1) ── InvokeAgentRuntime (my aws login) ──►    │   AgentCore Gateway (IAM auth)
   Dashboard: reads DynamoDB only                               │     │ one Lambda target per tool
   Ask: the chat path                                           ▼     ▼
                                               SNS alert topic ◄── notify_owner Lambda
                                               (1 subscription)    read_email, upsert_application,
                                                                   list_applications Lambdas
```

- **InboxAgentAlwaysFree** gains the 4 tool Lambdas and their roles, the DLQ alarm, and the topic policy change. It loses the temporary reader once the cutover passes.
- **InboxAgentAgentCore** (cloud mode only) gains the Runtime, the Gateway with its 4 Lambda targets, the queue consumer Lambda and its event source mapping. Memory (week 4) and Evaluations (week 6) come later.
- **InboxAgentCi** (new, deployed once by me by hand) holds the GitHub OIDC provider and the deploy role.

## Steps

Each step ends with `uv run pytest` green, and steps that touch `infra/` also end with `cdk synth` in both modes.

### 0. Decisions, dependencies and documents
- I accept or reject each OQ-42 item, and approve the dependencies in OQ-42 (n).
- Then: PRD v0.4.2 and the design doc revision after 98 get the accepted rows; each design-doc edit is read back. The PDF stays the revision 90 export until I re-export it.
- `uv add` the approved packages into the right groups (`agent`, `app`, `local`, dev). Nothing else is installed.

### 1. Interfaces for model, tools and memory (MODE-1)
- **Model:** one factory, `chat_model(purpose)`, returns a LangChain chat model. Cloud: `ChatBedrockConverse` with `eu.amazon.nova-micro-v1:0` (classify) or `eu.amazon.nova-lite-v1:0` (chat). Local: `ChatOllama`. The existing `Model` protocol in `model.py` wraps it, so `classify.py` and its tests keep working.
- **Tools:** one `Tools` interface with three implementations: the Gateway MCP client (cloud), the local MCP server over stdio (local), and an in-memory fake (tests).
- **Memory:** a `Memory` interface with a no-op implementation. AgentCore Memory and the local store come in week 4 (MEM-1, MEM-2).
- `AGENT_MODE` picks all three. Tests: each mode gets the right implementations, and an unknown mode fails.

### 2. The tool handlers (TOOL-1, week 3 subset)
Plain functions first, then thin Lambda handlers that read the tool name from the Gateway's client context. Inputs as drafted in OQ-42 (d).
- `read_email`: fetch with the read grant (`inbox-agent-poller`, ING-4), trim (CLS-3), return header fields, the trimmed text and the calendar start. Reuses `gmail.py`, `messages.py`, `trim.py`, `calendar_invite.py`.
- `upsert_application`: TRK-1 matching and update, the source-message dedup, no backward moves on automatic updates. Reuses `matching.py`, `tracker_update.py`, `tracker.py`.
- `list_applications`: TRK-2 filters (status including "open", company, days since last contact). New code; the alias item is skipped.
- `notify_owner`: structured inputs only, the ALR-1 text built in code, one publish to the topic. Reuses `alerts.py`.
- Tests: TRK-2 filters alone and combined on a seeded fake table; no recipient field in the `notify_owner` schema; `read_email` never returns more than 2,000 characters of text; logs carry IDs only.

### 3. The LangGraph graph and the Runtime entry point
- **Batch path** (fixed edges, OQ-42 (b)): check handled → `read_email` → classify (Nova Micro) → `upsert_application` → claim alert → `notify_owner` → mark handled. The node bodies are the steps of `reader.handle_email`. The processed-email log is reached directly from graph code, not as a tool.
- **Chat path** (OQ-42 (h)): Nova Lite with `list_applications` bound, a small step limit, and a system prompt that treats tool results as data.
- **Entry point:** a `bedrock-agentcore` app with one handler that routes on the payload type: `new_email` to the batch path, `chat` to the chat path. Week 4 adds the stale check; decision messages never reach the graph (APR-3).
- Tests with the fake model and fake tools: the reader's existing cases carried over (a duplicate delivery gives 1 update and at most 1 alert, a failed publish releases the claim, a calendar start wins); a hostile email body cannot change the batch path's tool sequence; the chat path answers both US-04 questions on a seeded table with a scripted fake model.

### 4. CDK: tools, Gateway, Runtime, consumer (TOOL-1, TOOL-3, MODE-2)
- **Always-free stack:** 4 tool Lambdas, each with an explicit 7-day log group and its own hand-built role (exact ARNs, no wildcards). Only `read_email`'s role gets the AgentCore Identity read-token calls; only `upsert_application` writes the tracker; `list_applications` gets `dynamodb:Scan` only; only `notify_owner` gets `sns:Publish`.
- **AgentCore stack:** the stable AgentCore constructs in aws-cdk-lib 2.273 (OQ-42 (i)), each given a hand-built role:
  - Runtime from a ZIP built for Linux arm64, Python 3.13; inbound IAM; its role may call the Nova Micro and Nova Lite `eu.` profiles and their Regional model ARNs only (TOOL-3), `InvokeGateway` on the one Gateway, and the processed-email log. Both profiles route to eu-central-1, eu-north-1, eu-west-1 and eu-west-3 (checked 2026-10-19), so that is 2 profiles and 8 model ARNs;
  - Gateway with IAM inbound auth and one Lambda target per tool; its role may invoke only those 4 functions;
  - the queue consumer Lambda (OQ-42 (a)), `bedrock-agentcore:InvokeAgentRuntime` on the one Runtime, its event source mapping created **disabled**.
- Tests: the existing guardrails extended to every new role and log group; the Gateway's tool list equals the week 3 list; the Runtime role names exactly the 2 profiles and their Regional model ARNs; local mode synthesizes no Runtime, Gateway or consumer; still no API Gateway API or function URL.

### 5. DLQ alarm and topic policy (ING-3, ALR-3)
- A CloudWatch alarm on the DLQ (`ApproximateNumberOfMessagesVisible` ≥ 1), action: the alert topic.
- Topic policy (OQ-42 (f), (g)): the Allow names the `notify_owner` role; CloudWatch gets an Allow scoped to that one alarm's ARN and this account; the Deny keeps every other principal out, with the alarm as its only exception.
- Tests: the exact statement set; no Allow with `Principal: "*"`; the reader's role is gone from the policy after cutover.

### 6. The Mac app: Dashboard and Ask (DASH-1, CHT-1, CHT-2)
- `mac_app/`, started with `uv run streamlit run mac_app/Home.py`. `.streamlit/config.toml` sets `server.address = "127.0.0.1"`, `server.headless = true` and `browser.gatherUsageStats = false`.
- **Dashboard:** totals, open applications, counts by status, open rows silent for 10+ days, key dates in the next 7 days, this week's alerts including "Possible:". DynamoDB reads only (Scan). For the alert counts, the alert claim in the processed-email log also records the alert kind (urgent or "Possible:"); the 30-day TTL covers a week.
- **Ask:** one text box. Cloud: `InvokeAgentRuntime` with my `aws login` session. Local: the local worker on 127.0.0.1:8080.
- Tests: Dashboard counts on a seeded fake table equal direct counts; the Dashboard module makes no write calls; the config binds to 127.0.0.1.

### 7. Local mode (MODE-1)
- The local worker is the same entry point, served on 127.0.0.1:8080. Tools come from the local MCP server over stdio, which opens no port. cedarpy is added in week 5 (MODE-4).
- Ollama with the model from OQ-42 (j), installed by me.
- Week 3 local check: the chat path answers both US-04 questions. Local alerts wait for the Mac worker (OQ-42 (j)).

### 8. GitHub Actions deploy with OIDC (OPS-1)
- `InboxAgentCi` stack (OQ-42 (l)): the GitHub OIDC provider and a deploy role whose trust allows only `repo:EgeCankaya/job-search-agent:ref:refs/heads/main`, and which may only assume the CDK bootstrap deploy, file-publishing and lookup roles. I deploy it once by hand; the pipeline never deploys it.
- `.github/workflows/deploy.yml`: on push to main, a `check` job (`uv sync`, `uv run pytest`, `cdk synth` in both modes), then a `deploy` job that needs it (OQ-42 (m)): `cdk deploy InboxAgentAlwaysFree InboxAgentAgentCore --require-approval never`. Actions pinned to commit SHAs; one deploy at a time; no `pull_request` trigger on the deploy job; `cdk.out` never uploaded.
- GitHub secrets, set by me: `AWS_DEPLOY_ROLE_ARN`, `AWS_ACCOUNT_ID` (so the account ID is masked in logs), `ALERT_EMAIL`. No AWS access keys.
- Tests: a workflow-file test (no `pull_request` on deploy, no `aws-access-key-id`, SHA-pinned actions); a CDK test of the trust condition.

### 9. Cutover from the temporary reader (I deploy each stage)
1. Deploy with the consumer's mapping disabled. The reader keeps reading the queue.
2. Check the chat path from the Ask page, and invoke the batch path by hand with `{"type": "new_email", "message_id": "<id of a test email already handled>"}`: it must return "duplicate" and change nothing.
3. One deploy flips three things together: the reader's mapping off, the consumer's mapping on, the topic Allow from the reader's role to the `notify_owner` role.
4. Send a test invite. Exactly 1 alert; the next poll re-sees it and nothing happens.
5. A later commit deletes `reader.py`, its Lambda, role, log group and mapping, and the reader tests that the graph tests now cover.

### 10. Exit checks
Run the 8 exit criteria above. The DLQ check: `aws sqs send-message` with a `new_email` body whose message ID doesn't exist, so `read_email` fails 3 times; the alarm email should arrive within 10 minutes; then delete or redrive the message (ING-3). Record results in CLAUDE.md.

## What I do by hand this week
- Accept or reject the OQ-42 items and the dependencies.
- Install Ollama and pull the model (OQ-42 (j)).
- Deploy each stage of step 9, and `InboxAgentCi` once.
- Create the 3 GitHub secrets.
- The day-8 read-token check, around 2026-10-26.
- Send the test invite and the DLQ test message.

## Risks for this week
- **Runtime ZIP size and arm64 wheels.** LangGraph and LangChain are pure Python, but some dependencies ship native wheels. The build must use `--python-platform aarch64-manylinux2014` and `--only-binary :all:`. If a package has no arm64 wheel, the build fails at synth, not at deploy.
- **Gateway tool names.** Gateway prefixes tool names with the target name. The graph maps them back; a test checks the mapping against the deployed list.
- **CloudWatch publishing to the topic.** The exception in OQ-42 (g) is from AWS's documented alarm-to-topic pattern, but only the exit check proves it, so the policy test plus the live alarm test both run.
- **The CDK bootstrap execution role has AdministratorAccess by default.** The pipeline inherits that power. I am the only pusher to main, and branch protection should require the check job. Narrowing the bootstrap policy is possible later; it's not in this plan.
- **Free plan and AgentCore charges (OQ-1).** The first Runtime and Gateway use is the first real AgentCore charge. Check Billing → Credits the day after the first deploy.
