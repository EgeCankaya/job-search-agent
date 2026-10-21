// OQ-44 (j): the agent (the Runtime's role) may call its five tools, never
// create_gmail_draft.
permit(
  principal == ?agent,
  action in [AgentCore::Action::"read-email___read_email", AgentCore::Action::"upsert-application___upsert_application", AgentCore::Action::"list-applications___list_applications", AgentCore::Action::"notify-owner___notify_owner", AgentCore::Action::"search-gmail___search_gmail"],
  resource == ?gateway
);
