"""Behavioural proofs for the `ActionExecutionGateway` (MZO-040, ADR-0037 XRD-09).

Four claims, in the order they matter:

  (a) SHADOW IS INERT — the choked path produces byte-identical outcomes with the gateway live
      and with it neutralized. This is the claim that makes wiring an unapproved gateway into the
      per-call effect path defensible at all; everything else is secondary to it.
  (b) ENFORCING + NOTHING APPROVED denies every declared class.
  (c) ENFORCING + ONE CLASS APPROVED allows that class and ONLY that class.
  (d) A DRAFT FILE CAN NEVER ALLOW — including the explicit forgery shape where every `aprovado`
      reads `true` and `modo` reads `enforcing` while `status` is still DRAFT.

Sibling of `tests/unit/sec/test_action_execution_fence.py`, which asserts the same properties
against the REAL shipped manifest rather than fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import action_execution
from maezo.gateway.action_execution import (
    APPROVER_DOMAINS,
    MODE_ENFORCING,
    MODE_SHADOW,
    MODE_UNRESOLVED,
    REASON_ACTION_UNDECLARED,
    REASON_ACTION_UNMAPPED,
    REASON_APPROVAL_INCOMPLETE,
    REASON_APPROVAL_PENDING,
    REASON_APPROVED,
    REASON_DOMAIN_UNKNOWN,
    REASON_DOMAINS_EMPTY,
    REASON_MANIFEST_DRAFT,
    REASON_MANIFEST_UNAVAILABLE,
    ActionExecutionGateway,
    evaluate_worker_task,
    load_action_approvals,
)
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
) -> dict[str, Any]:
    def _acao(name: str) -> dict[str, Any]:
        is_approved = name in approved_classes
        return {
            "descricao": f"fixture class {name}",
            "dominios_exigidos": sorted(APPROVER_DOMAINS),
            "aprovacoes": {d: _approval_block(approved=is_approved) for d in sorted(APPROVER_DOMAINS)},
        }

    return {
        "version": 1,
        "status": status,
        "modo": modo,
        "acoes": {_CLASS: _acao(_CLASS), _OTHER_CLASS: _acao(_OTHER_CLASS)},
        "mapeamento_topicos": {_TOPIC: _CLASS, _OTHER_TOPIC: _OTHER_CLASS},
    }


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
        assert decision.enforced is True, "a denial under `modo: enforcing` must actually block"
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
