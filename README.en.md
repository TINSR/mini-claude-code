# Mini Claude Code

[中文](README.md) · [English](README.en.md) · [CI](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml)

I built this terminal coding agent while working through [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code). Give it a task and it can read files, edit code, and run commands to check the result. Development and testing currently focus on Windows and PowerShell.

It started as a single file of roughly 4,500 lines. The modular version adds sessions, memory, subagents, scheduled tasks, Git worktrees, and stdio MCP. The original snapshot is in [`docs/legacy_main.py`](docs/legacy_main.py) for comparison.

## Run it

You need Python 3.11+ and PowerShell; Git is needed for worktrees.

```powershell
git clone https://github.com/TINSR/mini-claude-code.git
cd mini-claude-code
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `MODEL_ID` in `.env`, then run `.\.venv\Scripts\mycc.exe`. The example uses the native Anthropic URL. For DeepSeek's compatible endpoint, use `https://api.deepseek.com/anthropic` and a model available to your account.

Configuration comes from the working directory; existing environment variables take precedence. To work on another project, launch this executable by its full path from that directory and put your configuration there. The sample `skills/` and `mcp_servers/` directories are not included in the wheel; copy them if needed.

Start with a small project and a task you can verify, such as fixing a failing test. Model calls use your API balance. Help and version commands work without a key.

## Using it

Use `/new`, `/sessions`, `/switch`, and `/history`; `/help` lists their arguments. Sessions live in `.sessions/`, memory in `.memory/`. `exit`, Ctrl+C, and EOF save the current session and close MCP connections. An active Cron turn finishes before shutdown.

Writes and commands that need approval ask first. Background agents and Cron cannot ask interactively, so they refuse operations that still require approval. They can use a saved approval for the same operation in the same workspace. This is a tool permission system, not an operating-system sandbox; try it on a project you can restore.

## Caching

The old implementation shortened previous tool results on each round, changing the request prefix. New results are now bounded before entering history, with full output saved to files. History stays unchanged until compaction is needed.

A recorded DeepSeek synthetic comparison raised the cache-hit share from about 31% to 82%. Uncached input fell from 25,459 to 11,021 tokens, while total prompt tokens rose from 36,979 to 61,965. For those input counts, the cached-token price must be below about 36.6% of the uncached price to reduce input cost. Real coding-task cost and quality have not been compared.

See [`docs/CACHE_POLICY.md`](docs/CACHE_POLICY.md) for data and conditions. Run `.\.venv\Scripts\mycc-usage.exe` to inspect usage. Usage logs omit prompt text, but sessions, memory, compaction snapshots, and saved tool output contain work content. Review them before sharing.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests examples
.\.venv\Scripts\python.exe examples/cache_replay.py
```

Tests use offline model doubles. MCP tests launch local Python subprocesses. The replay is offline; live benchmark scripts make paid API calls.

Architecture and release notes are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md). A complete real coding-task demo is still missing. Compaction breaks the previous cache prefix, and memory calls add usage.

## Credits and license

Based on the teaching implementation in [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code), with further changes here. [MIT](LICENSE); upstream attribution is retained in the license.
