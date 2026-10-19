"""DL-0134: the engine (Java) refusal reasons and the Python reader reasons must not drift.

`PortalReadRefusal.Reason` (Java, engine log only) and `read_credentials.UnavailableReason`
(Python, job stderr/events) name WHY a portal read was refused. Both are CLOSED vocabularies.
Tokens listed in the Java "Shared with the Python reader vocabulary" block keep the same
spelling and meaning on both sides; any other token must not collide with a Python token,
otherwise the same word would mean two different things in two logs.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
REFUSAL = ROOT / "src/maezo/portal/engine/java/src/main/java/br/com/maezo/human/PortalReadRefusal.java"
SHARED_START = "// Shared with the Python reader vocabulary"
SHARED_END = "// Engine-side only."
ENTRY = re.compile(r'^\s*([A-Z][A-Z0-9_]*)\("([a-z][a-z0-9_]*)"\)[,;]\s*$')
TOKEN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def _java_vocabulary() -> tuple[dict[str, str], set[str]]:
    """Enum constant -> token, and the tokens of the shared block, parsed from the Java source."""
    text = REFUSAL.read_text(encoding="utf-8")
    body = text[text.index("enum Reason {") : text.index("final String token;")]
    entries: dict[str, str] = {}
    shared: set[str] = set()
    in_shared = False
    for line in body.splitlines():
        if SHARED_START in line:
            in_shared = True
            continue
        if SHARED_END in line:
            in_shared = False
            continue
        match = ENTRY.match(line)
        if match is None:
            continue
        name, token = match.groups()
        assert name not in entries, f"duplicate Java constant {name}"
        entries[name] = token
        if in_shared:
            shared.add(token)
    return entries, shared


def test_java_vocabulary_is_closed_unique_and_snake_case() -> None:
    entries, shared = _java_vocabulary()
    assert len(entries) >= 40, "the Java enum was not parsed; the fence would prove nothing"
    tokens = list(entries.values())
    assert len(tokens) == len(set(tokens)), "two Java constants share a token"
    for name, token in entries.items():
        assert TOKEN.fullmatch(token), f"{name}: {token!r} is not a closed snake_case token"
        assert token == name.lower(), f"{name} must spell its token {name.lower()!r}"
    assert {"unclassified", "engine_exception"} <= set(tokens)
    assert shared, "the shared block is empty or its markers moved"
    assert "unclassified" in shared


def _python_reasons() -> frozenset[str] | None:
    module = importlib.import_module("maezo.gateway.human.read_credentials")
    reasons = getattr(module, "UNAVAILABLE_REASONS", None)
    return None if reasons is None else frozenset(reasons)


def test_shared_tokens_match_the_python_reader_vocabulary() -> None:
    python = _python_reasons()
    if python is None:
        pytest.skip(
            "read_credentials.UNAVAILABLE_REASONS is not on this tree yet "
            "(ramo fix/portal-escalation-pendencias); the cross-check activates when it lands"
        )
    entries, shared = _java_vocabulary()
    java = set(entries.values())
    missing = sorted(shared - python)
    assert not missing, f"shared Java tokens absent from the Python vocabulary: {missing}"
    homonyms = sorted((java & python) - shared)
    assert not homonyms, (
        f"Java tokens that also exist in Python but are not declared shared: {homonyms}; "
        "move them to the shared block (same meaning) or rename them"
    )
