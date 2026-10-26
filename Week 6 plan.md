# Weeks 6 to 7 plan: evaluation and write-up

**Date:** 2026-10-26
**Status:** OQ-47 (`Design doc edits for OQ-47.md`) **accepted on 2026-10-26** as recommended: (h) as (h1); (n) part 1 only, with (n1) or (n2) decided at going public; (o) on condition of a source check. No emails are labeled yet, and no before-agent time log exists (M7 reports with-agent minutes only). Step 0's documents and step 1 are done (2026-10-26). Next: step 0's read-only checks and the `Evaluate` spike after `aws login`; my labeling; step 2. Week 5 met all three exits on 2026-10-25. Live state: `Project status.md`.
**Sources:**
- PRD v0.4.6: §9 EVAL-1, EVAL-2, OPS-1; §10 NFR-PRIV-3, NFR-PRIV-6, NFR-OBS-1 to 3, NFR-LAT; §11 M1 to M8; §12; §14 weeks 6 to 7; §15; §18 (OQ-7, OQ-8, OQ-17 to OQ-19, OQ-23, OQ-24, OQ-29, OQ-37).
- The design doc (Claude Doc, revision 116): the evaluation section and table, the components row "CloudWatch + AgentCore Evaluations", the mode table, the build plan.
- `Week 5 plan.md`, `eval/run_injection.py`, `.github/workflows/check.yml` and `deploy.yml`, and the handover of 2026-10-25.

## Due now, outside this plan (my hands)

- **Today, 2026-10-26: the day-8 read-token check** (week 3 exit 4). After `aws login`:
  ```bash
  uv run python spikes/agentcore_identity_gmail.py --grant read
  ```
  Pass: a token with no sign-in prompt, and the query runs. Every poll since 2026-10-20 has used that token, so it very likely works; this run makes it formal.
- **End of week 5 (about 2026-10-27/28): the log scan again** (week 5 plan step 9):
  ```bash
  uv run python scan_logs.py
  ```
  Pass: 0 matches in every log group, `CLEAN`.
- Both need a fresh `aws login`. My session had expired on 2026-10-26, so none of the read-only checks below could run yet.

## Checked before planning (2026-10-26)

| Check | Result |
|---|---|
| AWS (read-only) | **Not run:** the `aws login` session had expired. The checks are listed in OQ-47 and in step 0 |
| boto3 in `uv.lock` (1.43.114) | Has `bedrock-agentcore` `Evaluate` (synchronous, takes OpenTelemetry spans and an expected tool trajectory) and batch evaluations from CloudWatch Logs, plus the control-plane evaluator calls. No SDK upgrade needed |
| AWS's docs on AgentCore Evaluations | LangGraph (Python) is supported through `opentelemetry-instrumentation-langchain` ≥ 0.55.0 or `openinference-instrumentation-langchain` ≥ 0.1.62. The getting-started path assumes spans in CloudWatch (ADOT, Transaction Search). Spans captured on the Mac and passed straight to `Evaluate` aren't documented: a spike decides (OQ-47 (g)) |
| The design doc | Tier 1 "on GitHub's runners, with synthetic emails, the injection set and the chat suite"; EV-3 "scored with AgentCore Evaluations"; MacBook mode "with a local judge model"; the components row says "Traces". No mention of OpenTelemetry by name |
| CI as built | `check.yml`: pytest and `cdk synth` on `ubuntu-24.04`, no secrets. `deploy.yml`: check, then deploy on `[self-hosted, macOS]`. No Tier 1 or Tier 2 yet (OQ-42 (m)'s stop-gap) |
| Labels and time log | None exist (my answer, 2026-10-26): labeling starts from zero, and M7 has no before-agent baseline (OQ-47 (d), (j)) |
| Repo visibility | **Private.** Every commit's author email is my Gmail (OQ-47 (n)) |

## Exit criteria (PRD §14, split over the two weeks)

**Week 6**
1. Tier 1 runs on GitHub's runners on every pull request and as the deploy's first job, from recorded replies (EV-1s, EV-3, EV-4), and is green on main.
2. The EV-1 test split is frozen, and the first full Tier 2 run on main has recorded M1 (per class and per language, plus end-to-end invite recall), M2, M3, M4 (live Nova) and M8.
3. The gate works: a deliberately weakened prompt fails Tier 1 on a test branch and fails a manual Tier 2 run there, and main passes both (OQ-47 (l)).

**Week 7**

4. M5 and M6 have measured values (with n), and M7 has a measured value or is stated as not measurable (OQ-47 (j)).
5. EV-6: the EV-1 test split and EV-3 on `qwen3.8:27b`, reported beside Nova.
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
  fails on: invite recall < 96%, M4 < 100%, a stale reply

Tier 2  tier2.yml, self-hosted Mac, my aws login; after a deploy that touched src/ eval/ policies/, weekly, manual
  EV-1 test split (M1, M2, M8, no new miss) + EV-4 live + EV-3 live with AgentCore Evaluations
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
- After `aws login`, the read-only checks in OQ-47's first table: the SCP simulation (with `aws:RequestedRegion`), `list-evaluators`, Transaction Search status, the Evaluations pricing page, the Nova quotas.
- The dependencies in (o): source check, my approval, then `uv add --group eval`.
- **The `Evaluate` spike** (one fictional EV-3 question, in-process, spans captured in memory, one `Evaluate` call). It decides between (g), (g2) and (g3). It's an AWS call I run, about 1,000 judge tokens.
- ~~PRD v0.4.7 and the design-doc revision after 116, each design edit read back.~~ **Done 2026-10-26:** PRD v0.4.7 (week 5's records, then OQ-47's rows) and design doc revision 120 (the rows of (a), (g), (h), (k), read back).

### 1. Labeling tools first (OQ-47 (d)): labeling is the long pole
- `eval/fetch_labeling.py --after YYYY-MM-DD --before YYYY-MM-DD [--dry-run]`:
  - the ING-1 query with that window, through `query.py` (dates are midnight in Istanbul; `--before` is exclusive);
  - the emails I put the Gmail label `eval-manual` on while reading the inbox (Gmail's web addresses don't show the API's message IDs, so a label carries my finds); one the query also returned stays `found_by=query`;
  - raw `.eml` plus `{found_by, fetched_at}` under `EVAL_DATA_DIR/emails/`;
  - counts printed, no subjects, no senders.
- `eval/label_app.py`: Streamlit on 127.0.0.1 (the repo's `.streamlit/config.toml`), port 8502: `uv run --group app streamlit run eval/label_app.py --server.port 8502`. One email at a time: the trimmed text (`trim.py`, exactly what the model sees) and the full text behind a toggle. Fields: class, company, role, key date (date, optional time), language, note. Writes `labels.jsonl` by eval-set ID. Shows progress by class and language.
- Tests with fakes: the window goes into the query; `found_by` is kept; nothing is printed but counts; the page writes a valid label line; `EVAL_DATA_DIR` inside the repo is refused.
- **Built 2026-10-26, not committed:** `eval/labeled_set.py` (the store: `emails/<eval id>.eml`, `index.jsonl`, `labels.jsonl`; directory 0700, files 0600; refused inside the repo), `eval/fetch_labeling.py`, `eval/label_app.py`, `tests/unit/test_labeling.py` (23 tests; 880 pass in all). Class and language start unchosen on the page.
- **Then I start labeling** (about 3 to 4 hours for 300), window by window, with the sampling rule.

### 2. The synthetic set and the chat suite (OQ-47 (f), (g))
- I write short pattern notes (no real text). A session drafts `eval/synthetic/syn01.json` … `syn60.json`: 30 Turkish and 30 English; all six classes; about 10 invites; hard cases. Each file: sender, subject, body, optional calendar part, labels.
- I review every one: accept, edit or drop, and set the labels.
- `eval/chat/`: a fictional tracker (about 15 rows, mixed statuses and dates relative to a fixed "today") and 40 questions (20 Turkish, 20 English), each with the expected `list_applications` calls and the facts the answer must contain.
- Schema tests like EV-4's: fictional domains only, every class and language present, every expected call valid against `tools.py`.

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

### 4. Tier 1 (OQ-47 (b))
- `eval/tier1.py`: EV-1s (per-class precision and recall, invite recall, M2), EV-3 (M3 and the answer check), EV-4 (M4), all with `--replay`. It writes the job summary and exits non-zero on: invite recall < 96%, M4 < 100%, or any `StaleReply`.
- `check.yml` gains one step after pytest. No secrets, no AWS, no Ollama.
- Tests: a scripted bad reply set fails each gate; a stale key fails with its message.
- Then I record (step 3) and push. Tier 1 is green on the GitHub runner.

### 5. The EV-1 runner and scorers (OQ-47 (e))
- `eval/split.py`: ⅓ dev and ⅔ test by class × language, fixed seed; `freeze` writes `manifest.json` with the set version and a hash per label line, and refuses to change a frozen test split.
- `eval/run_labeled.py --split test|dev --mode cloud|local [--pace 12]` runs the classifier and extraction over the `.eml` files. Scorers:
  - **M1** per class and language, end-to-end invite recall (`manual` invites the query misses count as misses);
  - **M2** with the normalization rules;
  - **M8** with `query.py`'s matcher;
  - the "no new miss" comparison against `runs/main-latest.json`.
  - OQ-24's check is reported alongside: the share of labeled fields that fall after the 2,000-character cut.
- Its output is the scores file: counts, metrics and eval-set IDs.
- Tests on a fictional mini-set under `tmp_path`: each metric by hand, the freeze refusal, pacing (calls per minute), no Gmail ID or text in the scores.

### 6. Tier 2 (OQ-47 (c))
- `tier2.yml` on `[self-hosted, macOS]`:
  - triggers: `workflow_call` from `deploy.yml` after the deploy, when `src/`, `eval/` or `policies/` changed; `schedule` Mondays 07:00 UTC; `workflow_dispatch`;
  - steps: a session check first (its own error), then `run_labeled.py --split test`, `run_injection.py --mode cloud`, `run_chat.py --mode cloud --evaluate`;
  - a 60-minute timeout;
  - it uploads the scores file and saves `runs/main-latest.json` only on `refs/heads/main`.
- Tests: no `pull_request`, no secrets, and only main updates the saved state.

### 7. AgentCore Evaluations for EV-3 (OQ-47 (g), whichever route the spike chose)
- `eval/run_chat.py`: the real chat graph in-process over the fictional tracker; M3 and the answer check in code. With `--evaluate` (Tier 2 only), it also runs the instrumentation, collects the session's spans, and calls `Evaluate` with `Builtin.ToolSelectionAccuracy` (`expectedTrajectory`) and `Builtin.Correctness` (`expectedResponse`). It reports their scores and judge tokens beside M3.
- Tests with a fake `Evaluate` client: the request carries only the suite's spans; partial failures are counted, not hidden.

### 8. The ops report (OQ-47 (i))
- `eval/ops_report.py --days 7`, read-only:
  - Logs Insights over the consumer and Runtime log groups: M5's n, p50, p95 and max (end-to-end and pipeline), with the small-n rule; detection → handled for all mail;
  - tokens per model from the log fields, cross-checked with Bedrock's CloudWatch metrics;
  - emails processed;
  - with `--month`, Cost Explorer by service, credits not netted, for M6.
- It writes a dated JSON on the Mac. I run it weekly, because logs keep 7 days.
- Tests with fakes: the percentile and small-n rule, no subject or company in the output.

### 9. The demo app for screenshots (OQ-47 (m))
- `mac_app/demo.py` runs the three pages with the tests' fake backend and the EV-3 fictional tracker, on 127.0.0.1. `app.py` doesn't change.
- An AppTest check that the demo renders all three pages from fictional data only.

### 10. Small fixes carried from week 5 (each its own commit, no OQ needed)
- `bedrock_agentcore.app`'s logger to WARNING.
- `GatewayRole`'s description: six tools, not four.
- `SqliteMemory.company_summary`: match the namespace exactly, not with `LIKE` on a prefix.
- **I decide separately:** the drafting retry tells the model which rule failed (a behavior change in `followup_draft.py`).

### 11. The first full run and the freeze (week 6)
- When the set has at least 250 emails (or all my mail holds):
  - `split.py freeze`;
  - one Tier 2 run on main (manual dispatch);
  - the baselines recorded in CLAUDE.md.
- Prompts are tuned only on dev from here (§12 step 2).

### 12. The gate check (OQ-47 (l))
- Branch `gate-check`:
  1. weak change (1) and re-recorded replies: Tier 1 must fail on M4;
  2. weak change (2): Tier 1 must fail on invite recall;
  3. a manual `tier2.yml` run on the branch must fail;
  4. main passes both tiers;
  5. delete the branch.
- I record the run links.

### 13. Week 7: measure, baseline, write
- EV-6 (step 5's runner with `--mode local`, and `run_chat.py --mode local`), with Ollama on.
- `ops_report.py` for M5 and M6 (the month to date); M7 from my week of time log.
- The README:
  - what it does, and the architecture (Mermaid);
  - the measured table (M1 to M8: value, n, date, set version);
  - how measured;
  - the two modes;
  - the safety layers and what was proven live;
  - the cost;
  - the screenshots.
- PRD v0.5.0: the §11 targets confirmed or revised from the run; the §18 confirmation pass; the milestone row.
- OQ-47 (n): a noreply author from now on; before going public, the history and Actions-log scan; then my choice of (n1) or (n2).

### 14. Exit checks
- Each exit as listed above, with the run links and the numbers recorded in CLAUDE.md.
- A short report to the head session after each.

## What I do by hand
- ~~Decide OQ-47 item by item; answer (d) and (j).~~ Done 2026-10-26. Approve the dependencies after a source check.
- `aws login`, then: the day-8 check, the end-of-week log scan, step 0's read-only checks, the `Evaluate` spike.
- Write the EV-1s pattern notes; review and label the 60 synthetic emails.
- **Fetch and label 250 to 300 emails**, reading each window's whole inbox for the job emails the query missed.
- Record replies with Nova whenever a prompt changes (`eval/record.py`), and commit them with the change.
- Push (or say yes to a session's push); run Tier 2 by hand for the first full run and the gate check.
- Keep a time log for one week (M7); run `ops_report.py` weekly.
- Turn on Transaction Search only if the spike says (g2).
- Check every screenshot for real data; decide (n1) or (n2) and when the repo goes public.

## Risks
- **Too few labeled emails or invites.** If my mail holds fewer than 250 job emails or 25 invites, the set is smaller and M1's floor means zero misses. Report what exists, with n. Don't pad the set with synthetic emails (they're EV-1s, which is separate).
- **Labeling time.** It's mine and it gates step 11. Starting step 1 first is the mitigation.
- **Replies go stale often.** Every prompt change needs a recording on the Mac. If that's a chore, OQ-47 (a2) (a Tier 1 job on the Mac for pushes) is the fallback.
- **Tier 2 starves live mail.** The pacing cap and the dead-letter alarm cover it. A Tier 2 run is never started during a deploy (one runner, so jobs queue anyway).
- **`aws login` expires.** Tier 2's weekly run will often fail at the session step. It fails with its own message, and a manual re-run follows.
- **AgentCore Evaluations won't score an agent outside the Runtime.** The (g2) and (g3) fallbacks are in OQ-47. M3 is computed in code either way.
- **Judge and Evaluations cost.** Unknown until the pricing check. The judge tokens are reported per run, and the design doc's $1 to $3 a month is the bar.
- **Nova at temperature 0 still varies.** Replay freezes one sample, and Tier 2's live runs show the spread. Report Tier 2 numbers as the measured ones.
- **The frozen split leaks into tuning.** Prompts are tuned on dev only. The test split's labels are read only by the scorer.
- **The account's 6-month horizon** (OQ-39 (c)) isn't affected: weeks 6 to 7 end in early November.
- **Going public exposes history.** The scan comes before the visibility flip, and the flip is mine.
