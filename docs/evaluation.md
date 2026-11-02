# Evaluation

The full numbers behind the [README's summary](../README.md#results), and how each
was measured. Requirements and targets are in the
[PRD](../Job%20search%20inbox%20agent%20PRD.md), sections 11 and 12.

**Read these with this in mind:** classification and extraction were measured on
synthetic email, not on my real mail, which is likely messier.

## All metrics

Set version `ev1s-v1`. Each number has its date and n.

| # | Metric | Nova (cloud) | `qwen3.8:27b` (local) | Target | Met in cloud? |
|---|---|---|---|---|---|
| M1 | Interview-invite recall, synthetic test split | **100%** (14 of 14), 2026-11-01 | **100%** (14 of 14), 2026-11-01 | ≥ 96% and no new miss | Yes, in one run |
| M2 | Company, role and date all right, synthetic test split | **93.8%** (75 of 80), 2026-11-01 | **97.5%** (78 of 80), 2026-11-01 | ≥ 90% | Yes |
| M3 | Right tool and parameters, 40 chat questions | **82.5%**, 2026-10-31 | **100%**, 2026-10-31 | ≥ 95% | **No** |
| M4 | Planted injection emails with no unsafe action | **100%** (24 of 24), 2026-11-01 | **100%** (24 of 24), 2026-11-01 | 100% | Yes |
| M5 | Alert latency, end to end (Gmail receipt to publish) | 1.9 and 18.7 min (**n = 2**), 2026-10-22 to 10-31 | not run | p95 ≤ 35 min | On n = 2 only |
| M5 | Alert latency, pipeline (detection to publish) | 0.32 and 0.33 min (**n = 2**), same window | not run | p95 < 5 min | On n = 2 only |
| M6 | Model cost per real email | about **$0.00008** (n = 10), 2026-10-26 to 10-30 | no model charge | ≤ $0.002 | Yes |
| M6 | All usage ÷ emails, October to date | about **$0.0065** ($0.26 ÷ 40 emails), 2026-10-31 | not run | ≤ $0.002 | **No** |
| M6 | AWS usage for the month, before credits | **$0.26**, 2026-10-31 | not run | ≤ $8 | Yes |
| M7 | Minutes saved a week | **Not measured** | | ≥ 50% fewer | Not measured |
| M8 | Query coverage | **Not measured** | | 100% | Not measured |

## By class

On the synthetic test split, one live run each on 2026-11-01. Class accuracy: Nova 95.0%,
the local model 98.8%.

| Class | Labeled | Nova precision | Nova recall | Local precision | Local recall |
|---|---|---|---|---|---|
| Offer | 6 | 100% | 100% | 100% | 100% |
| Interview invite | 14 | 93.3% | 100% | 100% | 100% |
| Assessment | 10 | 100% | 100% | 100% | 100% |
| Rejection | 16 | 94.1% | 100% | 94.1% | 100% |
| Recruiter outreach | 14 | 100% | 78.6% | 100% | 100% |
| Other | 20 | 90.5% | 95.0% | 100% | 95.0% |

Invite recall by language: 7 of 7 Turkish and 7 of 7 English, for both models.

## What's wrong

- **Invite recall has a short record on this prompt.** Before 2026-11-01, Nova Micro
  read "not this role, but an interview for that one" as a rejection plus outreach, and
  missed 1 of 14 test invites (92.9%) and 3 of 10 dev invites. The classifier's prompt
  now says that such an email fits both classes. I tuned that on the dev split: two
  runs, 100% of 10 invites each, against 70% before. Then one run on test gave 14 of 14,
  with M2 up from 92.5% to 93.8% and M4 still 24 of 24.
- **The test split isn't blind to this fix.** The test miss (syn014) is what first showed
  the pattern, and it led to the harder dev emails the fix was tuned on. So 100% on test
  says less than the number suggests, and real invites of this kind are untested.
- **One false invite alert.** A recruiter email that mentions an interview (syn060) now
  reads as an invite. Recruiter outreach recall is 78.6%, also through emails read as
  "other".
- **M3 is 82.5%.** It stays a reported number, not a gate. In the same run, 80% of
  the answers held every expected fact. AgentCore Evaluations scored tool selection
  0.94 (33 tool calls) and correctness 0.75 (40 questions).
- **The local model scored at least as well as Nova on every set,** one run each, and
  higher on class accuracy, M2 and M3. It's a far larger model: about 4 s an email and
  9 s a question on my Mac, against Nova's fractions of a cent in the cloud. It can't
  replace Nova, because the worker that would process live email on the Mac isn't
  built. The same synthetic-set caveat applies to it.
- **All-in cost per email is over target** because October's bill includes this
  evaluation work and almost no mail volume: 40 emails in the month. The target
  assumed about 30 emails a day.

## How it was measured

- **The labeled set is synthetic.** A model wrote 120 emails (Turkish and English)
  from the class definitions, split and frozen at 80 test and 40 dev. Four harder
  dev emails came after the freeze; the test split didn't change. A cold subagent,
  which saw no labels, labeled the 120 again: classes agreed 120 of 120 on the first
  pass, and two field disagreements were rewritten. That labeler wasn't a separate
  person or session. Real accuracy may be lower.
- **The evaluation harness isn't the deployed Runtime.** It runs the real graphs,
  prompts and tool code with the mode's real model, in-process over fakes, with
  cedarpy authorizing each tool call.
- **Two tiers.** Tier 1 runs on GitHub's runners on every pull request and before
  every deploy, and *replays recorded model replies*, so it checks the code and
  prompts, not the model's drift. Tier 2 runs on my Mac against the live models,
  after any deploy that touches agent or eval code, and weekly.
- **The gate was tested** on a throwaway branch. A prompt without its manipulation
  rule failed Tier 1 on M4 ([run](https://github.com/EgeCankaya/job-search-agent/actions/runs/41194127147));
  one without the invite definition failed Tier 1
  ([run](https://github.com/EgeCankaya/job-search-agent/actions/runs/41195082257))
  and a manual Tier 2 ([run](https://github.com/EgeCankaya/job-search-agent/actions/runs/41195089255))
  on invite recall, with the invite gate switched on for the test. Neither deployed.
  At that time main failed the floor too, so the gate only reported from 2026-10-31
  until the prompt fix on 2026-11-01. It blocks again now, and main passes it in replay.
  Tier 2's M4 failure wasn't run live.
- **Nova varies at temperature 0**, so a single run is one sample: M3 was 87.5%
  when recorded and 82.5% live two days later on the same code, and on the current
  prompt M2 on test was 93.8% live and 95.0% in the replies Tier 1 replays.
- **The results come from two prompts.** M1, M2 and M4 are from the current prompt. M3
  didn't change, because the chat path doesn't use the classifier's prompt.
- **The chat suite was adjusted once.** After Nova's first recording, five questions
  got a second accepted set of filters, where the question itself states the filter.
  That raised Nova's recorded M3 from 75% to 87.5%.
- **M5 has n = 2.** Real alerts are rare and logs keep 7 days, so this covers 10
  days rather than the two weeks the PRD asks for, and with n < 20 the p95 is just
  the maximum. Excluded: a fictional test invite on 2026-10-19 (3.4 min). Polling
  every 30 minutes bounds the end-to-end figure.
- **M6: token counts are exact, prices are estimated** (Micro from a third-party
  price list, Lite from the US list price). The model cost per email uses the 3 days
  with no evaluation run. Cost Explorer is usage before credits, and it lags, so
  the latest evaluation runs may not be in the $0.26 yet.
- **M7 has no baseline.** I kept no time log before the agent. My own estimate is
  about 2 hours a week with it, which is an estimate and not a saving.
- **M8 and end-to-end recall aren't measured.** The set holds only email the query
  would return, so an invite the Gmail query never fetches can't show up as a miss.
- **The log scan skips fragments under 8 characters,** so short subjects and names
  aren't searched (13 of 45 on 2026-10-28).

## Run it

The first two call Nova or Ollama; replay calls nothing.

```bash
uv run --all-groups python eval/tier1.py
uv run --all-groups python eval/run_labeled.py --mode cloud --split test --pace 12
uv run --all-groups python eval/run_chat.py --mode local
uv run --all-groups python eval/run_injection.py --mode local
uv run --all-groups python eval/ops_report.py --days 7
```
