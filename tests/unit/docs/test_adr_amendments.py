"""WP-ADR-RECONCILIACAO (gaps AF-02, AF-03, AF-04, AF-10, AF-15, AF-17, AF-18).

Seven ADRs asserted a mechanism or a file that never existed, or that became obsolete, with no
`amended-by`/`superseded-by`/`obsolete-section` marker. Each one now carries an APPEND-ONLY
`## Emenda 2026-09-03` block. This module is the fence that keeps those amendments HONEST, in
three independent directions:

  1. **The amendment exists and does not claim ratification.** An amendment authored by an agent
     stays `Proposto (amendment) — DRAFT/verify` until a human signs it (`/docs/adr/` is
     CODEOWNED — `.github/CODEOWNERS:61`). A future edit that silently promotes one of these
     blocks to "Accepted"/"Ratificado" without changing this fence turns red here.

  2. **The citations still point where they say they point.** Every amendment cites the original
     claim by line number. Instead of hard-coding those line numbers a second time, this module
     DERIVES each one from the claim's own text and compares it against the number the amendment
     wrote down. That is the AF-18a lesson applied to our own work: anchor by CONTENT, and let
     the fence catch the day the anchor drifts. It also proves the amendments are append-only —
     each original claim must still sit above its amendment heading, at its stated line.

  3. **The reconciled facts are still facts.** An amendment that says "this file does not exist"
     is only true while it does not exist. Each code-side fact the seven amendments rest on is
     re-derived here from the tree, so the day one of them changes (someone finally writes
     `PostgresDedupeStore`, or ports `contract_extraction`) this fence — not a reader — is what
     notices the amendment went stale.

Nothing here asserts that any amendment was ratified, and nothing here asserts a policy value is
correct; the one governed value it touches (`modo:` in `action-approvals.yaml`) is checked only
for AGREEMENT with what the ADR-0005 amendment says it observed, so a legitimate owner flip stays
possible — it just has to update the sentence it invalidates.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_ADR_DIR = _REPO_ROOT / "docs" / "adr"

_AMENDMENT_HEADING = "## Emenda 2026-09-03"
_DRAFT_STATUS = "**Status:** Proposto (amendment) — DRAFT/verify"
_MARKERS = ("amended-by", "superseded-by", "obsolete-section")

#: gap id -> ADR filename. The seven ADRs this work package reconciled.
_AMENDED_ADRS: dict[str, str] = {
    "AF-02": "0024-durable-idempotency-resume-inbound-drivers.md",
    "AF-03": "0005-hitl-architectural-guarantee.md",
    "AF-04": "0008-autonomy-levels.md",
    "AF-10": "0032-adr0015-status-correction.md",
    "AF-15": "0015-a2a-delegation-runtime.md",
    "AF-17": "0006-phi-two-zones.md",
    "AF-18": "0012-dmn-deterministic-tool.md",
}

#: (ADR filename, the ORIGINAL claim's own text, the line the amendment cites for it).
#: The claim text is matched at its FIRST occurrence, because several amendments quote the claim
#: they are correcting — the original always comes first in an append-only file.
_CITED_CLAIMS: tuple[tuple[str, str, int], ...] = (
    (
        "0005-hitl-architectural-guarantee.md",
        "**PEP** no Tool Gateway avalia a matriz de autonomia (ADR-0008) antes de TODO tool call efetivo.",
        10,
    ),
    ("0006-phi-two-zones.md", "mapa de reidentificacao so no gateway", 11),
    (
        "0008-autonomy-levels.md",
        "Matriz **acao x nivel** em YAML versionado (`src/maezo/policies/autonomy/`)",
        14,
    ),
    ("0012-dmn-deterministic-tool.md", "Pipeline `contract_extraction` (portado) gera DMN", 16),
    ("0015-a2a-delegation-runtime.md", "Três tópicos declarados em `config/topic_registry.yaml`", 80),
    (
        "0024-durable-idempotency-resume-inbound-drivers.md",
        "que implementa `IdempotencyGuard` DIRETAMENTE",
        44,
    ),
    ("0024-durable-idempotency-resume-inbound-drivers.md", "Nova migracao `0008_driver_idempotency`", 46),
    ("0024-durable-idempotency-resume-inbound-drivers.md", "**Fiacao nos dois sitios**", 62),
    (
        "0032-adr0015-status-correction.md",
        "four files ADR-0015 §Decisao describes have never existed in this repo.",
        32,
    ),
    ("0032-adr0015-status-correction.md", "`dispatcher.py`, and `facts.py` do not.", 88),
    ("0032-adr0015-status-correction.md", "BLOCKED-ON the dispatcher port (P3 backlog)", 104),
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected {path} to exist"
    return path.read_text(encoding="utf-8")


def _first_line_containing(text: str, needle: str) -> int | None:
    for index, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return index
    return None


# =================================================================================================
# 1. The amendments exist, and none of them claims ratification
# =================================================================================================


def test_every_reconciled_adr_carries_a_dated_amendment_block() -> None:
    missing = [
        f"{gap}:{name}"
        for gap, name in _AMENDED_ADRS.items()
        if _AMENDMENT_HEADING not in _read(_ADR_DIR / name)
    ]
    assert not missing, f"ADRs without a `{_AMENDMENT_HEADING}` block: {missing}"


def test_every_amendment_is_draft_and_names_its_gap() -> None:
    """No agent-authored amendment may read as ratified, and each must be traceable to its gap."""
    for gap, name in _AMENDED_ADRS.items():
        text = _read(_ADR_DIR / name)
        amendment = text[text.index(_AMENDMENT_HEADING) :]
        assert _DRAFT_STATUS in amendment, f"{name}: amendment must stay DRAFT/verify until a human signs"
        assert gap in amendment, f"{name}: amendment must name its gap id ({gap})"
        assert "(R1, AGENTE)" in amendment, f"{name}: amendment must disclose that its author is an agent"


def test_every_amendment_carries_a_status_marker() -> None:
    """`amended-by` / `superseded-by` / `obsolete-section` — the marker whose absence WAS the gap."""
    for name in _AMENDED_ADRS.values():
        text = _read(_ADR_DIR / name)
        amendment = text[text.index(_AMENDMENT_HEADING) :]
        found = [marker for marker in _MARKERS if marker in amendment]
        assert found, f"{name}: amendment carries none of {_MARKERS}"


def test_no_amendment_claims_ratification() -> None:
    forbidden = ("Ratificado", "ratificada pelo dono", "Status:** Accepted (amendment)")
    for name in _AMENDED_ADRS.values():
        text = _read(_ADR_DIR / name)
        amendment = text[text.index(_AMENDMENT_HEADING) :]
        hits = [word for word in forbidden if word in amendment]
        assert not hits, f"{name}: an agent-authored amendment may not claim ratification ({hits})"


# =================================================================================================
# 2. The amendments are append-only, and their line citations still resolve
# =================================================================================================


def test_cited_claim_lines_resolve_to_the_line_the_amendment_names() -> None:
    """Derive each citation from the claim's own text; compare with the number written down."""
    drifted: list[str] = []
    for name, claim, cited_line in _CITED_CLAIMS:
        text = _read(_ADR_DIR / name)
        actual = _first_line_containing(text, claim)
        if actual != cited_line:
            drifted.append(f"{name}: {claim!r} cited at :{cited_line}, found at :{actual}")
    assert not drifted, "ADR amendment citations drifted from the text they cite:\n" + "\n".join(drifted)


def test_every_cited_claim_sits_above_its_amendment() -> None:
    """Append-only proof: the corrected text must still precede the correction, unmodified."""
    for name, _claim, cited_line in _CITED_CLAIMS:
        text = _read(_ADR_DIR / name)
        heading_line = _first_line_containing(text, _AMENDMENT_HEADING)
        assert heading_line is not None, f"{name}: no amendment heading"
        assert cited_line < heading_line, (
            f"{name}: claim at :{cited_line} is not above the amendment at :{heading_line} — "
            "amendments are appended, never interleaved"
        )


# =================================================================================================
# 3. The code-side facts the amendments rest on are re-derived from the tree
# =================================================================================================


def _src_and_spec_hits(needle: str) -> list[str]:
    hits: list[str] = []
    for root in (_REPO_ROOT / "src", _REPO_ROOT / "spec"):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".so"}:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if needle in content:
                hits.append(str(path.relative_to(_REPO_ROOT)))
    return sorted(hits)


def test_af02_postgres_dedupe_store_still_does_not_exist() -> None:
    """ADR-0024's prescribed class. If someone builds it, the amendment must be revisited."""
    assert _src_and_spec_hits("PostgresDedupeStore") == []


def test_af02_driver_idempotency_table_is_created_by_migration_0003_not_0008() -> None:
    versions = _REPO_ROOT / "src" / "maezo" / "platform" / "migrations" / "versions"
    assert "CREATE TABLE IF NOT EXISTS driver_idempotency" in _read(versions / "0003_a2a_idempotency.py")
    assert not (versions / "0008_driver_idempotency.py").exists()
    assert (versions / "0008_a2a_fact_outbox.py").is_file()


def test_af02_the_two_drivers_the_adr_is_about_are_gone() -> None:
    """ADR-0024's SUBJECT — the Kafka->graph drivers and their guard seam — no longer exists."""
    assert not (_REPO_ROOT / "src" / "maezo" / "runtime" / "inbound_driver.py").exists()
    gone = ("class InboundDriver", "class ResumeDriver", "IdempotencyGuard", "InMemoryIdempotencyStore")
    for symbol in gone:
        assert _src_and_spec_hits(symbol) == [], f"{symbol} reappeared; ADR-0024's amendment is stale"


def test_af03_the_per_call_autonomy_chokepoint_exists() -> None:
    """The ADR-0005 amendment says mechanism 1 is BUILT (and inert), not absent. Pin both halves."""
    effect_pep = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "effect_pep.py")
    assert "verdict = ctx.autonomy.evaluate(" in effect_pep
    tool_registry = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "tool_registry.py")
    assert "autonomy=_build_autonomy(tenant)" in tool_registry
    assert "return build_pep(tenant=tenant)" in tool_registry
    base = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "seams" / "_base.py")
    assert "if decision.enforced and not decision.allow:" in base


def test_af03_amendment_agrees_with_the_live_enforcement_mode() -> None:
    """The one governed value this fence touches. A legitimate `shadow -> enforcing` flip stays
    possible; it just cannot leave the ADR-0005 amendment asserting the old mode."""
    approvals = _read(_REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml")
    modes = [line.split(":", 1)[1].strip() for line in approvals.splitlines() if line.startswith("modo:")]
    assert len(modes) == 1, f"expected exactly one top-level `modo:` key, found {modes}"
    adr = _read(_ADR_DIR / _AMENDED_ADRS["AF-03"])
    amendment = adr[adr.index(_AMENDMENT_HEADING) :]
    assert f"modo: {modes[0]}" in amendment, (
        f"action-approvals.yaml is `modo: {modes[0]}`, but ADR-0005's amendment does not say so — "
        "update the amendment in the same change that flips the mode"
    )


def test_af04_src_maezo_policies_still_does_not_exist() -> None:
    assert not (_REPO_ROOT / "src" / "maezo" / "policies").exists()
    autonomy = _REPO_ROOT / "spec" / "policies" / "autonomy"
    names = sorted(p.name for p in autonomy.glob("*.yaml"))
    assert names == ["L0-core.yaml", "_hard_frozen.yaml", "action-approvals.yaml", "tenants-amh.yaml"]


def test_af10_the_a2a_runtime_modules_adr0032_called_nonexistent_now_exist() -> None:
    a2a = _REPO_ROOT / "src" / "maezo" / "a2a"
    for module in ("dispatcher.py", "delegation.py", "facts.py", "idempotency.py", "signing.py"):
        assert (a2a / module).is_file(), f"{module} missing; ADR-0032's amendment is stale"


def test_af15_config_topic_registry_yaml_still_does_not_exist() -> None:
    assert not (_REPO_ROOT / "config" / "topic_registry.yaml").exists()
    facts = _read(_REPO_ROOT / "src" / "maezo" / "a2a" / "facts.py")
    for topic in (
        '"agents.events.delegation.requested"',
        '"agents.events.delegation.completed"',
        '"agents.events.delegation.rejected"',
    ):
        assert topic in facts
    registry = _read(_REPO_ROOT / "src" / "maezo" / "platform" / "topic_registry.py")
    assert '_RESERVED_PREFIXES: frozenset[str] = frozenset({"agents.events", "agents.audit"})' in registry


def test_af17_no_reversible_reidentification_vault_is_disclosed_anywhere() -> None:
    dispatch = _read(_REPO_ROOT / "src" / "maezo" / "platform" / "webhooks" / "whatsapp" / "dispatch.py")
    assert "no persistent, reversible phone-number vault exists in v2" in dispatch
    adapters = _read(_REPO_ROOT / "src" / "maezo" / "agents" / "helena" / "adapters.py")
    assert "no persistent, reversible hash->phone vault exists in v2 yet" in adapters


def test_af18_contract_extraction_pipeline_still_does_not_exist() -> None:
    assert _src_and_spec_hits("contract_extraction") == []


# =================================================================================================
# The AF-02 documentary residue: two documents asserted a delivery that never landed
# =================================================================================================


def test_af02_false_landing_claims_carry_an_errata() -> None:
    for relative in ("docs/archive/predeploy-dl-rows-STAGED.md", "docs/reports/predeploy-audit-report.md"):
        text = _read(_REPO_ROOT / relative)
        assert "PR #181" in text, f"{relative}: the corrected claim itself must remain (append-only)"
        assert "## ERRATA 2026-09-03" in text, f"{relative}: false-landing claim carries no errata"
        assert "03c6437" in text, f"{relative}: errata must name the real PR #181 commit"
