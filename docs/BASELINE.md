# Refactoring baseline

The modular project is derived from the original learning implementation at
`my_claude_code/main.py`.

The untouched reference snapshot is stored as `docs/legacy_main.py`.

| Property | Value |
|---|---|
| SHA-256 | `EBC9BC5ADF64B67B247068F6BA7215EFF79B74978505B98686D18F1BDCD52C30` |
| Total lines | 4583 |
| Non-empty lines | 3609 |
| Original size | 108101 bytes |
| Baseline interpreter | Python 3.13 via Miniconda |

## Refactoring contract

1. Preserve the behavior, prompts, tool schemas, function names, and algorithms.
2. Prefer moving function bodies unchanged over rewriting them.
3. Limit changes to imports, module boundaries, state ownership, and entry-point wiring.
4. Validate each extraction before moving to the next subsystem.
5. Keep private runtime data and credentials outside the repository.

## Documented compatibility fixes

The contract above describes the original mechanical extraction. The 2026-09-12
cache-policy change intentionally evolves the modular implementation: append-only
history, bounded new tool outputs, safe compaction, stable system prompts, paged
reads, provider-aware caching and usage logging. See [CACHE_POLICY.md](CACHE_POLICY.md).
The original snapshot and its hash remain unchanged. Extraction scripts are historical
reproduction tools, not upgrade scripts; rerunning them can overwrite current fixes.

The modular package intentionally fixes two integration defects without changing the
immutable snapshot:

- API retry exhaustion is raised after the retry loop instead of after its first iteration.
- The bundled MCP stdio server forces UTF-8 streams so Chinese JSON works on Windows systems
  whose active code page is GBK.
