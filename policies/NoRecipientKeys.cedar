// OQ-45 (b), ALR-3: no tool takes a recipient, so any call whose input has a
// recipient-like key is denied, for every IAM caller and every tool. The
// Gateway's schemas don't refuse extra keys; the tool's own check does too.
forbid(
  principal is AgentCore::IamEntity,
  action,
  resource == ?gateway
) when { context.input has to || context.input has recipient || context.input has recipients || context.input has phone || context.input has email || context.input has cc || context.input has bcc };
