# Mini Claude Code

[English](README.md) · [中文](README.zh.md)

A Claude Code–style coding agent written in Python from scratch: tool use, context compaction, long-term memory, cron, multi-agent teammates, Git worktrees, and MCP — without LangChain or similar frameworks.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)](https://www.microsoft.com/windows)
[![CI](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml)

**Windows learning / portfolio project**, not an Anthropic product and not a production sandbox. Split from a 4,583-line single file; the original snapshot and hash are in [`docs/BASELINE.md`](docs/BASELINE.md). Inspired by [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code).

## What it does

You type a task. The agent calls PowerShell, reads and writes files, and keeps going until it has an answer. Behind that loop:

- One authorization path for the main agent, subagents, background workers, teammates, and cron
- Context compaction that keeps tool-call / tool-result pairs intact
- Persistent tasks, memory, sessions, and scheduled jobs as local files you can inspect
- Isolated Git worktrees for teammate work
- MCP tools over stdio JSON-RPC, discovered at runtime

## Quick start

Needs **Windows**, **Python 3.11+**, and **PowerShell**. Git worktrees also need the current directory to be a Git repo.

From the project root (this folder if you cloned this repo; `mini-claude-code/` if you cloned the parent tutorial repo):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Help and version work with no API key:

```powershell
mycc --help
mycc --version
```

A free offline check of the cache-prefix design (no network):

```powershell
python examples/cache_replay.py
```

To talk to a real model, edit `.env` then run `mycc` or `mini-claude-code`. Existing environment variables win over `.env`. For DeepSeek’s Anthropic-compatible API set `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` and a `MODEL_ID` your account can use.

The wheel does not ship the sample `skills/` or `mcp_servers/`. Copy those into the working directory if you run from somewhere else.

## Architecture

```text
User
  │
  ▼
CLI / Agent Loop
  ├── Prompt + Skills + Memory
  ├── Context compaction + Recovery
  ├── Tool registry + Hooks + Security
  ├── Background tasks + Cron
  ├── Shared tasks + Worktrees
  ├── Teammate runtime + Message bus
  └── MCP client ── stdio JSON-RPC ── MCP Server
```

| Module | Role |
|---|---|
| `cli.py` | Entry point, tool schemas, agent loop |
| `tools/` | Filesystem and PowerShell tools |
| `tool_executor.py` | Shared authorize-then-run entry |
| `security.py` | Deny list, approvals, MCP annotations, per-thread prompt policy |
| `atomic_io.py` | Crash-safe writes for local JSON state |
| `context_manager.py` | Compaction, transcripts, large-output persistence |
| `model_gateway.py` | Stable tool order, cache markers, budget, raw usage logs |
| `usage_report.py` | Totals by model and purpose; does not invent a billing formula |
| `memory_manager.py` | Extract, retrieve, index, consolidate |
| `task_manager.py` | Persistent tasks, dependencies, claim / complete |
| `worktree_manager.py` | Create, keep, and safely delete worktrees |
| `background_tasks.py` | Background threads and completion notices |
| `cron_scheduler.py` | Cron validation, persistence, delivery to the owning session |
| `team_runtime.py` | Teammate mailboxes, lifecycle, request protocol |
| `mcp_client.py` | MCP lifecycle, timeouts, response matching |
| `session_manager.py` | Sessions, atomic save, switch, history render |

Design notes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (Chinese). Cache rules: [`docs/CACHE_POLICY.md`](docs/CACHE_POLICY.md).

### Session commands

```text
/new [title]      new session and switch to it
/session          current session
/sessions         all sessions
/switch <id>      switch and reprint history
/rename <title>   rename the current session
/history          reprint current history
/delete <id>      delete a session that is not current
/help             command help
```

Sessions live in `.sessions/`. Long-term memory in `.memory/` is shared across sessions.

## Cache evidence (optional)

`python examples/cache_replay.py` compares the old rolling rewrite of tool results with append-only history. On the same 10 synthetic requests, the previous prefix is kept on **2/9 vs 9/9** adjacent pairs. That is a local structure check, **not** a provider hit rate. Append-only also sends more characters: 119,326 vs 228,330.

The same sequence was sent live (`python examples/cache_live_benchmark.py`, DeepSeek `deepseek-v4-flash`, 10 rounds per strategy). Provider-reported cache-read share went **31.1% → 82.2%**, uncached input tokens **25,459 → 11,021**, while total prompt tokens **36,979 → 61,965**. Append-only is cheaper only while a cached token costs less than **36.6%** of an uncached one (about 0.61× at a 1/10 discount, 1.17× more expensive at 1/2). One provider, one model, repetitive synthetic text, thinking off, `max_tokens=32`, no task-quality comparison.

Raw numbers: [offline](docs/cache_replay_result.json), [live](docs/cache_live_result.json). Method and limits: [CACHE_POLICY.md](docs/CACHE_POLICY.md).

```powershell
python -m mini_claude_code.usage_report
```

Logs are `.transcripts/usage_*.jsonl` (metadata and raw usage only). Transcript and tool-result files contain workspace content and must stay private.

## Tests

```powershell
python -m pytest
python -m compileall -q src
python -m ruff check src tests examples
python examples/cache_replay.py
```

Offline tests cover path safety, hooks, permissions, context, memory, task dependencies, worktree deletion, background tasks, cron, team mail, and MCP. They also check that a refused write does not run on the main, subagent, or teammate path; that a turn mid-switch still saves to its own session; that an interrupted save leaves the previous file intact; and that `tests/fake_mcp_server.py` covers handshake order, stray notifications, timeouts, process exit, and concurrent callers.

Tests do not call a real model API and do not create or delete real Git worktrees. MCP transport tests start a local Python subprocess and wait a few seconds.

## Design choices

1. **No agent framework** — the API messages and state machine stay visible.
2. **Keep the teaching shape** — the split mostly moved the original functions; new behavior is additive.
3. **Explicit wiring** — threads, prompts, MCP, and teammates do not rely on implicit cross-file globals.
4. **Protect the workspace by default** — paths stay inside the working directory; known-dangerous PowerShell is denied; a dirty worktree is not deleted unless discard is explicit.
5. **Local files first** — tasks, memory, mail, transcripts, and cron state are inspectable files.

## Limits

This is a learning project. PowerShell, writes, network search, and concurrent teammates belong in a trusted directory you are watching.

**Teammate permissions.** Background threads never prompt. An operation that would ask the user is refused with an explanation. Teammates can read files, glob, and use task tools; they can write only if you already saved a long-term approval for that exact operation; PowerShell is only what the deny list already allows.

**Other gaps.** The PowerShell deny list is keyword matching, not a sandbox. Long-term approvals match workspace + tool + full arguments, so one changed character asks again. Cache numbers above are one synthetic live run, not a real-task quality or full-bill study. Session commands wait while a cron turn is running. A clean Windows install from scratch, and shipping Skills/MCP inside the wheel, have not been verified. Compaction resets the request prefix, so a long session will not keep the 82.2% hit share.

See [SECURITY.md](SECURITY.md) for the permission model.

## Layout

```text
.
├── src/mini_claude_code/   package
├── tests/                  offline regression tests
├── examples/               cache replay (free) and live benchmark (paid)
├── skills/                 sample skills (not in the wheel)
├── mcp_servers/            sample MCP server (not in the wheel)
├── scripts/                historical extract scripts; do not re-run
├── docs/                   architecture, baseline snapshot, cache data
└── .github/workflows/      CI (runs when this folder is the repo root)
```

## License

[MIT](LICENSE)
