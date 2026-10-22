// OQ-45 (b), ALR-3: no tool takes a recipient, so any call whose input has a
// recipient-like key is denied, for every IAM caller and every tool. The
// Gateway's schemas don't refuse extra keys; the tool's own check does too.
// `context has input` first: AgentCore's own actions (InvokeAgent) may have no
// input, and validation refuses an unguarded access (run 39775820712).
forbid(
  principal is AgentCore::IamEntity,
  action,
  resource == ?gateway
) when { context has input && (context.input has to || context.input has recipient || context.input has recipients || context.input has phone || context.input has email || context.input has cc || context.input has bcc) };
