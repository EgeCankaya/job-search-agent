// OQ-44 (j), TOOL-4: no other IAM caller may save a draft. A forbid wins over
// any permit. Scoped to IAM callers, the only kind this Gateway accepts: an
// unscoped forbid also covers OAuth users, for whom it denies every request,
// and AgentCore's validation refuses that as "overly restrictive" (first
// deploy, 2026-10-20). Default deny covers the rest.
forbid(
  principal is AgentCore::IamEntity,
  action == AgentCore::Action::"create-gmail-draft___create_gmail_draft",
  resource == ?gateway
) unless { principal == ?workflow };
