# Contributing

The GitHub front page is [README.md](README.md) (Chinese). English: [README.en.md](README.en.md).

## Local checks

```powershell
python -m pip install -e ".[dev]"
python -m compileall -q src
python -m pytest
python -m ruff check src tests examples
python examples/cache_replay.py
python -m pip wheel --no-deps . --wheel-dir dist
```

The legacy snapshot is intentionally immutable. Do not edit `docs/legacy_main.py` or update its expected hash to hide a change. New behavior belongs in the modular package with an offline test.

Runtime files such as `.env`, `.memory/`, `.sessions/`, `.tasks/`, `.mailboxes/`, `.transcripts/`, `.worktrees/`, and `.scheduled_tasks.json` must not be committed.
