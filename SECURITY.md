# Security

Mini Claude Code can execute shell commands, modify files, start subprocesses, and call network-backed MCP tools. Run it only inside a trusted workspace and review approval prompts carefully.

## Built-in safeguards

- filesystem paths are resolved and restricted to the current workspace;
- known destructive PowerShell commands are denied;
- mutating tools require approval;
- MCP annotations participate in permission decisions;
- dirty Git Worktrees are not deleted unless discard is explicitly enabled;
- credentials, conversation sessions, and runtime state are excluded by `.gitignore`.

These checks reduce accidental damage but are not a hardened sandbox. Do not expose the CLI as an unauthenticated remote service.

Every tool call is authorized in `tool_executor`, so the main loop, subagents,
background workers, teammates and Cron delivery are all subject to the same
PreToolUse decision. Most paths call `execute_tool`; the main loop calls the same
`authorize_tool` step separately because it branches on compaction and background
dispatch between the decision and the handler. Session switching is serialized
against Cron delivery by `agent_lock`.

Whether a thread may prompt for approval is declared per thread. Only the main thread
is interactive; teammate, background and Cron threads are not, and an operation that
would need a prompt is refused there with an explanation rather than being executed or
blocking on stdin. Those threads can therefore only run operations the policy allows
outright, or an identical operation the user previously granted a long-term approval
for. Approval fingerprints include the workspace, so a grant made in the main
workspace does not carry into a teammate's Worktree. In practice teammates are
read-only plus whatever was pre-approved.

Known gaps (2026-09-13): the PowerShell deny list is keyword matching, not a policy
engine, and it is not a sandbox. A fresh-Windows install has not been verified. Cron
delivery is at-least-once, so a batch that fails partway can redeliver jobs that
already reached a session. See [release readiness](docs/RELEASE_READINESS.md) for the
current gap list and acceptance criteria.

## Reporting

Do not open a public issue containing credentials or private prompts. Remove secrets from logs and provide a minimal reproduction when reporting a security problem.
