# Weeks 6 to 7 plan: evaluation and write-up

**Date:** 2026-10-26
**Status:** OQ-47 (`Design doc edits for OQ-47.md`) **accepted on 2026-10-26** as recommended: (h) as (h1); (n) part 1 only, with (n1) or (n2) decided at going public; (o) on condition of a source check. No emails are labeled yet, and no before-agent time log exists (M7 reports with-agent minutes only). Step 0's documents and step 1 are done (2026-10-26). Next: step 0's read-only checks and the `Evaluate` spike after `aws login`; my labeling; step 2. Week 5 met all three exits on 2026-10-25. Live state: `Project status.md`. **Amended 2026-10-28 to 2026-10-29 (OQ-47 (d'), (d''), (f''), (j')):** no labeled set of my own email; EV-1s (120 synthetic emails, a frozen split, labeled twice by independent sessions) is the labeled set; M8 and end-to-end recall aren't measured; M7 is my estimate (about 2 hours a week). Sessions may run the Bedrock evaluation commands themselves (2026-10-29). PRD v0.4.9, design doc revision 131.
**Sources:**
- PRD v0.4.6: §9 EVAL-1, EVAL-2, OPS-1; §10 NFR-PRIV-3, NFR-PRIV-6, NFR-OBS-1 to 3, NFR-LAT; §11 M1 to M8; §12; §14 weeks 6 to 7; §15; §18 (OQ-7, OQ-8, OQ-17 to OQ-19, OQ-23, OQ-24, OQ-29, OQ-37).
- The design doc (Claude Doc, revision 116): the evaluation section and table, the components row "CloudWatch + AgentCore Evaluations", the mode table, the build plan.
- `Week 5 plan.md`, `eval/run_injection.py`, `.github/workflows/check.yml` and `deploy.yml`, and the handover of 2026-10-25.

## Due now, outside this plan (my hands)

- **Today, 2026-10-26: the day-8 read-token check** (week 3 exit 4). After `aws login`:
  ```bash
  uv run python spikes/agentcore_identity_gmail.py --grant read
  ```
  Pass: a token with no sign-in prompt, and the query runs. **Passed 2026-10-28:** a read token for `inbox-agent-poller` with no sign-in; the ING-1 query over the last 7 days returned 19 messages (week 3 exit 4 met).
- **End of week 5 (about 2026-10-27/28): the log scan again** (week 5 plan step 9):
  ```bash
  uv run python scan_logs.py
  ```
  Pass: 0 matches in every log group, `CLEAN`. **Passed 2026-10-28:** 19 processed emails in the last 7 days (all still in Gmail), 32 fragments searched (13 too short), 0 matches in all 11 log groups, `CLEAN`.
- Both need a fresh `aws login`. My session had expired on 2026-10-26, so none of the read-only checks below could run yet.

## Checked before planning (2026-10-26)

| Check | Result |
|---|---|
| AWS (read-only) | First attempt: the `aws login` session had expired. **Run later on 2026-10-26, after deploy run 40312699411 (see step 0)** |
| boto3 in `uv.lock` (1.43.114) | Has `bedrock-agentcore` `Evaluate` (synchronous, takes OpenTelemetry spans and an expected tool trajectory) and batch evaluations from CloudWatch Logs, plus the control-plane evaluator calls. No SDK upgrade needed |
| AWS's docs on AgentCore Evaluations | LangGraph (Python) is supported through `opentelemetry-instrumentation-langchain` ≥ 0.55.0 or `openinference-instrumentation-langchain` ≥ 0.1.62. The getting-started path assumes spans in CloudWatch (ADOT, Transaction Search). Spans captured on the Mac and passed straight to `Evaluate` aren't documented: a spike decides (OQ-47 (g)) |
| The design doc | Tier 1 "on GitHub's runners, with synthetic emails, the injection set and the chat suite"; EV-3 "scored with AgentCore Evaluations"; MacBook mode "with a local judge model"; the components row says "Traces". No mention of OpenTelemetry by name |
| CI as built | `check.yml`: pytest and `cdk synth` on `ubuntu-24.04`, no secrets. `deploy.yml`: check, then deploy on `[self-hosted, macOS]`. No Tier 1 or Tier 2 yet (OQ-42 (m)'s stop-gap) |
| Labels and time log | None exist (my answer, 2026-10-26): labeling starts from zero, and M7 has no before-agent baseline (OQ-47 (d), (j)) |
| Repo visibility | **Private.** Every commit's author email is my Gmail (OQ-47 (n)) |

## Exit criteria (PRD §14, split over the two weeks)

**Week 6**
1. Tier 1 runs on GitHub's runners on every pull request and as the deploy's first job, from recorded replies (EV-1s, EV-3, EV-4), and is green on main.
2. EV-1s's test split is frozen, and the first full Tier 2 run on main has recorded M1 (per class and per language), M2, M3 and M4 (live Nova). M8 and end-to-end invite recall are not measured (OQ-47 (d'), (d''))
3. The gate works: a deliberately weakened prompt fails Tier 1 on a test branch and fails a manual Tier 2 run there, and main passes both (OQ-47 (l)).

**Week 7**

4. M5 and M6 have measured values (with n); M7 is stated as not measured, with my estimate (OQ-47 (j')).
5. EV-6: EV-1s's test split and EV-3 on `qwen3.8:27b`, reported beside Nova.
6. The README shows only measured numbers, with a "how measured" section, a Mermaid architecture diagram and screenshots made from fictional data.
7. PRD v0.5.0 confirms or revises the §11 targets and does §18's confirmation pass.

Also due:
- week 3 exit 1 (both US-04 questions on the Ask page, cloud);
- from week 2: a real reply landing on an imported row;
- the first monthly pruning of approval versions (November, OQ-45 (j)).

## Shape of the result

```
the Mac (EVAL_DATA_DIR = ~/inbox-agent-eval, never in the repo)
  emails/*.eml  ◄── eval/fetch_labeling.py (read grant; query window + IDs I found by hand)
  labels.jsonl  ◄── eval/label_app.py (127.0.0.1, no model pre-fill)
  manifest.json     set version, split (⅓ dev / ⅔ test by class × language), frozen
  runs/main-latest.json   caught invites, for "no new miss"

repo
  eval/synthetic/*.json   EV-1s, 60 fictional emails (drafted by a session, reviewed by me)
  eval/chat/*.json        EV-3, 40 questions over a fictional tracker, expected tool calls
  eval/injection/*.json   EV-4 (week 5)
  eval/replies/<set>/     model replies keyed by sha256(model, system, prompt) ◄── eval/record.py (Mac, Nova)

Tier 1  check.yml, GitHub runner, every PR and every deploy
  pytest → synth → eval/tier1.py: EV-1s + EV-3 + EV-4 through the real code, replay model
  fails on: M4 < 100%, a stale reply; invite recall < 96% reported only until the classifier catches invites inside a rejection (OQ-47 (b'))

Tier 2  tier2.yml, self-hosted Mac, my aws login; after a deploy that touched src/ eval/ policies/, weekly, manual
  EV-1 test split (M1, M2, no new miss) + EV-4 live + EV-3 live with AgentCore Evaluations
  ≤ 12 model calls a minute per model (live mail shares the quota)
  uploads: scores with eval-set IDs only

eval/ops_report.py (me, weekly): M5 from Logs Insights, M6 from token fields + Cost Explorer
mac_app/demo.py: the three pages on a fictional tracker, for the README screenshots
```

No new AWS resource in either stack. The deployed code changes only if OQ-47 (h2) is chosen.

## Steps

Each step ends with `uv run pytest` green. A step that touches a workflow also ends with the workflow tests (no `pull_request` trigger on anything that runs on the Mac, no secrets, actions pinned to SHAs).

### 0. Decisions, checks, dependencies, documents
- ~~I accept or reject each OQ-47 item, and answer the two questions in (d) and (j).~~ Done 2026-10-26.
- ~~After `aws login`, the read-only checks in OQ-47's first table.~~ **Done 2026-10-26 (read-only):**
  - SCP simulation with `aws:RequestedRegion` = eu-north-1: `bedrock-agentcore:Evaluate`, `ListEvaluators`, `GetEvaluator`, `logs:StartQuery`, `GetQueryResults`, `ce:GetCostAndUsage`, `cloudwatch:GetMetricData` all **allowed**.
  - `list-evaluators` in eu-north-1: `Builtin.ToolSelectionAccuracy` and `Builtin.ToolParameterAccuracy` (tool-call level), `Builtin.Correctness` (trace), `Builtin.TrajectoryExactOrderMatch`, `TrajectoryInOrderMatch`, `TrajectoryAnyOrderMatch` and `GoalSuccessRate` (session), among others (19 built-in, plus third-party DeepEval and AutoEval ones).
  - Transaction Search is **off** (`get-trace-segment-destination`: `XRay`, `ACTIVE`). Only route (g2) would need it on.
  - Pricing (AWS's page, 2026-10-26): built-in evaluators are billed by token, $0.0024 per 1,000 input and $0.012 per 1,000 output, model included; no Evaluations free tier. At about 1,000 in and 200 out per result, 40 questions × 2 evaluators ≈ $0.40 a Tier 2 run, so about $2 a month at 5 runs. That's inside the design doc's $1 to $3; Tier 2 runs the judge only on the weekly and manual runs if it climbs.
  - Nova quotas unchanged: cross-Region Micro and Lite at 20 requests and 400,000 tokens a minute; in-Region Lite at 10 and 200,000. The 12-a-minute pace stands.
- The dependencies in (o): source check, my approval, then `uv add --group eval`.
- **The `Evaluate` spike** (one fictional EV-3 question, in-process, spans captured in memory, one `Evaluate` call). It decides between (g), (g2) and (g3). It's an AWS call I run, about 1,000 judge tokens.
- ~~PRD v0.4.7 and the design-doc revision after 116, each design edit read back.~~ **Done 2026-10-26:** PRD v0.4.7 (week 5's records, then OQ-47's rows) and design doc revision 120 (the rows of (a), (g), (h), (k), read back).

### 1. Labeling tools first (OQ-47 (d)): labeling is the long pole
- `eval/fetch_labeling.py --after YYYY-MM-DD --before YYYY-MM-DD [--dry-run]`:
  - the ING-1 query with that window, through `query.py` (dates are midnight in Istanbul; `--before` is exclusive);
  - **since OQ-47 (d') (2026-10-28) the query's hits only:** no manual read of the inbox. `--manual-label` (off unless given) would add emails I put a Gmail label on;
  - raw `.eml` plus `{found_by, fetched_at}` under `EVAL_DATA_DIR/emails/`;
  - counts printed, no subjects, no senders.
- `eval/label_app.py`: Streamlit on 127.0.0.1 (the repo's `.streamlit/config.toml`), port 8502: `uv run --group app streamlit run eval/label_app.py --server.port 8502`. One email at a time: the trimmed text (`trim.py`, exactly what the model sees) and the full text behind a toggle. Fields: class, company, role, key date (date, optional time), language, note. Writes `labels.jsonl` by eval-set ID. Shows progress by class and language.
- Tests with fakes: the window goes into the query; `found_by` is kept; nothing is printed but counts; the page writes a valid label line; `EVAL_DATA_DIR` inside the repo is refused.
- **Built 2026-10-26, not committed:** `eval/labeled_set.py` (the store: `emails/<eval id>.eml`, `index.jsonl`, `labels.jsonl`; directory 0700, files 0600; refused inside the repo), `eval/fetch_labeling.py`, `eval/label_app.py`, `tests/unit/test_labeling.py` (23 tests; 880 pass in all). Class and language start unchosen on the page.
- ~~Then I start labeling.~~ **Dropped (OQ-47 (d''), 2026-10-29):** the first fetch (59 September emails, in `~/inbox-agent-eval` on my Mac) stays unlabeled, and the tools are kept, unused.

### 2. The synthetic set and the chat suite (OQ-47 (d''), (f''), (g))
- A session drafts `eval/synthetic/syn001.json` … `syn120.json` from CLS-1's definitions alone (no real mail, nothing from me): 60 Turkish and 60 English; all six classes; about 20 invites; hard cases (an invite inside a rejection thread, a test link without the word "assessment", a calendar-only invite, a key date after the 2,000-character cut, recruiter outreach that names an interview). Each file: sender, subject, body, optional calendar part, labels.
- **A second, independent session labels them blind** (it sees the emails, not the labels). Every disagreement is rewritten until it's unambiguous, or dropped; the agreement rate is recorded.
- `eval/chat/`: a fictional tracker (about 15 rows, mixed statuses and dates relative to a fixed "today") and 40 questions (20 Turkish, 20 English), each with the expected `list_applications` calls and the facts the answer must contain.
- Schema tests like EV-4's: fictional domains only, every class and language present, every expected call valid against `tools.py`.
- **Built 2026-10-29:** `eval/synthetic/syn001`–`syn120.json` (60 tr, 60 en; invite 20, assessment 16, offer 10, rejection 24, outreach 20, other 30; 30+ hard cases), the rules in `eval/synthetic/LABELING.md`, the loader `eval/synthetic_set.py` (builds the real RFC 822 message with its calendar part). **Blind second labeling** by a fresh labeler that saw only the rules and the emails: class 120/120, all fields 118/120 at the first pass; the two misses (syn034, syn104 named the company only in the domain and the quoted thread) were rewritten and agreed on a second blind pass: 120/120, none dropped (`eval/synthetic/agreement.json`, `blind_labels.jsonl`). The labelers were subagents of the building session, started cold. EV-3: `eval/chat/tracker.json` (17 rows, every status, today 2026-10-26), `questions.json` (40), `eval/chat_suite.py`. Tests: `tests/unit/test_eval_sets.py`.

### 3. Record and replay (OQ-47 (a))
- `eval/replay.py`:
  - `ReplayModel` (behind the `Model` interface) and a replay LangChain chat model for the chat path;
  - the key is `sha256(model_id, system, prompt)`, or for chat the serialized messages plus the tool specs;
  - a miss raises `StaleReply` with the set and case ID.
- `eval/record.py --sets ev1s,ev3,ev4`, run by me with Nova: real calls through `chat_model()`, with each reply written under `eval/replies/`. Unchanged keys are kept, keys nothing uses any more are deleted, and a summary is printed.
- `run_injection.py` and the new runners take `--replay`.
- Tests:
  - the same case run twice gives the same key;
  - a one-character prompt change misses;
  - replayed EV-4 equals the recorded run's scores;
  - reply files hold no address outside `example.com`.
- **Built 2026-10-29:** `eval/replay.py` (`ReplayChat`, `RecordingChat`, `ReplyStore`; one file per set, `eval/replies/<set>.json`; a recording stopped early keeps every paid reply), `eval/pacing.py` (12 calls a minute per model ID, shared by purposes), `eval/record.py`; `--replay` on `run_injection.py`, `run_labeled.py`, `run_chat.py`. A key repeated within a case gets one reply (a retry at temperature 0). Recorded with Nova on the Mac 2026-10-29. Tests: `tests/unit/test_replay.py`.

### 4. Tier 1 (OQ-47 (b))
- `eval/tier1.py`: EV-1s (per-class precision and recall, invite recall, M2), EV-3 (M3 and the answer check), EV-4 (M4), all with `--replay`. It writes the job summary and exits non-zero on: invite recall < 96%, M4 < 100%, or any `StaleReply`.
- `check.yml` gains one step after pytest. No secrets, no AWS, no Ollama.
- Tests: a scripted bad reply set fails each gate; a stale key fails with its message.
- Then I record (step 3) and push. Tier 1 is green on the GitHub runner.
- **Amended 2026-10-31 (OQ-47 (b'), my yes):** the invite-recall floor is reported, not gating (`INVITE_GATE_BLOCKS = False` in `eval/tier1.py`), until the classifier catches invites inside a rejection; stale replies and M4 still fail. With the recorded replies Tier 1 passes, and reports 92.9% (syn014). Next: Nova Lite for classification on dev only, reported before any change. **Tier 2 too (my word, 2026-10-31):** its floor and "no new miss" are reported only, by one switch, `INVITE_GATE_BLOCKS` in `eval/run_labeled.py`; M4 still fails a run. Step 12's invite-recall half needs the switch on. EV-4's live runs are paced too now (`mode_models`), which step 6 assumed.
- **Nova Lite as classifier, dev only, two runs (2026-10-31), the committed prompt:** invite recall 80% of 10 both times (Micro's recording 70%), class accuracy 79.5% (same), M2 79.5% and 84.1% (Micro 86.4%). Lite catches the two English invites inside a rejection and misses both Turkish ones, and it reads receipts and notices as outreach (outreach precision 46%, "other" recall 30%; Micro 100% and 90%). One invite in ten is the whole gain, so it isn't a fix. Nothing changed; the test split and EV-4 weren't run with Lite.
- **Built 2026-10-29:** `eval/tier1.py` (gates on EV-1s's test split; dev reported) and its step in `check.yml`. Tests: `tests/unit/test_tier1.py`, `test_workflows.py`.

### 5. The labeled-set runner and scorers on EV-1s (OQ-47 (e), (d''))
- `eval/split.py`: ⅓ dev and ⅔ test by class × language, fixed seed; `freeze` writes `manifest.json` with the set version and a hash per label line, and refuses to change a frozen test split.
- `eval/run_labeled.py --split test|dev --mode cloud|local [--pace 12]` runs the classifier and extraction over EV-1s. Scorers:
  - **M1** per class and language;
  - **M2** with the normalization rules;
  - the "no new miss" comparison against `runs/main-latest.json`.
  - OQ-24's check is reported alongside: the share of labeled fields that fall after the 2,000-character cut.
- Its output is the scores file: counts, metrics and case IDs.
- Tests on a fictional mini-set: each metric by hand, the freeze refusal, pacing (calls per minute), no email text in the scores.
- **Built 2026-10-29** (pulled forward, as recording needs it): `eval/split.py` (dev = round(n/3) per class × language), `eval/run_labeled.py` (the real batch graph per email; the prediction is the graph's state; the key date only for invites and assessments; M2 compares the wall-clock time). **Frozen 2026-10-29** as `ev1s-v1` (80 test, 40 dev; `eval/synthetic/manifest.json`), before any prompt was tuned. Then 4 dev emails were added (syn121–124, invites inside a rejection; both such emails in the frozen set had fallen in test), labeled blind 4/4. The OQ-24 check counts labeled times only (2 of 22 fall after the cut: the two long invites).

### 6. Tier 2 (OQ-47 (c))
- `tier2.yml` on `[self-hosted, macOS]`:
  - triggers: `workflow_call` from `deploy.yml` after the deploy, when `src/`, `eval/` or `policies/` changed; `schedule` Mondays 07:00 UTC; `workflow_dispatch`;
  - steps: a session check first (its own error), then `run_labeled.py --split test`, `run_injection.py --mode cloud`, `run_chat.py --mode cloud --evaluate`;
  - a 60-minute timeout;
  - it uploads the scores file and saves `runs/main-latest.json` only on `refs/heads/main`.
- Tests: no `pull_request`, no secrets, and only main updates the saved state.
- **Built 2026-10-29:** `.github/workflows/tier2.yml` (upload-artifact pinned to v7.0.2, `cf430e0…`); the saved main run is `${EVAL_DATA_DIR:-~/inbox-agent-eval}/runs/main-latest.json`; `deploy.yml` gains a `changes` job (a diff of the push for `src/`, `eval/`, `policies/`; a manual run counts as changed) and calls Tier 2 after the deploy. `--evaluate` joins the EV-3 step once the `eval` group is approved.

### 7. AgentCore Evaluations for EV-3 (OQ-47 (g), whichever route the spike chose)
- `eval/run_chat.py`: the real chat graph in-process over the fictional tracker; M3 and the answer check in code. With `--evaluate` (Tier 2 only), it also runs the instrumentation, collects the session's spans, and calls `Evaluate` with `Builtin.ToolSelectionAccuracy` (`expectedTrajectory`) and `Builtin.Correctness` (`expectedResponse`). It reports their scores and judge tokens beside M3.
- Tests with a fake `Evaluate` client: the request carries only the suite's spans; partial failures are counted, not hidden.
- **Built 2026-10-29, not run:** `eval/agentcore_eval.py` (in-memory spans, OpenInference's unified attributes, one session per question, both evaluators) and `run_chat.py --evaluate`; tests with fakes (`tests/unit/test_agentcore_eval.py`). AWS's LangGraph page says unified telemetry keeps the content on the span attributes, which is what in-memory capture gives. **Approved 2026-10-31 and added** (`uv add --group eval`; three transitive packages capped at versions two weeks old in `[tool.uv] constraint-dependencies`: `openinference-instrumentation` 0.1.71, `openinference-semantic-conventions` 0.1.41, `wrapt` 2.5.1). **Spike 2026-10-31: route (g) works.** `Evaluate` took the in-memory spans: ToolSelectionAccuracy 1.0 on c02 (1,348 in / 196 out judge tokens); Correctness needs the question's trace in its context (an expected response is trace-level), then scored 3 of 3. `tier2.yml` runs EV-3 with `--evaluate`. Was waiting on: my approval of the `eval` group: `opentelemetry-sdk` 1.45.1 (2026-10-06; OpenTelemetry's own project; no PyPI attestation) and `openinference-instrumentation-langchain` 0.1.79 (2026-10-09; PyPI attestation from `Arize-ai/openinference`'s publish workflow). Then the one-question spike.

### 8. The ops report (OQ-47 (i))
- `eval/ops_report.py --days 7`, read-only:
  - Logs Insights over the consumer and Runtime log groups: M5's n, p50, p95 and max (end-to-end and pipeline), with the small-n rule; detection → handled for all mail;
  - tokens per model from the log fields, cross-checked with Bedrock's CloudWatch metrics;
  - emails processed;
  - with `--month`, Cost Explorer by service, credits not netted, for M6.
- It writes a dated JSON on the Mac. I run it weekly, because logs keep 7 days.
- Tests with fakes: the percentile and small-n rule, no subject or company in the output.
- **Built 2026-10-29:** `eval/ops_report.py`. The live path logs no token counts, so tokens come from Bedrock's CloudWatch metrics, which also count evaluation runs from the Mac (the report says so). First run (7 days to 2026-10-29, read-only, no `--month`): 20 emails processed, 2 alerts; M5 end-to-end n 2, max 18.7 min; pipeline max 0.33 min; detection → handled n 20, p50 0.28 min, p95 0.58 min. Saved on the Mac.

### 9. The demo app for screenshots (OQ-47 (m))
- `mac_app/demo.py` runs the three pages with the tests' fake backend and the EV-3 fictional tracker, on 127.0.0.1. `app.py` doesn't change.
- An AppTest check that the demo renders all three pages from fictional data only.
- **Built 2026-10-29:** `mac_app/demo.py`: the Dashboard over the EV-3 tracker, Ask through the real chat graph answered from Nova's recorded EV-3 replies (other questions get a note), two fictional drafts. `uv run --all-groups streamlit run mac_app/demo.py --server.port 8503`.

### 10. Small fixes carried from week 5 (each its own commit, no OQ needed)
- `bedrock_agentcore.app`'s logger to WARNING.
- `GatewayRole`'s description: six tools, not four.
- `SqliteMemory.company_summary`: match the namespace exactly, not with `LIKE` on a prefix.
- **I decide separately:** the drafting retry tells the model which rule failed (a behavior change in `followup_draft.py`).
- **Built 2026-10-29:** the SDK sets `bedrock_agentcore.app` to INFO itself, so `logs.QUIET_LOGGERS` names it; `GatewayRole`'s description says six; `SqliteMemory.company_summary` matches the namespace exactly (with or without the trailing slash). A test for each.

### 11. The first full run and the freeze (week 6)
- When EV-1s and its blind second labeling are done:
  - `split.py freeze`;
  - one Tier 2 run on main (manual dispatch);
  - the baselines recorded in CLAUDE.md.
- Prompts are tuned only on dev from here (§12 step 2).

- **Recorded 2026-10-29 (Nova, the committed prompt), not yet a Tier 2 run:** EV-1s test (ev1s-v1, 80): invite recall 92.9% (13/14, syn014 missed), class accuracy 92.5%, M2 92.5% with the arrival-date guard (88.8% without); dev (44): invite recall 70% (3 of the 4 new invites inside a rejection missed). EV-4 M4 24/24. EV-3 M3 87.5% (75% before five question-stated filters were accepted), facts 85%. **Tier 1 fails on main on invite recall.** Findings: Micro reads "not this role, but an interview for that one" as rejection plus outreach; with no date in the text it returns the quoted Date line (fixed in code: `batch_path._not_the_arrival`); it isn't stable at temperature 0 (syn124 flipped between two calls). A prompt revision tuned on dev didn't beat the original on dev and, measured once on test, lowered M2 to 76% and let inj09 through (M4 23/24); it was reverted. The owner decides how Tier 1 lands (report to the head session, 2026-10-29).

### 12. The gate check (OQ-47 (l))
- Branch `gate-check`:
  1. weak change (1) and re-recorded replies: Tier 1 must fail on M4;
  2. weak change (2): Tier 1 must fail on invite recall;
  3. a manual `tier2.yml` run on the branch must fail;
  4. main passes both tiers;
  5. delete the branch.
- I record the run links.

- **Run 2026-10-31 (my "ok"), branch deleted afterward:**
  - Weak change 1 (the prompt before `b584c61`, replies re-recorded): before the re-recording Tier 1 failed as stale; after it, [run 41194127147](https://github.com/EgeCankaya/job-search-agent/actions/runs/41194127147) failed Tier 1 on M4, 22/24 (inj05, inj09). No deploy.
  - Weak change 2 (no invite definition, replies re-recorded, `INVITE_GATE_BLOCKS` on): [run 41195082257](https://github.com/EgeCankaya/job-search-agent/actions/runs/41195082257) failed Tier 1 on invite recall, 0 of 14, with M4 24/24. No deploy.
  - Tier 2 by hand on weak change 2: [run 41195089255](https://github.com/EgeCankaya/job-search-agent/actions/runs/41195089255) failed on invite recall, 0 of 14, 13 new misses against the saved main run, which it didn't replace (hash unchanged).
  - Main: [run 41189897837](https://github.com/EgeCankaya/job-search-agent/actions/runs/41189897837) passed both tiers. **Limit:** main passes the invite rule only while it is reported (OQ-47 (b')). With the switch on, main fails the floor too (13 of 14), so that half shows the gate fires, not that it separates a weak prompt from main at the floor; its 13 new misses do separate them. Tier 2's M4 failure wasn't run live: the one Tier 2 run used weak change 2.
  - A lead, one sample: weak change 1, without the manipulation rule, caught all 14 test invites and 8 of 10 on dev (the committed prompt: 13 of 14, 7 of 10), and let two planted emails through.

### 13. Week 7: measure, baseline, write
- EV-6 (step 5's runner with `--mode local`, and `run_chat.py --mode local`), with Ollama on.
- `ops_report.py` for M5 and M6 (the month to date); M7 from my week of time log.
- The README:
  - what it does, and the architecture (Mermaid);
  - the measured table (M1 to M7: value, n, date, set version; M8 and end-to-end recall marked not measured);
  - how measured;
  - the two modes;
  - the safety layers and what was proven live;
  - the cost;
  - the screenshots.
- PRD v0.5.0: the §11 targets confirmed or revised from the run; the §18 confirmation pass; the milestone row.
- OQ-47 (n): a noreply author from now on; before going public, the history and Actions-log scan; then my choice of (n1) or (n2).
  - **History scan done 2026-10-29 (read-only):** 76 commits, 651 objects. No account ID or Gmail address in any tracked file, in any commit message, or in the 26 readable Actions logs (2 of 28 had no log left). The 12-digit hits are false positives (digits inside hashes and `uv.lock` checksums, the fake test account `111122223333`, fictional `90555…` numbers); the addresses are fictional or public ATS senders. The design PDF wasn't parsed (no PDF tool installed), but the Claude Doc it was exported from holds no account ID or address. **What going public exposes:** all 152 author and committer entries carry my Gmail.
- **Built and measured 2026-10-31, not committed:**
  - **EV-6** (`eval/results/`, gitignored): `qwen3.8:27b` on EV-1s's test split, invite recall 100% (14 of 14), class accuracy 97.5%, M2 97.5% (347 s); EV-3 M3 100%, facts 100% (368 s, 78 calls); EV-4 M4 24/24, accurate under attack 24/24 (250 s). One run each.
  - **M5:** n = 2 real alerts in the logs still held (2026-10-22 to 10-31): end to end 1.91 and 18.74 min, pipeline 0.32 and 0.33 min.
  - **M6:** the window's token figure includes evaluation runs, so the model cost per email comes from the 3 days without one (2026-10-26, 10-27, 10-30): 10 emails, about $0.00008 each. All-in about $0.0065: $0.26 October to date (Cost Explorer lags) ÷ 40 emails in the processed log. Read the closed month again in November.
  - **README** rewritten, and **PRD v0.5.0**: targets confirmed as written (M1, M3 and M6's all-in figure not met, none lowered); §18's pass leaves no Provisional answer.
  - **Screenshots** in `docs/screenshots/`, from `mac_app/demo.py`. They showed two things, both fixed: the demo's alerts fell before its Monday, so the Dashboard said 0; the Ask page's caption named a real company.
  - **Found:** the Mac worker isn't built. `AGENT_MODE=local` runs the chat path and the evaluations only, though the PRD describes the worker (ING-1, ING-3, APR-1, APR-3, NFR-REL-4, US-08) and §14 gives it no week. It's recorded under OQ-22 and in the README; building it is the owner's decision.
  - **My decisions, 2026-11-01:** the Mac worker stays unbuilt (OQ-22); the cold subagent's blind labeling stands, as disclosed; try a narrower classifier fix on dev (candidate: the invite definition covers another role, and a line says an email declining one role and proposing an interview for another fits both classes; dev first, then one test run and EV-4, reverted if M2 or M4 falls); the repo goes public after the README polish; `Project status.md` is gitignored.
  - **Before going public, 2026-11-01:** README split, the detail moved to `docs/evaluation.md`, two simpler diagrams. Sample companies in the tree became fictional (they stay in the history unless (n1) replaces them). Scan: 76 of 89 commits carry my Gmail as author; no Gmail in any tracked file or commit message; 33 Actions runs, 0 hits for 12-digit numbers, other addresses or phone numbers (a run with no log left counts as no hit). No license file. (n1) or (n2) is still mine.
  - **Classifier fix, 2026-11-01 (OQ-47 (b'')):** dev two runs, invite recall 100% of 10, M2 95.5% (70% and 86.4% before); test one run, invite recall 14 of 14, class accuracy 95.0%, M2 93.8%, misclassified syn045, syn053, syn060 (a false invite), syn119; EV-4 M4 24/24, accurate 22/24. Replies re-recorded for EV-1s and EV-4 (replay: M2 95.0%). Local on the same prompt: invite recall 100%, class accuracy 98.8%, M2 97.5%, M4 24/24. `INVITE_GATE_BLOCKS` is True. M3 is unchanged: the chat path doesn't use this prompt.
  - **Going public, 2026-11-03:** (n1) force-pushed (tip `031c48a`); deploy and Tier 2 green in [run 41611586445](https://github.com/EgeCankaya/job-search-agent/actions/runs/41611586445), after a re-run for an expired `aws login`. Tier 2 on main: invite recall 100% (14 of 14) with the invite gate blocking, so step 12's limit is gone and main passes both tiers at the floor; M2 95.0%; M4 24/24, accurate under attack 21/24; M3 87.5%. The screenshots were checked and committed (`62c0677`). My decisions: M2 stays reported (OQ-47 (b''')); MIT license. The documents moved into `docs/`, `docs/decisions/` and `docs/plans/`.

### 14. Exit checks
- Each exit as listed above, with the run links and the numbers recorded in CLAUDE.md.
- A short report to the head session after each.

## What I do by hand
- ~~Decide OQ-47 item by item; answer (d) and (j).~~ Done 2026-10-26. Approve the dependencies after a source check.
- `aws login`, then: the day-8 check, the end-of-week log scan, step 0's read-only checks, the `Evaluate` spike.
- ~~Write the EV-1s pattern notes; review the synthetic emails; fetch and label my own email.~~ Dropped (OQ-47 (d''), (f''), 2026-10-29).
- ~~Record replies with Nova.~~ Sessions run the Bedrock evaluation commands themselves since 2026-10-29; I commit what they record when I say yes.
- Push (or say yes to a session's push); run Tier 2 by hand for the first full run and the gate check.
- ~~Keep a time log for M7~~ (my estimate is about 2 hours a week, OQ-47 (j')); run `ops_report.py` weekly, or let a session run it (read-only).
- Turn on Transaction Search only if the spike says (g2).
- Check every screenshot for real data; decide (n1) or (n2) and when the repo goes public.

## Risks
- **Synthetic email flatters the classifier.** Emails written from the definitions are cleaner than real mail, so M1 and M2 likely overstate real accuracy. The README says the numbers come from synthetic email; the hard cases and the blind second labeling keep the set honest.
- **Replies go stale often.** Every prompt change needs a recording on the Mac. If that's a chore, OQ-47 (a2) (a Tier 1 job on the Mac for pushes) is the fallback.
- **Tier 2 starves live mail.** The pacing cap and the dead-letter alarm cover it. A Tier 2 run is never started during a deploy (one runner, so jobs queue anyway).
- **`aws login` expires.** Tier 2's weekly run will often fail at the session step. It fails with its own message, and a manual re-run follows.
- **AgentCore Evaluations won't score an agent outside the Runtime.** The (g2) and (g3) fallbacks are in OQ-47. M3 is computed in code either way.
- **Judge and Evaluations cost.** Unknown until the pricing check. The judge tokens are reported per run, and the design doc's $1 to $3 a month is the bar.
- **Nova at temperature 0 still varies.** Replay freezes one sample, and Tier 2's live runs show the spread. Report Tier 2 numbers as the measured ones.
- **The frozen split leaks into tuning.** Prompts are tuned on dev only. The test split's labels are read only by the scorer.
- **The account's 6-month horizon** (OQ-39 (c)) isn't affected: weeks 6 to 7 end in early November.
- **Going public exposes history.** The scan comes before the visibility flip, and the flip is mine.
