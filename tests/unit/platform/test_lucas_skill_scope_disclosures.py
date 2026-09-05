"""LUC-13/LUC-14 (Agent Fleet Audit — DOCSTRING-DENIES-RUNTIME / DEAD-FIELD-OR-LITERAL shape) —
`spec/agents/lucas/agent.yaml`'s `a2a.skills` is a contract-visible capability list: a bare skill
id there reads as "Lucas performs this action end-to-end". Two of the three declared skills are
narrower than that in the real graph:

  - `collection_nudge` (LUC-13): the graph is purely REACTIVE (one entry point, `receive`; the
    `LEMBRETE` DMN output only ever fires INSIDE an already-received turn, `graph.py`'s
    `_build_message` branch) — there is no proactive outbound-initiating node/timer, so a
    beneficiary only gets a reminder if they happen to send an inbound turn first.
  - `boleto_2via` (LUC-14): `prompts.py::message_prompt` only ever tells the beneficiary HOW to
    obtain the 2nd copy from the facts already provided — there is no boleto/link EMISSION tool
    in Lucas's allowlist (`mcp-dmn.evaluate`, `mcp-whatsapp.send_message`,
    `mcp-cibseven.start_process` only), so Lucas never actually issues one.

Both scopes are ALREADY correct in the code (the prompt is honest — it forbids inventing a
boleto number; the graph never claims to initiate contact) — the drift is that `agent.yaml`
declares the skill id BARE, with no comment disclosing the narrower scope, unlike
`agent.yaml`'s own historical convention for `collection_nudge`
(`docs/reports/T0.3-agent-yaml-drift.md:491`: "lembrete de vencimento (nunca ameaca suspensao)")
which was dropped somewhere between that report and the current file. This fence pins the
disclosure back onto the yaml line itself so a future edit cannot silently drop it again without
this test going RED.

Building either skill's REAL end-to-end capability (a proactive outbound channel for
`collection_nudge`; a boleto-issuance worker for `boleto_2via`) is explicitly OUT OF this fence's
scope — both need new infrastructure/a business decision outside a docs-and-graph WP's charter
(see the triage's own `fix_shape` for LUC-13/LUC-14). This fence only asserts the DECLARATION is
truthful about what exists today, mirroring the same remedy already applied to other
declared-but-narrower-than-it-sounds capabilities in this repo (e.g. `memory: {episodic,
semantic}` de-declaration, CC-07).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LUCAS_AGENT_YAML = _REPO_ROOT / "spec" / "agents" / "lucas" / "agent.yaml"


def _skill_line(text: str, skill_id: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == f"- {skill_id}" or stripped.startswith(f"- {skill_id} "):
            return line
    raise AssertionError(f"skill {skill_id!r} not found as its own list item in {_LUCAS_AGENT_YAML}")


def test_collection_nudge_discloses_it_is_reactive_only() -> None:
    text = _LUCAS_AGENT_YAML.read_text(encoding="utf-8")
    line = _skill_line(text, "collection_nudge")
    assert "#" in line, f"collection_nudge precisa de um comentario de escopo (LUC-13): {line!r}"
    comment = line.split("#", 1)[1].lower()
    assert "reativ" in comment, f"comentario nao disclosa o escopo reativo (LUC-13): {line!r}"


def test_boleto_2via_discloses_it_is_informational_only() -> None:
    text = _LUCAS_AGENT_YAML.read_text(encoding="utf-8")
    line = _skill_line(text, "boleto_2via")
    assert "#" in line, f"boleto_2via precisa de um comentario de escopo (LUC-14): {line!r}"
    comment = line.split("#", 1)[1].lower()
    assert "informacional" in comment, f"comentario nao disclosa o escopo informacional (LUC-14): {line!r}"
