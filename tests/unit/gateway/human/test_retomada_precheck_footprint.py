"""P5 (16/10/2026): the portal's resume pre-check does NOT load langgraph, the LLM SDK nor `maezo.runtime`.

AGENTS.md rule 6 keeps LLM SDKs inside `runtime.inference`; the portal BFF never loaded them, and
the pre-check must not change that. Measured in a FRESH interpreter (in this process other tests
have already imported them, so an in-process check would prove nothing).
"""

import json
import subprocess
import sys

import pytest

FORBIDDEN = (
    "anthropic",
    "langgraph",
    "maezo.runtime",
    "maezo.agents.helena.graph",
    "maezo.agents.helena.prompts",
)


def _footprint(module: str) -> dict[str, bool]:
    probe = (
        "import json,sys;"
        f"__import__({module!r});"
        f"names={FORBIDDEN!r};"
        "print(json.dumps({m: any(k == m or k.startswith(m + '.') for k in sys.modules) for m in names}))"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "module",
    [
        "maezo.gateway.human.retomada_precheck",
        "maezo.gateway.human.gateway",
        "maezo.portal.api.tasks",
        "maezo.portal.api.app",
    ],
)
def test_the_portal_side_never_loads_the_model_stack(module: str) -> None:
    loaded = _footprint(module)
    assert set(loaded) == set(FORBIDDEN)  # the probe measured every name
    assert not any(loaded.values()), {k: v for k, v in loaded.items() if v}


def test_the_probe_sees_the_stack_when_it_is_loaded() -> None:
    # Positive control: the same probe on the Helena's graph reports the stack, so a green above is real.
    loaded = _footprint("maezo.agents.helena.graph")
    assert loaded["langgraph"] and loaded["maezo.runtime"]
