"""WP-ADR-RECONCILIACAO (gaps AF-02, AF-03, AF-04, AF-10, AF-15, AF-17, AF-18).

Seven ADRs asserted a mechanism or a file that never existed, or that became obsolete, with no
`amended-by`/`superseded-by`/`obsolete-section` marker. The correction of record is a NEW ADR —
`docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md` — because
`docs/adr/README.md:8` says an `Accepted` ADR changes only by a new ADR, and because ADR-0032's own
`## Convencao seguida` (`0032:119-128`) already litigated exactly that question. This module is the
fence that keeps that ADR HONEST, in four independent directions:

  1. **The correction exists and does not claim ratification.** An ADR authored by an agent stays
     `Proposto — DRAFT/verify` until a human signs it (`/docs/adr/` is CODEOWNED —
     `.github/CODEOWNERS:61`). A future edit that silently promotes it to "Accepted"/"Ratificado"
     without changing this fence turns red here.

  2. **THE GOVERNANCE RULE ITSELF.** The seven `Accepted` ADRs must be BYTE-IDENTICAL to the base
     commit `71dd4da`. This is the invariant the first attempt at this work package violated (it
     appended `## Emenda 2026-09-03` blocks INSIDE all seven), and it is the reason the correction
     now lives in its own file. Pinning the digests — rather than shelling out to git — keeps the
     check honest in a shallow CI clone, and makes an in-place edit of an `Accepted` ADR a RED
     test rather than a silent convention breach. To regenerate after a legitimate, owner-ratified
     change to one of the seven:

         git show 71dd4da:docs/adr/<file>.md | shasum -a 256

     Regenerating is itself a governance act: do it only alongside the ADR that authorises it.

  3. **The citations still point where they say they point.** ADR-0041 cites each original claim by
     line number, in another file. Instead of hard-coding those line numbers a second time, this
     module DERIVES each one from the claim's own text in the CITED ADR, and separately asserts
     that ADR-0041 wrote that exact `NNNN:L` token down. That is the AF-18a lesson applied to our
     own work: anchor by CONTENT, and let the fence catch the day the anchor drifts. Direction 2
     makes the drift impossible without a red test; direction 3 makes it impossible to paper over
     by editing only ADR-0041.

  4. **The reconciled facts are still facts.** An ADR that says "this file does not exist" is only
     true while it does not exist. Each code-side fact the seven reconciliations rest on is
     re-derived here from the tree, so the day one of them changes (someone finally writes
     `PostgresDedupeStore`, ports `contract_extraction`, gives the worker seam context a
     capability view, or gates `cibseven.start_process`) this fence — not a reader — is what
     notices ADR-0041 went stale.

Nothing here asserts that ADR-0041 was ratified, and nothing here asserts a policy value is
correct; the governed values it touches (`modo:` and the `choked: false` count in
`action-approvals.yaml`) are checked only for AGREEMENT with what ADR-0041 says it observed, so a
legitimate owner flip stays possible — it just has to update the sentences it invalidates.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_ADR_DIR = _REPO_ROOT / "docs" / "adr"

#: The correction of record. A NEW ADR, per `docs/adr/README.md:8`.
_ADR_0041 = "0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md"

_DRAFT_STATUS = "**Status:** **Proposto — DRAFT/verify"
_MARKERS = ("amended-by", "superseded-by", "obsolete-section")

#: The base commit whose bytes the seven `Accepted` ADRs must still carry.
_BASE_COMMIT = "71dd4da"

#: gap id -> ADR filename. The seven ADRs this work package reconciled — WITHOUT editing them.
_RECONCILED_ADRS: dict[str, str] = {
    "AF-02": "0024-durable-idempotency-resume-inbound-drivers.md",
    "AF-03": "0005-hitl-architectural-guarantee.md",
    "AF-04": "0008-autonomy-levels.md",
    "AF-10": "0032-adr0015-status-correction.md",
    "AF-15": "0015-a2a-delegation-runtime.md",
    "AF-17": "0006-phi-two-zones.md",
    "AF-18": "0012-dmn-deterministic-tool.md",
}

#: sha256 of each of the seven files at `71dd4da` — see direction 2 in the module docstring.
_BASELINE_SHA256: dict[str, str] = {
    "0005-hitl-architectural-guarantee.md": (
        "a1f61a0e602307b63df91aac59a9b9dd8edcb28405e4007b9a930ac12830b0d3"
    ),
    "0006-phi-two-zones.md": ("c530273e5d362a5734801b3d3350e68ce98e92c2abfeeed614e4d9511f46e56a"),
    "0008-autonomy-levels.md": ("a318ea85ce1b94bb4a82f8834ba9c9870cf6af2774209c3decfde7d621fe3da1"),
    "0012-dmn-deterministic-tool.md": (
        "d82de0050495f259c28c2bb69f4354e6777eb57dde3bf2e6111ca3478b0176f7"
    ),
    "0015-a2a-delegation-runtime.md": (
        "a4a1ed108310170ab886ba0e8da0e70b84fce4f6ed284070f67a0496994ac9a4"
    ),
    "0024-durable-idempotency-resume-inbound-drivers.md": (
        "ca0690ed63f1ca9eed1124a8d63249b6e41483df0b0b54a90fbead5e6c49f961"
    ),
    "0032-adr0015-status-correction.md": (
        "a0737c13b4e329fffe40d0ae62636fec3e0a3e6a7bb5672d5731c431e84b608f"
    ),
}

#: (ADR filename, the ORIGINAL claim's own text, the line ADR-0041 cites for it).
#: The claim text is matched at its FIRST occurrence in the CITED ADR.
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


def _adr0041() -> str:
    return _read(_ADR_DIR / _ADR_0041)


def _first_line_containing(text: str, needle: str) -> int | None:
    for index, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return index
    return None


# =================================================================================================
# 1. The correction of record exists, and it does not claim ratification
# =================================================================================================


def test_the_reconciliation_adr_exists_and_names_all_seven_gaps() -> None:
    text = _adr0041()
    missing = [gap for gap in _RECONCILED_ADRS if gap not in text]
    assert not missing, f"{_ADR_0041} does not name gap(s) {missing}"
    unnamed = [name.split("-")[0] for name in _RECONCILED_ADRS.values() if name.split("-")[0] not in text]
    assert not unnamed, f"{_ADR_0041} does not name reconciled ADR(s) {unnamed}"


def test_the_reconciliation_adr_is_draft_and_discloses_its_agent_author() -> None:
    """No agent-authored ADR may read as ratified while a human signature is pending."""
    text = _adr0041()
    assert _DRAFT_STATUS in text, f"{_ADR_0041}: must stay `Proposto — DRAFT/verify` until a human signs"
    assert "AGENTE" in text, f"{_ADR_0041}: must disclose that its author is an agent"
    assert ".github/CODEOWNERS:61" in text, f"{_ADR_0041}: must name the owner gate it is pending on"


def test_the_reconciliation_adr_carries_the_status_markers() -> None:
    """`amended-by` / `superseded-by` / `obsolete-section` — the markers whose absence WAS the gap."""
    text = _adr0041()
    found = [marker for marker in _MARKERS if marker in text]
    assert found, f"{_ADR_0041}: carries none of {_MARKERS}"


def test_the_reconciliation_adr_does_not_claim_ratification() -> None:
    text = _adr0041()
    forbidden = ("Ratificado", "ratificada pelo dono", "Status:** Accepted")
    hits = [word for word in forbidden if word in text]
    assert not hits, f"{_ADR_0041}: an agent-authored ADR may not claim ratification ({hits})"
    # Closes the "add a second Status line and leave the DRAFT one in place" hole: exactly one.
    assert text.count("**Status:**") == 1, (
        f"{_ADR_0041}: expected exactly one `**Status:**` line, found {text.count('**Status:**')} — "
        "a second one can contradict the first without tripping the DRAFT assertion"
    )


def test_the_reconciliation_adr_is_indexed_in_the_adr_readme() -> None:
    """ADR-0032:112-114 named the hazard: an amendment nobody finds from the index is no mitigation.

    This repo's index convention marks the AMENDING ADR's row (0027, 0032, 0034, 0035, 0037, 0038,
    0039 all do), never the amended one — so the row for 0041 IS the discovery path.
    """
    readme = _read(_ADR_DIR / "README.md")
    row = [line for line in readme.splitlines() if line.startswith("| 0041 |")]
    assert len(row) == 1, f"docs/adr/README.md: expected exactly one 0041 row, found {len(row)}"
    for number in sorted(name.split("-")[0] for name in _RECONCILED_ADRS.values()):
        assert number in row[0], f"docs/adr/README.md 0041 row does not name ADR-{number}"


# =================================================================================================
# 2. THE GOVERNANCE RULE — the seven `Accepted` ADRs are byte-identical to the base commit
# =================================================================================================


def test_the_seven_accepted_adrs_are_byte_identical_to_the_base_commit() -> None:
    """`docs/adr/README.md:8` — an `Accepted` ADR changes ONLY by a new ADR with `Supersedes`.

    This is the invariant, not a style preference: the audit's line anchors into these seven files
    stay valid only while nobody inserts a line, and the repo's own ADR-0032 (`0032:119-128`)
    already ruled that in-place amendment of an `Accepted` ADR is not sanctioned here.
    """
    drifted: list[str] = []
    for name, expected in _BASELINE_SHA256.items():
        actual = hashlib.sha256((_ADR_DIR / name).read_bytes()).hexdigest()
        if actual != expected:
            drifted.append(f"{name}: {actual} != {expected} (at {_BASE_COMMIT})")
    assert not drifted, (
        "An `Accepted` ADR was edited in place. `docs/adr/README.md:8` allows this only via a NEW "
        "ADR with `Supersedes`; write one (see ADR-0041) instead of editing these files:\n"
        + "\n".join(drifted)
    )


def test_no_reconciled_adr_carries_an_in_place_amendment_block() -> None:
    """The specific breach this work package had to undo, asserted by name so the failure reads."""
    offenders = [
        name for name in _RECONCILED_ADRS.values() if "## Emenda" in _read(_ADR_DIR / name)
    ]
    assert not offenders, (
        "in-place `## Emenda` block(s) found inside `Accepted` ADR(s): "
        f"{offenders} — the correction of record belongs in {_ADR_0041}"
    )


# =================================================================================================
# 3. The line citations still resolve, in BOTH directions
# =================================================================================================


def test_cited_claim_lines_resolve_to_the_line_the_reconciliation_adr_names() -> None:
    """Derive each citation from the claim's own text; compare with the number written down."""
    drifted: list[str] = []
    for name, claim, cited_line in _CITED_CLAIMS:
        text = _read(_ADR_DIR / name)
        actual = _first_line_containing(text, claim)
        if actual != cited_line:
            drifted.append(f"{name}: {claim!r} cited at :{cited_line}, found at :{actual}")
    assert not drifted, "ADR-0041's citations drifted from the text they cite:\n" + "\n".join(drifted)


def test_the_reconciliation_adr_actually_writes_every_citation_it_is_held_to() -> None:
    """Direction 3's other half: the `NNNN:L` token must be IN ADR-0041, not only in this fence.

    Without this, someone could delete a citation from ADR-0041 and the content-derived check above
    would keep passing over a claim the document no longer makes.
    """
    text = _adr0041()
    missing = [
        f"`{name.split('-')[0]}:{cited_line}`"
        for name, _claim, cited_line in _CITED_CLAIMS
        if f"`{name.split('-')[0]}:{cited_line}`" not in text
    ]
    assert not missing, f"{_ADR_0041} does not write citation token(s) {missing}"


# =================================================================================================
# 4. The code-side facts ADR-0041 rests on are re-derived from the tree
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
    """ADR-0024's prescribed class. If someone builds it, ADR-0041 §1 must be revisited."""
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
        assert _src_and_spec_hits(symbol) == [], f"{symbol} reappeared; ADR-0041 §1 is stale"


def test_af02_all_four_false_landing_documents_are_named_and_two_carry_an_errata() -> None:
    """§1(e) inventories FOUR documents. Two got an errata; the other two must at least be LISTED."""
    adr = _adr0041()
    for relative in ("docs/archive/predeploy-dl-rows-STAGED.md", "docs/reports/predeploy-audit-report.md"):
        text = _read(_REPO_ROOT / relative)
        assert "PR #181" in text, f"{relative}: the corrected claim itself must remain (append-only)"
        assert "## ERRATA 2026-09-03" in text, f"{relative}: false-landing claim carries no errata"
        assert "03c6437" in text, f"{relative}: errata must name the real PR #181 commit"
        assert relative in adr, f"{relative} is not named in {_ADR_0041} §1(e)"
    for relative in ("docs/archive/HANDOFF-predeploy.yaml", "docs/design/audit-emit-path-wiring.md"):
        assert "PostgresDedupeStore" in _read(_REPO_ROOT / relative), (
            f"{relative} no longer carries the residue; drop it from ADR-0041 §1(e) in the same change"
        )
        assert relative in adr, f"{relative} carries the residue but is not listed in {_ADR_0041} §1(e)"


def test_af03_the_per_call_autonomy_chokepoint_exists() -> None:
    """ADR-0041 §2(a) says mechanism 1 is BUILT (and inert), not absent. Pin both halves."""
    effect_pep = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "effect_pep.py")
    assert "verdict = ctx.autonomy.evaluate(" in effect_pep
    tool_registry = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "tool_registry.py")
    assert "autonomy=_build_autonomy(tenant)" in tool_registry
    assert "return build_pep(tenant=tenant)" in tool_registry
    base = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "seams" / "_base.py")
    assert "if decision.enforced and not decision.allow:" in base


def test_af03_the_ladder_still_short_circuits_at_l1_before_the_autonomy_matrix() -> None:
    """§2(b): the matrix (L-2) is reached ONLY where a capability view exists.

    Three facts hold this up, and all three must stay true together: L-1 precedes L-2 in the
    ladder, L-1 denies unconditionally on `capabilities is None`, and the worker seam context sets
    exactly that. Give the worker context a capability view and this goes red — correctly: ADR-0041
    §2 would then be describing a world that no longer exists.
    """
    effect_pep = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "effect_pep.py")
    ladder = effect_pep.index("ladder: tuple[tuple[EffectLayer")
    l1 = effect_pep.index("EffectLayer.CAPACIDADE", ladder)
    l2 = effect_pep.index("EffectLayer.AUTONOMIA", ladder)
    assert l1 < l2, "L-1 (CAPACIDADE) must precede L-2 (AUTONOMIA) in the decision ladder"
    assert "        if reason is not None:\n            return deny_at(reason, layer)" in effect_pep, (
        "the ladder no longer returns on the first non-None reason; ADR-0041 §2(b) is stale"
    )
    assert (
        "    capabilities = ctx.capabilities\n"
        "    if capabilities is None:\n"
        "        return REASON_CAPABILITIES_UNAVAILABLE" in effect_pep
    ), "L-1 no longer denies unconditionally without a capability view; ADR-0041 §2(b) is stale"
    tool_registry = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "tool_registry.py")
    worker_ctx = tool_registry[tool_registry.index("def build_worker_seam_context") :]
    worker_ctx = worker_ctx[: worker_ctx.index("def _build_autonomy")]
    assert "capabilities=None," in worker_ctx, (
        "`build_worker_seam_context` now has a capability view — the four worker legs listed in "
        "ADR-0041 §2(b) would reach L-2, and that table must be rewritten"
    )


def test_af03_the_worker_action_gate_is_not_the_autonomy_matrix() -> None:
    """§2(b): `harness._evaluate_action_gate` routes to L-5 RATIFICATION, not to the ADR-0008 matrix."""
    harness = _read(_REPO_ROOT / "src" / "maezo" / "tools" / "workers" / "harness.py")
    assert "def _evaluate_action_gate" in harness
    action_execution = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "action_execution.py")
    assert "def evaluate_worker_task" in action_execution
    assert "ActionExecutionGateway(approvals)" in action_execution
    assert "maezo.gateway.pep" not in action_execution, (
        "`action_execution.py` now imports the autonomy PEP — ADR-0041 §2(b) says it does not, and "
        "the L-5-is-not-L-2 distinction it draws would need rewriting"
    )


def test_af03_cibseven_start_process_is_still_an_ungated_pass_through() -> None:
    """§2(c): the highest-consequence catalogued operation the per-effect PEP never sees."""
    seam = _read(_REPO_ROOT / "src" / "maezo" / "gateway" / "seams" / "cibseven.py")
    body = seam[seam.index("async def start_process_instance") :]
    body = body[: body.index("async def correlate_message")]
    assert "await gate(" not in body, (
        "`start_process_instance` is now gated — ADR-0041 §2(c) and the manifest's `choked: false` "
        "for that surface must be revisited in the same change"
    )
    assert "PASS-THROUGH, deliberately ungated" in body, "the disclosed hole lost its disclosure"
    assert "await gate(self._seam" in seam, "the sibling operations must still be gated"


def test_af03_reconciliation_adr_agrees_with_the_live_enforcement_mode_and_choked_count() -> None:
    """The governed values this fence touches. A legitimate owner flip stays possible; it just
    cannot leave ADR-0041 asserting the old mode or the old count."""
    approvals = _read(_REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml")
    modes = [line.split(":", 1)[1].strip() for line in approvals.splitlines() if line.startswith("modo:")]
    assert len(modes) == 1, f"expected exactly one top-level `modo:` key, found {modes}"
    adr = _adr0041()
    assert f"modo: {modes[0]}" in adr, (
        f"action-approvals.yaml is `modo: {modes[0]}`, but ADR-0041 does not say so — "
        "update the ADR in the same change that flips the mode"
    )
    choked_false = sum(1 for line in approvals.splitlines() if line.strip() == "choked: false")
    assert choked_false == 3, (
        f"action-approvals.yaml now documents {choked_false} `choked: false` surfaces, not 3 — "
        "ADR-0041 §2(c) names three and must be updated with the manifest"
    )
    assert adr.count("choked: false") >= 1, "ADR-0041 must still quote the `choked: false` marker"


def test_af04_src_maezo_policies_still_does_not_exist() -> None:
    assert not (_REPO_ROOT / "src" / "maezo" / "policies").exists()
    autonomy = _REPO_ROOT / "spec" / "policies" / "autonomy"
    names = sorted(p.name for p in autonomy.glob("*.yaml"))
    assert names == ["L0-core.yaml", "_hard_frozen.yaml", "action-approvals.yaml", "tenants-amh.yaml"]


def test_af10_the_a2a_runtime_modules_adr0032_called_nonexistent_now_exist() -> None:
    a2a = _REPO_ROOT / "src" / "maezo" / "a2a"
    for module in ("dispatcher.py", "delegation.py", "facts.py", "idempotency.py", "signing.py"):
        assert (a2a / module).is_file(), f"{module} missing; ADR-0041 §4 is stale"


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
