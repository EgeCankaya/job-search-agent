# Design doc edits for OQ-39: the AWS Free plan

**For:** "Job search inbox agent design" (Claude Doc, revision 51; PDF, 14 pages) and the PRD v0.3.2
**Decision that triggered it:** on 2026-10-17 I chose to stay on the AWS Free plan, not the Paid plan.
**Status:** proposed. Accept or reject each item below (PRD §13). Nothing has been applied to the design doc or the PRD yet.
**Code already changed:** items (d), the budgets, and (f), the Region.

## What the research found (2026-10-17)

- **AgentCore is available on the Free plan.** AWS's list of services for new accounts has "Amazon Bedrock Agent Core" and "Amazon Bedrock" in the Free Tier, with no listed exclusions ([Supported AWS services for Sign up for AWS (new)](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html)). The AWS Billing docs point to this list for Free plan eligibility ([Explore AWS services with AWS Free Tier](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html)). That contradicts the design doc's "Paid plan exclusive". The same list also covers Lambda, API Gateway, DynamoDB, SQS, EventBridge, Systems Manager, CloudWatch, CloudFormation and AWS Budgets, so everything in M1 is covered.
- **Cross-Region features are not supported on the Free plan, for any service** (same page). That rules out the `eu.` cross-Region inference profile the design uses for Nova.
- **The Free plan ends at 6 months or when the credits run out, whichever comes first, and can't be extended** ([AWS Free Tier FAQs](https://aws.amazon.com/free/faqs/)). The account is then closed. AWS keeps the data for 90 days, and you can get it back only by upgrading to the Paid plan; downloading it also needs an upgrade. The Paid plan can't be downgraded back to Free.
- **Nothing is ever charged on the Free plan** until you upgrade (same FAQ). So there is no KDV to pay.
- **AWS Organizations and IAM Identity Center are not supported** on the new sign-up (same services page). This needs no doc change, because the design already keeps the account standalone.
- **The account uses AWS's new sign-up experience** (projects, AWS Settings, Builder ID). That experience assigns one home Region from the country in the contact address, and Turkey maps to **eu-north-1 (Stockholm)**. The Region can't be changed, and resources can't be created in other Regions ([AWS Regions for your projects](https://docs.aws.amazon.com/accounts/latest/reference/project-regions.html)). One exception: for Bedrock, "you can use APIs that do not create resources in all commercial AWS Regions that are enabled by default" (same page). So model calls may still go to Frankfurt.
- **Other Regions are unlocked only by "activate advanced features".** That requires upgrading to the Paid plan first and can't be reversed ([Activate advanced AWS features](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html)). Staying on the Free plan therefore means staying in Stockholm.
- **In this experience there is no root user**, because a Builder ID owns the account. **IAM users can be used only programmatically**, not in the console. The account sits in an AWS-managed organization automatically ([Compare sign-up options](https://docs.aws.amazon.com/accounts/latest/reference/sign-up-for-aws.html)). The CLI signs in with `aws login`, as an assumed `AccountFullAccessRole`.
- **Unconfirmed:** one forum report says a Free plan account had Bedrock quotas of 0 tokens and 0 requests per minute, even with credits. This is anecdotal, but it makes the week 1 quota check more important.

## What the account showed (measured 2026-10-17, read-only CLI checks)

| Check | Result |
|---|---|
| Nova Micro in eu-north-1 | `INFERENCE_PROFILE` only. A direct `amazon.nova-micro-v1:0` call is refused: "on-demand throughput isn't supported" |
| Nova Micro in eu-central-1 | The same refusal. Frankfurt offers no in-Region Nova Micro either |
| Nova Lite in eu-north-1 | `INFERENCE_PROFILE,ON_DEMAND`, so in-Region calls are possible |
| `eu.amazon.nova-micro-v1:0`, `eu.amazon.nova-lite-v1:0` profiles in eu-north-1 | `ACTIVE`. Calls weren't refused for access; they were throttled ("Too many tokens per day"). Whether the Free plan blocks the cross-Region routing itself shows only once quota exists |
| Bedrock quotas for Nova Micro and Nova Lite in eu-north-1 | **All inference quotas are 0**: on-demand and cross-Region, tokens and requests per minute, tokens per day. Adjustable in Service Quotas: cross-Region tokens per minute for Micro (`L-DC7FF66C`) and Lite (`L-7C42E72A`). Not adjustable, so they need a support case: tokens per day for Micro (`L-D2912E70`) and Lite (`L-45E0AD92`), cross-Region requests per minute for Micro (`L-3F110E0F`) and Lite (`L-89F8391A`), and on-demand tokens and requests per minute for Lite (`L-70423BF8`, `L-E386A278`) |
| AWS default values for those quotas | Far above 0: 400,000 cross-Region tokens per minute and 400 requests per minute per model; 200,000 on-demand tokens and 200 requests per minute for Lite; 5.76 billion tokens per day. So the account is held at 0 below the defaults, not given a low default. Service Quotas accepts requests only above the default ("You must provide a quota value greater than the default quota value of 400000.0"), so restoring the quotas takes a support case |
| AgentCore in eu-north-1 | The control plane responds for Runtime, Gateway, Memory, Identity (OAuth2 credential providers, workload identities), Policy engines and Evaluations (built-in evaluators listed). Nothing is created yet |

## (a) Account plan: Free instead of Paid

| # | Location | Current text | Replace with |
|---|---|---|---|
| a1 | Cost section, first paragraph | "The credits should cover this project for most or all of the first 12 months, but only on the Paid plan, and only if you stay on Amazon's own models and avoid the always-on services listed below." | "The account is on the Free plan, so credits pay for all usage and nothing is ever charged. The Free plan ends 6 months after sign-up, or earlier if the credits run out, so the project has 6 months on AWS. Stay on Amazon's own models and avoid the always-on services listed below." |
| a2 | "Why the Paid plan." paragraph | "The Free plan never charges you, but AWS's Free Tier page lists AgentCore as "Paid plan exclusive" (AWS Free Tier). The Free plan also closes the account after 6 months or when credits run out. On the Paid plan, credits are spent first and your card is charged only for usage beyond them." | Heading "Why the Free plan." Text: "The Free plan never charges you, and AWS's list of Free plan services includes Bedrock and AgentCore. It has two limits: no cross-Region features (see the model section), and the account closes after 6 months or when credits run out. Upgrading to the Paid plan later is possible, but you can't go back." |
| a3 | Build plan, week 1 | "Create the AWS account on the Paid plan, set the budget alerts, check Nova quotas in Frankfurt." | "Create the AWS account on the Free plan, set the budget alerts, check where Nova can be called in-Region (Stockholm or Frankfurt) and check its quotas." |
| a4 | Open questions, first bullet | "Does the AgentCore console let you create a runtime on the Paid plan, and do the first charges show as offset by credits?" | "Does the AgentCore console let you create a runtime on the Free plan, and do the first charges show as taken from credits?" |

**PRD:** NFR-COST-5 becomes "Free plan and standalone". OQ-1 is reworded the same way. In §14 week 1, "Paid plan" becomes "Free plan".
**If rejected:** the docs keep the Paid plan, which no longer matches your account.

## (b) Model calls: two routes, both blocked until the quotas are raised

The measurements leave two ways to call Nova on this account. Frankfurt adds nothing, because it has no in-Region Nova Micro either.

1. **Design route:** Nova Micro for classification and Nova Lite for chat, both through the `eu.` profile from Stockholm. This keeps the design unchanged, but only works if the Free plan's "no cross-Region features" rule doesn't block the routing once quota exists.
2. **Free plan route:** Nova Lite for everything, called in-Region in Stockholm, with no cross-Region inference. H1 already names Nova Lite for classification as its first fallback. At about 30 emails a day, the price difference is a fraction of a cent a month.

Request quota for both routes in one support case, asking AWS to lift the account's 0 values to the defaults or to modest values. Service Quotas can't do it, because it accepts only values above the default. Use route 1 if its call succeeds once the quota arrives; otherwise use route 2.

| # | Location | Current text | Replace with (route 2; with route 1 only b4 changes) |
|---|---|---|---|
| b1 | Components section, models paragraph | "Use Nova Micro for classifying emails and Nova Lite for chat and drafting, called through the eu. cross-region profile so data stays in the EU." | "Use Nova Lite for classifying emails, chat and drafting, called in-Region in Stockholm, because the Free plan does not support cross-Region inference and Nova Micro is offered in Stockholm only through a cross-Region profile. Data stays in the EU." |
| b2 | "How the model runs", step 2 | "with a model ID such as eu.amazon.nova-micro-v1:0" | "with an in-Region model ID: amazon.nova-lite-v1:0" |
| b3 | "How the model runs", step 3 | "The eu. prefix lets Bedrock run the request in any EU region with capacity and returns the answer." | "The request runs in Stockholm only. There is no failover to other EU Regions, so throughput is limited to Stockholm's quota." |
| b4 | Guardrails, quota bullet | "Check Service Quotas for Nova in Frankfurt; new accounts sometimes start near zero and need a support request" | "Check Service Quotas for Nova in Stockholm. This account started with every Nova inference quota at 0, and the tokens-per-day quotas can be raised only through a support case" |

**PRD (route 2):**
- CLS-1, H1 and the §5 hypotheses swap Nova Micro for Nova Lite in classification.
- NFR-PRIV-5 and TOOL-3 change from "the `eu.` inference profile" to "in-Region `amazon.nova-lite-v1:0` in eu-north-1". TOOL-3's IAM permission names that one model ARN.
- §16a's "Bedrock Nova quotas" row becomes a **blocker** until the quota requests are granted.

**If neither route works after the increase,** use a local model from the start (EV-6, MacBook mode), or activate advanced features (Paid plan, irreversible).
**If rejected:** the design keeps the `eu.` profile, which may not work on the Free plan.

## (c) 6 months on AWS, then upgrade or leave

| # | Location | Current text | Replace with |
|---|---|---|---|
| c1 | "When to switch." paragraph | "Switch when Billing → Credits shows less than $20 left, or in month 11, since credits expire at month 12. The $1 budget alert is the backstop: after the credits are gone, the first real charge triggers it." | "The Free plan closes the account at month 6, or earlier if credits run out. MacBook mode doesn't avoid that, because it still uses DynamoDB, SQS and Lambda in this account. By month 5, or when Billing → Credits shows less than $20 left: export the tracker to CSV on the Mac, then either upgrade to the Paid plan (credits then last to month 12, and KDV applies) or end the AWS deployment." |
| c2 | "Credit-saver mode on the MacBook", first paragraph | "AWS then costs about $0 to $1 a month, plus KDV." | "AWS usage then drops to about $0 to $1 a month of credits." |
| c3 | Cost section, "Month 13 onward." paragraph | "Credits expire, so the same usage becomes a real bill of roughly $2 to $8 plus KDV, unless you switch to credit-saver mode on the MacBook, which brings AWS close to $0." | "Month 7 onward. The Free plan account is closed unless you upgraded. After an upgrade, the remaining credits last to month 12, then the same usage becomes a real bill of roughly $2 to $8 plus KDV." |

**PRD:** §15's switch triggers change to month 5 or under $20 of credits, plus the tracker export. H4's "If wrong: stay in cloud mode after the credits end" becomes "upgrade to the Paid plan, or stop at month 6". The air-gapped mode (§4, deferred) becomes the only way to keep using the agent after month 6 without paying. §16 gets a risk: "Free plan account closes at month 6; data recoverable only by upgrading."
**If rejected:** the docs keep a 12-month horizon that the account can't reach.

## (d) Budgets count credit usage (applied in the code)

On the Free plan, credits cover everything, so a budget that subtracts credits always shows $0 and never fires. The $1, $5 and $10 budgets now exclude credits (`IncludeCredit: false`), so they alert on monthly credit burn. NFR-COST-1 is unchanged.

| # | Location | Current text | Replace with |
|---|---|---|---|
| d1 | Guardrails, first bullet | "AWS Budgets alerts at $1, $5 and $10 (free, and it earns one of the activity credits)" | "AWS Budgets alerts at $1, $5 and $10 of usage before credits, so they track credit burn (free, and it earns one of the activity credits)" |

**PRD:** §15's "the first real charge triggers the $1 budget alert" is replaced by c1.
**If rejected:** revert `cost_types` in `infra/always_free_stack.py`. The budgets would then never fire on the Free plan.

## (e) No KDV while on the Free plan

| # | Location | Current text | Replace with |
|---|---|---|---|
| e1 | Cost section, paragraph after the estimate table | "Credits are not applied against taxes, so in year one you pay the KDV on your usage: roughly $0.40 to $1.60 a month at the 20% standard rate (estimate; check the first invoice)." | "On the Free plan nothing is charged, so no KDV is due. KDV would start only after an upgrade to the Paid plan." |
| e2 | "Tax." paragraph | "Credits are not applied against taxes (AWS Promotional Credit Terms, section 7), so expect KDV on credit-covered usage too. Confirm on the first invoice." | "Applies only after an upgrade: Turkish accounts are billed in TRY by AWS Turkey with KDV added, and credits are not applied against taxes (AWS Promotional Credit Terms, section 7)." |
| e3 | Risks table, "Credits not covering everything", "Why it matters" | "KDV is not covered by credits; Marketplace model coverage is undocumented" | "Marketplace model coverage is undocumented; the Free plan blocks cross-Region inference" |

**PRD:** NFR-COST-6 and OQ-5 apply only after an upgrade. G6 and NFR-COST-2 change from "$ before KDV" to "$ of credits per month". M6 is unchanged.
**If rejected:** the docs keep a KDV cost that the Free plan never charges.

## (f) Home Region: Stockholm instead of Frankfurt (applied in the code)

The account's new sign-up experience pins all resources to eu-north-1 (see the research). `REGION` in `infra/config.py` is now `eu-north-1`. Model calls follow item (b).

| # | Location | Current text | Replace with |
|---|---|---|---|
| f1 | Overview, first paragraph | "in the Frankfurt region (eu-central-1)" | "in the Stockholm Region (eu-north-1), the home Region AWS assigns to accounts from Turkey under the new sign-up experience" |
| f2 | Guardrails, second bullet | "Check Service Quotas for Nova in Frankfurt; new accounts sometimes start near zero and need a support request" | "Check where Nova runs on demand (Stockholm or Frankfurt) and its Service Quotas there; new accounts sometimes start near zero and need a support request" |
| f3 | Guardrails, "Keep the account standalone" bullet | "Keep the account standalone; joining AWS Organizations ends credit eligibility" | "Don't join or create another AWS Organization. The new sign-up experience already places the account in an AWS-managed organization; confirm under Billing → Credits that the credits are applied" |

**PRD:**
- OPS-1 deploys to eu-north-1.
- NFR-COST-5 drops "outside AWS Organizations" and replaces it with the f3 wording.
- §16a: the AgentCore row becomes "Verified in Stockholm (control plane, 2026-10-17)". The "Lambda durable functions in Frankfurt" row becomes a **research target for Stockholm**, checked in week 4 when the approval flow is built.
- §17 and OQ-27 cite Frankfurt availability for durable functions; recheck them for Stockholm.
**If rejected:** the only way back to Frankfurt is upgrading to the Paid plan and activating advanced features, which is irreversible.

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Free plan instead of Paid | | | |
| (b) Model routes | | | Blocked until the Nova quotas are raised; then pick route 1 or 2 |
| (c) 6-month horizon | | | Put the account's sign-up date + 5 months in the calendar |
| (d) Budgets count credit usage | | | Already in the code |
| (e) No KDV on the Free plan | | | |
| (f) Home Region: Stockholm | | | Already in the code; AgentCore verified in Stockholm; durable functions checked in week 4 |
