# CLAUDE.md

A personal Job Search Inbox Agent. It watches Gmail for job-search email, keeps
an applications tracker current, alerts the owner on WhatsApp only for interview
invites, assessments and offers, and saves follow-ups as Gmail drafts only after
an Approve tap. **It never sends email.** The owner is the sole builder and sole
user. The repo is **public**.

## Source of truth and order of authority (PRD §13)

1. **Design doc** owns architecture: services, models, modes, data flow. The
   live version is the Claude Doc https://claude.ai/artifact/T8g4UsfvBmdx7e8yhGbWz9
   (revision 51). `Job search inbox agent design.pdf` is an export of revision 51.
2. **`Job search inbox agent PRD.md`** (v0.3.2) owns requirements and targets:
   FRs §9, NFRs §10, metrics §11, CI gates §12, milestones §14, dependencies §16a.
3. **The code follows both.** Never add tools, tool inputs, endpoints or
   capabilities beyond PRD §9, or architecture beyond the design doc.
4. **Changes to the design doc go through an OQ-style item** that the owner
   accepts or rejects one by one. Examples: `Design doc edits for OQ-32.md`
   (all accepted and applied) and `Design doc edits for OQ-39.md` (proposed).

**If the PRD and the code would disagree, stop and ask the owner. Don't invent
scope.**

`Job search inbox agent PRD review.md` is a private working note. It is
gitignored and must stay out of the repo.

## Working rules from the owner

- **Ask before installing anything** (Homebrew, npm, uv packages beyond the project).
- **Never deploy** and never create AWS or Meta accounts or enter credentials. The owner does these.
- **Never commit secrets, emails, phone numbers or personal data.** Tests use fictional values (`example.com`, `90555…`).
- Commit only when asked. Commit messages end with the co-author line.
- Stay inside the current milestone. Week 2+ items (Gmail, Bedrock, LangGraph, AgentCore resources, webhook signature check, GitHub Actions) wait for their week.
- Research notes go to `/Users/egemencankaya/Vault/OpenCode Research/` (from `~/AGENTS.md`).

## Stack and layout

AWS CDK in Python, region **eu-north-1 (Stockholm)**, Python 3.13, managed with **uv**. The
docs still say eu-central-1; see the account section and OQ-39 (f).

```
app.py                       CDK entry point (calls infra.app.build)
infra/config.py              settings: tag, region, AGENT_MODE, alert email, SSM names
infra/app.py                 builds the stacks for the configured mode
infra/always_free_stack.py   tables, queues, webhook, budgets
infra/agentcore_stack.py     empty until week 3
src/webhook/whatsapp_webhook.py   webhook Lambda (stdlib + boto3 only)
src/budgets/budgets_resource.py   deploy-time custom resource that creates the budgets
tests/unit/                  Lambda handler tests
tests/infra/                 CDK assertion tests
```

### Commands

```bash
uv sync
uv run pytest
cdk synth -c alert_email=alerts@example.com
cdk synth -c alert_email=alerts@example.com -c agent_mode=local
```

Set `JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1` to silence the jsii warning
about Node 26 (it works; Node 24 LTS is the supported line).

### Settings

| Context | Env var | Notes |
|---|---|---|
| `alert_email` | `ALERT_EMAIL` | Budget alert address. Required. Never commit it |
| `agent_mode` | `AGENT_MODE` | `cloud` (default) or `local`. `local` leaves out the AgentCore stack (MODE-1, MODE-2) and warns that the MacBook-mode function-URL webhook isn't built yet |

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
- Webhook Lambda (Python 3.13, arm64, 128 MB, 10 s) behind an HTTP API at `GET`/`POST /whatsapp`, stage throttle 10 rps, burst 20.
  - It answers Meta's verify-token handshake (constant-time compare).
  - It replies "pong" to "ping" from the owner's number only, sending to the number from SSM, never to the sender (ALR-3).
  - It drops non-owner messages and logs only counts (CHT-2 item 3, NFR-PRIV-3).
  - It returns 200 for anything well-formed, because Meta retries non-200 responses.
  - Graph API version is `v23.0`.
- 3 AWS Budgets ($1, $5, $10, monthly, email), with `IncludeCredit: false` (see the Free plan section), created by the custom resource described under Gotchas.

**`InboxAgentAgentCore`**: empty, deployed only in cloud mode.

**Guardrails, all tested:**
- `project=inbox-agent` tag on everything;
- 7-day retention on every log group;
- no EC2/VPC, ELB, OpenSearch Serverless or SES resources;
- the webhook role is hand-built: no managed policies, no wildcards. It allows only `logs:CreateLogStream`/`PutLogEvents` on its own log group and `ssm:GetParameters` on its 5 parameter ARNs.

### Gotchas learned

- **`AWS::Budgets::Budget` doesn't exist in eu-north-1.** CDK's built-in validator fails the synth once credentials are present. The budgets are made by a deploy-time custom resource instead: `src/budgets/budgets_resource.py`, `Custom::InboxAgentBudgets`. It calls the global Budgets API, replaces the budgets on Create and Update, and deletes them on Delete. Its role has only `budgets:ModifyBudget` and `budgets:TagResource` on the 3 budget ARNs. The `project` tag is passed as a property, because `Tags.of()` can't reach API-made budgets.
- **`cdk synth` only validates against the Region once the CLI is signed in.** Always run it after changes, not just pytest.
- **CloudFormation can't create SecureString parameters.** CDK only references the names in `infra/config.py`. The owner sets the values with `aws ssm put-parameter --type SecureString`:
  - `/inbox-agent/meta/verify-token`
  - `/inbox-agent/meta/access-token`
  - `/inbox-agent/meta/app-secret` (used by the week 3 signature check)
  - `/inbox-agent/meta/phone-number-id`
  - `/inbox-agent/owner/whatsapp-number`
- **Use an explicit `logs.LogGroup` passed to the Function, not `log_retention`.** `log_retention` adds a custom-resource Lambda with its own log group. A test checks that every function has `LoggingConfig.LogGroup`.
- **The `aws/ssm` managed KMS key allows decryption via SSM** for principals in the account, so no `kms:Decrypt` grant is needed.
- **`cdk.json` carries the recommended feature flags** from `cdk init`.

## AWS Free plan (decided 2026-10-17)

The owner is on the **AWS Free plan and will stay on it**. Research findings:
- AWS's current docs list Bedrock and AgentCore as available on the Free plan. That contradicts the design doc's "Paid plan exclusive".
- **No cross-Region features on the Free plan**, so the design's `eu.` Nova inference profile is unavailable. Models must be called in-Region: Stockholm if Nova runs on demand there, otherwise Frankfurt. Bedrock allows calls that create no resources in any default Region.
- **The account closes 6 months after sign-up, or when the credits run out**, with no extension. Data can be recovered only by upgrading within 90 days. You can't downgrade from Paid back to Free.
- Nothing is ever charged, so no KDV.

### The account: AWS's new sign-up experience

- **Home Region is fixed at eu-north-1.** It was picked from the contact country (Turkey) and can't be changed. Resources can't be created in other Regions. Unlocking Regions needs the Paid plan plus "activate advanced features", which is irreversible, so it's ruled out.
- **No root user and no console IAM users.** A Builder ID owns the account; IAM users work only programmatically. Account settings and MFA live in AWS Settings (settings.aws.com). The account is in an AWS-managed organization automatically.
- **CLI access:** `aws login` (CLI region set to `eu-north-1`). It yields an assumed `AccountFullAccessRole` session. A CLI region of `eu-central-1` made the login fail.
- **Never write the account ID into the repo.** CDK reads it from `CDK_DEFAULT_ACCOUNT`.

**Changes so far:**
- **Code:** the budgets exclude credits so they track credit burn, and `REGION` is `eu-north-1`.
- **Docs:** everything else is proposed in `Design doc edits for OQ-39.md`, items (a) to (e): Free plan, in-Region model calls, the 6-month horizon, the budgets and no KDV, plus (f) the home Region. **Nothing has been applied to the PRD or the design doc yet.** Apply them only after the owner accepts each item (`prd-engine:prd-revise` fits the PRD cascade).

## Interface change (proposed 2026-10-17, OQ-40)

The owner doesn't want a Meta account. `Design doc edits for OQ-40.md` proposes replacing WhatsApp with two things:
- **Email alerts** through an SNS topic. The tool `send_whatsapp` becomes `notify_owner`.
- **A local Python Streamlit app on 127.0.0.1** with three pages: Dashboard (new DASH-1), Ask (CHT-1) and Follow-ups (APR-2/3).

The webhook, API Gateway and Meta SSM parameters would then be removed, leaving no public endpoint. **Not accepted yet. Don't build the UI or remove the webhook until the owner accepts OQ-40.** The deployed webhook is harmless meanwhile: it returns 500 without the SSM values.

## Current state (2026-10-17)

- **Commit `e723332` on `main`:** the M1 code plus the PRD, the design PDF and the OQ-32 edits.
- **Uncommitted:** the move to eu-north-1, the budgets custom resource (`src/budgets/`) and its tests, the README notes, `Design doc edits for OQ-39.md`, `Design doc edits for OQ-40.md`, and this file.
- **The owner's CLI login works** (`aws login`, eu-north-1).
- **Deployed 2026-10-17 by the owner:**
  - `cdk bootstrap` and `cdk deploy --all` in eu-north-1. Both stacks are `CREATE_COMPLETE`.
  - The 3 budgets exist with `IncludeCredit: false`, and 15 resources carry the project tag.
  - The webhook returns 500 until the SSM values exist.
  - Keep the webhook URL out of the repo; it is unauthenticated until the week 3 signature check.
- **Nova quota requests filed 2026-10-17:**
  - Service Quotas `L-DC7FF66C` and `L-7C42E72A` at 500,000, both pending.
  - A support case (category "General") for the tokens-per-day and per-minute quotas.
- **Measured 2026-10-17** (details in OQ-39):
  - Nova Micro is profile-only in both eu-north-1 and eu-central-1; Nova Lite runs on demand in eu-north-1.
  - The `eu.` profiles are `ACTIVE`, but calls get throttled because **every Nova inference quota is 0**.
  - AgentCore's control plane (Runtime, Gateway, Memory, Identity, Policy, Evaluations) responds in eu-north-1.
- **Blocker for week 2:** the Nova quotas. The account is held at 0, while the AWS defaults are 400k cross-Region tokens per minute and 5.76B tokens per day. Service Quotas rejects requests at or below the default, so the owner files **one support case** to lift them. After that, pick OQ-39 (b) route 1 (`eu.` profile, design unchanged) or route 2 (Nova Lite in-Region for everything).
- **Week 1 items the owner does by hand:**
  - MFA in AWS Settings;
  - (on hold until OQ-40 is decided) Meta app, templates, SSM values, webhook registration;
  - the AgentCore test runtime for OQ-1;
  - the time-log baseline for M7.
