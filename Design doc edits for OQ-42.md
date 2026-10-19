# Design doc edits for OQ-42: building week 3, the agent

**For:** the PRD v0.4.1 and the design doc (Claude Doc, revision 98)
**What triggered it:** week 3 starts on 2026-10-18. The PRD and the design doc name the week 3 parts (LangGraph on AgentCore Runtime, tracker tools through the Gateway, the Ask and Dashboard pages, the interfaces for model, tools and memory, the GitHub Actions deploy, the dead-letter alarm, the reader's replacement), but leave the choices below open. Each needs my decision before code.
**Status:** **all fourteen items accepted on 2026-10-18** (all four dependency groups in (n) approved), **and applied on 2026-10-19** to the PRD (v0.4.2) and the source Claude Doc, [Job search inbox agent design](https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9) (revision 98 → 105). Each design-doc edit was checked by reading the doc back. The PDF in this folder is still the revision 90 export; re-export it to match. The (n) Python packages are added with `uv add` in the `agent`, `local` and `app` groups; Ollama, the model and the CI actions are not installed yet. No code is written yet.
**Depends on:** OQ-36 (approval call path), OQ-40 (SNS alerts, Mac app), OQ-41 (e) (the temporary queue reader), OQ-41 (f) (route 1).
**Plan:** `Week 3 plan.md` shows where each item lands.

Every item below gives a recommendation and the main alternative. "No change" means the PRD or the design doc already says it.

## (a) A queue consumer Lambda passes each message to the agent

AgentCore Runtime is request-driven: it can't read SQS itself. Something must take each queue message and call the Runtime. The PRD already assumes "the SQS consumer" in APR-3, which recognizes Approve and Skip decisions and keeps them away from the model, but no document says what it is.

**Recommendation:** a small Lambda, deployed only in cloud mode, triggered by the agent queue. It checks the message type and calls `InvokeAgentRuntime` once per `new_email` message, with a session ID derived from the Gmail message ID. It keeps the reader's settings: partial batch failures, batch size 5, maximum concurrency 2, and a queue visibility timeout of 6 times its own timeout. In week 4 it also takes decision messages straight to the approval workflow (APR-3) and the daily check to the Runtime. It is APR-3's "SQS consumer", so the OQ-36 call path is decided; the week 4 check (the Gateway telling two IAM identities apart) stays open.

**Cutover:** the reader stays deployed, with its mapping off, until the week 3 exit check passes, and is then deleted.

**Alternative:** the Runtime polls SQS in a long-running session. Rejected: sessions end after idle time, and it would pay for active time while waiting.

| PRD section | Change |
|---|---|
| ING-3 | Add: "In cloud mode a queue consumer Lambda hands each message to the agent; in MacBook mode the Mac worker reads the queue" |
| APR-3 step 1 | "The SQS consumer" → "The queue consumer (a Lambda in cloud mode, the Mac worker in MacBook mode)" |
| §14 week 3 | "approval call path decided (OQ-36)" → add "[v0.4.2] decided: the queue consumer of OQ-42 (a)" |
| §18 OQ-36 | Provisional → **Resolved (path decided in OQ-42 (a))**; the week 4 two-identity check stays |

| Design doc place | Change |
|---|---|
| Components table, AWS Lambda row | "Gmail poller, tracker tools, and in week 2 only a temporary queue reader" → "Gmail poller, tracker tools, and the queue consumer that hands each message to the agent" |
| How it works, step 2 | "It is queued, and the agent classifies it…" → "It is queued, a small Lambda hands it to the agent, and the agent classifies it…" |

## (b) The batch path is a fixed sequence; the model only classifies

The design says the reader's steps become the agent's nodes. It doesn't say whether the model picks tools on the batch path.

**Recommendation:** the batch path is a fixed graph: check handled → `read_email` → classify and extract (Nova Micro, one call) → `upsert_application` → claim alert → `notify_owner` → mark handled. The model returns classes and fields only. It never picks a tool or its order, so a hostile email can't add a tool call (NFR-SEC-2). The tracker write and the alert still go through the Gateway tools, so Cedar (week 5) covers them.

The processed-email log (ING-2) is the agent's own bookkeeping, not a tool: graph code reads and writes it with the Runtime role, as the reader does. That keeps the tool list at the PRD's six.

**Alternative:** a tool-calling agent on the batch path. Rejected: more model calls per email, and a larger injection surface for no gain.

| PRD section | Change |
|---|---|
| TRK-1 or §9 conventions | Add: "On the batch path the tool sequence is fixed in code; the model classifies and extracts but does not choose tools" |

| Design doc place | Change |
|---|---|
| Agent design, first paragraph | After "a batch path that classifies new emails": add "(a fixed sequence of steps, where the model only classifies and extracts)" |

## (c) One Lambda per tool, in the always-free stack; 4 tools in week 3

**Recommendation:**
- **One Lambda per tool**, each with its own role: `read_email` gets the read token only, `upsert_application` writes the tracker only, `list_applications` reads it only, `notify_owner` publishes only. One shared Lambda would hold all four permissions.
- **Week 3 builds 4 tools:** `read_email`, `upsert_application`, `list_applications`, `notify_owner`. `search_gmail` arrives in week 4 with its first user, the follow-up drafting (APR-2 finds the thread's latest email), together with `create_gmail_draft`. The Gateway's list equals the PRD's six from week 4 (TOOL-1 acceptance).
- **The tool Lambdas live in the always-free stack.** Lambda is always free, and the topic policy can then name the `notify_owner` role in its own stack. The AgentCore stack imports the 4 function ARNs; nothing imports from the AgentCore stack, so MacBook mode can still remove it (MODE-2). In MacBook mode the Lambdas sit idle: the local MCP server runs the same code.

**Alternative:** tool Lambdas in the AgentCore stack. Then the topic policy would import from the stack that MacBook mode deletes, which blocks the delete.

| PRD section | Change |
|---|---|
| TOOL-1 | Add: "Week 3 serves `read_email`, `upsert_application`, `list_applications` and `notify_owner`; week 4 adds `search_gmail` and `create_gmail_draft`" |
| MODE-2 | Add: "The tool Lambdas are in the always-free stack" |

Design doc: no change. The components table already says Lambda runs the tracker tools.

## (d) Tool inputs

The PRD fixes two inputs (`approval_id`, no recipient) and says any new input needs a PRD revision. The design lists `upsert_application`'s fields as company, role, status, last contact, next step. The batch path needs a few more.

**Recommendation:**

| Tool | Inputs | Returns |
|---|---|---|
| `read_email` | `message_id` | Sender, subject, received time, the trimmed text (CLS-3, at most 2,000 characters), the calendar start if any. Trimming in the tool means no path can hand a model an untrimmed body (NFR-PRIV-2) |
| `upsert_application` | `company`, `role`, `status`, `last_contact`, `next_step` (the design's five), plus optional `message_id`, `key_date`, `key_date_type` | The row's company, role and status. With a `message_id` the call is an automatic update: TRK-1 matching, the source-message dedup, no backward moves. Without one it is a manual set from the Ask page, which may set any status (TRK-1) |
| `list_applications` | Optional `status` (a status or "open"), `company`, `min_days_since_contact` | Matching rows without message IDs or follow-up state |
| `notify_owner` | `kind` (urgent or possible), `email_class`, `company`, optional `role`, optional `date` | Nothing. The tool writes the ALR-1 text itself. No free-text input, so no input can carry email text or an instruction into the alert (NFR-PRIV-3, NFR-SEC-2 (d)); no recipient (ALR-3) |

**Alternative:** the design's five inputs only, with dedup done outside the tool. Then a retried email could write the row twice.

| PRD section | Change |
|---|---|
| §9 conventions, MCP tool names | Add the input table above as the fixed input lists |
| ALR-3 acceptance | Add: "The `notify_owner` schema has no free-text field" |

| Design doc place | Change |
|---|---|
| Agent design, tools list, `notify_owner` | "It has no recipient input." → "It has no recipient input and no free-text input: it takes the class, company, role and date, and writes the alert itself." |

## (e) IAM authentication at the Gateway and the Runtime

**Recommendation:** both use AWS IAM (SigV4). The Runtime calls the Gateway with its own role, and the Mac app calls the Runtime with my `aws login` session (CHT-2). This is also what Cedar needs in week 4 and 5 to tell the agent from the approval workflow (`AgentCore::IamEntity`, TOOL-4). The MCP client signs requests with botocore's SigV4 signer, about 30 lines of code, instead of another package.

**Alternative:** JWT through a Cognito user pool. Rejected: a new service and a new sign-in, and Cedar would see a token, not the role.

| PRD section | Change |
|---|---|
| TOOL-1 | Add: "The Gateway and the Runtime accept only IAM-signed (SigV4) calls" |

| Design doc place | Change |
|---|---|
| Interface, Owner check | Add: "The Gateway and the Runtime accept only IAM-signed calls." |

## (f) The alert topic's publisher is the `notify_owner` tool's role

ALR-3 says only "the agent's role" may publish. With Gateway Lambda tools, the call to SNS comes from the `notify_owner` Lambda, not from the Runtime. So "the agent's role" has to mean one of them.

**Recommendation:** the `notify_owner` Lambda's role is the only publisher. The Runtime role gets no `sns:Publish`, so the only way to the topic is the tool: through the Gateway, and from week 5 under Cedar.

**Alternative:** the Runtime role publishes directly. That skips the Gateway and Cedar for alerts.

MacBook mode's publisher is decided when the Mac worker is built (see (j)).

| PRD section | Change |
|---|---|
| ALR-3 | "The topic policy SHALL let only the agent's role publish (in week 2, the temporary queue reader's role)" → "The topic policy SHALL let only the `notify_owner` tool's role publish (in week 2, the temporary queue reader's role)" |
| ALR-3 acceptance | "…allows publishing only from the agent's role" → "…only from the `notify_owner` tool's role, plus the dead-letter alarm (OQ-42 (g))" |
| §14 week 3, OQ-41 (e) entry | "the topic policy moves to the agent's role" → "…to the `notify_owner` tool's role" |

| Design doc place | Change |
|---|---|
| Interface, Email alerts | "Only the agent's role may publish to it." → "Only the `notify_owner` tool may publish to it, plus the dead-letter alarm." |

## (g) The dead-letter alarm's exception in the topic policy

The topic's `DenyPublishFromOtherPrincipals` uses `ArnNotEquals aws:PrincipalArn`. A CloudWatch alarm publishes as the service, which carries no `aws:PrincipalArn`, so today's policy denies it.

**Recommendation:** keep one topic and add a narrow exception:
- an Allow for `cloudwatch.amazonaws.com`, with `aws:SourceArn` equal to the one alarm's ARN and `aws:SourceAccount` equal to this account;
- the Deny gains `StringNotEquals aws:SourceArn` (that alarm's ARN). An IAM principal can't set `aws:SourceArn`, so my session and every other role stay denied.

The alarm watches `ApproximateNumberOfMessagesVisible` on the DLQ, at 1 or more, and its email carries no email text. The exit check proves it: a message that fails 3 times, then the alarm email within 10 minutes.

**Alternative:** a second SNS topic for operations alarms, with its own email subscription. Rejected: a second subscription to confirm and watch, and ALR-3's "one subscription" story gets a footnote anyway.

| PRD section | Change |
|---|---|
| ING-3 | Add: "The alarm publishes to the alert topic, whose policy allows the service only for that alarm's ARN" |

Design doc: covered by the (f) sentence.

## (h) The chat path gets `list_applications` only, in week 3

The design says both paths share the same tools. Cedar can't tell the chat path from the batch path, because both run as the Runtime role.

**Recommendation:** in week 3 the chat path binds only `list_applications` (CHT-1). TRK-1's manual status change from the Ask page waits until I want it, then it adds `upsert_application`. `notify_owner` is never bound to the chat path. Memory joins in week 4.

**Alternative:** bind all tools, as the design's sentence reads. Rejected for now: the chat path doesn't need writes to answer questions.

| PRD section | Change |
|---|---|
| CHT-1 | Add: "In week 3 the chat path may call only `list_applications`" |

| Design doc place | Change |
|---|---|
| Agent design | "Both share the same tools and memory." → "Both share the same memory. The chat path uses the read tools; only the batch path writes the tracker or sends alerts." |

## (i) How the agent is packaged and deployed in CDK

aws-cdk-lib 2.273, already in the project, has stable AgentCore constructs (`Runtime`, `Gateway` with `GatewayAuthorizer.using_aws_iam()`, Lambda targets, `AgentRuntimeArtifact.from_code_asset`), plus the raw CloudFormation ones.

**Recommendation:**
- Use those constructs, with every role passed in hand-built, and fall back to the raw CloudFormation resource wherever a construct adds a wildcard or a managed policy. The existing guardrail tests decide.
- The Runtime is a ZIP (the design's "no container"), Python 3.13, built at synth time by uv for Linux arm64 (`--python-platform aarch64-manylinux2014 --only-binary :all:`). No Docker.
- No new CDK package.

**Alternative:** the separate alpha construct library, or Docker bundling. Both add an install.

PRD and design doc: no change. These are build details (PRD §13).

## (j) Local mode in week 3

The week 3 exit says the same graph runs locally against Ollama.

**Recommendation:**
- **Model:** `qwen3:32b` in Ollama (about 20 GB at 4-bit). It is in the design's 27B to 32B range and supports tool calls, which the chat path needs. Its Turkish is unverified; EV-6 measures it. Fallback: `mistral-small3.2` (24B).
- **Tools:** the local MCP server runs the same tool code and talks over stdio, so it opens no port at all. cedarpy is added in week 5 (MODE-4).
- **Worker:** the same Runtime entry point, served on 127.0.0.1:8080, which the Ask page calls in local mode.
- **Week 3 local check:** the chat path answers both US-04 questions. Local alerts wait for the Mac worker (queue reader, poll, Keychain), which no milestone schedules yet. Who may publish in MacBook mode (my session, or a local-worker role) is decided then.

**Alternative:** a smaller model (8B) for faster development runs. It would say little about H4 and H6.

| PRD section | Change |
|---|---|
| §14 week 3 exit | "The same graph runs locally against Ollama with `AGENT_MODE=local`" → add "(the chat path; the Mac worker's queue reading comes later)" |
| §16a Ollama row | Add the chosen model |

Design doc: no change.

## (k) Traces: log fields now, OpenTelemetry in week 6

NFR-OBS-1 asks for OpenTelemetry spans with the Gmail receipt time and the detection time. AgentCore's built-in tracing needs the AWS OpenTelemetry distribution in the ZIP and CloudWatch Transaction Search turned on for the account (an account setting I would change).

**Recommendation:** in week 3, every step logs a structured line with the message ID, the Gmail receipt time, the detection time and the publish time, never email text. M5 can be computed from these with CloudWatch Logs Insights. Spans, the distribution and Transaction Search come in week 6, with the latency report.

**Alternative:** spans from week 3. One more dependency and an account setting now, for a report written in week 6.

| PRD section | Change |
|---|---|
| NFR-OBS-1 | Add: "Until week 6, the same times are carried as structured log fields" |

Design doc: no change.

## (l) GitHub OIDC: a small CI stack I deploy once

**Recommendation:**
- A third stack, `InboxAgentCi`, with the GitHub OIDC provider and one deploy role. The trust allows only `repo:EgeCankaya/job-search-agent:ref:refs/heads/main` with audience `sts.amazonaws.com`. The role may only `sts:AssumeRole` on the CDK bootstrap deploy, file-publishing and lookup roles, named exactly.
- I deploy it once by hand. The pipeline deploys only `InboxAgentAlwaysFree` and `InboxAgentAgentCore`, so it can never widen its own trust.
- GitHub secrets: `AWS_DEPLOY_ROLE_ARN`, `AWS_ACCOUNT_ID` (GitHub masks it wherever it appears in the logs, which matters once the repo is public), `ALERT_EMAIL`. No access keys.
- Actions pinned to commit SHAs, one deploy at a time, `cdk.out` never uploaded.
- The Tier 2 role (OPS-1, Nova and Evaluations only) is week 6.

**Alternative:** the provider and role made with the AWS CLI. Not in code, so not reviewable.

| PRD section | Change |
|---|---|
| OPS-1 | Add: "The OIDC provider and the deploy role are a separate stack that I deploy by hand; the pipeline never deploys it. The deploy role may only assume the CDK bootstrap roles" |
| MODE-2 | Add: "A third stack holds the CI identity" |

| Design doc place | Change |
|---|---|
| Build plan, week 3 | Add: "Deploy from main through GitHub Actions with OIDC, add the dead-letter alarm, and retire the temporary queue reader" |

## (m) The deploy gate until Tier 1 exists

OPS-1 deploys when "Tier 1 passes". Tier 1's sets (EV-1s, EV-3, EV-4) are written in weeks 5 to 7, so there is no Tier 1 in week 3.

**Recommendation:** until Tier 1 exists, the deploy needs a green `check` job: `uv run pytest` and `cdk synth` in both modes. When Tier 1 lands, it joins the `check` job, and the deploy needs both.

**Alternative:** no automatic deploy until week 6. Then the week 3 exit "a push to main deploys" can't pass.

| PRD section | Change |
|---|---|
| OPS-1 | Add: "Until Tier 1 exists (week 6), the unit tests and `cdk synth` gate the deploy" |
| §12 CI gate, Deploy bullet | Same note |

## (n) New dependencies

Nothing is installed until I approve each group.

| Group | Packages | Where they run | Why |
|---|---|---|---|
| Agent | `langgraph`, `langchain-aws`, `bedrock-agentcore`, `mcp`, `langchain-mcp-adapters` | Runtime ZIP, local worker | The graph (design doc), Converse through LangChain (design doc), the Runtime's HTTP entry point, the MCP client and server, MCP tools as LangChain tools |
| Local | `langchain-ollama`; the Ollama app (Homebrew `ollama`); the `qwen3:32b` model (about 20 GB) | The Mac | Local mode (j) |
| Mac app | `streamlit` (brings pandas, pyarrow, altair and others) | The Mac | Already chosen in OQ-40 |
| CI | GitHub actions `actions/checkout`, `astral-sh/setup-uv`, `actions/setup-node`, `aws-actions/configure-aws-credentials`; the `aws-cdk` CLI from npm | GitHub's runners only | OPS-1 |

Not added: `mcp-proxy-for-aws` (SigV4 is done in (e) without it), the alpha CDK library and Docker ((i)), the OpenTelemetry distribution (week 6, (k)). The tool Lambdas need nothing beyond the Lambda runtime's boto3.

| PRD section | Change |
|---|---|
| §16a | Add rows for LangGraph and langchain-aws, bedrock-agentcore, the MCP SDK and adapters; Streamlit stays as is |

## What the code change will be (after acceptance)

The steps in `Week 3 plan.md`: interfaces (1), tool handlers (2), the graph and entry point (3), CDK (4), the DLQ alarm and topic policy (5), the Mac app (6), local mode (7), OIDC and the workflow (8), the cutover in three deploys (9), and the exit checks (10).

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Queue consumer Lambda; OQ-36 path decided | Accept | 2026-10-18 | |
| (b) Fixed batch path; the model only classifies | Accept | 2026-10-18 | |
| (c) One Lambda per tool, always-free stack, 4 tools in week 3 | Accept | 2026-10-18 | |
| (d) Tool inputs | Accept | 2026-10-18 | |
| (e) IAM auth at the Gateway and the Runtime | Accept | 2026-10-18 | |
| (f) Publisher is the `notify_owner` tool's role | Accept | 2026-10-18 | |
| (g) DLQ alarm exception in the topic policy | Accept | 2026-10-18 | |
| (h) Chat path gets `list_applications` only | Accept | 2026-10-18 | |
| (i) aws-cdk-lib AgentCore constructs, uv-built arm64 ZIP | Accept | 2026-10-18 | |
| (j) Local mode: `qwen3:32b`, stdio MCP server, chat-path check | Accept | 2026-10-18 | |
| (k) Log fields now, OpenTelemetry in week 6 | Accept | 2026-10-18 | |
| (l) CI stack deployed once by hand | Accept | 2026-10-18 | |
| (m) Unit tests and synth gate the deploy until Tier 1 | Accept | 2026-10-18 | |
| (n) Dependencies | Accept | 2026-10-18 | All four groups: agent, local, Mac app, CI |
