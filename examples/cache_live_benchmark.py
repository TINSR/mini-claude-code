"""Live cache benchmark: send the offline replay sequence for real.

Replays exactly the message sequences from ``cache_replay.py`` against a real
provider and records the raw ``usage`` counters, so the offline structural claim
(stable prefixes) can be checked against what the provider actually reports in
``cache_read_input_tokens``.

This spends real money. Each strategy uses its own system salt so the two runs
cannot warm each other's cache.

    python examples/cache_live_benchmark.py --rounds 10 --output result.json

Reads ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL and MODEL_ID from the environment,
falling back to a .env file in the working directory.
"""

import argparse
import json
import os
import time
from pathlib import Path

import anthropic
from cache_replay import build_messages

from mini_claude_code.model_gateway import call_model, provider_name

# Long enough to be worth caching anywhere, and shaped like a real agent system
# prompt rather than a toy string.
SYSTEM_TEMPLATE = (
    "You are a code review assistant for run {salt}.\n\n"
    "You follow these rules when reviewing a synthetic project:\n"
    + "".join(
        f"{index}. Rule {index}: report findings tersely and never invent files.\n"
        for index in range(1, 61)
    )
    + "\nAlways answer with a single short sentence.\n"
)


def counters(usage):
    data = usage.model_dump() if usage is not None else {}
    return {
        "input": data.get("input_tokens"),
        "cache_read": data.get("cache_read_input_tokens"),
        "cache_write": data.get("cache_creation_input_tokens"),
        "output": data.get("output_tokens"),
    }


def run_strategy(client, model, label, legacy, args):
    system = SYSTEM_TEMPLATE.format(salt=f"{label}-{int(time.time())}")
    extra = {} if args.thinking else {"thinking": {"type": "disabled"}}
    per_round = []

    print(f"\n=== {label} ===")
    print(f'{"round":>5} {"input":>8} {"cache_read":>11} {"cache_write":>12} {"output":>7}')

    for index in range(args.rounds):
        messages = build_messages(legacy, index)
        response = call_model(
            client,
            f"bench_{label}",
            model=model,
            max_tokens=args.max_tokens,
            system=system,
            messages=messages,
            **extra,
        )

        row = counters(getattr(response, "usage", None))
        row["round"] = index
        row["sent_chars"] = len(json.dumps(messages, ensure_ascii=False))
        per_round.append(row)

        print(
            f'{index:>5} {row["input"]:>8} {row["cache_read"]:>11} '
            f'{row["cache_write"]:>12} {row["output"]:>7}'
        )
        time.sleep(args.pause)

    def total(field):
        return sum(row[field] or 0 for row in per_round)

    # input_tokens and cache_read_input_tokens are disjoint: the provider bills the
    # miss as input and reports the hit separately, so a hit rate must use the sum
    # of the two as its denominator.
    uncached = total("input")
    cache_read = total("cache_read")
    prompt_total = uncached + cache_read

    return {
        "label": label,
        "rounds": args.rounds,
        "per_round": per_round,
        "totals": {
            "uncached_input_tokens": uncached,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": total("cache_write"),
            "prompt_tokens_total": prompt_total,
            "cache_hit_share": round(cache_read / prompt_total, 4) if prompt_total else None,
            "output_tokens": total("output"),
            "sent_chars": total("sent_chars"),
        },
    }


def load_env():
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def breakeven(legacy, append):
    """Cached-token price ratio below which append-only is the cheaper strategy."""
    extra_hits = append["cache_read_input_tokens"] - legacy["cache_read_input_tokens"]
    saved_misses = legacy["uncached_input_tokens"] - append["uncached_input_tokens"]
    if extra_hits <= 0:
        return None
    return round(saved_misses / extra_hits, 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Keep the model's thinking mode on. Models that require thinking blocks "
        "to be replayed will reject these synthetic assistant turns.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    load_env()
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )
    model = os.environ["MODEL_ID"]

    print(f"provider : {provider_name(client)}")
    print(f"model    : {model}")
    print(f"rounds   : {args.rounds} per strategy")

    legacy = run_strategy(client, model, "legacy", True, args)
    append = run_strategy(client, model, "append_only", False, args)
    ratio = breakeven(legacy["totals"], append["totals"])

    report = {
        "measurement": "Live provider counters. cache_read_input_tokens is the "
        "provider-reported hit, not a local prefix metric.",
        "provider": provider_name(client),
        "model": model,
        "thinking_enabled": args.thinking,
        "legacy_rolling_rewrite": legacy,
        "append_only": append,
        "cached_price_breakeven_ratio": ratio,
        "breakeven_note": "Append-only is cheaper when the cached-token price is below "
        "this fraction of the uncached price. Above it, the larger prompt wins.",
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")

    print("\n=== 汇总 ===")
    header = f'{"策略":<24}{"未命中(计费)":>14}{"命中":>9}{"提示词合计":>13}{"命中占比":>11}'
    print(header)
    for section in (legacy, append):
        totals = section["totals"]
        print(
            f'{section["label"]:<24}{totals["uncached_input_tokens"]:>14}'
            f'{totals["cache_read_input_tokens"]:>9}{totals["prompt_tokens_total"]:>13}'
            f'{totals["cache_hit_share"]:>11.1%}'
        )

    if ratio is not None:
        print(
            f"\n盈亏平衡：缓存命中单价低于未命中单价的 {ratio:.1%} 时，"
            f"append-only 更便宜。"
        )


if __name__ == "__main__":
    main()
