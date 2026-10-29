# Design doc edits for OQ-47: building weeks 6 to 7, evaluation and write-up

**For:** the PRD v0.4.6 and the design doc (Claude Doc, revision 116)
**What triggered it:** week 5 met all three exits on 2026-10-25, and weeks 6 to 7 start. The PRD (§11, §12, §14) and the design doc name the parts but leave the choices below open: the labeled set (EV-1) and its split, the synthetic set (EV-1s), the chat suite (EV-3) and AgentCore Evaluations, the two CI tiers, OpenTelemetry (NFR-OBS-1), latency and cost from CloudWatch (M5, M6), the local baseline (EV-6), and the README. One question is already known to be hard: **Tier 1 runs on GitHub's runners, which have no AWS credential (OQ-43) and no Ollama, so as built it can't call a model** (the OQ-45 (f) note).
**Status:** **accepted on 2026-10-26** as recommended (my words: "Do what you think is best, if not sure ask me"): (a) to (m) and (p); (h) as (h1); (n) for its first part (noreply from now on, the history scan before going public), with (n1) or (n2) decided when the repo goes public; (o) on condition that each package's source is checked before `uv add`. My answers: no emails are labeled yet (d), and no time log from before the agent exists (j), so M7 reports the with-agent minutes only. **Applied on 2026-10-26** to the PRD (v0.4.7, with week 5's records) and the source Claude Doc (revision 116 → 120: the rows of (a), (g), (h) and (k)); each design-doc edit was read back. The PDF in this folder is still the revision 90 export. Step 1's labeling tools are built.
**Depends on:** OQ-8, OQ-18, OQ-19, OQ-29, OQ-32 (g), OQ-37, OQ-42 (k), (m), OQ-43, OQ-45 (f), (f').
**Plan:** `Week 6 plan.md` shows where each item lands.

Every item gives a recommendation and the main alternative. "No change" means the PRD or the design doc already says it. Two items needed a fact only I know; answered 2026-10-26: no emails are labeled yet (d), and no time log from before the agent exists (j).

## Not checked yet (my `aws login` session had expired on 2026-10-26)

Read-only checks for step 0, once I sign in:

| Check | Why |
|---|---|
| Policy simulator with `aws:RequestedRegion` = eu-north-1: `bedrock-agentcore:Evaluate`, `ListEvaluators`, `GetEvaluator`; `logs:StartQuery`, `GetQueryResults`; `ce:GetCostAndUsage`; `cloudwatch:GetMetricData` | The SCP has surprised us before (OQ-43) |
| `list-evaluators` in eu-north-1 | The exact built-in IDs (the docs name `Builtin.ToolSelectionAccuracy`, `Builtin.Correctness`, `Builtin.Helpfulness`, `Builtin.GoalSuccessRate`) |
| `xray get-trace-segment-destination` | Whether CloudWatch Transaction Search is on (item (g) may need it; turning it on is an account setting, mine to change) |
| AgentCore Evaluations pricing page | The design doc's $1 to $3 a month is an estimate; each `Evaluate` result reports its judge tokens |
| The Nova quotas | Tier 2 shares them with live mail (item (c)) |

Found by reading (2026-10-26):
- The installed boto3 (1.43.114) has `bedrock-agentcore` `Evaluate` (synchronous; takes OpenTelemetry spans as `sessionSpans`, with an optional `expectedTrajectory` of tool names and `expectedResponse`) and batch evaluations that read spans from CloudWatch Logs. No new AWS SDK is needed.
- AWS's docs list LangGraph (Python) as supported through `opentelemetry-instrumentation-langchain` (≥ 0.55.0) or `openinference-instrumentation-langchain` (≥ 0.1.62). Their getting-started page assumes the agent exports its spans to CloudWatch (ADOT plus Transaction Search). **Whether `Evaluate` accepts spans captured in memory on the Mac, without CloudWatch, is not documented.** Item (g) tests it before relying on it.
- Those instrumentation libraries put prompts and replies in span attributes. Inside the deployed Runtime the prompts are email text, so NFR-PRIV-3 and NFR-OBS-3 rule that out (items (g), (h)).

## (a) Tier 1 gets its model replies by replay, recorded on the Mac

Tier 1 must run on GitHub's runners and on pull requests (§12), with no AWS credential (OQ-43, NFR-SEC-3). A GitHub runner (4 vCPU, 16 GB) can't hold `qwen3.8:27b` (18 GB). So Tier 1 can't call either mode's model.

**Recommendation: record and replay.**
- `eval/record.py`, run by me on the Mac with my `aws login` session, runs EV-1s, EV-3 and EV-4 through the cloud-mode models (Nova Micro and Lite) and saves every model reply under `eval/replies/<set>/`. Each reply is keyed by a SHA-256 of the model ID, the system prompt and the prompt (for the chat path: the messages and the tool specs). The harnesses fix "today", so the keys are stable.
- Tier 1 runs the same real graph code with a replay model that answers from those files. **A missing key fails the job** ("replies are stale: re-record on the Mac"). So any change to a prompt, the trimming, a tool description or a model ID can't pass Tier 1 until I re-record, and the re-recorded replies are then scored like any others.
- The files hold only model replies to fictional emails and questions. They are committed, so a reviewer can see what each score was computed from. A test checks they hold no address outside `example.com` and its subdomains.
- What it doesn't show: a change in the model itself (Nova updates, the variation at temperature 0). Tier 2 calls the live models weekly and on main (item (c)).

**Alternatives:**
- **(a2) A Tier 1 job on the self-hosted runner, on `push` only.** The real model every time, no fixtures. Only accounts with write access can push, so code from forks never runs there. But it needs the Mac awake and a fresh `aws login` for every branch push, pull requests get no model scores, and §12 and the design doc say Tier 1 runs on GitHub's runners. I'd take this if keeping recordings current turns out to be a chore.
- Rejected: an AWS credential in GitHub (OQ-43, NFR-SEC-3); a small CPU model on the GitHub runner (it scores a model neither mode uses, and needs an install there).

| PRD section | Change |
|---|---|
| §12 CI gate, Tier 1 row, Sets | add: "with model replies recorded on the Mac and replayed; a missing or stale reply fails the tier [OQ-47 (a)]" |
| §12, after the table | add: "GitHub's runners have no AWS credential and no local model, so Tier 1 replays replies that I record with the cloud-mode models on the Mac. A change to a prompt or a model ID fails Tier 1 until the replies are re-recorded. Model drift shows in Tier 2 [OQ-47 (a)]" |

| Design doc place | Change |
|---|---|
| Evaluation section, the two-tier paragraph | "Tier 1 runs on every change on GitHub's runners, with synthetic emails, the injection set and the chat suite." → add: "GitHub's runners have no AWS credential and no local model, so Tier 1 replays model replies recorded on the Mac; a prompt change needs a fresh recording." |

## (b) What Tier 1 runs, and where it sits

**Recommendation:**
- Tier 1 joins `check.yml` as one more step after the unit tests: EV-1s (invite recall, per-class precision and recall, M2), EV-4 (M4, through the existing harness with the replay model) and EV-3 (M3), all from replies.
- It fails on: invite recall on EV-1s below 96% (with about 10 invites, any miss), M4 below 100%, or a stale reply. M2 and M3 are reported and become gates under EVAL-2's rule (2 Tier 2 runs at or above the floor).
- It runs on every pull request and every deploy, not only when agent, prompt, tool or eval code changes. Replay takes seconds, a path filter would be one more thing to get wrong, and the deploy is then always gated by Tier 1. This retires OQ-42 (m)'s stop-gap (unit tests and synth only).
- The existing tests with scripted compromised models stay: they show that the code and policy layers catch NFR-SEC-2 (a) to (c) whatever the model says.

**Alternative:** a separate `tier1.yml` with the §12 path filter. That's closer to the PRD's wording, but the deploy would then also need it as a `needs`.

| PRD section | Change |
|---|---|
| §12 Tier 1 row, Trigger | "Every push and pull request that touches agent, prompt, tool or eval code" → "Every pull request, and every push to main as the deploy's first job [OQ-47 (b)]" |
| §12 Deploy bullet, OPS-1 | "Until Tier 1 exists (week 6), the unit tests and `cdk synth` gate the deploy" → "Tier 1 runs in the same check as the unit tests and `cdk synth` [OQ-47 (b)]" |

Design doc: no change.

## (c) Tier 2: when it runs, how it paces, and what leaves the Mac

**Recommendation:**
- A `tier2.yml` on `[self-hosted, macOS]`, with my `aws login` session, triggered by: a call from `deploy.yml` after a successful deploy when the push touched `src/`, `eval/` or `policies/`; a weekly schedule (Monday 07:00 UTC); and a manual run. **Never `pull_request`** (a test enforces it, as for the deploy).
- **After the deploy, not before**, as OPS-1 reads now: Tier 1's replay already shows a prompt change's scores before it deploys, and a Tier 2 run takes about 25 minutes. A red Tier 2 turns main red until I revert (EVAL-1).
- Sets: the EV-1 test split (M1 with the "no new miss" rule, M2, M8), EV-3 with AgentCore Evaluations (item (g)), and EV-4 re-run with the live Nova models.
- **Pacing:** the Nova quotas are account-wide (20 requests a minute for cross-Region Micro and Lite), and live mail uses them too. Tier 2 caps itself at 12 model calls a minute per model, so a throttled poll can't push live mail toward the dead-letter queue.
- **Where the private set lives:** outside the checkout, which each job wipes: `$HOME/inbox-agent-eval/` (`EVAL_DATA_DIR`, set in the runner's `.env`). FileVault on.
- **What leaves the Mac:** a scores file uploaded as an artifact, plus the job summary: per-class counts, metric values, the set version, and the **eval-set IDs** (`ev1-0137`) of missed emails, never Gmail message IDs, subjects or senders. On a public repo anyone signed in can download artifacts. The ID-to-message mapping stays on the Mac.
- **"No new miss":** each main run's caught invites are saved on the Mac (`runs/main-latest.json`). A run on another branch compares against it but never replaces it.
- An expired `aws login` fails the job at a first "session" step with its own message, so it's never mistaken for a gate failure.

**Alternative:** Tier 2 before the deploy (`deploy` needs both tiers). A regressed prompt then never goes live, but every code push waits about 25 minutes, and OPS-1 changes.

| PRD section | Change |
|---|---|
| §12 Tier 2 row, Trigger | → "After a deploy from main that changes `src/`, `eval/` or `policies/`; weekly; manual. **Never on pull_request**" |
| §12 Results bullet | "the IDs of missed emails" → "the eval-set IDs of missed emails (never Gmail IDs or text) [OQ-47 (c)]" |
| §12, a new bullet | "**Pacing:** Tier 2 stays under 12 model calls a minute per model, because live mail shares the account's Nova quotas [OQ-47 (c)]" |

Design doc: no change (it already says "on pushes to main and weekly, never on pull requests, and uploads only scores").

## (d) EV-1: how emails are fetched, stored and labeled

The §12 sampling rule stays: for each date window, every email the query returns, plus every job email I find by reading the whole inbox for that window myself.

**Recommendation:**
- `eval/fetch_labeling.py`, run by me: for a window (`--after`, `--before`) it runs the ING-1 query with that window, and fetches the job emails I found by reading, which I mark with the Gmail label `eval-manual` (Gmail's web addresses don't show the API's message IDs; build detail, 2026-10-26). It saves each as raw `.eml` with `found_by` = `query` or `manual` under `EVAL_DATA_DIR/emails/`, using the read token as `inbox-agent-poller`, as `scan_logs.py` does. It prints counts only.
- `eval/label_app.py`: a small Streamlit page on 127.0.0.1, separate from the Mac app (which keeps its three pages). For each email it shows the trimmed text the model sees, and the full text on request. I enter the class (CLS-1's definitions and precedence), company, role, key date and language, plus a note. It writes `EVAL_DATA_DIR/labels.jsonl`.
- **No model pre-fill:** labels pre-filled by Nova would pull my labels toward Nova's answers, and the test would then partly measure agreement with itself.
- Windows: back-fill past months until the set has 250 to 300 emails and at least 25 invites, if my mail holds that many. If it doesn't, I report what exists; the §11 small-numbers note applies.
- About 40 seconds an email, so about 3 to 4 hours of my time for 300.

**Answered (2026-10-26):** no emails are labeled yet, so the set starts from zero.

**Alternative:** labeling in a spreadsheet from a CSV the fetch script writes. No new page, but the email text has to be read somewhere else, and a CSV of email text is easy to leave lying around.

| PRD section | Change |
|---|---|
| §12 Process, step 1 | add: "Emails are fetched by a script with the read grant and stored as files on the Mac; labels are entered on a local labeling page with no model pre-fill [OQ-47 (d)]" |

Design doc: no change.

## (d') Amendment: no manual read of the inbox (decided 2026-10-28)

When the first fetch ran (September: 59 emails from the query), I chose not to read each window's whole inbox by hand: too much work for the result.

**Decision:** the labeled set holds only what the ING-1 query returns.
- M1 (per class, per language) and M2 are measured on those emails, as before.
- **M8 (query coverage) and end-to-end invite recall are not measured:** a set built from the query's own hits can't show what the query misses. The README says so.
- `fetch_labeling.py` searches the query only; `--manual-label` stays as an option, off unless given.

**Alternatives not chosen:** a wider automatic search (job words anywhere in the email) as the stand-in for my read, which would measure M8 against that net; or a hand-read sample of one or two weeks.

| PRD section | Change |
|---|---|
| §12 Process step 1, sampling rule | (b), the manual read, is dropped |
| §11 M1 | end-to-end invite recall: not measured |
| §11 M8 | not measured |
| §14 weeks 6 to 7 exit | "M1 to M8 have measured values" → "M1 to M7 …; M8 is not measured" |
| §18 OQ-37 | Resolved: query hits only |

Applied to PRD v0.4.8 on 2026-10-28. Design doc: no change.

## (d'') Amendment: no labeled set of my own email (decided 2026-10-29)

After the first fetch I chose not to label my own email at all. The options were: skip it; let Claude label it in a session (my email would then go to Anthropic as well as AWS, against NFR-PRIV-6); or a stronger Bedrock model labels it (accuracy would then mean agreement with that model).

**Decision: skip it.** EV-1 is dropped, and the synthetic set becomes the labeled set:
- **EV-1s grows to 120 emails** (60 Turkish, 60 English; about 20 interview invites; all six classes; hard cases such as an invite inside a rejection thread, a test link with no word "assessment", long emails whose key fact falls after the 2,000-character cut).
- It gets the split and freeze of item (e): ⅓ dev, ⅔ test, stratified by class × language, frozen at the first full run. Prompts are tuned on dev only.
- **Who writes and labels it:** a session drafts each email from CLS-1's definitions, with no real mail and no pattern notes from me (I declined them, item (f'') below), and labels it by construction. **A second, independent session labels the same emails blind;** an email the two disagree on is rewritten until it's unambiguous, or dropped. The agreement rate is reported.
- **M1 and M2 are measured on EV-1s's test split, in both tiers:** Tier 1 from replies, Tier 2 against the live models. The "no new miss" rule applies to it.
- **What this can't show:** how the classifier does on my real mail. Synthetic emails written by a model are likely easier and more regular than real ones. The README says plainly that the accuracy numbers come from synthetic email, and the CV line says "on a synthetic test set", not "on N labeled emails".
- The live system's own counts (emails processed, alerts sent, "Possible:" alerts) are reported as operating numbers, with no accuracy claim.
- The labeling tools from step 1 (`eval/labeled_set.py`, `fetch_labeling.py`, `label_app.py`) are kept in the repo, unused. The 59 September emails fetched on 2026-10-28 sit in `~/inbox-agent-eval` on my Mac; deleting them is mine.
- NFR-PRIV-6 no longer has anything to govern: no real email is in any evaluation set.

**Alternatives not chosen:** Claude labels my email (a new processor for my email, and NFR-PRIV-6 changes); a Bedrock model labels it (circular accuracy).

## (f'') Amendment: EV-1s needs nothing from me (decided 2026-10-29)

I declined to write pattern notes or review the synthetic emails. The drafting session works from CLS-1's definitions and general knowledge of job-search email, never from my mail, and the blind second labeling in (d'') replaces my review.

## (j') Amendment: M7 is my estimate (2026-10-29)

I won't keep a time log. My estimate of tracking time with the agent is **about 2 hours a week**. M7 is reported as "not measured: no baseline, and the with-agent figure is my estimate of about 2 hours a week", never as a saving.

| PRD section | Change, for (d''), (f''), (j') |
|---|---|
| §12 Test sets | EV-1: dropped. EV-1s: 120 emails, the split and freeze, drafted and labeled by a session, checked by a blind second labeling. EV-2: EV-1s only. EV-6: EV-1s's test split and EV-3 |
| §12 Process | steps 1 to 2 apply to EV-1s; the sampling rule no longer applies |
| §12 Tier 2 row, Sets | "EV-1 test split" → "EV-1s's test split against the live models" |
| §11 M1, M2 | measured on EV-1s's test split (synthetic) |
| §11 M7 | my estimate, not a measurement |
| §10 NFR-PRIV-6 | no real email is in an evaluation set |
| §14 weeks 6 to 7 | "250 to 300 labeled emails finished" → "EV-1s (120 synthetic emails) finished and frozen" |
| §15 step 1 | "the EV-1 test split" → "EV-1s's test split" |
| §18 OQ-29 | the split applies to EV-1s |

Applied on 2026-10-29: PRD v0.4.9 for (d''), (f''), (j'), and the source Claude Doc revision 120 → 131 for (d'') (ten places, each read back).

| Design doc place | Change |
|---|---|
| The CV-gaps paragraph | "precision and recall on emails you label yourself" → "precision and recall on a frozen synthetic test set" |
| The headline-number paragraph | → recall on a frozen synthetic test set of 120 emails written from the class definitions, labeled twice, with real email kept out of the evaluation sets |
| The evaluation table, classification row | "Labeled set of 250 to 300 emails, run through the classifier" → "120 synthetic labeled emails (a frozen test split), run through the classifier" |
| The two-tier paragraph | "Tier 2 runs the private labeled set" → "Tier 2 runs the same sets against the live models" |
| The CV line | "on 300 labeled emails" → "on a synthetic test set" |
| The build plan, weeks 6 to 7 | "Finish the 250 to 300 labeled emails" → "Write the 120 synthetic labeled emails" |
| Second evaluation baseline | "Run the 300 labeled emails" → "Run the synthetic test set and the chat suite" |
| Private storage | "Keep the raw labeled emails on the Mac only" → "Keep real email out of the evaluation sets: they are synthetic" |
| Before you switch | "Run the labeled email set on the local model" → "Run the synthetic test set on the local model" |
| Risks, "Too few labeled interview invites" | fix → "Write about 20 invites into the synthetic set; report n" |

## (e) The split, the freeze and the scoring rules

**Recommendation:**
- **Split:** ⅓ dev, ⅔ test, stratified by class × language, with a fixed seed. A manifest (set version, eval-set ID, split, SHA-256 of each email's label line) lives on the Mac; only the version string and counts appear in scores.
- **Freeze** at the first full run (week 6). New emails go to dev. Changing the test split makes a new set version, and results on different versions are never compared (§12, OQ-29).
- **M1:** class after precedence; per-class precision and recall, per language; invite recall is the headline. End-to-end invite recall (the query and the classifier together) counts `manual` invites the query missed as misses.
- **M2:** an email counts only if company, role and date are all right:
  - company: equal after TRK-1 normalization (aliases applied);
  - role: equal after the TRK-1 role key;
  - date: the same day, and the same time when the label has one;
  - a field labeled "none" is right only if the model also says none.
- **M8:** the query matcher (`query.py`) run over the labeled emails' headers: invites, assessments and offers it would return ÷ all of them.

**Alternative:** stratify by class only (§12 as written). Simpler, but the Turkish invites could all land in dev.

| PRD section | Change |
|---|---|
| §12 Test sets, EV-1 row | "stratified by class" → "stratified by class and language [OQ-47 (e)]" |
| §11 M2 row, How measured | add: "company and role compared after TRK-1 normalization; date to the day, and the time when labeled [OQ-47 (e)]" |

Design doc: no change.

## (f) EV-1s: who writes the 60 synthetic emails

§12 says "Emails I write myself from the patterns in the real set". Sixty emails is about 3 hours of writing.

**Recommendation:** a session drafts them, and I review each and own the labels.
- The session works only from CLS-1's class definitions, the precedence rules, and pattern notes I write (for example "ATS invite with a scheduling link", "Turkish rejection that thanks you and keeps your CV"). **It never reads my real mail or the labeled set.**
- 60 emails, 30 Turkish and 30 English, all six classes, about 10 invites, plus a few hard cases (an invite inside a rejection thread, an assessment link with no word "assessment").
- Fictional companies, `example.com` addresses, `+90 555 …` numbers; a schema test like EV-4's.
- I accept, edit or drop each email, and set its labels.

**Alternative:** I write all 60 myself, as §12 says now.

| PRD section | Change |
|---|---|
| §12 Test sets, EV-1s row, Content | "Emails I write myself from the patterns in the real set" → "Emails drafted by a session from the class definitions and my pattern notes (never from real mail), each reviewed and labeled by me [OQ-47 (f)]" |

Design doc: no change.

## (g) EV-3 and M3: a scored chat suite, with AgentCore Evaluations alongside

The deployed Runtime's chat path reads the real tracker, so its answers have no fixed truth, and calling it records memory events. EV-3 can't run against it.

**Recommendation:**
- `eval/chat/`: 40 questions, Turkish and English, against a seeded fictional tracker of about 15 rows (fakes, as in the injection harness). Each question lists the expected `list_applications` calls (status, company, `min_days_since_contact`), or none, and the facts the answer must contain.
- `eval/run_chat.py` runs the **real chat graph in-process** with the mode's chat model.
- **M3 is scored in code:** a question is right when its tool calls equal the expected ones (order ignored, company compared after normalization). The answer check (expected counts and names present) is reported beside it. Both are deterministic, so they can gate under EVAL-2.
- **AgentCore Evaluations, Tier 2 only:** the in-process run is instrumented with one OpenTelemetry LangChain instrumentation library (item (o)), and its spans go to `Evaluate` with `Builtin.ToolSelectionAccuracy` (with `expectedTrajectory`) and `Builtin.Correctness` (with `expectedResponse`). Their scores are reported next to M3, not gated, because the judge is a model. The spans hold only the fictional suite.
- **First, a one-question spike:** does `Evaluate` accept spans captured in memory on the Mac?
  - If yes, that's the route: nothing goes to CloudWatch.
  - If no, **(g2):** the eval run exports its spans to CloudWatch with ADOT from the Mac (they're fictional). That needs Transaction Search on, which is an account setting I change, plus `aws-opentelemetry-distro`.
  - If that fails too, **(g3):** M3 is scored in code only, and the README says AgentCore Evaluations couldn't score an agent outside the Runtime.
- No instrumentation goes into the deployed Runtime or the Lambdas (item (h)).

**Alternative:** score M3 only with AgentCore Evaluations, as the design doc's table says ("scored with AgentCore Evaluations"). A judge model's score then gates the build, and Tier 1 has no M3.

| PRD section | Change |
|---|---|
| §11 M3 row, How measured | → "40 scripted questions on a seeded fictional tracker, run in-process; tool and parameters scored in code; AgentCore Evaluations' tool-selection and correctness scores reported alongside in Tier 2 [OQ-47 (g)]" |
| §12 Tier 2 row, Sets | "EV-3 with AgentCore Evaluations (cloud mode)" → add: "(the in-process run's spans; fictional data only)" |

| Design doc place | Change |
|---|---|
| Evaluation table, chat row | "30 to 50 scripted chat questions, scored with AgentCore Evaluations" → "30 to 50 scripted chat questions on a fictional tracker, scored in code for tool and parameters, and with AgentCore Evaluations" |

## (h) OpenTelemetry (NFR-OBS-1): log fields for the pipeline, OpenTelemetry for the eval run only

NFR-OBS-1 says each email's path "SHALL produce OpenTelemetry spans in CloudWatch", carrying the receipt and detection times; until week 6 they are log fields (OQ-42 (k)). Those fields already gave week 3's latencies (receipt → alert 3.4 min, detection → alert 0.3 min).

What spans in the live path would take:
- ADOT in the Runtime and an ADOT Lambda layer in the poll, consumer and tool functions (new dependencies);
- Transaction Search on (an account-level setting);
- a check that no span attribute holds email text. LangChain's auto-instrumentation records prompts, and in the batch path the prompt is the email.

**Recommendation (h1):**
- NFR-OBS-1 is met by the structured log fields, with the Gmail message ID as the correlation key across poll, consumer and Runtime, and M5 is computed with Logs Insights (item (i)). No ADOT in the live path.
- OpenTelemetry is used where it adds something: the instrumented eval run behind AgentCore Evaluations (item (g)).
- The README says so plainly.

**Alternative (h2):** content-free spans in the live path: hand-made spans (`opentelemetry-sdk` and the ADOT exporter, no auto-instrumentation) in the poll, consumer and Runtime, Transaction Search on, and the canary test extended to span attributes. That is real tracing for the CV, at the cost of new dependencies in three bundles, an account setting, a new privacy surface, and about a day.

| PRD section | Change (h1) |
|---|---|
| NFR-OBS-1 | → "Each email's path (poll → queue → classify → tracker → alert) SHALL log structured events that carry the Gmail receipt time, the detection time and the message ID, so that NFR-LAT-1 and NFR-LAT-2 can be computed. OpenTelemetry spans are used for the evaluation run (EV-3), not the live path, because LangChain instrumentation records prompts, which are email text there [OQ-47 (h)]" |
| §11 M5 row, How measured | "CloudWatch traces" → "CloudWatch Logs Insights over the structured log fields" |

| Design doc place | Change (h1) |
|---|---|
| Components, "CloudWatch + AgentCore Evaluations" row | "Traces, latency, token use, and scored test runs" → "Structured logs for latency and token use; traced and scored evaluation runs" |

## (i) M5 and M6 from CloudWatch and billing

**Recommendation:** `eval/ops_report.py`, run by me, read-only, counts only:
- **M5:**
  - Logs Insights over the Runtime's and the consumer's log groups for the reporting window (weeks 2 to 7, as far back as 7-day retention allows). So I run it weekly and keep each week's output on the Mac.
  - Reported: n, p50, p95 and max for end-to-end (receipt → publish) and pipeline (detection → publish). **When n < 20, p95 is reported as the maximum and says so.**
  - The same for every processed email (detection → handled), which has a much larger n.
- **M6:**
  - model tokens from the run log fields (exact) and the Bedrock CloudWatch metrics as a cross-check;
  - AgentCore and other usage from Cost Explorer (`GetCostAndUsage` by service, credits not netted; a few requests at $0.01 each);
  - emails processed from the processed-email log.
  - Two numbers: model cost per email, and all-in usage per email for the month.
- The script never prints a subject, a sender or a company.

**Alternative:** I read Cost Explorer in the console and type the monthly totals into the script. That avoids the API calls, but the numbers can't be reproduced.

| PRD section | Change |
|---|---|
| §11 M5 row | add: "n reported; with n < 20 the p95 is the maximum [OQ-47 (i)]" |
| §11 M6 row | add: "reported both as model cost per email and as all-in usage per email [OQ-47 (i)]" |

Design doc: no change.

## (j) M7 without a time log from before the agent

M7 compares tracking minutes against "my time log for at least 1 week before the agent". The week 1 to 2 baseline is still listed as open, and the agent has handled my mail since week 2, so a "before" week can't be measured any more.

**Answered (2026-10-26):** no such log exists.

**Recommendation:** log one week of my tracking minutes with the agent (week 7), and report M7 as "baseline not recorded; N minutes a week with the agent". No estimate after the fact.

**Alternative:** a retrospective estimate of the before-agent minutes, labeled as an estimate. That's easy to argue with, and the README promises measured numbers only.

| PRD section | Change |
|---|---|
| §11 M7 row | add: "the baseline was not recorded; M7 reports the with-agent minutes only [OQ-47 (j)]" |

Design doc: no change.

## (k) EV-6, the local baseline

**Recommendation:**
- Once, in week 7, on the Mac: the EV-1 test split and EV-3 through `qwen3.8:27b`, with the same scorers as Nova.
- Both results go into the README. It isn't a gate.
- It also serves as §15's before-switch check (OQ-23).
- EV-4 local was already measured in week 5 (24/24).
- No local judge model: M3's in-code scoring makes one unnecessary.

**Alternative:** score EV-3 locally with a local judge model, as the design doc's mode table says ("local judge model"). Not needed when the score is computed in code.

| Design doc place | Change |
|---|---|
| Mode table, Evaluations row, MacBook column | "The same test sets, run locally with a local judge model" → "The same test sets, run locally, scored in code" |

PRD: no change.

## (l) The gate check (the weeks 6 to 7 exit)

The exit says: "Both tiers fail on a deliberately weakened change and pass on main". §12 adds: "the same change merged to a throwaway main fails Tier 2". A real main deploys, so the weakened change must never land there.

**Recommendation:**
- **Two known weak changes:**
  - (1) the classifier prompt before `ac98e12` (without the manipulation rule), which scored M4 18/24 with Nova Micro in week 5;
  - (2) a prompt with the interview-invite definition removed, for invite recall.
- **Tier 1:** a branch `gate-check` with change (1) and replies re-recorded for it fails Tier 1 on M4. The same with (2) fails on invite recall. Without the re-recording, it fails as stale; that's noted, but it isn't the proof.
- **Tier 2:** a manual run of `tier2.yml` on `gate-check` stands in for the "throwaway main": the deploy job runs only for `refs/heads/main`, so nothing deploys. Its scores are compared against the saved main state, which the run never replaces.
- Then a run on main passes both tiers. The branch is deleted.

**Alternative:** a temporary second repository as the throwaway main. More faithful to the wording, but it needs its own runner and session, and the result is less visible.

| PRD section | Change |
|---|---|
| §12, "Check that the gate works" | → "…a deliberately weakened prompt on a test branch fails Tier 1 (with its replies re-recorded). A manual Tier 2 run on that branch fails Tier 2; the deploy runs only for main [OQ-47 (l)]" |
| EVAL-1 acceptance, first bullet | the same wording |

Design doc: no change.

## (m) The README and the screenshots

**Recommendation:**
- **Numbers:** only measured ones, each with its date, set version, n and "how measured". Placeholders are removed, not kept as targets.
- **How measured** says, at least:
  - the harness isn't the deployed Runtime (OQ-45 (f));
  - token counts are exact and prices are estimated;
  - `NoRecipientKeys` is inert on the Gateway, and each tool's schema check refuses recipient keys there (OQ-46);
  - the log scan skips fragments under 8 characters;
  - Tier 1 replays recorded replies (item (a));
  - M5's small n (item (i)) and M7's missing baseline (item (j)).
- **Architecture diagram:** Mermaid in the README. GitHub renders it, it's diffable, and no image tool is needed.
- **Screenshots** of the Dashboard, Ask and Follow-ups pages with fictional data only. `mac_app/demo.py` starts the same pages with the tests' fake backend and a fictional tracker, on 127.0.0.1. The app itself doesn't change, and nothing is written to AWS. PNGs go in `docs/`.
- I check every screenshot for real data before it's committed.

**Alternative:** screenshots of the real app with real rows blurred. That's risky (a missed row is public), and it means editing images by hand.

PRD and design doc: no change (§14 already asks for "numbers, an architecture diagram and Mac app screenshots").

## (n) Before the repo goes public

The repo is private (since 2026-10-18), while CLAUDE.md and the PRD call it public. The write-up is for reviewers, so it goes public at the end of week 7, or when I decide. Two things to settle first:
- **Every commit's author email is my personal Gmail.** That's git metadata, not file content, and it becomes public with the repo.
- **The Actions logs become public.** The deploy masks the account ID and the alert address; older runs should be checked.

**Recommendation:**
- From now on, commit as my GitHub noreply address.
- Before going public, a session scans the whole history (every blob, every commit message) and the Actions logs for 12-digit numbers, addresses outside `example.com`, phone numbers and the alert address, and reports counts and locations.
- Then **I pick one:**
  - (n1) rewrite the authors with `git filter-repo --mailmap`. That changes every SHA, so the session updates the SHAs quoted in CLAUDE.md, the plans, the OQ files and the PRD from filter-repo's commit map, in the same step. It needs `git-filter-repo` (Homebrew), and a force-push to main, which deploys.
  - (n2) keep the history and accept that the Gmail shows on the old commits.
- Flipping the visibility is mine.

**Alternative:** a fresh public repo with one squashed commit. It's clean, but it loses the Actions history and the commit trail that OPS-1's evidence points at.

PRD and design doc: no change.

## (o) New dependencies

| Group | Package | Where | Why |
|---|---|---|---|
| `eval` (new group, the Mac and Tier 2 only; not in the Runtime or Lambda bundles) | `opentelemetry-sdk`, and one of `openinference-instrumentation-langchain` (≥ 0.1.62) or `opentelemetry-instrumentation-langchain` (≥ 0.55.0) | The EV-3 run behind AgentCore Evaluations (item (g)) | The spans `Evaluate` reads |
| `eval`, only if (g2) | `aws-opentelemetry-distro` | Exporting the eval run's spans to CloudWatch | The documented route |
| Homebrew, only if (n1) | `git-filter-repo` | The author rewrite | — |

- Each is source-checked before approval, as cedarpy was.
- Versions at least two weeks old, pinned in `uv.lock`.
- Nothing is added until I approve it.
- Tier 1 needs none of them: replay has no model library to call.

## (p) The documents

**Recommendation:**
- **Now:** PRD v0.4.7 takes the accepted rows above. The design doc's revision after 116 takes the accepted design-doc rows ((a), (g), (h), (k)), each read back. The PDF stays the revision 90 export until I re-export it.
- **After the first full run (week 6):** PRD v0.5.0 confirms or revises the §11 targets and does §18's confirmation pass (each Provisional answer becomes Resolved, or is changed), as §12 step 4 and the §14 exit ask.
- **The milestone row** in §14 for weeks 6 to 7 gains the accepted items, as week 5's did.
- The week 5 PRD records (M4 in both modes, OQ-46's ALR-3 reading), which the head session lists as the week 5 wrap-up, land first, so only one writer edits the PRD at a time.

**Alternative:** one PRD revision at the end of week 7. Fewer edits, but the PRD would disagree with the code for two weeks.

## What the code change will be (after acceptance)

The steps in `Week 6 plan.md`: the labeling tools (1), the synthetic set and the chat suite (2), record and replay (3), Tier 1 (4), the EV-1 runner and scorers (5), Tier 2 (6), AgentCore Evaluations (7), the ops report (8), the demo app for screenshots (9), then the first full run, the gate check, EV-6, the README and the PRD revision.

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Tier 1 replays model replies recorded on the Mac; a stale reply fails | Accept | 2026-10-26 | in chat |
| (b) Tier 1 in `check.yml` on every PR and deploy: EV-1s, EV-4, EV-3 | Accept | 2026-10-26 | in chat |
| (c) Tier 2 after deploy, weekly and manual; paced; eval-set IDs only leave the Mac | Accept | 2026-10-26 | in chat |
| (d) EV-1: fetch script, local labeling page, no model pre-fill | Accept | 2026-10-26 | no emails labeled yet |
| (d') No manual inbox read; M8 and end-to-end recall not measured | Accept | 2026-10-28 | in chat: "ok skip" |
| (d'') No labeled set of my own email; EV-1s (120, split, blind second labeling) is the labeled set | Accept | 2026-10-29 | in chat: "Skip it" |
| (f'') EV-1s needs nothing from me | Accept | 2026-10-29 | in chat |
| (j') M7 is my estimate (about 2 hours a week), not a measurement | Accept | 2026-10-29 | in chat |
| (e) Split by class × language; the freeze; M1, M2, M8 scoring rules | Accept | 2026-10-26 | in chat |
| (f) EV-1s drafted by a session from definitions and my notes, reviewed by me | Accept | 2026-10-26 | in chat |
| (g) EV-3 in-process; M3 scored in code; AgentCore Evaluations on the eval run's spans (spike first) | Accept | 2026-10-26 | the spike picks (g), (g2) or (g3) |
| (h) NFR-OBS-1 by log fields; OpenTelemetry for the eval run only (h1), or live spans (h2) | Accept (h1) | 2026-10-26 | in chat |
| (i) M5 and M6 from Logs Insights, metrics and Cost Explorer; small-n rule | Accept | 2026-10-26 | in chat |
| (j) M7 without a before-agent log | Accept | 2026-10-26 | no before-agent log exists |
| (k) EV-6 once in week 7, scored in code, no local judge | Accept | 2026-10-26 | in chat |
| (l) The gate check on a `gate-check` branch with two known weak prompts | Accept | 2026-10-26 | in chat |
| (m) README: measured numbers only, Mermaid diagram, screenshots from fictional data | Accept | 2026-10-26 | in chat |
| (n) Before going public: noreply now, a history scan, then (n1) rewrite or (n2) keep | Accept, part 1 | 2026-10-26 | (n1) or (n2) when going public |
| (o) Dependencies: `eval` group (OpenTelemetry SDK + one LangChain instrumentation) | Accept, on condition | 2026-10-26 | source check before `uv add` |
| (p) PRD v0.4.7 now, v0.5.0 after the first full run; design doc rows read back | Accept | 2026-10-26 | in chat |
