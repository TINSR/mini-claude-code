"""Keep offline test credentials and metrics separate from user configuration."""
import os
import threading

import pytest

os.environ['ANTHROPIC_API_KEY'] = 'test-key-not-a-real-credential'
os.environ['ANTHROPIC_BASE_URL'] = 'https://example.invalid'
os.environ['MODEL_ID'] = 'test-model'


@pytest.fixture(autouse=True)
def isolated_metrics(tmp_path, monkeypatch):
    from mini_claude_code import model_gateway
    monkeypatch.setattr(model_gateway, 'USAGE_LOG', tmp_path / 'usage.jsonl')
    monkeypatch.setattr(model_gateway, '_state', threading.local())
    monkeypatch.setenv('CONTEXT_TOKEN_BUDGET', '32000')
    monkeypatch.setenv('PROMPT_CACHE_MODE', 'auto')
