// OQ-44 (j), APR-3: the approval workflow's role may save a draft, and only
// with an approval_id.
permit(
  principal == ?workflow,
  action == AgentCore::Action::"create-gmail-draft___create_gmail_draft",
  resource == ?gateway
) when { context.input has approval_id };
