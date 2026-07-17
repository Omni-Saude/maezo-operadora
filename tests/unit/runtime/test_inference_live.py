"""Live Anthropic API integration test for maezo.runtime.inference (T1.7).

This test makes a REAL network call to the Anthropic API — it is not a
docker-compose "integration" test in the ADR-0011 sense (no CIB Seven
engine involved), so it lives alongside the unit tests rather than under
``tests/integration/`` (which is reserved/gated for the real-engine lane,
see ci.yml).

Fail-soft, loud skip: if neither MAEZO_ANTHROPIC_API_KEY nor
ANTHROPIC_API_KEY is present in the environment, this test is SKIPPED
with an explicit reason — it never fabricates a pass and never silently
disappears from the report. Run it locally with:

    MAEZO_ANTHROPIC_API_KEY=sk-ant-... uv run pytest -m llm_live -v

The API key is read only from the environment and is NEVER logged,
printed, or asserted against in this file.
"""

from __future__ import annotations

import os

import pytest

from maezo.runtime.inference import InferenceProvider, InferenceSettings

pytestmark = pytest.mark.llm_live

_HAS_KEY = bool(os.environ.get("MAEZO_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(
    not _HAS_KEY,
    reason=(
        "No MAEZO_ANTHROPIC_API_KEY / ANTHROPIC_API_KEY in environment — "
        "skipping live Anthropic API call. This is a loud, explicit skip, "
        "not a silent pass: set one of those env vars to exercise the real "
        "provider end-to-end."
    ),
)
@pytest.mark.asyncio
async def test_anthropic_provider_live_generate_returns_real_completion() -> None:
    """A non-noop, non-mock provider must return a real completion (T1.7 acceptance)."""
    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))

    response = await provider.generate(
        prompt="Reply with exactly the single word: pong",
    )

    # Non-sensitive result metadata only — never print the key or full
    # response content in CI logs beyond what's needed to prove liveness.
    assert isinstance(response, str)
    assert len(response) > 0
    print(f"live anthropic generate() ok: provider={provider.provider_name} response_len={len(response)}")
