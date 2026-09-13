from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_MAIN = ROOT / "docs" / "legacy_main.py"
EXPECTED_SHA256 = "EBC9BC5ADF64B67B247068F6BA7215EFF79B74978505B98686D18F1BDCD52C30"


def test_legacy_snapshot_is_unchanged() -> None:
    digest = hashlib.sha256(LEGACY_MAIN.read_bytes()).hexdigest().upper()

    assert digest == EXPECTED_SHA256


def test_legacy_snapshot_compiles() -> None:
    source = LEGACY_MAIN.read_text(encoding="utf-8")

    compile(source, str(LEGACY_MAIN), "exec")

