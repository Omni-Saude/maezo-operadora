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

§Delta-F5: the ORCHESTRATOR decision this cycle is to keep the disclosure as a plain YAML COMMENT
(not a structured, schema-validated field like the sibling A2A-YAML-DISCLOSURE WP's
`handler_status`/`handler_symbol`) — the owner memo (PR #348, item 15) still has to decide whether
to extend that structured-field pattern to skill scope, and this fence must not pre-empt that
decision. But a comment-substring check (`"reativ" in comment`) is satisfiable by a comment that
asserts the OPPOSITE claim while still using the pinned word (e.g. "NAO e reativo: ... ainda usa a
palavra reativo"). `_full_disclosure_comment` below concatenates the skill line's own trailing
comment with every immediately-following pure-comment continuation line (this yaml wraps the
disclosure across 2-3 physical lines) and the two tests assert EQUALITY against the FULL expected
sentence (exact text, whitespace-normalized) — not a keyword substring — so a rewrite that keeps
the word but flips the claim, or drops the comment entirely, both go RED.

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

# §Delta-F5: the FULL expected disclosure sentence for each skill (exact text, whitespace-
# normalized) — copied verbatim from `spec/agents/lucas/agent.yaml`'s current comment. Pinning
# the whole sentence (not a `"reativ"`/`"informacional"` substring) means a rewrite that keeps
# the keyword but asserts the OPPOSITE claim can no longer pass.
_COLLECTION_NUDGE_DISCLOSURE = (
    "LUC-13: reativo -- unico ponto de entrada (receive), sem no/timer que inicie contato; o "
    "LEMBRETE da DMN so dispara DENTRO de um turno ja recebido (J2, confirmacao_pagamento)."
)
_BOLETO_2VIA_DISCLOSURE = (
    "LUC-14: informacional -- so explica como obter a 2a via a partir dos fatos ja fornecidos; "
    "sem worker/tool de emissao de boleto/link no allowlist."
)


def _skill_line_index(text: str, skill_id: str) -> int:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"- {skill_id}" or stripped.startswith(f"- {skill_id} "):
            return i
    raise AssertionError(f"skill {skill_id!r} not found as its own list item in {_LUCAS_AGENT_YAML}")


def _full_disclosure_comment(text: str, skill_id: str) -> str:
    """Returns the FULL disclosure comment for `skill_id`'s yaml list item: the trailing comment
    on the skill's own line, concatenated with every immediately-following pure-comment
    continuation line (a line that is not itself a new `- ` list item), whitespace-normalized to
    a single string. Stops at the first non-comment line or the next list item."""
    lines = text.splitlines()
    start = _skill_line_index(text, skill_id)
    first = lines[start]
    if "#" not in first:
        raise AssertionError(f"{skill_id} precisa de um comentario de escopo: {first!r}")
    parts = [first.split("#", 1)[1]]
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        parts.append(stripped[1:])
    return " ".join(" ".join(parts).split())


def test_collection_nudge_discloses_it_is_reactive_only() -> None:
    text = _LUCAS_AGENT_YAML.read_text(encoding="utf-8")
    comment = _full_disclosure_comment(text, "collection_nudge")
    assert comment == _COLLECTION_NUDGE_DISCLOSURE, (
        "o comentario de escopo de collection_nudge (LUC-13) nao bate com a sentenca EXATA "
        "esperada -- §Delta-F5 pina a sentenca inteira (nao so' a palavra 'reativ') para que uma "
        f"reescrita que afirme o OPOSTO va vermelha: {comment!r} != {_COLLECTION_NUDGE_DISCLOSURE!r}"
    )


def test_boleto_2via_discloses_it_is_informational_only() -> None:
    text = _LUCAS_AGENT_YAML.read_text(encoding="utf-8")
    comment = _full_disclosure_comment(text, "boleto_2via")
    assert comment == _BOLETO_2VIA_DISCLOSURE, (
        "o comentario de escopo de boleto_2via (LUC-14) nao bate com a sentenca EXATA esperada "
        "-- §Delta-F5 pina a sentenca inteira (nao so' a palavra 'informacional') para que uma "
        f"reescrita que afirme o OPOSTO va vermelha: {comment!r} != {_BOLETO_2VIA_DISCLOSURE!r}"
    )
