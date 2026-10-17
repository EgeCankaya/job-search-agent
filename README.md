# Job Search Inbox Agent

A personal agent that watches Gmail for job-search email, keeps an applications
tracker current, alerts me on WhatsApp only for interview invites, assessments
and offers, and saves follow-ups as Gmail drafts only after I tap Approve. It
never sends email.

AWS serverless plus Amazon Bedrock AgentCore, defined in AWS CDK (Python), in
eu-north-1 (Stockholm), the home Region AWS assigned to this account. Requirements live in the PRD; architecture lives in the design doc.

## Status: milestone M1 (week 1), skeleton

| Stack | Contents |
|---|---|
| `InboxAgentAlwaysFree` | DynamoDB tables (applications tracker; processed-email log and pending drafts, both with 30-day TTL), one SQS queue (14-day retention) with a dead-letter queue after 3 receives, the WhatsApp webhook Lambda behind API Gateway, AWS Budgets alerts at $1, $5 and $10 |
| `InboxAgentAgentCore` | Empty until week 3. Deployed only when `AGENT_MODE=cloud` |

The account is on the AWS Free plan: credits pay for all usage and nothing is
ever charged, so the budgets exclude credits and alert on how fast credits are
used. CloudFormation has no budget resource in eu-north-1, so a deploy-time
Lambda creates them through the Budgets API. The Free plan closes the account 6 months after sign-up or when the
credits run out, whichever comes first.

Day-one guardrails in code: every resource tagged `project=inbox-agent`, 7-day
log retention, no VPC/NAT/load balancer/OpenSearch Serverless/SES inbound, and
a webhook IAM role scoped to its own log group and SSM parameters.

## Layout

```
app.py                       CDK entry point
infra/                       stacks and settings
src/webhook/                 WhatsApp webhook Lambda (stdlib only)
src/budgets/                 deploy-time custom resource that creates the budgets
tests/unit/                  handler tests
tests/infra/                 CDK assertion tests
```

## Develop

```bash
uv sync
uv run pytest
cdk synth -c alert_email=you@example.com
```

Settings, as CDK context (`-c key=value`) or environment variables:

| Context | Env var | Values |
|---|---|---|
| `alert_email` | `ALERT_EMAIL` | Address for budget alerts. Required; never committed |
| `agent_mode` | `AGENT_MODE` | `cloud` (default) or `local`. `local` leaves out the AgentCore stack |

## Secrets

CloudFormation cannot create SecureString parameters, so the stack only
references these names. Put the values in place yourself:

| Parameter | Value |
|---|---|
| `/inbox-agent/meta/verify-token` | A random string you choose, also entered in the Meta webhook settings |
| `/inbox-agent/meta/access-token` | Meta System User token |
| `/inbox-agent/meta/app-secret` | Meta app secret (used by the week 3 signature check) |
| `/inbox-agent/meta/phone-number-id` | Phone number ID of the agent's WhatsApp sender |
| `/inbox-agent/owner/whatsapp-number` | Your own number, digits with country code |

Never commit application CSVs, `.env` files or `cdk.out/`; `.gitignore`
excludes them.
