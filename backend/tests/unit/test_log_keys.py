"""Every autonomous-engine log entry must carry a translatable key."""

from __future__ import annotations

import ast
from pathlib import Path

ENGINE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "application"
    / "services"
    / "autonomous_engine.py"
)


def _add_log_calls() -> list[ast.Call]:
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_log"
    ]


def test_every_engine_log_has_a_key() -> None:
    calls = _add_log_calls()
    assert calls, "no add_log calls found"
    missing = [call.lineno for call in calls if not any(kw.arg == "key" for kw in call.keywords)]
    assert missing == [], f"add_log without key= at lines {missing}"


def test_log_keys_are_namespaced() -> None:
    for call in _add_log_calls():
        key = next(kw.value for kw in call.keywords if kw.arg == "key")
        assert isinstance(key, ast.Constant)
        assert isinstance(key.value, str)
        assert "." in key.value, f"key {key.value!r} should look like 'area.event'"
