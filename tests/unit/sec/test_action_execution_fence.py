"""Structural fence for the `ActionExecutionGateway` approval records (MZO-040, ADR-0037 XRD-09).

SPEC-level and ARCHITECTURE-level assertions against the REAL shipped artifacts — never a fixture
copy of them. Sibling of `test_auto_approval_criteria_fence.py` (GAP-AUTH-4) and
`test_dentro_teto_source.py` (T1.9), same posture.

The claims pinned here:

  1. the shipped manifest exists, parses, and is DRAFT + shadow — the honest state of three open
     human gates (`PLANS.md` §0.6, DL-0042), asserted so a silent flip cannot slip in;
  2. NOTHING is approved, and every accountability field is the `PENDENTE` placeholder;
  3. a DRAFT manifest can never produce an ALLOW, tested on a FORGED copy of the real file;
  4. every action class declares the three code-frozen approver domains, and every declared class
     carries at least one real `file:line` runtime referent that EXISTS in the tree;
  5. every topic in `mapeamento_topicos` is a real BPMN external-task topic AND maps to a declared
     class — a typo here would silently un-gate an effect under enforcement;
  6. the manifest path is CODEOWNERS-covered — the whole design rests on ratification being a
     reviewed DATA change;
  7. the reason vocabulary is a closed set of bounded, non-PHI tokens.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import action_execution
from maezo.gateway.action_execution import (
    APPROVER_DOMAINS,
    MODE_SHADOW,
    PENDING_PLACEHOLDER,
    STATUS_RATIFIED,
    ActionExecutionGateway,
    load_action_approvals,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST = _REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml"
_BPMN_DIR = _REPO_ROOT / "spec" / "processes" / "bpmn"
_CODEOWNERS = _REPO_ROOT / ".github" / "CODEOWNERS"
_CAMUNDA_NS = "{http://camunda.org/schema/1.0/bpmn}"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bpmn_topics() -> set[str]:
    """Every `camunda:topic` declared across the real BPMN tree."""
    topics: set[str] = set()
    for path in sorted(_BPMN_DIR.glob("*.bpmn")):
        for element in ET.parse(path).getroot().iter():
            topic = element.get(f"{_CAMUNDA_NS}topic")
            if topic:
                topics.add(topic)
    return topics


# ---------------------------------------------------------------------------------------------
# 1 + 2. The shipped state: DRAFT, shadow, nothing approved
# ---------------------------------------------------------------------------------------------


def test_shipped_manifest_exists_and_is_wellformed(manifest: dict[str, Any]) -> None:
    """Deleting or breaking this file must fail CI, not silently downgrade enforcement to shadow.

    The loader fails closed on an absent manifest by design (it must not brick every external
    task), which means an in-repo deletion would be INVISIBLE at runtime once someone has flipped
    to `enforcing`. This assertion is the tripwire that closes that gap on the repo side; the
    residual runtime-only case (editing the deployed config) is disclosed in
    `docs/reviews/mzo-040-approval-packet.md`.
    """
    assert _MANIFEST.is_file()
    assert isinstance(manifest, dict)
    assert manifest["version"] == 1
    assert isinstance(manifest["acoes"], dict) and manifest["acoes"]
    assert isinstance(manifest["mapeamento_topicos"], dict) and manifest["mapeamento_topicos"]
    assert "unratified" not in manifest, "the shipped manifest is the real record, not the template"


def test_shipped_manifest_is_draft_and_shadow(manifest: dict[str, Any]) -> None:
    """The honest state of the three open gates, asserted so a silent flip cannot slip in.

    If this test starts failing, someone RATIFIED the action-execution gateway or turned
    enforcement on. That is a legitimate act — the whole design exists to make it possible — but
    it must be a reviewed, deliberate one, so it has to update this pin alongside the manifest,
    `PLANS.md` §0.6 and the decisions log.
    """
    assert manifest["status"] == "DRAFT"
    assert manifest["modo"] == MODE_SHADOW
    assert load_action_approvals(_MANIFEST).mode == MODE_SHADOW


def test_shipped_manifest_approves_nothing(manifest: dict[str, Any]) -> None:
    """No agent may fill an approval block. A forged approval is a compliance event, not a bug."""
    approvals = load_action_approvals(_MANIFEST)
    assert approvals.approved == frozenset()

    forged: list[str] = []
    for name, entry in manifest["acoes"].items():
        for domain, block in entry["aprovacoes"].items():
            if block.get("aprovado") is not False:
                forged.append(f"{name}.{domain}.aprovado")
            for field in ("aprovador", "data", "evidencia_ref"):
                if block.get(field) != PENDING_PLACEHOLDER:
                    forged.append(f"{name}.{domain}.{field}")
    assert forged == [], f"approval fields are no longer PENDING placeholders: {forged}"


def test_every_declared_class_denies_on_the_shipped_manifest(manifest: dict[str, Any]) -> None:
    gateway = ActionExecutionGateway(load_action_approvals(_MANIFEST))
    for name in manifest["acoes"]:
        decision = gateway.evaluate(name)
        assert decision.allow is False, f"{name} is ALLOWED on the shipped manifest"
        assert decision.enforced is False, "the shipped manifest must not enforce"


# ---------------------------------------------------------------------------------------------
# 3. The draft may never do — on a FORGERY of the real file
# ---------------------------------------------------------------------------------------------


def test_draft_file_can_never_allow_even_when_forged(tmp_path: Path, manifest: dict[str, Any]) -> None:
    """Take the REAL manifest, flip every `aprovado` to true and `modo` to enforcing, keep DRAFT.

    The fixture-based sibling in `tests/unit/gateway/` proves this property on a synthetic file;
    this one proves it on the actual shipped record, which is the artifact a forger would edit.
    """
    forged = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    forged["modo"] = "enforcing"
    assert forged["status"] == "DRAFT"
    for entry in forged["acoes"].values():
        for block in entry["aprovacoes"].values():
            block["aprovado"] = True
            block["aprovador"] = "Forged Approver"
            block["data"] = "2026-01-01"
            block["evidencia_ref"] = "forged://evidence"

    path = tmp_path / "forged.yaml"
    path.write_text(yaml.safe_dump(forged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    gateway = ActionExecutionGateway(load_action_approvals(path))
    assert gateway.approvals.approved == frozenset()
    for name in manifest["acoes"]:
        assert gateway.evaluate(name).allow is False, f"forged DRAFT ALLOWED {name} — fence broken"


def test_flipping_status_alone_still_requires_real_approvals(tmp_path: Path) -> None:
    """The converse: `status: RATIFICADO` on the shipped (unsigned) blocks still approves nothing.

    Ratifying is not one flip — it is the approvers' blocks PLUS the status. Neither half alone
    opens anything, which is what keeps a single careless edit from granting authority.
    """
    data = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    data["status"] = STATUS_RATIFIED
    data["modo"] = "enforcing"
    path = tmp_path / "status-only.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert load_action_approvals(path).approved == frozenset()


# ---------------------------------------------------------------------------------------------
# 4. Every class: three domains, and a real runtime referent
# ---------------------------------------------------------------------------------------------


def test_every_class_requires_all_three_approver_domains(manifest: dict[str, Any]) -> None:
    """`PLANS.md` §0.6 states the gate as Médica + ANS + Security, without per-class carve-outs.

    Narrowing a class's required set is a governance decision for a human, not a taxonomy tweak.
    This pin makes any narrowing visible as a test change alongside the manifest change.
    """
    for name, entry in manifest["acoes"].items():
        assert set(entry["dominios_exigidos"]) == set(APPROVER_DOMAINS), name
        assert set(entry["aprovacoes"]) == set(APPROVER_DOMAINS), name


_REFERENCE_RE = re.compile(r"^(?P<path>[\w./_-]+):(?P<line>\d+)$")


def test_every_class_cites_a_runtime_referent_that_exists(manifest: dict[str, Any]) -> None:
    """No class was invented to round out a taxonomy: each cites real code at a real line.

    A class with no runtime referent would be an approver signing for something that does not
    exist — the exact failure mode this whole packet is built to avoid.
    """
    problems: list[str] = []
    for name, entry in manifest["acoes"].items():
        surfaces = entry.get("superficies") or []
        if not surfaces:
            problems.append(f"{name}: no `superficies` declared")
            continue
        for surface in surfaces:
            match = _REFERENCE_RE.match(str(surface.get("referencia", "")))
            if match is None:
                problems.append(f"{name}: malformed referencia {surface.get('referencia')!r}")
                continue
            target = _REPO_ROOT / match.group("path")
            if not target.is_file():
                problems.append(f"{name}: {match.group('path')} does not exist")
                continue
            line_count = len(target.read_text(encoding="utf-8").splitlines())
            if int(match.group("line")) > line_count:
                problems.append(
                    f"{name}: {match.group('path')}:{match.group('line')} is past EOF ({line_count})"
                )
            if not isinstance(surface.get("choked"), bool):
                problems.append(f"{name}: `choked` must be an explicit bool for {match.group('path')}")
    assert problems == [], problems


def test_at_least_one_class_is_choked_so_shadow_produces_evidence(manifest: dict[str, Any]) -> None:
    """The gateway must actually see traffic, or "approve against evidence" is an empty promise."""
    choked = [
        name
        for name, entry in manifest["acoes"].items()
        if any(s.get("choked") is True for s in entry.get("superficies") or [])
    ]
    assert choked, "no class flows through the wired chokepoint — there would be no shadow evidence"


# ---------------------------------------------------------------------------------------------
# 5. The topic map: real topics, declared classes
# ---------------------------------------------------------------------------------------------


def test_every_mapped_topic_is_a_real_bpmn_external_task_topic(
    manifest: dict[str, Any], bpmn_topics: set[str]
) -> None:
    """A typo'd topic silently un-gates the effect it was meant to gate (it becomes unmapped)."""
    unknown = sorted(t for t in manifest["mapeamento_topicos"] if t not in bpmn_topics)
    assert unknown == [], f"topics not declared by any BPMN: {unknown}"


def test_every_mapped_topic_points_at_a_declared_class(manifest: dict[str, Any]) -> None:
    declared = set(manifest["acoes"])
    dangling = sorted(
        f"{topic} -> {name}" for topic, name in manifest["mapeamento_topicos"].items() if name not in declared
    )
    assert dangling == [], f"topic map points at undeclared classes: {dangling}"


def test_the_map_is_deliberately_partial_and_unmapped_topics_fail_closed(
    manifest: dict[str, Any], bpmn_topics: set[str]
) -> None:
    """Recorded, not hidden: most topics are UNMAPPED, and under enforcement they DENY.

    Classifying the remaining topics is a human act. This asserts the consequence is real, so the
    approval packet's warning ("completing the map is part of the ratification act") is backed by
    behaviour rather than prose.
    """
    unmapped = bpmn_topics - set(manifest["mapeamento_topicos"])
    assert unmapped, "if every topic became mapped, this pin (and the packet) must be updated"
    gateway = ActionExecutionGateway(load_action_approvals(_MANIFEST))
    sample = sorted(unmapped)[0]
    assert gateway.classify(sample) is None
    assert gateway.evaluate(gateway.classify(sample)).allow is False


# ---------------------------------------------------------------------------------------------
# 6 + 7. CODEOWNERS coverage and the bounded non-PHI vocabulary
# ---------------------------------------------------------------------------------------------


def test_the_manifest_path_is_codeowners_gated() -> None:
    """Ratification is a DATA change with no code and no redeploy — so the data needs a reviewer.

    The same reasoning `.github/CODEOWNERS` already records for the GAP-AUTH-4 manifest, and the
    same finding (GK-criteria finding 1) that caught a FALSE "CODEOWNERS-gated" claim once before:
    do not repeat the claim without checking the file.
    """
    owners = _CODEOWNERS.read_text(encoding="utf-8")
    lines = [
        line.strip() for line in owners.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    covering = [
        line
        for line in lines
        if line.split()[0] in ("/spec/policies/autonomy/", "/spec/policies/autonomy/action-approvals.yaml")
    ]
    assert covering, "spec/policies/autonomy/action-approvals.yaml has no CODEOWNERS entry"
    for line in covering:
        assert "@Omni-Saude/security" in line, f"security review is not required by: {line}"


def test_every_reason_token_is_bounded_and_non_phi() -> None:
    """The whole vocabulary is a closed enum of bounded tokens — nothing it emits can carry PHI."""
    reasons = [
        value
        for name, value in vars(action_execution).items()
        if name.startswith("REASON_") and isinstance(value, str)
    ]
    assert reasons
    for token in reasons:
        assert action_execution._is_bounded_token(token), token


def test_the_approver_domain_set_is_code_frozen() -> None:
    """The three domains come from `PLANS.md` §0.6 / DL-0042, not from editable data."""
    assert frozenset({"medica", "ans", "seguranca"}) == APPROVER_DOMAINS
