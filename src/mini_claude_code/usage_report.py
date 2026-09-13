"""Summarize local usage logs without guessing compatible-provider billing semantics."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def summarize(paths):
    groups = defaultdict(
        lambda: {
            "requests": 0,
            "errors": 0,
            "stable_prefix_requests": 0,
            "usage_present": 0,
            "raw_token_sums": {},
            "field_samples": {},
        }
    )
    malformed = 0
    for path in paths:
        with Path(path).open(encoding="utf-8") as source:
            for line in source:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("Expected event")
                except (ValueError, TypeError):
                    malformed += 1
                    continue
                key = (
                    event.get("provider", "unknown"),
                    event.get("model", "unknown"),
                    event.get("purpose", "unknown"),
                )
                row = groups[key]
                row["requests"] += 1
                row["errors"] += bool(event.get("error_type"))
                row["stable_prefix_requests"] += bool(event.get("stable_request_prefix"))
                usage = event.get("usage")
                if not isinstance(usage, dict):
                    continue
                row["usage_present"] += 1
                for field, value in usage.items():
                    if field.endswith("_tokens") and type(value) in (int, float):
                        row["raw_token_sums"][field] = row["raw_token_sums"].get(field, 0) + value
                        row["field_samples"][field] = row["field_samples"].get(field, 0) + 1
    return {
        "note": "Raw counters, not a price estimate. Stable prefix != provider cache hit. "
        "Absent counters are not zero. Compatible endpoint input totals need verification.",
        "malformed_lines": malformed,
        "groups": [
            dict(provider=key[0], model=key[1], purpose=key[2], **row)
            for key, row in sorted(groups.items())
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="*", help="Explicit usage JSONL paths; default .transcripts")
    args = parser.parse_args()
    paths = (
        [Path(path) for path in args.logs]
        if args.logs
        else sorted(Path(".transcripts").glob("usage_*.jsonl"))
    )
    print(json.dumps(summarize(paths), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
