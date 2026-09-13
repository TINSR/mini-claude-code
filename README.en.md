# Mini Claude Code

[中文](README.md) · [English](README.en.md)

[![CI](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml)

I rebuilt a Claude Code–style agent after [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code). No LangChain. Windows + PowerShell. It reads and writes files, runs commands, and decides what to do next.

It started as a 4.5k-line file. I split it; the old snapshot is still in [`docs/legacy_main.py`](docs/legacy_main.py), untouched. Not an official product. Not a sandbox.

## Run it

Python 3.11+, from the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Put a key in `.env`, then `mycc`. `mycc --help` works without one.

For DeepSeek, set `ANTHROPIC_BASE_URL` to `https://api.deepseek.com/anthropic` and `MODEL_ID` to whatever your account has.

Session commands: `/new`, `/sessions`, `/switch`, `/history`. History lives in `.sessions/`. Stuff that should survive a session switch is in `.memory/`.

## What’s in here

The loop is just messages and `tool_use`. Writes and dangerous commands ask first. Subagents, background jobs, teammates, and cron share one permission path. Background threads never prompt — they refuse instead of blocking.

History is append-only so prefixes stay stable. When it gets huge, a summary replaces the old turns and the full log goes under `.transcripts/`. Memories are markdown files, pasted onto the current user message, not into the system prompt.

Teammates can work in a Git worktree. MCP is stdio. `skills/` and `mcp_servers/` are samples and are not in the wheel.

Longer notes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (Chinese).

## Caching

I wanted to know if a stable prefix actually costs more. Offline replay (`python examples/cache_replay.py`, no API) keeps the previous prefix on 9/9 adjacent pairs instead of 2/9, and sends about twice the characters.

The same sequence on DeepSeek: hit share about 31% → 82%, uncached tokens down by more than half, total prompt up. Cheaper only if a cached token is under ~37% of the miss price. One synthetic run. Details in [`docs/CACHE_POLICY.md`](docs/CACHE_POLICY.md).

Usage: `python -m mini_claude_code.usage_report`. Logs in `.transcripts/usage_*.jsonl`, no prompt text.

## Tests

```powershell
python -m pytest
python -m ruff check src tests examples
```

No live model. No real git worktrees. MCP tests spawn a local Python process and take ~30s.

## Don’t expect

The PowerShell filter is keyword matching, not a sandbox. Teammates are mostly read-only unless you already approved that exact write. Compaction breaks the cache prefix. Session commands wait if cron is in the middle of a turn.

## License

[MIT](LICENSE)
