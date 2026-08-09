"""The DL-0043 remediation flag can never activate itself (test (c) of the brief).

The whole design rests on one property: `spec/policies/privacy/phi-business-key-remediation.yaml`
is a DECLARATION, and only a complete, accountable human ratification turns a declaration into
behaviour. These tests attack that property from every angle a real mistake or a forgery would
take — a DRAFT file declaring `pseudo_keys`, a flag flipped without a reviewer, a truthy-but-not-
`True` value, a placeholder template, a corrupt file — and require `off` from all of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.platform.privacy.phi_key_policy import (
    PhiKeyMode,
    load_phi_key_policy,
    phi_key_policy,
)
from tests.support.privacy_policy import phi_key_policy_path, write_manifest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHIPPED_MANIFEST = _REPO_ROOT / "spec" / "policies" / "privacy" / "phi-business-key-remediation.yaml"


# ---------------------------------------------------------------------------
# The shipped artifact — pinned state
# ---------------------------------------------------------------------------


def test_shipped_manifest_exists_and_is_inert() -> None:
    """The artifact in the repo is DRAFT/off/unratified — nothing was decided by shipping it."""
    assert _SHIPPED_MANIFEST.is_file()
    policy = load_phi_key_policy(_SHIPPED_MANIFEST)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.declarado is PhiKeyMode.OFF
    assert policy.ratificado is False
    assert policy.scrubbing_enabled is False
    assert policy.pseudo_keys_enabled is False


def test_default_resolution_finds_the_shipped_manifest_and_is_off() -> None:
    """With no override, the loader resolves through `resolve_spec_dir` and still lands on off."""
    with phi_key_policy_path(None):
        assert phi_key_policy().modo is PhiKeyMode.OFF


# ---------------------------------------------------------------------------
# The `pseudo_keys` pre-requisite block — ratification must not proceed unaware
# ---------------------------------------------------------------------------
#
# `pseudo_keys` DECLARES that guards dual-read the legacy and pseudo key forms. Exactly ONE
# consumer does (`inadimplencia._query_ja_em_rescisao_cancel` via `contract_business_key_forms`,
# the anti-dupla-TERMINACAO guard). The START side does not: `start_process_idempotent` takes a
# single business key for both `find_active_instance` and `start_dedup_key`. Ratifying the mode
# as if the dual-read were universal DOUBLE-STARTS SP-OP-INADIMPLENCIA-001/SP-OP-CANCEL-001. The
# manifest therefore has to carry those gaps in machine-readable form, and these tests make
# deleting or quietly "closing" them a failing change rather than an invisible one.

_REQUIRED_PREREQ_IDS = frozenset(
    {
        "start_idempotency_dual_read",
        "start_dedup_key_dual_read",
        "beneficiario_pseudo_id_no_handoff",
    }
)


def _shipped_manifest_data() -> dict[str, object]:
    import yaml

    data = yaml.safe_load(_SHIPPED_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_manifest_declares_the_unmet_pseudo_keys_prerequisites() -> None:
    """The three known-open gaps are NAMED in the artifact, each with its adverse effect."""
    prereqs = _shipped_manifest_data().get("pre_requisitos_pseudo_keys")
    assert isinstance(prereqs, list) and prereqs, (
        "the manifest must carry a `pre_requisitos_pseudo_keys` block — without it a ratifier "
        "reading only the `modo` comment would believe the dual-read is universal"
    )
    by_id = {item["id"]: item for item in prereqs if isinstance(item, dict) and "id" in item}
    missing = _REQUIRED_PREREQ_IDS - set(by_id)
    assert not missing, f"pre-requisite(s) dropped from the manifest: {sorted(missing)}"
    for prereq_id in sorted(_REQUIRED_PREREQ_IDS):
        item = by_id[prereq_id]
        assert item["atendido"] is False, (
            f"{prereq_id} is recorded as met — if the CODE really closed it, this test and the "
            f"docstrings in base.contract_business_key_forms / PhiKeyMode.PSEUDO_KEYS must be "
            f"updated in the SAME change; flipping the flag alone is the failure mode this pins"
        )
        assert str(item.get("onde", "")).strip(), f"{prereq_id}: `onde` (file::symbol) is required"
        assert str(item.get("efeito_se_ratificado_sem_isto", "")).strip(), (
            f"{prereq_id}: the adverse effect must be stated, not left to the reader"
        )


def test_pseudo_keys_is_not_ratified_while_a_prerequisite_is_unmet() -> None:
    """THE fence: the shipped artifact may not be in force as `pseudo_keys` with gaps open.

    Deliberately stated over the DECLARED mode plus the ratification block rather than over the
    resolved policy: `load_phi_key_policy` would report `off` for a DRAFT anyway, which would
    make this test pass for the wrong reason the moment someone flipped `status`.
    """
    data = _shipped_manifest_data()
    prereqs = data.get("pre_requisitos_pseudo_keys")
    assert isinstance(prereqs, list)
    unmet = [i["id"] for i in prereqs if isinstance(i, dict) and i.get("atendido") is False]
    if not unmet:
        return  # every gap closed — the mode becomes ratifiable and this fence stops applying
    ratificacao = data.get("ratificacao")
    assert isinstance(ratificacao, dict)
    in_force = (
        str(data.get("status", "")).strip().upper() == "RATIFICADO" and ratificacao.get("ratificado") is True
    )
    assert not (in_force and str(data.get("modo", "")).strip().lower() == "pseudo_keys"), (
        f"`modo: pseudo_keys` ratified while these pre-requisites are unmet: {sorted(unmet)} — "
        f"each one is a documented double-start / double-audit exposure"
    )


# ---------------------------------------------------------------------------
# Duplicate top-level keys: a document that reads one way and loads another
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [("off", "pseudo_keys"), ("pseudo_keys", "off")],
)
def test_a_duplicated_modo_key_refuses_the_whole_manifest(tmp_path: Path, first: str, second: str) -> None:
    """`yaml.safe_load` keeps the LAST duplicate silently — so both orders must be refused.

    `off` then `pseudo_keys` is the forgery: a reviewer reading top-down sees the inert default
    and the file activates the mode. `pseudo_keys` then `off` is the mirror hazard: a
    ratification the reviewer believes they granted is silently inert. Neither document says what
    it appears to say, so neither is trusted.
    """
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        f'version: 1\nstatus: RATIFICADO\nmodo: "{first}"\nratificacao:\n  ratificado: true\n'
        f'  revisor: "r"\n  ratificado_em: "2026-01-01"\nmodo: "{second}"\n',
        encoding="utf-8",
    )
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.ratificado is False
    assert policy.motivo == "chave_duplicada"


@pytest.mark.parametrize("key_block", ["status: RATIFICADO", "unratified: false"])
def test_any_duplicated_top_level_key_refuses_the_manifest(tmp_path: Path, key_block: str) -> None:
    """Not just `modo`: `status` decides whether ANY mode is in force, and a duplicated
    `unratified` would let a template marker be visually present but load away."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        f"version: 1\n{key_block}\nstatus: DRAFT\nmodo: pseudo_keys\nratificacao:\n"
        f'  ratificado: true\n  revisor: "r"\n  ratificado_em: "2026-01-01"\n{key_block}\n',
        encoding="utf-8",
    )
    assert load_phi_key_policy(manifest).motivo == "chave_duplicada"


def test_duplicate_keys_nested_in_a_block_are_not_this_gates_business(tmp_path: Path) -> None:
    """Scope pin: the check is the document ROOT only. Nothing nested can flip the mode, and
    widening it would change `yaml.safe_load` semantics for a file this loader does not own."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: scrub_only\nratificacao:\n  ratificado: true\n"
        '  revisor: "r"\n  revisor: "outro"\n  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.SCRUB_ONLY
    assert policy.ratificado is True


def test_the_shipped_manifest_has_no_duplicate_top_level_keys() -> None:
    """The artifact itself must pass its own gate — otherwise it silently loads as `off` for a
    reason nobody intended, and the DRAFT/off pin above would be true by accident."""
    from maezo.platform.privacy.phi_key_policy import _duplicate_top_level_keys

    assert _duplicate_top_level_keys(_SHIPPED_MANIFEST.read_text(encoding="utf-8")) == ()


# ---------------------------------------------------------------------------
# THE forgery shape: a DRAFT manifest that declares an active mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_draft_status_can_never_enable_any_mode_but_off(tmp_path: Path, modo: str) -> None:
    """`status: DRAFT` + `modo: pseudo_keys` (fully-formed ratification block) => off.

    This is the forgery the brief names: someone stages the mode they want and leaves the file
    in DRAFT (or a reviewer approves the mode line without noticing the status). The loader must
    refuse to promote a draft, and must still REPORT what was declared so the discrepancy is
    visible rather than silently normalized away.
    """
    manifest = write_manifest(tmp_path / "m.yaml", modo=modo, status="DRAFT")
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.declarado is PhiKeyMode(modo)  # staged, and honestly reported
    assert policy.ratificado is False
    assert policy.motivo == "status_nao_ratificado"


@pytest.mark.parametrize("status", ["", "draft", "Rascunho", "APROVADO", "RATIFICADO_PARCIAL"])
def test_only_the_exact_ratificado_status_token_opens_the_gate(tmp_path: Path, status: str) -> None:
    """Anything that is not the `RATIFICADO` literal is a draft. No near-misses count."""
    manifest = write_manifest(tmp_path / "m.yaml", modo="pseudo_keys", status=status)
    assert load_phi_key_policy(manifest).modo is PhiKeyMode.OFF


def test_missing_status_key_is_a_draft(tmp_path: Path) -> None:
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        'version: 1\nmodo: pseudo_keys\nratificacao:\n  ratificado: true\n  revisor: "r"\n'
        '  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    assert load_phi_key_policy(manifest).modo is PhiKeyMode.OFF


# ---------------------------------------------------------------------------
# Accountability: all three ratification fields, or nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        'ratificacao:\n  ratificado: false\n  revisor: "r"\n  ratificado_em: "2026-01-01"\n',
        'ratificacao:\n  ratificado: true\n  revisor: null\n  ratificado_em: "2026-01-01"\n',
        'ratificacao:\n  ratificado: true\n  revisor: "   "\n  ratificado_em: "2026-01-01"\n',
        'ratificacao:\n  ratificado: true\n  revisor: "r"\n  ratificado_em: null\n',
        'ratificacao:\n  ratificado: true\n  revisor: "r"\n',
        "ratificacao:\n  ratificado: true\n",
        "ratificacao: {}\n",
        "",
    ],
)
def test_incomplete_ratification_is_not_a_ratification(tmp_path: Path, block: str) -> None:
    """A flag flipped with no reviewer/date reads as an unfinished edit, never as authority."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(f"version: 1\nstatus: RATIFICADO\nmodo: pseudo_keys\n{block}", encoding="utf-8")
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.ratificado is False


@pytest.mark.parametrize("truthy", ["'true'", "'yes'", "1", "'RATIFICADO'", "[1]"])
def test_ratificado_must_be_the_boolean_literal_true(tmp_path: Path, truthy: str) -> None:
    """Mirrors `auth_criteria`'s `is True` pin: truthy junk is refused, not coerced."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        f"version: 1\nstatus: RATIFICADO\nmodo: scrub_only\nratificacao:\n  ratificado: {truthy}\n"
        '  revisor: "r"\n  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    assert load_phi_key_policy(manifest).modo is PhiKeyMode.OFF


# ---------------------------------------------------------------------------
# The happy path exists — the gate opens for a real, complete ratification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("modo", "scrub", "pseudo"),
    [("off", False, False), ("scrub_only", True, False), ("pseudo_keys", True, True)],
)
def test_full_ratification_activates_the_declared_mode(
    tmp_path: Path, modo: str, scrub: bool, pseudo: bool
) -> None:
    """The gate is not decorative: a complete ratification really does activate the mode."""
    policy = load_phi_key_policy(write_manifest(tmp_path / "m.yaml", modo=modo))
    assert policy.modo is PhiKeyMode(modo)
    assert policy.ratificado is True
    assert policy.scrubbing_enabled is scrub
    assert policy.pseudo_keys_enabled is pseudo


def test_pseudo_keys_implies_scrubbing() -> None:
    """`pseudo_keys` is a superset of `scrub_only` — new key formats do not un-close the egress."""
    from maezo.platform.privacy.phi_key_policy import PhiKeyPolicy

    policy = PhiKeyPolicy(modo=PhiKeyMode.PSEUDO_KEYS, declarado=PhiKeyMode.PSEUDO_KEYS, ratificado=True)
    assert policy.scrubbing_enabled is True


# ---------------------------------------------------------------------------
# Every failure mode resolves to off, and none of them raise
# ---------------------------------------------------------------------------


def test_unratified_template_marker_is_refused_wholesale(tmp_path: Path) -> None:
    """The retention-matrix template guard, mirrored: a placeholder copy activates nothing."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        "unratified: true\nversion: 1\nstatus: RATIFICADO\nmodo: pseudo_keys\nratificacao:\n"
        '  ratificado: true\n  revisor: "r"\n  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.motivo == "unratified_template"


def test_bare_yaml_off_is_read_as_the_off_mode_not_as_a_broken_manifest(tmp_path: Path) -> None:
    """YAML 1.1 resolves a bare `off` to the boolean `false`. It must still mean the off MODE.

    Otherwise the most natural way to write the default would make the manifest look malformed —
    inert either way, but for a wrong and confusing reason, and it would train operators to
    ignore a `phi_key_policy_inactive` warning that is supposed to mean something.
    """
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: off\nratificacao:\n  ratificado: true\n"
        '  revisor: "r"\n  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.ratificado is True  # a correctly-read manifest, not a rejected one
    assert policy.motivo == ""


def test_bare_yaml_on_is_not_a_mode(tmp_path: Path) -> None:
    """`on` resolves to boolean True, which is not one of the three modes — refused, so: off."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: on\nratificacao:\n  ratificado: true\n"
        '  revisor: "r"\n  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    policy = load_phi_key_policy(manifest)
    assert policy.modo is PhiKeyMode.OFF
    assert policy.motivo == "modo_desconhecido"


@pytest.mark.parametrize("modo", ["scrub", "PSEUDO_KEYS_v2", "'on'", "1", "[]", "null"])
def test_unknown_mode_token_is_off(tmp_path: Path, modo: str) -> None:
    """An unrecognized `modo` is not guessed at — it is inert."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        f"version: 1\nstatus: RATIFICADO\nmodo: {modo}\nratificacao:\n  ratificado: true\n"
        '  revisor: "r"\n  ratificado_em: "2026-01-01"\n',
        encoding="utf-8",
    )
    assert load_phi_key_policy(manifest).modo is PhiKeyMode.OFF


def test_missing_file_is_off(tmp_path: Path) -> None:
    policy = load_phi_key_policy(tmp_path / "does-not-exist.yaml")
    assert policy.modo is PhiKeyMode.OFF
    assert policy.motivo == "file_not_found"


def test_directory_path_is_off(tmp_path: Path) -> None:
    assert load_phi_key_policy(tmp_path).modo is PhiKeyMode.OFF


@pytest.mark.parametrize(
    "body",
    [
        "modo: [unclosed\n",  # malformed YAML
        "- just\n- a\n- list\n",  # non-mapping root
        "",  # empty file -> None root
        "just a string\n",
    ],
)
def test_corrupt_manifest_is_off_and_never_raises(tmp_path: Path, body: str) -> None:
    manifest = tmp_path / "m.yaml"
    manifest.write_text(body, encoding="utf-8")
    assert load_phi_key_policy(manifest).modo is PhiKeyMode.OFF


def test_non_utf8_manifest_is_off(tmp_path: Path) -> None:
    """cp1252 bytes: `UnicodeDecodeError` subclasses ValueError, not OSError — caught explicitly."""
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(b"status: RATIFICADO\nmodo: pseudo_keys\nrevisor: Jo\xe3o\n")
    assert load_phi_key_policy(manifest).modo is PhiKeyMode.OFF


def test_env_override_is_honored_and_cached_accessor_agrees(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path / "m.yaml", modo="scrub_only")
    with phi_key_policy_path(manifest):
        assert phi_key_policy().modo is PhiKeyMode.SCRUB_ONLY
    # ...and the override is fully undone on exit (no leak into the next test).
    with phi_key_policy_path(None):
        assert phi_key_policy().modo is PhiKeyMode.OFF
