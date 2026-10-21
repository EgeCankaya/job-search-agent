// OQ-45 (a), TOOL-2: nothing may send or forward. No such tool exists, and
// AgentCore refuses a policy that names a tool the Gateway doesn't serve, so
// this denies every action outside the PRD's six, for every IAM caller. A
// tool added later is denied until this list names it.
forbid(
  principal is AgentCore::IamEntity,
  action,
  resource == ?gateway
) unless { action in [AgentCore::Action::"read-email___read_email", AgentCore::Action::"upsert-application___upsert_application", AgentCore::Action::"list-applications___list_applications", AgentCore::Action::"notify-owner___notify_owner", AgentCore::Action::"search-gmail___search_gmail", AgentCore::Action::"create-gmail-draft___create_gmail_draft"] };
