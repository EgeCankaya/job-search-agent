# Job Search Inbox Agent

A personal agent that watches Gmail for job-search email, keeps an applications
tracker current, emails me only for interview invites, assessments and offers,
and saves follow-ups as Gmail drafts only after I click Approve in a local Mac
app. It never sends or forwards email from Gmail.

AWS serverless plus Amazon Bedrock AgentCore, defined in AWS CDK (Python), in
eu-north-1 (Stockholm), the home Region AWS assigned to this account. Requirements live in the PRD; architecture lives in the design doc.

## Status: milestone M1 (week 1), skeleton

| Stack | Contents |
|---|---|
| `InboxAgentAlwaysFree` | DynamoDB tables (applications tracker; processed-email log and pending drafts, both with 30-day TTL), one SQS queue (14-day retention) with a dead-letter queue after 3 receives, an SNS alert topic with one email subscription (mine), AWS Budgets alerts at $1, $5 and $10 |
| `InboxAgentAgentCore` | Empty until week 3. Deployed only when `AGENT_MODE=cloud` |

The account is on the AWS Free plan: credits pay for all usage and nothing is
ever charged, so the budgets exclude credits and alert on how fast credits are
used. CloudFormation has no budget resource in eu-north-1, so a deploy-time
Lambda creates them through the Budgets API. The Free plan closes the account 6 months after sign-up or when the
credits run out, whichever comes first.

Day-one guardrails in code: every resource tagged `project=inbox-agent`, 7-day
log retention, no VPC/NAT/load balancer/OpenSearch Serverless/SES inbound, no
public endpoint (no API Gateway, no Lambda function URL), and hand-built IAM
roles with no wildcards. The alert topic's policy lets only this account's principals
publish, over TLS; week 3 narrows it to the agent's role.

## Layout

```
app.py                       CDK entry point
infra/                       stacks and settings
import_applications.py       one-off import of current applications (TRK-3)
src/budgets/                 deploy-time custom resource that creates the budgets
src/inbox_agent/             runtime code: Gmail poll, query builder, tracker matching, classifier
tests/unit/                  unit tests with in-memory fakes for Gmail, the model, DynamoDB and SQS
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
| `alert_email` | `ALERT_EMAIL` | My address, for budget alerts and the agent's alert emails. Required; never committed |
| `agent_mode` | `AGENT_MODE` | `cloud` (default) or `local`. `local` leaves out the AgentCore stack |

## Alerts

After a deploy, AWS emails a subscription confirmation to `alert_email`.
Confirm it, or no alert arrives. Alerts come from
`no-reply@sns.amazonaws.com`; the agent's Gmail query (week 2) excludes that
sender (ING-1).

Never commit application CSVs, `.env` files or `cdk.out/`; `.gitignore`
excludes them.
