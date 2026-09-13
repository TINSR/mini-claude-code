# Changelog

## 0.1.0 — 2026-09-13

First public snapshot of the modular agent.

- Agent loop with PowerShell, filesystem tools, sessions, memory, cron, teammates, worktrees, and MCP
- One authorization entry for the main loop, subagents, background workers, teammates, and cron
- Append-only history, bounded new tool results, and transactional compaction
- Crash-safe writes for session, cron, and approval JSON
- MCP transport with handshake order, request timeouts, and strict response matching
- Offline tests plus a live DeepSeek cache comparison documented in `docs/CACHE_POLICY.md`
