"""Behavioural proofs for the `ActionExecutionGateway` (MZO-040, ADR-0037 XRD-09).

Five claims, in the order they matter:

  (a) SHADOW IS INERT — the choked path produces byte-identical outcomes with the gateway live
      and with it neutralized. This is the claim that makes wiring an unapproved gateway into the
      per-call effect path defensible at all; everything else is secondary to it.
  (b) ENFORCING + NOTHING APPROVED denies every declared class.
  (c) ENFORCING + ONE CLASS APPROVED allows that class and ONLY that class.
  (d) A DRAFT FILE CAN NEVER ALLOW — including the explicit forgery shape where every `aprovado`
      reads `true` and `modo` reads `enforcing` while `status` is still DRAFT.
  (e) THE DEPLOYMENT OVERRIDE CANNOT ENFORCE — a manifest reached through
      `MAEZO_ACTION_APPROVALS_PATH` never passed a reviewer, so it may only enforce when a second,
      explicit companion env flag says so. Default: shadow-only.

Sibling of `tests/unit/sec/test_action_execution_fence.py`, which asserts the same properties
against the REAL shipped manifest rather than fixtures.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import pytest
import structlog
import yaml

from maezo.gateway import action_execution
from maezo.gateway.action_execution import (
    APPROVER_DOMAINS,
    ENFORCEMENT_ENFORCING,
    ENFORCEMENT_SHADOW,
    EVENT_ENFORCED,
    EVENT_SHADOW,
    MANIFEST_PATH_ENV,
    MODE_ENFORCING,
    MODE_SHADOW,
    MODE_SHADOW_OVERRIDE,
    MODE_UNRESOLVED,
    OVERRIDE_ENFORCEMENT_ENABLED,
    OVERRIDE_ENFORCEMENT_ENV,
    REASON_ACTION_UNDECLARED,
    REASON_ACTION_UNMAPPED,
    REASON_APPROVAL_INCOMPLETE,
    REASON_APPROVAL_PENDING,
    REASON_APPROVED,
    REASON_DOMAIN_UNKNOWN,
    REASON_DOMAINS_EMPTY,
    REASON_DOMAINS_INCOMPLETE,
    REASON_INTERNAL_ERROR,
    REASON_MANIFEST_DRAFT,
    REASON_MANIFEST_UNAVAILABLE,
    REASON_OVERRIDE_NOT_ENFORCEABLE,
    ActionExecutionGateway,
    evaluate_worker_task,
    load_action_approvals,
)
from maezo.tools.workers import harness as harness_module
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerHarness,
)

_TOPIC = "operadora.auth.issue_authorization"
_CLASS = "autorizacao_emissao"
_OTHER_CLASS = "pagamento_emissao"
_OTHER_TOPIC = "operadora.pagto.release_low_value_payment"


# ---------------------------------------------------------------------------------------------
# Manifest fixtures — built here, never copied from spec/, so a shipped-file edit cannot silently
# turn these behavioural proofs green.
# ---------------------------------------------------------------------------------------------


def _approval_block(*, approved: bool) -> dict[str, Any]:
    if not approved:
        return {"aprovado": False, "aprovador": "PENDENTE", "data": "PENDENTE", "evidencia_ref": "PENDENTE"}
    return {
        "aprovado": True,
        "aprovador": "Fixture Approver, fixture role",
        "data": "2026-01-01",
        "evidencia_ref": "fixture://evidence",
    }


def _manifest(
    *,
    status: str = "DRAFT",
    modo: str = "shadow",
    approved_classes: frozenset[str] = frozenset(),
    class_enforcement: str | None = None,
    default_enforcement: str | None = None,
    acoes_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A fixture manifest.

    ONDA 1 §7.3 made enforcement TWO-DIMENSIONAL: `Decision.enforced` is now
    `modo == enforcing AND acoes.<class>.enforcement == enforcing`, with an ABSENT per-class field
    resolving to `shadow`. So a fixture that means "this manifest enforces" must now say so in
    BOTH dimensions — `class_enforcement` defaults to mirroring `modo`, which keeps every existing
    proof asserting exactly the claim it asserted before, expressed in the new data model. The
    `shadow`×`enforcing` cross-product is proved separately in
    `test_effect_enforcement.py::test_enforcement_is_the_conjunction_of_both_dimensions`.
    """
    resolved_enforcement = class_enforcement if class_enforcement is not None else modo

    def _acao(name: str) -> dict[str, Any]:
        is_approved = name in approved_classes
        return {
            "descricao": f"fixture class {name}",
            "enforcement": resolved_enforcement,
            "dominios_exigidos": sorted(APPROVER_DOMAINS),
            "aprovacoes": {d: _approval_block(approved=is_approved) for d in sorted(APPROVER_DOMAINS)},
        }

    manifest: dict[str, Any] = {
        "version": 1,
        "status": status,
        "modo": modo,
        "acoes": {_CLASS: _acao(_CLASS), _OTHER_CLASS: _acao(_OTHER_CLASS)},
        "mapeamento_topicos": {_TOPIC: _CLASS, _OTHER_TOPIC: _OTHER_CLASS},
    }
    if default_enforcement is not None:
        manifest["enforcement_padrao_nao_mapeado"] = default_enforcement
    if acoes_map is not None:
        manifest["mapeamento_acoes"] = acoes_map
    return manifest


def _write(tmp_path: Path, manifest: dict[str, Any], name: str = "action-approvals.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _gateway(tmp_path: Path, manifest: dict[str, Any]) -> ActionExecutionGateway:
    return ActionExecutionGateway(load_action_approvals(_write(tmp_path, manifest)))


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    """`action_approvals` is lru_cached on the resolved path; a stale entry would fake a pass."""
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_ambient_env_override() -> Any:
    """No ambient deployment override may leak into these proofs, in either direction.

    The path override and its enforcement companion are DEPLOYMENT surfaces; every test in this
    file drives the loader through the explicit `path` argument instead, and the few that do
    exercise the override set it themselves. Clearing both here means a developer with either
    variable exported cannot turn a real failure green (or a real pass red).
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv(MANIFEST_PATH_ENV, raising=False)
        mp.delenv(OVERRIDE_ENFORCEMENT_ENV, raising=False)
        yield


# ---------------------------------------------------------------------------------------------
# (d) THE DRAFT MAY NEVER DO — the forgery shape, tested explicitly
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("modo", [MODE_SHADOW, MODE_ENFORCING])
def test_draft_file_can_never_allow_even_when_every_block_is_forged(tmp_path: Path, modo: str) -> None:
    """THE fence. `status: DRAFT` + every `aprovado: true` + `modo: enforcing` STILL denies.

    This is the shape a forged ratification actually takes: someone flips the booleans (or an
    agent is talked into it) without the outer act that a CODEOWNER reviews. The status gate is
    checked BEFORE any block is read, so the forged blocks are never even consulted, and the
    denial reason names the real problem instead of "pending".
    """
    gateway = _gateway(
        tmp_path,
        _manifest(status="DRAFT", modo=modo, approved_classes=frozenset({_CLASS, _OTHER_CLASS})),
    )
    assert gateway.approvals.approved == frozenset()
    for name in (_CLASS, _OTHER_CLASS):
        decision = gateway.evaluate(name)
        assert decision.allow is False, f"{name} ALLOWED from a DRAFT manifest — fence broken"
        assert decision.reason == REASON_MANIFEST_DRAFT


@pytest.mark.parametrize("status", ["draft", "ratificado", "RATIFICADO ", "", None, True, 1])
def test_only_the_exact_ratified_literal_opens_the_gate(tmp_path: Path, status: Any) -> None:
    """Near-misses are not ratifications: lowercase, trailing space, empty, null, non-strings."""
    manifest = _manifest(modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["status"] = status
    gateway = _gateway(tmp_path, manifest)
    assert gateway.evaluate(_CLASS).allow is False


# ---------------------------------------------------------------------------------------------
# (b) ENFORCING + NO APPROVAL = DENY EVERY CLASS
# ---------------------------------------------------------------------------------------------


def test_enforcing_with_nothing_approved_denies_every_declared_class(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING))
    assert gateway.mode == MODE_ENFORCING
    assert gateway.approvals.declared == frozenset({_CLASS, _OTHER_CLASS})
    for name in sorted(gateway.approvals.declared):
        decision = gateway.evaluate(name)
        assert decision.allow is False
        assert decision.enforced is True, (
            "a denial must actually block once BOTH dimensions say enforcing — `modo: enforcing` "
            "globally and `acoes.<class>.enforcement: enforcing` per class (ONDA 1 §7.3, which "
            "`_manifest` mirrors off `modo` by default). Before §7.3 this read `modo` alone; the "
            "conjunction is proved dimension-by-dimension in test_effect_enforcement.py"
        )
        assert decision.reason == REASON_APPROVAL_PENDING


def test_unmapped_topic_denies_and_unknown_class_denies(tmp_path: Path) -> None:
    """XRD-09: "ação/política desconhecida ... negam". Both unknowns are distinguishable."""
    gateway = _gateway(tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING))
    assert gateway.classify("operadora.nao.mapeado") is None
    assert gateway.evaluate(gateway.classify("operadora.nao.mapeado")).reason == REASON_ACTION_UNMAPPED
    assert gateway.evaluate("classe_inexistente").reason == REASON_ACTION_UNDECLARED
    assert gateway.evaluate(None).allow is False
    assert gateway.evaluate("   ").allow is False


# ---------------------------------------------------------------------------------------------
# (c) ENFORCING + ONE CLASS APPROVED = THAT CLASS ALONE ALLOWS
# ---------------------------------------------------------------------------------------------


def test_enforcing_with_one_ratified_class_allows_only_that_class(tmp_path: Path) -> None:
    gateway = _gateway(
        tmp_path,
        _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS})),
    )
    allowed = gateway.evaluate(_CLASS)
    assert allowed.allow is True
    assert allowed.reason == REASON_APPROVED
    assert allowed.enforced is True

    denied = gateway.evaluate(_OTHER_CLASS)
    assert denied.allow is False
    assert denied.reason == REASON_APPROVAL_PENDING


def test_topic_routing_resolves_the_class_a_human_declared(tmp_path: Path) -> None:
    gateway = _gateway(
        tmp_path,
        _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS})),
    )
    assert gateway.evaluate(gateway.classify(_TOPIC)).allow is True
    assert gateway.evaluate(gateway.classify(_OTHER_TOPIC)).allow is False


# ---------------------------------------------------------------------------------------------
# Partial / forged approval blocks — each must read as "not approved", never as a ratification
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aprovador", "PENDENTE"),
        ("aprovador", ""),
        ("aprovador", "   "),
        ("aprovador", None),
        ("data", "PENDENTE"),
        ("data", None),
        ("evidencia_ref", "PENDENTE"),
        ("evidencia_ref", ""),
    ],
)
def test_pending_or_blank_accountability_field_is_not_an_approval(
    tmp_path: Path, field: str, value: Any
) -> None:
    """`aprovado: true` with an unfilled accountability field is an unfinished edit, not a sign-off."""
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["aprovacoes"]["medica"][field] = value
    gateway = _gateway(tmp_path, manifest)
    decision = gateway.evaluate(_CLASS)
    assert decision.allow is False
    assert decision.reason == REASON_APPROVAL_INCOMPLETE


@pytest.mark.parametrize("truthy", ["true", "True", 1, "yes", [1]])
def test_only_the_boolean_literal_true_counts_as_approved(tmp_path: Path, truthy: Any) -> None:
    """The repo's fail-closed pin idiom: truthy junk is REFUSED, `is True` is required."""
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["aprovacoes"]["ans"]["aprovado"] = truthy
    assert _gateway(tmp_path, manifest).evaluate(_CLASS).allow is False


def test_a_missing_domain_block_denies(tmp_path: Path) -> None:
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    del manifest["acoes"][_CLASS]["aprovacoes"]["seguranca"]
    assert _gateway(tmp_path, manifest).evaluate(_CLASS).allow is False


def test_emptying_the_required_domain_list_cannot_approve(tmp_path: Path) -> None:
    """ "Approve by deleting the requirement" — the obvious shortcut, closed explicitly.

    An empty `dominios_exigidos` is NOT "no requirements to satisfy"; it is an unusable record.
    Without this rule, `dominios_exigidos: []` would make `all(...)` vacuously true and grant the
    class with zero human attestations.
    """
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["dominios_exigidos"] = []
    decision = _gateway(tmp_path, manifest).evaluate(_CLASS)
    assert decision.allow is False
    assert decision.reason == REASON_DOMAINS_EMPTY


def test_an_unknown_approver_domain_denies_rather_than_being_ignored(tmp_path: Path) -> None:
    """The domain set is code-frozen; inventing `dominios_exigidos: [marketing]` must not pass."""
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["dominios_exigidos"] = ["marketing"]
    manifest["acoes"][_CLASS]["aprovacoes"]["marketing"] = _approval_block(approved=True)
    decision = _gateway(tmp_path, manifest).evaluate(_CLASS)
    assert decision.allow is False
    assert decision.reason == REASON_DOMAIN_UNKNOWN


def test_dropping_a_required_domain_from_the_list_cannot_approve_the_class(tmp_path: Path) -> None:
    """Narrowing the required set is a governance act; two of three signatures is not three."""
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["aprovacoes"]["medica"] = _approval_block(approved=False)
    assert _gateway(tmp_path, manifest).evaluate(_CLASS).allow is False


# ---------------------------------------------------------------------------------------------
# CARDINALITY of `dominios_exigidos` — a KNOWN domain set is not a COMPLETE one
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "required",
    [
        ["medica"],
        ["ans"],
        ["seguranca"],
        ["medica", "ans"],
        ["medica", "medica", "medica"],
        ["ans", "ans"],
    ],
    ids=["medica", "ans", "seguranca", "two-of-three", "medica-x3", "ans-x2"],
)
def test_a_partial_domain_set_cannot_approve_a_class(tmp_path: Path, required: list[str]) -> None:
    """The hole the vocabulary check alone left open: `dominios_exigidos: [medica]` + one block.

    Every entry here is a VALID domain, so the unknown-domain fence never fires; what is wrong is
    the CARDINALITY. Without set-equality, one approver's signature would have granted the class,
    and `[medica, medica, medica]` would have counted a single attestation three times. The gate
    is Médica + ANS + Security (`PLANS.md` §0.6, DL-0042) — all three, once each.
    """
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["dominios_exigidos"] = required
    # The blocks named by the shortened list ARE fully signed — the record is internally
    # consistent, and would have read as approved before this fence existed.
    decision = _gateway(tmp_path, manifest).evaluate(_CLASS)
    assert decision.allow is False, f"{required} approved a class with fewer than three domains"
    assert decision.reason == REASON_DOMAINS_INCOMPLETE


def test_declaring_all_three_domains_leaves_the_approval_path_unchanged(tmp_path: Path) -> None:
    """The cardinality fence is a NO-OP for a well-formed record — including in a shuffled order."""
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["dominios_exigidos"] = ["seguranca", "medica", "ans"]
    decision = _gateway(tmp_path, manifest).evaluate(_CLASS)
    assert decision.allow is True
    assert decision.reason == REASON_APPROVED


def test_an_unknown_domain_still_reports_unknown_not_incomplete(tmp_path: Path) -> None:
    """Vocabulary is checked BEFORE cardinality: `[marketing]` is a different mistake from `[medica]`."""
    manifest = _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    manifest["acoes"][_CLASS]["dominios_exigidos"] = ["marketing"]
    assert _gateway(tmp_path, manifest).evaluate(_CLASS).reason == REASON_DOMAIN_UNKNOWN


# ---------------------------------------------------------------------------------------------
# Loader fail-closed modes — never raises, never defaults to approved, never turns enforcement on
# ---------------------------------------------------------------------------------------------


def test_missing_file_fails_closed_without_raising(tmp_path: Path) -> None:
    approvals = load_action_approvals(tmp_path / "absent.yaml")
    assert approvals.approved == frozenset()
    assert approvals.degraded is True
    assert approvals.mode == MODE_UNRESOLVED
    decision = ActionExecutionGateway(approvals).evaluate(_CLASS)
    assert decision.allow is False
    assert decision.reason == REASON_MANIFEST_UNAVAILABLE
    assert decision.enforced is False, "a broken manifest must not silently switch enforcement ON"


@pytest.mark.parametrize(
    "raw",
    [
        "unratified: true\nstatus: RATIFICADO\nmodo: enforcing\n",  # template guard
        "[]",  # root not a mapping
        "acoes: RATIFICADO\nstatus: RATIFICADO\n",  # `acoes` present but not a mapping
        "status: RATIFICADO\nmodo: enforcing\nmapeamento_topicos: 7\n",  # topic map not a mapping
        "a: b\n- c\n",  # genuinely malformed YAML (ParserError)
        "*nope",  # undefined alias (ComposerError)
    ],
)
def test_unusable_manifests_fail_closed(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "action-approvals.yaml"
    path.write_text(raw, encoding="utf-8")
    approvals = load_action_approvals(path)
    assert approvals.approved == frozenset()
    assert approvals.mode == MODE_UNRESOLVED
    assert approvals.degraded is True


def test_a_wellformed_but_empty_manifest_approves_nothing(tmp_path: Path) -> None:
    """Valid YAML of the wrong shape is NOT "degraded" — it is an empty, honest record.

    `status: RATIFICADO` with no `acoes` parses cleanly, so the loader reports it as loaded rather
    than broken. It must still approve nothing: emptying the file is not a way to grant anything.
    """
    path = tmp_path / "action-approvals.yaml"
    path.write_text("version: 1\nstatus: RATIFICADO\nmodo: enforcing\n", encoding="utf-8")
    approvals = load_action_approvals(path)
    assert approvals.degraded is False
    assert approvals.approved == frozenset()
    assert approvals.declared == frozenset()
    assert ActionExecutionGateway(approvals).evaluate(_CLASS).allow is False


def test_a_directory_path_fails_closed(tmp_path: Path) -> None:
    assert load_action_approvals(tmp_path).degraded is True


@pytest.mark.parametrize(
    ("raw", "shadowed"),
    [
        ("version: 1\nstatus: DRAFT\nmodo: shadow\nstatus: RATIFICADO\n", "status"),
        ("version: 1\nstatus: RATIFICADO\nmodo: shadow\nmodo: enforcing\n", "modo"),
    ],
    ids=["status-shadowed", "modo-shadowed"],
)
def test_a_duplicate_top_level_key_is_refused_instead_of_last_one_wins(
    tmp_path: Path, raw: str, shadowed: str
) -> None:
    """YAML's default is LAST-one-wins; for a governance record that is a silent override.

    A reviewer reading the diff sees `status: DRAFT` near the top and approves; twenty lines down a
    second `status: RATIFICADO` is what `yaml.safe_load` would actually keep. The manifest's whole
    security property is "what the reviewer saw is what the loader sees", so a duplicated key is a
    LOAD FAILURE here — fail-closed, like every other unusable-manifest shape.
    """
    path = tmp_path / "action-approvals.yaml"
    path.write_text(raw, encoding="utf-8")
    approvals = load_action_approvals(path)
    assert approvals.degraded is True, f"the second `{shadowed}` silently won"
    assert approvals.mode == MODE_UNRESOLVED
    assert approvals.approved == frozenset()


def test_a_duplicate_nested_key_is_refused_too(tmp_path: Path) -> None:
    """The same shadowing one level down — a second `aprovacoes:` block under one class."""
    path = tmp_path / "action-approvals.yaml"
    path.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: enforcing\n"
        "acoes:\n"
        f"  {_CLASS}:\n"
        "    dominios_exigidos: [medica, ans, seguranca]\n"
        "    aprovacoes: {}\n"
        "    aprovacoes:\n"
        "      medica: {aprovado: true}\n",
        encoding="utf-8",
    )
    assert load_action_approvals(path).degraded is True


def test_a_merge_key_is_refused_with_a_dedicated_reason_not_invalid_yaml(tmp_path: Path) -> None:
    """W5 GK MINOR (item 6): a YAML merge key (`<<: *anchor`) trips the SAME underlying
    `ConstructorError` PyYAML raises for a genuinely unsupported tag — but the manifest is
    well-formed YAML, not malformed. It must get its OWN reason, `merge_key_unsupported`, never
    the generic `invalid_yaml` a ratifier reaching for an anchor would have to puzzle over (there
    is no syntax error to find). Fail-closed SHAPE is unchanged either way — degraded, unresolved,
    nothing approved — only the log's `reason` token differs.
    """
    path = tmp_path / "action-approvals.yaml"
    path.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: enforcing\n"
        "base: &base\n"
        "  dominios_exigidos: [medica, ans, seguranca]\n"
        "acoes:\n"
        f"  {_CLASS}:\n"
        "    <<: *base\n"
        "    descricao: fixture\n",
        encoding="utf-8",
    )
    with structlog.testing.capture_logs() as logs:
        approvals = load_action_approvals(path)

    assert approvals.degraded is True
    assert approvals.mode == MODE_UNRESOLVED
    assert approvals.approved == frozenset()

    unavailable = [e for e in logs if e.get("event") == "action_approvals_manifest_unavailable"]
    assert len(unavailable) == 1, logs
    assert unavailable[0]["reason"] == "merge_key_unsupported"
    assert unavailable[0]["reason"] != "invalid_yaml"


def test_an_unrelated_unsupported_tag_still_gets_the_generic_invalid_yaml_reason(tmp_path: Path) -> None:
    """Contrast case: an unrelated unsupported YAML tag (never a merge key) still raises the SAME
    `ConstructorError` class, but must keep the generic `invalid_yaml` reason — that document
    really is one this loader cannot make sense of, unlike a `<<` merge key.

    NOT proof the merge-key carve-out is narrow to `<<` itself — the discriminator is a substring
    test on `exc.problem` (`_MERGE_KEY_TAG in exc.problem`), so it is narrowed by TAG, not by
    `<<`-key-usage specifically; see `test_a_merge_tagged_value_also_gets_merge_key_unsupported`
    below for the pinned collision (V3 GK REVISE — the earlier "proven NARROW" docstring here
    overstated this)."""
    path = tmp_path / "action-approvals.yaml"
    path.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: enforcing\nacoes: !!python/object/apply:builtins.list []\n",
        encoding="utf-8",
    )
    with structlog.testing.capture_logs() as logs:
        approvals = load_action_approvals(path)

    assert approvals.degraded is True
    unavailable = [e for e in logs if e.get("event") == "action_approvals_manifest_unavailable"]
    assert len(unavailable) == 1, logs
    assert unavailable[0]["reason"] == "invalid_yaml"


def test_a_merge_tagged_value_also_gets_merge_key_unsupported(tmp_path: Path) -> None:
    """COLLISION, DOCUMENTED NOT FIXED (V3 GK REVISE): the `merge_key_unsupported` discriminator
    (`_MERGE_KEY_TAG in exc.problem`, `action_execution.py:667`) is a SUBSTRING test on the tag
    name, not a check that a `<<` key specifically was used. A value explicitly tagged `!!merge`
    — never used as a `<<` key, so never a real merge — carries the identical
    `tag:yaml.org,2002:merge` tag PyYAML puts in `exc.problem` for a genuine `<<` key, and so ALSO
    reports `merge_key_unsupported`. This is NOT re-engineered here: fail-closed shape is identical
    either way (degraded, unresolved, nothing approved), so the collision is a documentation-
    precision gap, not a behavioral one — pinned here so it stays honest, not "proven narrow"."""
    path = tmp_path / "action-approvals.yaml"
    path.write_text(
        "version: 1\nstatus: RATIFICADO\nmodo: enforcing\ndescricao: !!merge fixture\n",
        encoding="utf-8",
    )
    with structlog.testing.capture_logs() as logs:
        approvals = load_action_approvals(path)

    assert approvals.degraded is True
    assert approvals.mode == MODE_UNRESOLVED
    assert approvals.approved == frozenset()

    unavailable = [e for e in logs if e.get("event") == "action_approvals_manifest_unavailable"]
    assert len(unavailable) == 1, logs
    assert unavailable[0]["reason"] == "merge_key_unsupported"


# ---------------------------------------------------------------------------------------------
# (e) THE DEPLOYMENT OVERRIDE MAY NOT ENFORCE ON ITS OWN
# ---------------------------------------------------------------------------------------------


def test_the_env_override_cannot_enforce_without_the_companion_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE BYPASS, closed. A runtime env var alone must never start blocking care-affecting calls.

    `MAEZO_ACTION_APPROVALS_PATH` swaps the governed manifest for a file no CODEOWNER ever saw —
    and with `main` carrying no server-side protection, the CODEOWNERS listing on the shipped file
    is advisory anyway. So an override-sourced manifest reading `status: RATIFICADO` + `modo:
    enforcing` resolves to `shadow_override`: it still EVALUATES (that is the override's legitimate
    use — previewing a candidate record), it never ENFORCES, and the ALLOW it produces carries a
    reason token that cannot be mistaken for a governed approval.
    """
    path = _write(
        tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    )
    monkeypatch.setenv(MANIFEST_PATH_ENV, str(path))

    approvals = load_action_approvals()  # no explicit path -> the env override is what resolves
    assert approvals.mode == MODE_SHADOW_OVERRIDE
    assert approvals.approved == frozenset({_CLASS}), "evaluation still runs; only enforcement is withheld"

    gateway = ActionExecutionGateway(approvals)
    allowed = gateway.evaluate(_CLASS)
    assert allowed.allow is True
    assert allowed.enforced is False, "an env var alone turned enforcement ON — the bypass is open"
    assert allowed.reason == REASON_OVERRIDE_NOT_ENFORCEABLE

    denied = gateway.evaluate(_OTHER_CLASS)
    assert denied.enforced is False
    assert denied.reason == REASON_APPROVAL_PENDING, "denial reasons stay precise under the override"


def test_the_env_override_enforces_only_with_the_explicit_companion_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged-rollout path: TWO deliberate, separately auditable env acts, never one."""
    path = _write(
        tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    )
    monkeypatch.setenv(MANIFEST_PATH_ENV, str(path))
    monkeypatch.setenv(OVERRIDE_ENFORCEMENT_ENV, OVERRIDE_ENFORCEMENT_ENABLED)

    gateway = ActionExecutionGateway(load_action_approvals())
    assert gateway.mode == MODE_ENFORCING
    allowed = gateway.evaluate(_CLASS)
    assert (allowed.allow, allowed.enforced, allowed.reason) == (True, True, REASON_APPROVED)
    assert gateway.evaluate(_OTHER_CLASS).enforced is True


@pytest.mark.parametrize("flag", ["", " ", "0", "true", "True", "yes", "1 ", "01"])
def test_only_the_exact_companion_literal_restores_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """The repo's fail-closed pin idiom applied to the flag: truthy junk is NOT authorisation."""
    path = _write(
        tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS}))
    )
    monkeypatch.setenv(MANIFEST_PATH_ENV, str(path))
    monkeypatch.setenv(OVERRIDE_ENFORCEMENT_ENV, flag)
    assert load_action_approvals().mode == MODE_SHADOW_OVERRIDE


def test_the_companion_flag_alone_grants_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No override in play: the companion flag is inert, and the explicit-path seam is untouched."""
    monkeypatch.setenv(OVERRIDE_ENFORCEMENT_ENV, OVERRIDE_ENFORCEMENT_ENABLED)
    gateway = _gateway(
        tmp_path,
        _manifest(status="RATIFICADO", modo=MODE_ENFORCING, approved_classes=frozenset({_CLASS})),
    )
    assert gateway.mode == MODE_ENFORCING
    assert gateway.evaluate(_CLASS).reason == REASON_APPROVED


def test_the_override_fence_is_narrow_a_shadow_override_stays_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the ENFORCEMENT leg is withheld — an override declaring `shadow` is reported as shadow."""
    path = _write(
        tmp_path, _manifest(status="RATIFICADO", modo=MODE_SHADOW, approved_classes=frozenset({_CLASS}))
    )
    monkeypatch.setenv(MANIFEST_PATH_ENV, str(path))
    gateway = ActionExecutionGateway(load_action_approvals())
    assert gateway.mode == MODE_SHADOW
    assert gateway.evaluate(_CLASS).reason == REASON_APPROVED


def test_an_override_sourced_load_is_logged_at_error_level_with_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swapping the governed record must leave a trace legible without reading the manifest."""
    path = _write(tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING))
    monkeypatch.setenv(MANIFEST_PATH_ENV, str(path))
    with structlog.testing.capture_logs() as logs:
        load_action_approvals()
    overridden = [e for e in logs if e.get("event") == "action_approvals_manifest_path_overridden"]
    assert len(overridden) == 1
    assert overridden[0]["log_level"] == "error"
    assert overridden[0]["path"] == str(path)
    assert overridden[0]["enforcement_permitted"] is False
    assert any(e.get("event") == "action_approvals_override_enforcement_refused" for e in logs)


def test_the_shipped_no_override_path_emits_no_override_logs() -> None:
    """The production resolution is unchanged: no override log, no enforcement withheld."""
    with structlog.testing.capture_logs() as logs:
        approvals = load_action_approvals()
    assert approvals.mode == MODE_SHADOW, "the shipped manifest resolves exactly as before"
    events = {e.get("event") for e in logs}
    assert "action_approvals_manifest_path_overridden" not in events
    assert "action_approvals_override_enforcement_refused" not in events


@pytest.mark.parametrize("modo", ["ENFORCING", "enforce", "", None, True])
def test_an_unrecognised_mode_never_enforces(tmp_path: Path, modo: Any) -> None:
    """`modo` is not guessed. Anything but the two exact literals resolves to `unresolved`."""
    manifest = _manifest(status="RATIFICADO", approved_classes=frozenset({_CLASS}))
    manifest["modo"] = modo
    gateway = _gateway(tmp_path, manifest)
    assert gateway.mode == MODE_UNRESOLVED
    assert gateway.evaluate(_CLASS).enforced is False


def test_shadow_mode_decision_never_enforces(tmp_path: Path) -> None:
    gateway = _gateway(
        tmp_path,
        _manifest(status="RATIFICADO", modo=MODE_SHADOW, approved_classes=frozenset({_CLASS})),
    )
    assert gateway.evaluate(_CLASS).enforced is False
    assert gateway.evaluate(_OTHER_CLASS).enforced is False


def test_evaluate_is_total_and_never_raises(tmp_path: Path) -> None:
    """The property the SHADOW wiring depends on: no input can make `evaluate` throw."""
    gateway = _gateway(tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING))
    for value in (None, "", "  ", 1, 3.5, [], {}, object()):
        decision = gateway.evaluate(value)  # type: ignore[arg-type]
        assert decision.allow is False


# ---------------------------------------------------------------------------------------------
# (a) SHADOW IS PROVABLY INERT ON THE REAL CHOKED PATH
# ---------------------------------------------------------------------------------------------


def _task(topic: str = _TOPIC, task_id: str = "task-1") -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables={"guia": "G-1"},
    )


def _harness(transport: FakeWorkerTransport, sink: FakeAuditSink) -> WorkerHarness:
    harness = WorkerHarness(transport, worker_id="w-1", tenant="fixture", audit_sink=sink)
    harness.register_worker(
        FunctionWorker(_TOPIC, lambda variables: {"desfecho": "APROVAR", "notice_sent": True})
    )
    harness.register_worker(FunctionWorker(_OTHER_TOPIC, lambda variables: {"pagamento_liberado": True}))
    return harness


def _observable(transport: FakeWorkerTransport, sink: FakeAuditSink) -> dict[str, Any]:
    """Every outcome the engine and the audit chain can see — timestamps/hashes excluded."""
    return {
        "completed": list(transport.completed),
        "failures": list(transport.failures),
        "bpmn_errors": list(transport.bpmn_errors),
        "unlocked": list(transport.unlocked),
        "audit": [(r.action, r.decision, r.details, r.dmn_versions) for r, _ in sink.emitted],
    }


async def _run(*, neutralized: bool, topics: list[str]) -> dict[str, Any]:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = _harness(transport, sink)
    if neutralized:
        # The gateway is removed from the path entirely — not merely told to allow. If shadow is
        # truly inert, this substitution is unobservable.
        harness._evaluate_action_gate = lambda task: None  # type: ignore[method-assign]
    for index, topic in enumerate(topics):
        await harness._handle(_task(topic, task_id=f"task-{index}"))
    return _observable(transport, sink)


async def test_shadow_wiring_is_provably_non_behavioural() -> None:
    """(a) THE INERTNESS PROOF. Identical engine outcomes and identical audit rows, gateway or not.

    Uses the REAL shipped manifest (no fixture, no env override): whatever
    `spec/policies/autonomy/action-approvals.yaml` says today, the choked path must not notice.
    Both a MAPPED topic (the gateway has an opinion, and it is a DENY) and an UNMAPPED one (the
    gateway has none) are exercised, because those are the two shapes production traffic takes.
    """
    topics = [_TOPIC, _OTHER_TOPIC]
    with_gateway = await _run(neutralized=False, topics=topics)
    without_gateway = await _run(neutralized=True, topics=topics)
    assert with_gateway == without_gateway


async def test_the_shipped_manifest_denies_on_the_live_path_but_does_not_block() -> None:
    """The other half of (a): the gateway really IS evaluating, and really IS denying.

    Without this, `test_shadow_wiring_is_provably_non_behavioural` would also pass if the wiring
    had silently become a no-op — an inert gateway and an ABSENT one look identical from the
    transport. Here the decision is inspected directly: DENY, and not enforced.
    """
    decision = evaluate_worker_task(topic=_TOPIC, tenant="fixture")
    assert decision.allow is False
    assert decision.enforced is False
    assert decision.mode == MODE_SHADOW

    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    await _harness(transport, sink)._handle(_task())
    assert len(transport.completed) == 1, "a shadow DENY must not stop the task completing"
    assert transport.failures == []


async def test_enforcing_denial_refuses_the_task_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The switch, exercised end-to-end: an enforced DENY must refuse, audit, and incident.

    Proves the branch is genuinely wired (not dead code that a mode flip would fail to reach) and
    that it takes the guard shape the repo already uses: `retries=0` (never retried, ADR-0008), a
    REFUSED audit row before the report (ADR-0030 §4), and the handler NEVER invoked.
    """
    from maezo.gateway.action_execution import Decision

    ran: list[str] = []
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w-1", tenant="fixture", audit_sink=sink)

    def _handler(variables: dict[str, Any]) -> dict[str, Any]:
        ran.append("handler")
        return {"desfecho": "APROVAR"}

    harness.register_worker(FunctionWorker(_TOPIC, _handler))
    monkeypatch.setattr(
        harness,
        "_evaluate_action_gate",
        lambda task: Decision(_CLASS, allow=False, reason=REASON_APPROVAL_PENDING, mode=MODE_ENFORCING),
    )

    await harness._handle(_task())

    assert ran == [], "the handler must never run when the gateway denies — the effect is pre-empted"
    assert transport.completed == []
    assert len(transport.failures) == 1
    task_id, _message, retries, _timeout = transport.failures[0]
    assert (task_id, retries) == ("task-1", 0)
    assert [r.decision for r, _ in sink.emitted] == ["REFUSED"]
    assert sink.emitted[0][0].details["guard_code"] == "ERR_ACTION_GATEWAY_NOT_HUMAN"


async def test_enforcing_allow_lets_the_task_through_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the switch: an APPROVED class under enforcement behaves exactly as today."""
    from maezo.gateway.action_execution import Decision

    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = _harness(transport, sink)
    monkeypatch.setattr(
        harness,
        "_evaluate_action_gate",
        lambda task: Decision(_CLASS, allow=True, reason=REASON_APPROVED, mode=MODE_ENFORCING),
    )
    await harness._handle(_task())
    assert len(transport.completed) == 1
    assert transport.failures == []
    assert [r.decision for r, _ in sink.emitted] == ["COMPLETE"]


async def test_a_gateway_that_explodes_cannot_crash_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt-and-suspenders: a raising gateway degrades to "no opinion", never to a stalled task."""

    def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError("gateway exploded")

    monkeypatch.setattr("maezo.tools.workers.harness.evaluate_worker_task", _boom)
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    await _harness(transport, sink)._handle(_task())
    assert len(transport.completed) == 1
    assert transport.failures == []


# ---------------------------------------------------------------------------------------------
# Telemetry: the EVENT NAME tracks the mode, not only the `mode=` field
# ---------------------------------------------------------------------------------------------


def test_the_telemetry_event_name_tracks_the_mode(tmp_path: Path) -> None:
    """A shadow observation and a call that actually blocked are different EVENTS, not one event
    with a field.

    Log routing, alerting and dashboards key on the event name long before anything parses
    `mode=`; emitting `..._shadow` for a dispatch that was really refused would make the first
    enforced denial in production invisible to every alert built on the shadow rollout. The `mode`
    field is still emitted, so nothing that filtered on it stops working.
    """
    shipped_path = _write(tmp_path, _manifest(status="RATIFICADO", modo=MODE_SHADOW))
    with structlog.testing.capture_logs() as shadow_logs:
        shadow = evaluate_worker_task(topic=_TOPIC, tenant="fixture", path=shipped_path)
    assert shadow.enforced is False
    shadow_lines = [e for e in shadow_logs if e.get("event") in (EVENT_SHADOW, EVENT_ENFORCED)]
    assert [e["event"] for e in shadow_lines] == [EVENT_SHADOW]
    assert shadow_lines[0]["mode"] == MODE_SHADOW, "the `mode` field must survive the rename"

    action_execution._load_cached.cache_clear()
    enforcing_path = _write(
        tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING), name="enforcing.yaml"
    )
    with structlog.testing.capture_logs() as enforced_logs:
        enforced = evaluate_worker_task(topic=_TOPIC, tenant="fixture", path=enforcing_path)
    assert enforced.enforced is True
    enforced_lines = [e for e in enforced_logs if e.get("event") in (EVENT_SHADOW, EVENT_ENFORCED)]
    assert [e["event"] for e in enforced_lines] == [EVENT_ENFORCED]
    assert enforced_lines[0]["mode"] == MODE_ENFORCING
    assert enforced_lines[0]["decision"] == "WOULD_DENY"
    assert enforced_lines[0]["reason"] == REASON_APPROVAL_PENDING


def test_a_hyphenated_tenant_collapses_to_invalido_in_telemetry(tmp_path: Path) -> None:
    """Documented, not silently lost: the tenant dimension is a bounded token, and `-` is not in it.

    `_TOKEN_RE` is `^[A-Za-z][A-Za-z0-9_]{0,39}$`, so a deployment whose tenant id is `omni-saude`
    emits `tenant=INVALIDO` on every gateway line — the telemetry is still non-PHI and still
    correct about the DECISION, but it cannot be sliced per tenant. Widening the regex is a PHI
    decision, not a formatting one, so the behaviour is pinned and disclosed in the approval packet
    instead of being changed here.
    """
    path = _write(tmp_path, _manifest(status="RATIFICADO", modo=MODE_SHADOW))
    with structlog.testing.capture_logs() as logs:
        evaluate_worker_task(topic=_TOPIC, tenant="omni-saude", path=path)
    line = next(e for e in logs if e.get("event") == EVENT_SHADOW)
    assert line["tenant"] == "INVALIDO"


@pytest.mark.parametrize("value", ["APROVADO\n", "amh\n", "amh\n\n", "amh\nreason=APROVADO"])
def test_a_newline_is_not_a_bounded_token(value: str) -> None:
    """`\\Z`, not `$` (ONDA 1 GK nit). Python's `$` ALSO matches just before a trailing newline.

    So `_is_bounded_token("APROVADO\\n")` was True, and the guard that exists to keep free text out
    of a structured log line would have passed a value carrying a line break — the classic
    log-injection primitive, on the exact field (`tenant`, and every `REASON_*` token) this
    module's NO-PHI claim rests on. `\\Z` anchors at the true end of the string.
    """
    assert action_execution._is_bounded_token(value) is False


def test_the_newline_tightening_is_strictly_narrowing() -> None:
    """The control for the test above: `\\Z` refuses ONLY the trailing-newline forms.

    Without this, "tighten the regex" and "break the regex" look identical from the red side.
    """
    for value in ("APROVADO", "amh", "a", "A" * 40, "GATEWAY_ERRO_INTERNO"):
        assert action_execution._is_bounded_token(value) is True, value
    assert action_execution._is_bounded_token("A" * 41) is False, "the length bound is unchanged"


# ---------------------------------------------------------------------------------------------
# DEVIATION-8: an INTERNAL ERROR must not fail open once a human has flipped to `enforcing`
# ---------------------------------------------------------------------------------------------


def _raising_evaluate(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("injected gateway failure")


@pytest.mark.parametrize(
    ("modo", "expected_enforced"),
    [(MODE_ENFORCING, True), (MODE_SHADOW, False)],
)
def test_an_internal_error_still_blocks_under_a_live_global_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modo: str, expected_enforced: bool
) -> None:
    """The fail-closed direction of the double-guarded fallback, in BOTH global modes.

    `_fail_closed_decision` declares `enforcement=enforcing` precisely so `Decision.enforced`
    reduces to the pre-Onda-1 `mode == enforcing` on a path where no class could be resolved. The
    call site used to OVERRIDE that declared default with `cached.enforcement_for(None)` — the
    ramp's `enforcement_padrao_nao_mapeado`, a decision about UNCLASSIFIED TRAFFIC — which turned
    a gateway failure under a live `modo: enforcing` back into a log line. §7.3's per-class
    dimension exists to let a class be evaluated before it can block; it is NOT a licence to
    downgrade the one path that only runs because something already went wrong.

    The fixture pins `enforcement_padrao_nao_mapeado: shadow` EXPLICITLY. That is what makes this
    test discriminating: with the root key absent it resolves to `enforcing` on its own (XRD-09's
    literal) and the pre-repair code would have passed for the wrong reason.
    """
    path = _write(
        tmp_path,
        _manifest(status="RATIFICADO", modo=modo, default_enforcement=ENFORCEMENT_SHADOW),
        name=f"internal-error-{modo}.yaml",
    )
    monkeypatch.setattr(ActionExecutionGateway, "evaluate", _raising_evaluate)

    with structlog.testing.capture_logs() as logs:
        decision = evaluate_worker_task(topic=_TOPIC, tenant="fixture", path=path)

    assert decision.allow is False
    assert decision.reason == REASON_INTERNAL_ERROR
    assert decision.action_class is None, "an internal error resolved no class, and must not claim one"
    assert decision.mode == modo, "the GLOBAL mode is still read from the cached manifest"
    assert decision.enforcement == ENFORCEMENT_ENFORCING, (
        "the call site overrode `_fail_closed_decision`'s declared default again"
    )
    assert decision.enforced is expected_enforced

    line = next(e for e in logs if e["event"] == "action_execution_gateway_internal_error")
    assert (line["mode"], line["enforcement"], line["enforced"]) == (
        modo,
        ENFORCEMENT_ENFORCING,
        expected_enforced,
    ), "the error line must report the value actually returned, not a second computation of it"


def test_the_internal_error_path_refuses_to_claim_enforcement_when_nothing_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the double guard: if even the cached read fails, the mode is UNRESOLVED.

    Fail-closed in BOTH directions — the DENY stands, and a process with no readable record may
    not CLAIM that it is enforcing.
    """
    monkeypatch.setattr(action_execution, "action_approvals", _raising_evaluate)
    decision = evaluate_worker_task(topic=_TOPIC, tenant="fixture", path=tmp_path / "absent.yaml")
    assert (decision.allow, decision.reason, decision.mode) == (
        False,
        REASON_INTERNAL_ERROR,
        MODE_UNRESOLVED,
    )
    assert decision.enforced is False


# ---------------------------------------------------------------------------------------------
# DATA-REACHABILITY: enforcement reached from the MANIFEST, with no patched `Decision`
# ---------------------------------------------------------------------------------------------


async def test_enforcement_is_reachable_from_data_alone_and_refuses_real_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """END-TO-END, no monkeypatched verdict: a ratified+enforcing MANIFEST refuses real dispatches.

    The two `_enforcing_*` tests above hand the harness a hand-built `Decision`, which proves the
    branch is wired but NOT that a human editing the YAML can ever reach it. Here the only thing
    supplied is the manifest FILE (through the explicit `path` seam — not the env override, which
    by design cannot enforce): the real loader parses it, the real `evaluate` decides, and two real
    tasks on two different topics are refused with the guard shape ADR-0008/ADR-0030 §4 prescribe.

    This is the claim the approvers are actually being asked to trust — "ratifying is a data change
    and nothing else" — so it is proved from the data end, not from a stubbed verdict.
    """
    path = _write(tmp_path, _manifest(status="RATIFICADO", modo=MODE_ENFORCING))
    monkeypatch.setattr(
        harness_module, "evaluate_worker_task", functools.partial(evaluate_worker_task, path=path)
    )

    ran: list[str] = []

    def _recording_handler(topic: str) -> Any:
        def _run(variables: dict[str, Any]) -> dict[str, Any]:
            ran.append(topic)
            return {"ok": True}

        return _run

    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w-1", tenant="fixture", audit_sink=sink)
    for topic in (_TOPIC, _OTHER_TOPIC):
        harness.register_worker(FunctionWorker(topic, _recording_handler(topic)))

    for index, topic in enumerate((_TOPIC, _OTHER_TOPIC)):
        await harness._handle(_task(topic, task_id=f"task-{index}"))

    assert ran == [], "the handler ran — the effect was NOT pre-empted"
    assert transport.completed == []
    assert [(task_id, retries) for task_id, _msg, retries, _timeout in transport.failures] == [
        ("task-0", 0),
        ("task-1", 0),
    ], "an enforced denial is a guard: never retried (ADR-0008)"
    assert [r.decision for r, _ in sink.emitted] == ["REFUSED", "REFUSED"]
    assert {r.details["guard_code"] for r, _ in sink.emitted} == {"ERR_ACTION_GATEWAY_NOT_HUMAN"}


async def test_the_same_data_in_shadow_leaves_both_tasks_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the test above: byte-identical manifest, `modo: shadow`, nothing refused.

    Isolates the ONE field that carries the switch. If this failed, the previous test would prove
    only "the fixture denies", not "flipping `modo` is what turns denial into refusal".
    """
    manifest = _manifest(status="RATIFICADO", modo=MODE_SHADOW)
    path = _write(tmp_path, manifest)
    monkeypatch.setattr(
        harness_module, "evaluate_worker_task", functools.partial(evaluate_worker_task, path=path)
    )
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = _harness(transport, sink)
    for index, topic in enumerate((_TOPIC, _OTHER_TOPIC)):
        await harness._handle(_task(topic, task_id=f"task-{index}"))
    assert len(transport.completed) == 2
    assert transport.failures == []
    assert [r.decision for r, _ in sink.emitted] == ["COMPLETE", "COMPLETE"]
