// OQ-45 (b), ALR-3: no tool takes a recipient, so any call whose input has a
// recipient-like key is denied, for every IAM caller and each of the six. The
// Gateway's schemas don't refuse extra keys; the tool's own check does too.
// It names the six tools: a forbid on every action also covers future tools,
// which AgentCore refuses as "Overly Restrictive" (run 39803468956). And
// `context has input` first: validation refuses an unguarded access to the
// optional input (run 39775820712).
// On AgentCore Gateway it never fires: Cedar sees only the inputs a tool's
// schema declares, so these keys are never present (week 5 probe, 2026-10-24).
// There each tool's own schema check refuses them; cedarpy, which sees the
// whole input, enforces this locally (OQ-46).
forbid(
  principal is AgentCore::IamEntity,
  action in [AgentCore::Action::"read-email___read_email", AgentCore::Action::"upsert-application___upsert_application", AgentCore::Action::"list-applications___list_applications", AgentCore::Action::"notify-owner___notify_owner", AgentCore::Action::"search-gmail___search_gmail", AgentCore::Action::"create-gmail-draft___create_gmail_draft"],
  resource == ?gateway
) when { context has input && (context.input has to || context.input has recipient || context.input has recipients || context.input has phone || context.input has email || context.input has cc || context.input has bcc) };
