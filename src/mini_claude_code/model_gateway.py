"""Stable request preparation and metadata-only usage logging.

Local prefix diagnostics are not provider cache hits. Raw usage is authoritative.
"""

import copy
import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit

_state = threading.local()
_log_lock = threading.Lock()
USAGE_LOG = Path.cwd() / ".transcripts" / f"usage_{time.time_ns()}.jsonl"


def serializable(value):
    if hasattr(value, "model_dump"):
        return serializable(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Small test doubles and protocol objects; never persist repr/memory addresses.
    if hasattr(value, "__dict__"):
        fields = vars(value).copy()
        if hasattr(value, "type"):
            fields["type"] = value.type
        return serializable(fields)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def token_budget():
    value = int(os.getenv("CONTEXT_TOKEN_BUDGET", "32000"))
    if value < 16000:
        raise ValueError("CONTEXT_TOKEN_BUDGET must be at least 16000")
    return value


def estimate_tokens(messages, system="", tools=None):
    """Conservative text heuristic, NOT the provider tokenizer or context limit."""
    text = json.dumps(serializable([system, tools or [], messages]), ensure_ascii=False)
    ascii_count = sum(ord(char) < 128 for char in text)
    return math.ceil(ascii_count / 3 + (len(text) - ascii_count) * 2)


def fingerprint(value):
    # Dict insertion order is retained: changes in wire ordering should be visible.
    return hashlib.sha256(json.dumps(serializable(value), ensure_ascii=False).encode()).hexdigest()


def provider_name(client):
    host = urlsplit(str(getattr(client, "base_url", ""))).hostname
    return {"api.anthropic.com": "anthropic", "api.deepseek.com": "deepseek"}.get(
        host, "compatible"
    )


def prepare_request(client, kwargs):
    request = copy.deepcopy(kwargs)
    if "tools" in request:
        request["tools"] = sorted(request["tools"], key=lambda tool: tool["name"])
    # Explicit block markers work with the minimum supported SDK; no new top-level
    # API parameter. DeepSeek caches automatically. Other compatible hosts opt in.
    mode = os.getenv("PROMPT_CACHE_MODE", "auto")
    if mode not in {"auto", "off", "anthropic"}:
        raise ValueError("PROMPT_CACHE_MODE must be auto, off or anthropic")
    if mode == "off" or (mode == "auto" and provider_name(client) != "anthropic"):
        return request
    # Honour externally managed breakpoints; do not add more than the API limit.
    blocks = list(request.get("tools", []))
    if isinstance(request.get("system"), list):
        blocks.extend(serializable(request["system"]))
    for message in request.get("messages", []):
        if isinstance(message.get("content"), list):
            blocks.extend(serializable(message["content"]))
    if "cache_control" in request or any("cache_control" in block for block in blocks):
        return request
    if request.get("tools"):
        request["tools"][-1]["cache_control"] = {"type": "ephemeral"}
    if isinstance(request.get("system"), str) and request["system"]:
        request["system"] = [
            {"type": "text", "text": request["system"], "cache_control": {"type": "ephemeral"}}
        ]
    for message in reversed(request.get("messages", [])):
        content = message.get("content")
        if isinstance(content, str):
            if not content:
                continue
            message["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
            break
        content = serializable(content)
        message["content"] = content
        for block in reversed(content or []):
            if block.get("type") in {"text", "tool_result", "tool_use"}:
                block["cache_control"] = {"type": "ephemeral"}
                return request
    return request


def log_event(event):
    try:
        with _log_lock:
            USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_LOG.open("a", encoding="utf-8") as output:
                output.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as error:
        # A log failure must not turn a paid successful call into a retry.
        print(f"[Usage] 日志写入失败：{type(error).__name__}")


def call_model(client, purpose, **kwargs):
    messages = kwargs.get("messages", [])
    request = prepare_request(client, kwargs)
    estimated = estimate_tokens(messages, kwargs.get("system", ""), request.get("tools"))
    if estimated + kwargs.get("max_tokens", 0) > token_budget():
        raise ValueError("Local context budget exceeded; compact or read smaller outputs")
    if not hasattr(_state, "previous"):
        _state.previous = OrderedDict()
    # Conversation list identity separates sessions/subagents on the same thread.
    key = (id(client), purpose, id(messages))
    previous = _state.previous.get(key)
    current = {
        "model": kwargs.get("model"),
        "system_hash": fingerprint(kwargs.get("system", "")),
        "tools_hash": fingerprint(request.get("tools", [])),
        "message_hashes": [fingerprint(message) for message in messages],
    }
    common = 0
    if previous:
        for old, new in zip(previous["message_hashes"], current["message_hashes"], strict=False):
            if old != new:
                break
            common += 1
    prefix_changed = bool(
        previous
        and any(
            previous[field] != current[field] for field in ("model", "system_hash", "tools_hash")
        )
    )
    event = {
        "timestamp": time.time(),
        "purpose": purpose,
        "provider": provider_name(client),
        "model": current["model"],
        "thread": threading.current_thread().name,
        "system_hash": current["system_hash"],
        "tools_hash": current["tools_hash"],
        "estimated_input_tokens": estimated,
        "message_count": len(messages),
        "previous_message_count": len(previous["message_hashes"]) if previous else None,
        "common_message_prefix": common if previous else None,
        "stable_request_prefix": bool(
            previous and not prefix_changed and common == len(previous["message_hashes"])
        ),
        "prefix_config_changed": prefix_changed,
        "max_tokens": kwargs.get("max_tokens"),
    }
    started = time.monotonic()
    try:
        response = client.messages.create(**request)
        # Only a successful response is a candidate cache predecessor.
        _state.previous[key] = current
        _state.previous.move_to_end(key)
        while len(_state.previous) > 64:
            _state.previous.popitem(last=False)
        event.update(
            stop_reason=getattr(response, "stop_reason", None),
            response_model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
        )
        try:
            event["usage"] = serializable(getattr(response, "usage", None))
        except (TypeError, ValueError):
            event["usage"] = None
            event["usage_error"] = "unserializable"
        return response
    except Exception as error:
        event["error_type"] = type(error).__name__
        raise
    finally:
        event["elapsed_seconds"] = round(time.monotonic() - started, 3)
        log_event(event)
