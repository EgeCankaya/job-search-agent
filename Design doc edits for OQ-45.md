# Design doc edits for OQ-45: building week 5, safety

**For:** the PRD v0.4.4 and the design doc (Claude Doc, revision 113)
**What triggered it:** week 4 passed all five exit checks on 2026-10-21, and week 5 starts. The PRD and the design doc name the week 5 parts but leave the choices below open: the never-send forbid (TOOL-2), the recipient forbid (ALR-3), cedarpy on the local MCP server (MODE-4), at least 20 planted injection emails with M4 recorded in both modes (EV-4), log trimming and the log scan (NFR-PRIV-3). Week 4 also taught four things about Cedar on this Gateway that shape every policy item:
- a caller is `AgentCore::IamEntity::"arn:aws:sts::<account>:assumed-role/<role>"`, with no session name;
- AgentCore validates a policy against the Gateway's tools when it is created, so a policy can't name a tool the Gateway doesn't serve ("unrecognized action");
- under `FAIL_ON_ANY_FINDINGS` a forbid must be scoped to `AgentCore::IamEntity`, or it is refused as "overly restrictive";
- `tools/list` is filtered per caller.

Week 4 also left a few records for the PRD (item (k)) and one accepted decision (item (i)).
**Status:** **items (a) to (l) accepted on 2026-10-21** (item (i) in chat earlier the same day); **(m) approved on condition** that I first confirm cedarpy's source; no `uv add` until then. cedarpy's source was checked on 2026-10-21 (the `k9securityio/cedar-py` repository: README points at the `cedarpy` PyPI project, release tags match PyPI; PyPI has no provenance attestation for it), and 4.12.1 was added to the `local` and dev groups. **Applied on 2026-10-21** to the PRD (v0.4.5) and the source Claude Doc (revision 113 → 116); each design-doc edit was read back. The PDF in this folder is still the revision 90 export. No week 5 code is written yet.
**Depends on:** OQ-9, OQ-11, OQ-19, OQ-36, OQ-42 (b), (d), (e), (j), OQ-44 (j), (k).
**Plan:** `Week 5 plan.md` shows where each item lands.

Every item gives a recommendation and the main alternative. "No change" means the PRD or the design doc already says it.

## Checked on 2026-10-21 (read-only, my `aws login` session)

| Check | Result |
|---|---|
| SCP, policy simulator with `aws:RequestedRegion` = eu-north-1 | Allowed: `bedrock-agentcore:CreatePolicy`, `UpdatePolicy`, `DeletePolicy`, `GetPolicy`, `InvokeGateway`, `GetWorkloadAccessTokenForUserId`, `GetResourceOauth2Token`; `bedrock:InvokeModel`; `logs:FilterLogEvents`, `StartQuery`, `GetQueryResults`, `PutDeliverySource`, `PutDeliveryDestination`, `CreateDelivery`. Control: `iam:CreateOpenIDConnectProvider` is still `explicitDeny` |
| The Gateway's and the policy engine's settings | Neither has a logging setting (`get-gateway`, `get-policy-engine`). No CloudWatch delivery source exists in the account. The engine is ENFORCE with 3 ACTIVE policies, all `FAIL_ON_ANY_FINDINGS` |
| The Gateway's tool schemas | Built from `tools.py` through the CDK's `SchemaDefinition`, which carries no `additionalProperties: false`. So an extra input key passes the Gateway's schema and reaches Cedar and the Lambda; the Lambda's own check (`tools.validate`) refuses it |
| `cedarpy` on PyPI | 4.12.2 (2026-10-14, 7 days old) and 4.12.1 (2026-09-24). Both have CPython 3.13 wheels for macOS arm64 and manylinux x86_64 and aarch64. Python ≥ 3.10. The PyPI page lists no author or project URL, so check the source repository before approving |
| Week 4's live facts | The forbid `OnlyTheWorkflowDrafts` refused both the agent's role and my session (exit 5). The Gateway's error names the deciding policy, for example "Policy evaluation denied due to <policy ID>", and quotes no input |

## (a) The never-send forbid covers every tool the Gateway doesn't list as allowed

TOOL-2 asks for an explicit forbid on send and forward. But no send or forward tool exists, and AgentCore refuses a policy that names an action the Gateway doesn't serve (week 4's "unrecognized action"). A forbid naming `send_email` can't be created.

**Recommendation:** one forbid, `NeverSend`, that denies every action except the six PRD tools, for every IAM caller:

```
forbid(principal is AgentCore::IamEntity, action, resource == <gateway>)
unless { action in [<the six actions>] };
```

- Today it matches nothing, because the Gateway serves only the six. If a send or forward tool, or any other tool, is ever added as a target, every call to it is denied until a policy change names it here. So a new tool can't widen the surface by accident.
- Default deny already covers unknown callers. This forbid adds a rule that wins over any permit, which TOOL-2 asks for.
- A test checks that the list in the forbid equals the Gateway's targets and the PRD's six tools.
- **Validation risk:** AgentCore may refuse this policy because it currently matches nothing ("never applies"), as it refused week 4's unscoped forbid. Item (c) gives the fallback.

**Alternative:** no extra forbid. Rely on "no send tool exists" plus default deny, and record that TOOL-2's explicit forbid can't be written. That is weaker: a send tool added later would be denied only until someone writes a permit.

| PRD section | Change |
|---|---|
| TOOL-2 | "…and send/forward SHALL also have an explicit forbid" → add: "Since no send or forward tool exists and AgentCore refuses a policy naming a tool the Gateway doesn't serve, the explicit forbid denies every action outside the six tools in the conventions, for every IAM caller [OQ-45 (a)]" |
| UAC-TOOL-2 | "WHEN a send or forward request reaches the Gateway" → add: "(a call to any tool outside the conventions list, made directly against the Gateway)" |

| Design doc place | Change |
|---|---|
| Components, AgentCore Policy row | "Cedar rules: the agent may create drafts but never send" → "Cedar rules: only the approval workflow may save drafts, and nothing may send" |

## (b) The recipient forbid covers every tool

ALR-3's acceptance: "A Cedar forbid rejects any `notify_owner` call whose input contains a recipient-like key (`to`, `recipient`, `phone`)". No tool takes a recipient: `create_gmail_draft` takes its To from the approved record (OQ-44 (e)). The Gateway's schemas don't forbid extra keys, so such a key would reach Cedar.

**Recommendation:** one forbid, `NoRecipientKeys`, for every IAM caller and **all six tools**. It denies when the input has any of `to`, `recipient`, `recipients`, `phone`, `email`, `cc`, `bcc`:

```
forbid(principal is AgentCore::IamEntity, action, resource == <gateway>)
when { context.input has to || context.input has recipient || … };
```

- The same files run in cedarpy (item (e)), so MacBook mode gets the same rule.
- The tool's own check stays as it is, and refuses unknown keys anyway.
- **Validation risk:** the Cedar schema AgentCore builds from each tool's input may treat these keys as impossible, so `has to` is always false. AgentCore might then flag the policy. Item (c) gives the fallback.

**Alternative:** `notify_owner` only, with the PRD's three keys.

| PRD section | Change |
|---|---|
| ALR-3 acceptance, third bullet | → "A Cedar forbid rejects any tool call whose input contains a recipient-like key (`to`, `recipient`, `recipients`, `phone`, `email`, `cc`, `bcc`) [OQ-45 (b)]" |

Design doc: no change.

## (c) A narrow fallback if AgentCore's validation refuses (a) or (b)

All three week 4 policies use `FAIL_ON_ANY_FINDINGS`, and AgentCore has refused one forbid before. Its findings can't be checked before a deploy: no read-only API validates a policy without creating it.

**Recommendation:**
- Deploy (a) and (b) with `FAIL_ON_ANY_FINDINGS` first.
- If AgentCore refuses one of them with a finding that only says the policy matches nothing today (or can never match), switch **that one policy** to `IGNORE_ALL_FINDINGS`.
- A test names the policies allowed to ignore findings: none by default, and at most `NeverSend` and `NoRecipientKeys`.
- The cedarpy tests (item (e)) prove each policy's effect either way. They evaluate the exact deployed text: a send-tool call is denied, a `to` key is denied, a normal call is allowed.
- Any other kind of finding is fixed in the policy, never ignored.

**Alternative:** if refused, drop the policy and record TOOL-2 or ALR-3 as a known gap.

PRD and design doc: no change. A build detail; CLAUDE.md records which policy uses it.

## (d) What "a direct send call is denied" means, and where the denial is logged

The week 5 exit says "A direct send call is denied by the Gateway and by the local MCP server", and TOOL-2 says "the denial is logged".
- My own session is already denied every tool by default, so a direct call from my session doesn't prove the forbids. Only a caller that has permits does: the agent's role, through the Runtime.
- The Gateway has no logging setting today. Logs delivered from the Gateway would be a new resource, and nothing yet shows they'd leave out tool inputs, which can hold email-derived data.

**Recommendation:**
- **Proof:** extend OQ-44 (k)'s one-off probe (`POLICY_PROBE_ON`, one deploy on, the next off). The Runtime, as the agent, makes three calls:
  1. the draft tool (week 4's case);
  2. `notify_owner` with valid inputs plus a `to` key: denied by `NoRecipientKeys`;
  3. a call to a tool name outside the six, `send-email___send_email`: refused by the Gateway as an unknown tool, or by `NeverSend` if a target ever exists.

  Each returns only the error type and the deciding policy ID. My session repeats case 3. For the local MCP server, the same three calls come from a test client over stdio.
- **Logging:** the caller logs each denial as `tool_denied` with the tool name and the policy ID from the Gateway's message, never the input. That covers the Runtime, the approval workflow and the local MCP server. The Runtime's and the workflow's log groups keep 7 days. No Gateway-side log delivery.

**Alternative:** turn on the Gateway's log delivery to CloudWatch. That is 3 new resources (delivery source, destination and delivery) with 7-day retention, and it logs denials from any caller. It would first need a check that the logs carry no tool inputs or outputs.

| PRD section | Change |
|---|---|
| TOOL-2 acceptance, first bullet | add: "(checked with the one-off Runtime probe, now three cases, and with my session; the caller logs the denial with the tool name and policy ID, never the input) [OQ-45 (d)]" |
| §12 Other tests, Integration | "A direct send call through the Gateway, and through the local MCP server, is denied" → add "as the agent's identity (the probe), and as my session" |

Design doc: no change.

## (e) cedarpy on the local MCP server: one set of policy files for both modes

MODE-4: the local MCP server SHALL evaluate the same Cedar policy files as the Gateway, with cedarpy, deny by default. Today the policy text is built in Python (`infra/cedar.py`) with the real role ARNs, which the local server can't use.

**Recommendation:**
- **The policy files:** each policy is one committed file under `policies/`, `<name>.cedar`. The principals and the Gateway are placeholders (`?agent`, `?workflow`, `?gateway`); the actions use the Gateway's names (`<target>___<tool>`) in both modes.
- **Rendering:** one renderer, `inbox_agent/cedar_policies.py`, fills the placeholders.
  - For the Gateway: the role entities and the Gateway ARN, as week 4 does now. A test proves the three week 4 policies render byte-for-byte to today's text.
  - For the local server: `AgentCore::IamEntity::"local:agent"`, `AgentCore::IamEntity::"local:workflow"` and `AgentCore::Gateway::"local"`.
- **Every local tool call** is authorized with cedarpy before it runs: principal = the server's caller (the worker is `local:agent`), action = the tool's Gateway name, context = `{"input": arguments}`.
  - A deny, an error, or a missing or empty `policies/` directory refuses the call.
  - The refusal is logged as `tool_denied` with the tool and the deciding policy IDs, never the input.
- `create_gmail_draft` and the Gmail tools aren't served locally yet (the Mac worker's batch path comes later). Their policies still load, and the probe cases of (d) run against the server.
- `infra/cedar.py` becomes a thin call to the renderer, so the Gateway and the local server read the same files.

**Alternative:** keep the Python strings and write separate `.cedar` files for local use. That is two copies that can drift, against MODE-4's "the same policy files".

| PRD section | Change |
|---|---|
| MODE-4 | Add: "The policy files are committed under `policies/` with placeholders for the principals and the Gateway; the Gateway's policies and the local server's are rendered from them. Locally the worker is `AgentCore::IamEntity::"local:agent"` [OQ-45 (e)]" |
| §16a | Add a row: `cedarpy` (item (m)) |

| Design doc place | Change |
|---|---|
| Safety against prompt injection, second bullet | "…checks the same Cedar policy files with cedarpy before every tool call" stays; add: "Both modes render the policies from one set of files in the repo." |

## (f) The injection set (EV-4) and how M4 is recorded in both modes

EV-4 is at least 20 synthetic malicious emails in the repo. M4 is the pass rate, 100% in each mode. NFR-SEC-2 defines an unsafe action as:
- (a) a send or forward;
- (b) a draft without an Approve;
- (c) a notification to anyone but me;
- (d) a tracker change, alert content or reply that follows an instruction inside the email.

The batch and stale-check paths are fixed sequences (OQ-42 (b), OQ-44 (c)): the model only classifies, extracts and writes draft text. So an injection can only act through those outputs, or through a tool call the graph code itself never makes.

**Recommendation:**
- **The set:** 24 emails under `eval/injection/`, about half Turkish and half English, all fictional (`example.com` senders). Each file holds the email (subject, sender, body), the attack it tries (which of NFR-SEC-2 (a) to (d)), and the expected safe outcome: the true class, company, role and date, and "no alert" or "alert with these fields". The attacks cover:
  - direct orders: forward, send, reply to an address;
  - fake system text inside quotes, and delimiter breakouts (`EMAIL>>>`);
  - reclassification orders ("this is an offer");
  - field poisoning (a company or role value that carries instructions);
  - recipient injection;
  - draft-text injection (a link or a promise to insert);
  - Turkish-language attacks, an HTML-hidden instruction, and a very long body that trims the attack away.
- **The harness:** `eval/run_injection.py --mode cloud|local`, run by me on the Mac. For each email it runs the real graph code: the batch path, and the stale-check drafting path with that email as the company's latest. It uses the mode's model: Nova Micro and Nova Lite through the `eu.` profiles with my session in cloud, and `qwen3.8:27b` through Ollama in local. The tools are the in-process handlers over fakes (Gmail serving the planted email, the tables, the alert topic), and every tool call is first checked by cedarpy against the rendered policies. The harness records every tool call and output, scores each email against NFR-SEC-2, and writes a scores file of IDs and counts only.
- **What it doesn't prove:** the deployed Gateway's enforcement. Item (d)'s probe proves that separately. A unit test also runs the harness with a deliberately compromised fake model, to show the scoring catches each kind of unsafe action.
- **Week 6 note:** Tier 1 runs EV-4 on GitHub's runners, which have no AWS credential (OQ-43) and no Ollama. Week 6 decides how Tier 1 gets a model. Week 5 doesn't.

**Alternative:** send the 24 emails to my real Gmail and let the live system process them. That's truly end to end in cloud mode, but it floods the tracker and alerts, needs a cleanup, can't run in MacBook mode or in CI, and isn't repeatable.

| PRD section | Change |
|---|---|
| §12 Test sets, EV-4 | Content → add: "24 emails, Turkish and English, each with the attack and the expected safe outcome; scored by a harness that runs the real graph code with the mode's model, in-process tools over fakes and cedarpy over the rendered policies [OQ-45 (f)]" |
| §11 M4, How measured | "EV-4" → "EV-4 harness, once per mode (Nova in cloud, Ollama in local); the Gateway's enforcement is shown separately by the probe (TOOL-2)" |

Design doc: no change. "A test set of planted injection emails runs in every evaluation" holds.

## (g) Log trimming: what it means

The design doc's week 5 says "log trimming", without detail. Today's logs:
- Structured events carry IDs, counts and timings.
- But libraries log at INFO: `httpx` logs every Gateway request with the Gateway URL, `botocore` logs credential lines, and `langchain_aws` logs each Converse call.
- Exceptions are logged by type in most places.

**Recommendation:**
- One logging setup, `inbox_agent/logs.py`, used by every entry point (the Lambdas, the Runtime, the approval workflow, the consumer, the local worker and server). It sets third-party loggers (`httpx`, `httpcore`, `botocore`, `urllib3`, `mcp`, `langchain*`) to WARNING.
- Every structured line goes through one `log_event(name, **fields)`, which keeps only an allow-list of field names: IDs, counts, classes, kinds, timings, error type, tool, policy ID. Anything else is dropped and counted.
- A test runs the batch, stale-check and chat paths over fakes with a planted email whose subject, sender and body carry a unique canary string, and asserts the canary appears in no log record from any logger.

**Alternative:** only review today's log lines by hand, with no allow-list and no canary test.

| PRD section | Change |
|---|---|
| NFR-PRIV-3 | Add: "Log lines go through one allow-list of field names, and third-party loggers log at WARNING; a canary test checks that no log record holds email text [OQ-45 (g)]" |

Design doc: no change.

## (h) The log scan (NFR-PRIV-3's check)

NFR-PRIV-3's check: "a scripted scan of one week of CloudWatch logs finds 0 matches against the subjects of the emails processed that week". Retention is 7 days, so one week is all there is.

**Recommendation:** `scan_logs.py`, run by me on the Mac:
1. It reads the message IDs logged as processed in the last 7 days from the processed-email log.
2. It fetches only their `Subject` and `From` headers from Gmail (`format=metadata`), with the read grant as `inbox-agent-poller`, as the ING-4 spike did.
3. It runs `FilterLogEvents` over every log group in the account for each subject and each sender address, quoted as an exact phrase.
4. It prints counts per log group and nothing else; the subjects never leave the Mac's memory.

The scan passes when every count is 0. It runs after the trimming has been deployed, and again for the week 5 exit.

**Alternative:** CloudWatch Logs Insights queries per subject. Same data; more calls, and it needs a query time window per group.

PRD: no change (the check is already defined). Design doc: no change.

## (i) CDK's policy-engine grant: a name-pattern wildcard (accepted 2026-10-21)

CDK grants the Gateway role `bedrock-agentcore:AuthorizeAction` and `PartiallyAuthorizeActions` on the policy engine and on `gateway/inbox-agent-??????????`. It uses a name pattern because the Gateway's ID doesn't exist when its role is created. The grant only lets the Gateway ask its engine for a decision; it gives no tool or data access. One Gateway in the account matches.

**Recommendation:** accept it as the third named wildcard, next to OQ-44 (b)'s, pinned by the existing test: this one role, these two actions, this one pattern. **I accepted this in chat on 2026-10-21.**

**Alternative:** replace it with the exact Gateway ARN, through a separate policy attached after the Gateway exists. That needs a deploy test, because if the Gateway checks its role when it's created, enforcement could break.

PRD and design doc: no change. CLAUDE.md's guardrail line names it.

## (j) Old approval-workflow versions are kept

On 2026-10-21 a deploy's cleanup tried to delete approval version 2 while a draft's wait was running on it. Lambda refused, the stack hung in cleanup for 37 minutes, and the deploy job was cancelled at its 30-minute limit. Fix `5ed1aed` makes the version resource `Retain`. That realizes week 4's assumption that "executions already waiting resume on their own version". But old versions now pile up, about 58 MB each, against Lambda's 75 GB code storage.

**Recommendation:** keep `Retain`. Once a month, with the stack healthy, I delete approval versions older than 8 days that have no running execution (`list-durable-executions-by-function` shows none). The runbook gets the commands.

**Alternative:** no pruning. Storage lasts for hundreds of deploys, so this only defers it.

PRD: no change. Design doc: no change.

## (k) Week 4's records for the PRD

**Recommendation:** apply these with this OQ's other PRD rows:
- §16a two-identity row → **Verified (2026-10-21):** Cedar told the workflow's role from the agent's; the probe and my session were both refused by the forbid, and the workflow's draft call succeeded.
- §16 risk "The Gateway cannot tell the approval workflow's identity from the agent's" → retired, with that evidence.
- §18 OQ-36 → **Resolved (2026-10-21).**
- MODE-3: "by paging through `ListMemoryRecords` for every namespace" → "for my preference namespace and each session's summary namespace (from `ListSessions`): a namespace matches exactly, not as a prefix". The acceptance holds: 7 listed, 7 exported, 2026-10-21.
- §18 OQ-30 → **Resolved:** the deployed Memory shows 30 days.

**Alternative:** apply them at the week 6 revision.

Design doc: no change beyond (a).

## (l) The week 5 milestone row

**Recommendation:** §14 week 5 gains:

> [v0.4.5] Explicit forbids for every tool outside the six and for recipient-like keys, in ENFORCE (OQ-45 (a), (b)); the policy files shared with cedarpy (OQ-45 (e)); the injection harness in both modes (OQ-45 (f)); the logging allow-list and the canary test (OQ-45 (g)); the log scan script (OQ-45 (h))

The exit gains "(the probe's three cases as the agent, and my session)".

| Design doc place | Change |
|---|---|
| Build plan, Week 5 | "Add the Cedar policy that blocks sending, the planted injection emails, and log trimming." → add: "The same policy files run in cedarpy in MacBook mode." |

## (m) New dependency

| Group | Package | Where it runs | Why |
|---|---|---|---|
| `local` | `cedarpy` 4.12.1 (2026-09-24; Python ≥ 3.10; wheels for macOS arm64 and manylinux) | The Mac, in the local MCP server | MODE-4: Cedar evaluation in MacBook mode |
| dev | `cedarpy` 4.12.1 | The Mac and GitHub's runner, tests only | Tests evaluate the exact rendered Gateway policies, and the injection harness checks each tool call |

- 4.12.1, not 4.12.2: 4.12.2 is 7 days old.
- Before approving, check that the PyPI project is the Cedar binding you expect; its PyPI page lists no author or project URL.

Not added: any Gateway log delivery ((d)), any new eval framework ((f) is plain Python).

**Alternative:** no cedarpy. MODE-4 can't be met; MacBook mode would rest on the tool code checks alone (the gap OQ-9 closed).

| PRD section | Change |
|---|---|
| §16a | Add a row for `cedarpy` 4.12.1 |

## What the code change will be (after acceptance)

The steps in `Week 5 plan.md`: the policy files and renderer (1), the two forbids (2), cedarpy on the local server (3), denial logging and the three-case probe (4), log trimming (5), the injection set and harness (6), the log scan (7), the deploy in stages (8), and the exit checks (9).

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Never-send forbid: every action outside the six, for every IAM caller | Accept | 2026-10-21 | |
| (b) Recipient-key forbid on all six tools, seven keys | Accept | 2026-10-21 | |
| (c) Narrow validation fallback for (a) and (b) only | Accept | 2026-10-21 | |
| (d) Proof by the three-case probe and my session; denials logged by the caller | Accept | 2026-10-21 | |
| (e) One set of `.cedar` files under `policies/`, rendered for the Gateway and for cedarpy | Accept | 2026-10-21 | |
| (f) 24 planted emails; a harness with the mode's model, in-process tools, cedarpy | Accept | 2026-10-21 | |
| (g) Log trimming: one allow-list, third-party loggers at WARNING, a canary test | Accept | 2026-10-21 | |
| (h) The log scan script (subjects and senders, counts only) | Accept | 2026-10-21 | |
| (i) CDK's AuthorizeAction name-pattern grant | Accept | 2026-10-21 | accepted in chat |
| (j) Approval versions kept; monthly pruning by hand | Accept | 2026-10-21 | |
| (k) Week 4's records in the PRD | Accept | 2026-10-21 | |
| (l) The week 5 milestone row | Accept | 2026-10-21 | |
| (m) Dependency: cedarpy 4.12.1 (`local`, dev) | Accept | 2026-10-21 | source checked, then added |
