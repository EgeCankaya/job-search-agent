# Design doc edits for OQ-43: deploying from main without OIDC

**For:** the PRD v0.4.2 and the design doc (Claude Doc, revision 105)
**What triggered it:** on 2026-10-19, starting week 3 step 8 (OPS-1), the account turned out unable to hold a GitHub OIDC provider.
**Status:** **accepted on 2026-10-19 (option (a)), and applied the same day** to the PRD (v0.4.3) and the source Claude Doc (revision 105 → 106). The design-doc edit was checked by reading the doc back.
**Depends on:** OQ-39 (Free plan, the new sign-up experience), OQ-42 (l), (m).

## The finding (2026-10-19)

The new sign-up experience places the account in an AWS-managed organization (OQ-39). That organization's service control policy explicitly denies the IAM OIDC provider actions:

| Check | Result |
|---|---|
| `iam:ListOpenIDConnectProviders` | AccessDenied, "explicit deny in a service control policy" |
| `iam:GetOpenIDConnectProvider` on `token.actions.githubusercontent.com` | AccessDenied, same SCP |
| Policy simulator, `iam:CreateOpenIDConnectProvider` | `explicitDeny`, `AllowedByOrganizations: False` |

An SCP applies to every principal in the account, CloudFormation's execution role included, so no stack can create the provider either. OPS-1's "authenticate through OIDC to an IAM role" can't be built here. Leaving the managed organization needs the Paid plan plus "activate advanced features", which is irreversible and was ruled out in OQ-39.

## The options

| Option | What it is | Keeps "no long-lived AWS keys in GitHub" | Visible in the repo | Cost and effort | Main weakness |
|---|---|---|---|---|---|
| **(a) Self-hosted runner on the Mac** | The deploy job runs on the Mac's GitHub Actions runner, which week 6 sets up anyway for Tier 2, using my `aws login` session. Never on `pull_request` | Yes: no AWS secret in GitHub at all | Yes: every deploy is an Actions run on main | Free; the runner moves from week 6 to week 3 | Deploys wait while the Mac sleeps or the `aws login` session has expired; the runner holds my full-access session |
| (b) AWS CodeBuild from GitHub | A CodeBuild project, connected to the repo through AWS CodeConnections, deploys on push to main and reports its status to the commit | Yes | Partly: a status check on each commit, logs in AWS | CodeBuild minutes; a CodeConnections GitHub app; the SCP may block parts of it too (unchecked) | More AWS setup, unverified on this account |
| (c) An IAM user's access keys in GitHub secrets | The usual fallback | **No**: breaks OPS-1 and NFR-SEC-3 | Yes | Small | A leaked key is a standing credential to the account |
| (d) Defer OPS-1 | Deploy by hand from the Mac until week 6 or later | Yes | No | None | The week 3 exit "a push to main deploys" can't pass; the CV gap stays open |

**Recommendation: (a).** It keeps the security property OPS-1 exists for (no AWS credential stored in GitHub), gives a reviewer the evidence OPS-1 wants (deploy runs on main in the Actions tab), costs nothing, and reuses the runner week 6 needs anyway. The runner's safety rules from §12 apply from day one: the deploy workflow never runs on `pull_request`, and only I push to main. The weakness is real but small for a one-person project: a deploy waits until the Mac is awake and `aws login` is fresh.

## What changes if (a) is accepted

| PRD section | Change |
|---|---|
| OPS-1 | "It SHALL authenticate through OIDC to an IAM role whose trust policy allows only this repo's main branch" → "It SHALL run on the self-hosted runner on my Mac, using my `aws login` session, and only for pushes to main. The account's managed-organization SCP denies IAM OIDC providers (OQ-43)." Keep "no long-lived AWS keys SHALL be stored in GitHub". The Tier 2 role sentence becomes: Tier 2 runs on the same runner and session |
| OPS-1 acceptance | "Assuming the deploy role from another branch fails" → "The deploy workflow has no `pull_request` trigger and runs only on main" |
| NFR-SEC-3 | "GitHub Actions SHALL deploy through OIDC, with no long-lived AWS keys" → "GitHub Actions SHALL deploy with no AWS credential stored in GitHub (OQ-43)" |
| §14 week 3 | "GitHub Actions deploy with OIDC (OPS-1)" → "GitHub Actions deploy on the self-hosted runner (OPS-1, OQ-43)"; the self-hosted runner moves from weeks 6 to 7 to week 3 |
| §16a GitHub row | Status: OIDC blocked by the account's SCP (checked 2026-10-19); self-hosted runner instead |
| §16 risks | Add: deploys wait while the Mac sleeps or the session has expired. Mitigation: a manual run (`workflow_dispatch`) after `aws login` |
| OQ-42 (l) | Superseded: no `InboxAgentCi` stack, no OIDC provider, no deploy role, no AWS secrets in GitHub |

| Design doc place | Change |
|---|---|
| Build plan, week 3 | "Deploy from main through GitHub Actions with OIDC" → "Deploy from main through GitHub Actions on a self-hosted runner on the Mac (the account can't hold an OIDC provider)" |
| Evaluation section, Tier 2 sentence | No change: it already runs on the Mac's runner |

## What the code change will be (after acceptance)

1. `.github/workflows/deploy.yml`: on push to main and `workflow_dispatch` only; a `check` job (pytest, synth in both modes) on GitHub's runners, then a `deploy` job on `[self-hosted, macOS]` that runs `cdk deploy InboxAgentAlwaysFree InboxAgentAgentCore`, reading the alert address from the topic's confirmed subscription, as I do by hand. Actions pinned to commit SHAs.
2. `.github/workflows/check.yml`: the same `check` job on `pull_request`, on GitHub's runners, with no secrets.
3. Tests on both files: no `pull_request` in the deploy workflow, the deploy job only on the self-hosted runner, no AWS key names, every action SHA-pinned.
4. You install and register the runner on the Mac (GitHub's runner package, as a launchd service), labelled `self-hosted, macOS`.

## Record your decisions

| Item | Accept / Reject | Date | Note |
|---|---|---|---|
| OPS-1 deploys on the self-hosted runner (a) | Accept | 2026-10-19 | |
