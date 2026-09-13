# Stable prefixes and bounded context

## What changed

Normal requests append messages without rewriting older results. `micro_compact`
is a compatibility no-op; the CLI no longer calls it. Big results are shortened once,
before their first model request. Each saved result has an immutable content-hash path.
Teammate Worktree results and summary snapshots are saved inside that Worktree so
its workspace-restricted file reader can follow the references.

`get_system_prompt` excludes changing memory indexes and tool-name lists. Relevant
memories still enter new user messages; tool schemas describe current capabilities.
The gateway sorts tools by name. Adding/removing an MCP tool really changes the prefix
and can invalidate a cache; sorting does not make different schemas identical.

Default limits:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `CONTEXT_TOKEN_BUDGET` | 32,000 | Estimated input including system/tools, plus output reserve |
| Single tool result | 12,000 chars | Larger output becomes a file reference + preview |
| Tool-result batch | 24,000 chars | Soft ceiling; preserve all call/result IDs even if metadata exceeds it |
| File read | 200 lines | `offset` is zero-based; `limit` is 1–2000 |
| Normal compaction tail | 6 messages | Move boundary backwards to preserve tool pairs |
| Reactive compaction tail | 2 messages | Same pairing rule; failed summary never replaces history |

The token estimate is a conservative text heuristic, not a tokenizer. A summary is
requested only if its visible input and at least 1,000 output tokens fit the budget.
Summary thinking is disabled, its output reserve is bounded, and a non-`end_turn`,
empty or non-shrinking result is rejected. If no safe compaction fits, the current
turn stops and the original messages remain. Use `/new` or a smaller task to continue.

## Provider behavior

- DeepSeek: automatic prefix caching, no extra cache markers by default.
- Native Anthropic host: up to three explicit ephemeral breakpoints on tools, system
  and the last eligible message block, added to a request copy. Thinking blocks are
  never directly marked. Existing explicit markers are left alone.
- Other compatible hosts: unchanged by default. Set `PROMPT_CACHE_MODE=anthropic`
  only after verifying Anthropic-style caching support. `off` disables added markers.

Stable prefixes do not guarantee hits. Minimum cache sizes, expiry, lookback limits,
server routing and cache persistence are provider concerns. New sessions, summaries,
tool changes, model changes and config changes may start a new prefix.

Official references checked on 2026-09-12:
[DeepSeek context caching](https://api-docs.deepseek.com/guides/kv_cache/),
[Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

## Reproducible offline check

```powershell
python examples/cache_replay.py
```

This replays ten synthetic requests. It compares only the old rolling-result rewrite
against append-only history; it does not simulate billing, full legacy compaction,
real model outputs or service cache persistence. The committed
[result](cache_replay_result.json) records 2/9 vs 9/9 unchanged previous prefixes.
It also records 119,326 vs 228,330 serialized message characters across all requests.
More stable context can mean more input: this is an explicit tradeoff, not a hidden win.

## Verify real usage and cost

Every model call records purpose (`main`, `subagent`, `teammate`, `summary`,
`memory_select`, `memory_extract`, `memory_consolidate`), model, raw usage, stop reason,
latency, output limit and prefix hashes. Logs omit keys and prompt/tool text.
Summary transcripts and persisted tool files do contain private work content.

```powershell
python -m mini_claude_code.usage_report
# Or select one run, rather than mixing several sessions:
python -m mini_claude_code.usage_report .transcripts/usage_RUN.jsonl
```

The report aggregates counters by provider/model/purpose and includes sample counts
for each field. It ignores malformed trailing lines but reports their count. Missing
usage is unknown, not zero. It does not assume that an Anthropic-compatible provider's
`input_tokens` includes/excludes cached tokens in the same way as the native API.
`stable_request_prefix` is local evidence, not a cache-hit count. Native SDK HTTP
retries are not individually expanded; outer retry calls are logged separately.

A live cache comparison has been run; see the next section. For a real *task* and
cost comparison, which has not been run, use fresh copies of the same sample project,
with the same provider/model and timing, recording task correctness, total requests,
cache counters, output tokens, repeated reads and summaries. Use multiple trials and
report cold-cache vs warm-cache conditions. Compare provider bills using the verified
field semantics and current prices; do not optimize the hit percentage alone.

## Live benchmark result (2026-09-13)

The offline replay above was sent for real, unchanged, against DeepSeek's
Anthropic-compatible endpoint (`api.deepseek.com/anthropic`, `deepseek-v4-flash`),
ten rounds per strategy, each strategy salted so it cannot warm the other's cache:

```powershell
python examples/cache_live_benchmark.py --rounds 10 --output docs/cache_live_result.json
```

| | Rolling rewrite (old) | Append-only (new) |
| --- | ---: | ---: |
| Uncached input tokens (billed as input) | 25,459 | **11,021** |
| `cache_read_input_tokens` (provider-reported hit) | 11,520 | **50,944** |
| Prompt tokens total | 36,979 | 61,965 |
| Cache hit share | 31.1% | **82.2%** |
| Serialized message chars | 119,326 | 228,330 |

The character totals match `cache_replay_result.json` exactly, so the offline
structural claim and these live counters describe the same requests.

Both effects are real and point in opposite directions. Append-only sends **1.68x
more prompt tokens**, and it also gets **57% fewer uncached tokens** because the
provider serves the repeated prefix from cache. Which one wins is purely a price
question: append-only is cheaper only while the cached-token price is below
**36.6%** of the uncached price. At a 1/10 cached discount it costs about 0.61x the
old strategy; at 1/4 about 0.84x; at 1/2 it becomes **more expensive** (1.17x).
Check the current price sheet before repeating any of these ratios.

`input_tokens` and `cache_read_input_tokens` are disjoint on this endpoint: the
miss is billed as input and the hit is reported separately. A hit rate must use
their sum as the denominator, otherwise it can exceed 100%.

Boundaries of this measurement: one provider, one model, one synthetic workload of
highly repetitive text, thinking mode disabled (the replayed assistant turns are
synthetic and carry no thinking blocks), `max_tokens=32` so output cost is not
exercised, and no task-quality comparison — whether keeping full history produces
better answers is not measured here. Two consecutive runs produced identical token
counts. Nothing here establishes a hit rate for a different provider, a real task,
or a cold cache after expiry.

Existing memory-call frequency and output-truncation retry behavior remain potential
follow-up cost optimizations.
