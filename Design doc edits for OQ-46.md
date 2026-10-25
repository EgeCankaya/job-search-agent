# Design doc edits for OQ-46: the recipient forbid on the Gateway

**For:** the PRD v0.4.5 and the design doc (Claude Doc, revision 116)
**What triggered it:** week 5 exit 1 (2026-10-24). The Runtime probe, as the agent, called `notify_owner` with valid inputs plus `"to": "someone@example.com"` (OQ-45 (d), case 2). The Gateway's policy engine did not refuse it: the call reached the `notify_owner` Lambda, whose own input check refused it (`ToolInputError`, "notify_owner has no input named like that (1 unknown)", 13:35:17 UTC), so nothing was published. `NoRecipientKeys` was ACTIVE in ENFORCE at the time.
**Status:** **option (a) accepted on 2026-10-25** (the owner, in chat: "Do what you think is best", after the three options below). Applied to PRD v0.4.6. Design doc: no change.
**Depends on:** OQ-11, OQ-42 (d), OQ-45 (b), (d), (e).

## What the evidence says

- Case 1 of the same probe (the draft tool) was refused by `OnlyTheWorkflowDrafts`, and `ApprovalDraft`'s `context.input has approval_id` lets the approval workflow draft (week 4 exit 2). So Cedar on this Gateway does see `context.input`, but only the keys the tool's schema declares.
- AgentCore builds a Cedar schema from each tool's input schema; a key the schema doesn't declare is not in `context.input`, so `context.input has to` is always false and `NoRecipientKeys` never fires. OQ-45 (b) named this risk.
- Locally, cedarpy evaluates the whole input: the stdio tests refuse `upsert_application` with `to` by `NoRecipientKeys` (`eval/results/exit1-stdio-2026-10-24.txt`).
- What already stops a recipient key in cloud mode, whoever calls: every tool's own schema check (`tools.validate`) refuses an unknown key before any work, and `notify_owner` publishes only to the one-subscription topic, which only its role may publish to (ALR-3). The probe proved the first live.

## Options considered

- **(a) Record the limit (accepted).** In cloud mode, ALR-3's "no recipient key" check is each tool's own schema check, shown live by the probe; in MacBook mode cedarpy also enforces `NoRecipientKeys`. `NoRecipientKeys` stays on the Gateway, documented as inert there (it would start working if AgentCore ever passes undeclared keys to Cedar). ALR-3's acceptance and UAC-ALR-3 say so.
- (b) Also add `additionalProperties: false` to the Gateway's tool schemas (a low-level override, since the CDK's `SchemaDefinition` has no such field), so the Gateway may refuse extra keys before the Lambda. Unknown whether the Gateway enforces it; needs a deploy and another probe; still a schema check, not Cedar. Not chosen now; can come back as its own item.
- (c) Declare the seven keys as optional properties in the Gateway's schemas so Cedar can see them. Rejected: the agent would be shown recipient inputs, against PRD §9's fixed inputs (OQ-42 (d)).

## PRD changes (v0.4.6)

| PRD section | Change |
|---|---|
| §9.4 ALR-3 acceptance, third bullet | "A Cedar forbid rejects any tool call whose input contains a recipient-like key (…)" → add: "In MacBook mode cedarpy enforces it on the whole input. On AgentCore Gateway, Cedar sees only the inputs a tool's schema declares, so the forbid can't fire there; in cloud mode each tool's own schema check refuses any undeclared key before it acts, shown by the week 5 probe (2026-10-24) [OQ-46]" |
| §10 threat row "An email instructs the agent to notify someone else" | mitigation: "Cedar forbid on recipient-like keys (ALR-3)" → "recipient-like keys refused by each tool's schema check, and by the Cedar forbid in MacBook mode (ALR-3, OQ-46)" |
| Appendix B, UAC-ALR-3 | "any `notify_owner` call with a recipient input is denied by Cedar" → "any `notify_owner` call with a recipient input is refused before it publishes: by Cedar in MacBook mode, by the tool's schema check in cloud mode [OQ-46]" |
| §18 | add OQ-46, Resolved (accepted 2026-10-25) |

Design doc: no change (it doesn't describe the recipient forbid's mechanism).

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| (a) Record the limit; tool schema check in cloud, cedarpy locally; `NoRecipientKeys` kept, inert on the Gateway | Accept | 2026-10-25 | "Do what you think is best" |
| (b) `additionalProperties: false` at the Gateway | Not now | 2026-10-25 | can return as its own item |
| (c) Declare recipient keys in the schemas | Reject | 2026-10-25 | against PRD §9 |
