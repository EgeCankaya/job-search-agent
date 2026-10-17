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
   (revision 90). `Job search inbox agent design.pdf` is an export of revision 90.
2. **`Job search inbox agent PRD.md`** (v0.4.0) owns requirements and targets:
   FRs §9, NFRs §10, metrics §11, CI gates §12, milestones §14, dependencies §16a.
3. **The code follows both.** Never add tools, tool inputs, endpoints or
   capabilities beyond PRD §9, or architecture beyond the design doc.
4. **Changes to the design doc go through an OQ-style item** that the owner
   accepts or rejects one by one. Examples: `Design doc edits for OQ-32.md`,
   `Design doc edits for OQ-39.md` and `Design doc edits for OQ-40.md`, all
   accepted and applied to the PRD and the design doc.

**If the PRD and the code would disagree, stop and ask the owner. Don't invent
scope.**

`Job search inbox agent PRD review.md` is a private working note. It is
gitignored and must stay out of the repo.

## Working rules from the owner

- **Ask before installing anything** (Homebrew, npm, uv packages beyond the project).
- **Never deploy** and never create accounts or enter credentials. The owner does these.
- **Never commit secrets, emails, phone numbers or personal data.** Tests use fictional values (`example.com`, `90555…`).
- Commit only when asked. Commit messages end with the co-author line.
- Stay inside the current milestone. Week 2+ items (Gmail, Bedrock, LangGraph, AgentCore resources, the Mac app, GitHub Actions) wait for their week.
- Research notes go to `/Users/egemencankaya/Vault/OpenCode Research/` (from `~/AGENTS.md`).

## Stack and layout

AWS CDK in Python, region **eu-north-1 (Stockholm)**, Python 3.13, managed with **uv**.
The layout below is the M1 code after the OQ-40 change: no inbound endpoint,
email alerts through SNS.

```
app.py                       CDK entry point (calls infra.app.build)
infra/config.py              settings: tag, region, AGENT_MODE, alert email
infra/app.py                 builds the stacks for the configured mode
infra/always_free_stack.py   tables, queues, alert topic, budgets
infra/agentcore_stack.py     empty until week 3
src/budgets/budgets_resource.py   deploy-time custom resource that creates the budgets
tests/unit/                  custom-resource handler tests
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
  - Topic policy (owner's pick, 2026-10-17): Allow `sns:Publish` to this account's principals only (`arn:aws:iam::<account>:root`, from `AWS::AccountId`), so the owner's `aws login` session can send the week 1 test alert. It also denies `sns:Publish` from other accounts (`aws:PrincipalAccount`) and without TLS. Week 3 narrows the Allow to the agent's role.
- No inbound HTTP endpoint (CHT-2): no API Gateway API and no Lambda function URL in any stack. The OQ-40 change removed the M1 WhatsApp webhook, its HTTP API and the five Meta and owner SSM parameter names.
- 3 AWS Budgets ($1, $5, $10, monthly, email), with `IncludeCredit: false` (see the Free plan section), created by the custom resource described under Gotchas.

**`InboxAgentAgentCore`**: empty, deployed only in cloud mode.

**Guardrails, all tested:**
- `project=inbox-agent` tag on everything;
- 7-day retention on every log group;
- no EC2/VPC, ELB, OpenSearch Serverless or SES resources;
- no API Gateway API or Lambda function URL, in either mode;
- every role is hand-built: no managed policies, no wildcards. The budgets role is the only one so far.
- resource policies use `Principal: "*"` only on a Deny with a condition (the topic policy), never on an Allow.

### Gotchas learned

- **`AWS::Budgets::Budget` doesn't exist in eu-north-1.** CDK's built-in validator fails the synth once credentials are present. The budgets are made by a deploy-time custom resource instead: `src/budgets/budgets_resource.py`, `Custom::InboxAgentBudgets`. It calls the global Budgets API, replaces the budgets on Create and Update, and deletes them on Delete. Its role has only `budgets:ModifyBudget` and `budgets:TagResource` on the 3 budget ARNs. The `project` tag is passed as a property, because `Tags.of()` can't reach API-made budgets.
- **`cdk synth` only validates against the Region once the CLI is signed in.** Always run it after changes, not just pytest.
- **CloudFormation can't create SecureString parameters.** A secret added later is only referenced by name in `infra/config.py`; the owner sets the values with `aws ssm put-parameter --type SecureString`.
- **Use an explicit `logs.LogGroup` passed to the Function, not `log_retention`.** `log_retention` adds a custom-resource Lambda with its own log group. A test checks that every function has `LoggingConfig.LogGroup`.
- **The `aws/ssm` managed KMS key allows decryption via SSM** for principals in the account, so no `kms:Decrypt` grant is needed.
- **`cdk.json` carries the recommended feature flags** from `cdk init`.
- **Check before an AWS service publishes to the alert topic** (for example the week 3 DLQ alarm). The `DenyPublishFromOtherAccounts` statement keys on `aws:PrincipalAccount`, which a service-principal call may not carry, so that publish could be denied. Test it, and add a `aws:SourceAccount`-scoped exception if needed.

## AWS Free plan (decided 2026-10-17)

The owner is on the **AWS Free plan and will stay on it**. Research findings:
- AWS's current docs list Bedrock and AgentCore as available on the Free plan.
- **No cross-Region features on the Free plan**, so the design's `eu.` Nova inference profile may be blocked. Measurements leave two routes (OQ-39 (b)): route 1, the `eu.` profile from Stockholm (Nova Micro + Nova Lite, design unchanged); route 2, Nova Lite in-Region in Stockholm for everything. Frankfurt adds nothing, because it has no in-Region Nova Micro either. **The route is not chosen yet**; it waits on the quotas (see Current state).
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

## Current state (2026-10-17)

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
- **Blocker for week 2:** the Nova quotas. The account is held at 0, while the AWS defaults are 400k cross-Region tokens per minute and 5.76B tokens per day. Service Quotas rejects requests at or below the default, so the owner files **one support case** to lift them. After that, use route 1 if its call succeeds, otherwise route 2 (OQ-39 (b)), and record the choice in the PRD.
- **Week 1 items the owner does by hand:**
  - MFA in AWS Settings;
  - a calendar reminder at sign-up + 5 months (OQ-39 (c));
  - the AgentCore test runtime for OQ-1;
  - the time-log baseline for M7.
