"""T3.3/T4 A3 — NotificationBridge certification (unit-level pins).

HISTORY: this module began (T3.3) as a NEGATIVE certification — it PINNED the bridge's dormant
state, so that the 5 repointed rules (RECURSO/FRAUDE/CRED/CANCEL/INADIMPLENCIA) could never be
silently mistaken for armed while the source BPMNs' completed-event payloads still lacked the
business-key anchors the bridge predicates fail-closed require.

T4 ARMING (this revision) is that CONSCIOUS re-certification the dormancy pins demanded ("rule
armed — update A3 certification"): the source BPMNs were enriched so the completed-event payloads
now carry the anchors, and the CONTAS→FRAUDE Phase-3 leg was landed (a human-gated
`decisao_contas == ENCAMINHAR_FRAUDE` branch + an in-flow `operadora.contas.start_fraude` worker).
Every one of the 5 rules is now ARMED against the REAL production payload shape. This module now
certifies that armed state:

  - each rule, fed the REAL (enriched) completed-event payload, STARTS its downstream process
    exactly once, with the SAME business key its in-flow fenced worker derives (CONVERGENCE — the
    L0 no-divergent-double-start requirement), and emits exactly one ADR-0007 audit row;
  - a re-delivery of the same event (or a bridge delivery AFTER the in-flow worker already
    started the instance) returns the EXISTING instance — ZERO double-start (idempotency);
  - the fail-closed residual still holds: a payload MISSING the anchor still REFUSES to start
    (the predicates never widened into a tautology);
  - INADIMPLENCIA stays dormant for a `beneficiario` contratual case (it fires only for
    `entidade_tipo == 'contrato'`), matching the in-flow `start_contratual` worker;
  - the structural fence (PIN 3) still admits NO raw/unfenced engine-start path in the bridge or
    its now-FOUR in-flow workers (start_recurso, start_fraude, start_credenciamento,
    start_contratual);
  - the source-level twins (PIN 5) now assert the CONTAS→FRAUDE branch is PRESENT (Phase-3
    landed) instead of absent.

REAL PAYLOAD SHAPES CITED (never invented — read directly from spec/ BPMNs, task requirement #1):

  spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn
    ST_PublishSemGlosa            event_desfecho=sem_glosa
                                    event_payload_vars=tenant_id,numero_lote_tiss,prestador_id
    ST_PublishEncaminhadaRecurso  event_desfecho=encaminhada_recurso
                                    event_payload_vars=tenant_id,numero_lote_tiss,glosa_id,
                                    numero_guia_tiss           <- T4 anchor added (arms RECURSO)
    ST_PublishGlosaAceitaHumano   event_desfecho=glosa_aceita_humano
                                    event_payload_vars=tenant_id,numero_lote_tiss,
                                    codigo_glosa_aceito,analista_id
    ST_PublishReenviada           event_desfecho=reenviada
                                    event_payload_vars=tenant_id,numero_lote_tiss,prestador_id
    ST_StartFraude                (topic operadora.contas.start_fraude — T4 Phase-3 in-flow worker)
    ST_PublishEncaminhadaFraude   event_desfecho=encaminhada_fraude   <- T4 branch landed
                                    event_payload_vars=tenant_id,numero_lote_tiss,prestador_id
                                    (arms CONTAS→FRAUDE)

  spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn
    ST_PublishFraudeConfirmada    event_desfecho=fraude_confirmada_humano
                                    event_payload_vars=tenant_id,numero_caso,investigator_id,tier
    ST_StartCredenciamento        (topic operadora.fraude.start_credenciamento)
    ST_PublishEncaminhadoCredenciamento  event_desfecho=encaminhado_credenciamento
                                    event_payload_vars=tenant_id,numero_caso,investigator_id,tier,
                                    prestador_id               <- T4 anchor added (arms CRED)
    ST_StartContratual            (topic operadora.fraude.start_contratual)
    ST_PublishEncaminhadoContratual event_desfecho=encaminhado_contratual
                                    event_payload_vars=tenant_id,numero_caso,investigator_id,tier,
                                    numero_contrato,entidade_tipo  <- T4 anchors added (arms
                                    CANCEL always; INADIMPLENCIA when entidade_tipo=contrato)
    ST_PublishEncaminhadoJuridico event_desfecho=encaminhado_juridico
    ST_PublishArquivado           event_desfecho=arquivado_sem_indicio
    ST_PublishMonitorar           event_desfecho=monitorar
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.notification_bridge import (
    CONTAS_COMPLETED_EVENT,
    FRAUDE_COMPLETED_EVENT,
    NotificationBridge,
    build_cibseven_process_starter,
)
from maezo.platform.notification_bridge import (
    _fraude_numero_caso_for_contas_handoff as _bridge_numero_caso,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.contas import _fraude_business_key as _contas_fraude_bk
from maezo.tools.workers.contas import _fraude_numero_caso_for_handoff as _worker_numero_caso
from maezo.tools.workers.contas import _recurso_business_key as _contas_recurso_bk
from maezo.tools.workers.fraude import _cancel_business_key as _fraude_cancel_bk
from maezo.tools.workers.fraude import _cred_business_key as _fraude_cred_bk
from maezo.tools.workers.fraude import _inadimplencia_business_key as _fraude_inad_bk
from tests.support.audit_fakes import FakeStartAuditSink

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTAS_BPMN = _REPO_ROOT / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_FRAUDE_BPMN = _REPO_ROOT / "spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn"

# NOTE: this module deliberately mixes async (bridge behavior) and sync (AST/spec-text) tests.
# `pyproject.toml`'s `asyncio_mode = "auto"` treats every `async def test_*` here as an asyncio
# test automatically — no per-test/module marker needed.


class _RecordingCibSevenTransport(FakeCibSevenTransport):
    """Records every start_process_instance call — the "was an engine start attempted at all"
    surface every armed/dormant pin below asserts on."""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        self.start_calls.append((process_key, business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


def _fenced_bridge() -> tuple[NotificationBridge, _RecordingCibSevenTransport, FakeStartAuditSink]:
    """A bridge wired through the REAL fenced starter (`build_cibseven_process_starter`) — the
    ONLY sanctioned production path — with a recording transport + audit sink so every pin below
    observes the two effects that matter: an engine start, and a durable audit row."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)
    bridge = NotificationBridge(cibseven_starter=starter)
    return bridge, transport, audit_sink


def _starts_for(
    transport: _RecordingCibSevenTransport, process_key: str
) -> list[tuple[str, str, dict[str, Any]]]:
    return [c for c in transport.start_calls if c[0] == process_key]


# =================================================================================================
# PIN 1 — ARMED: each rule, fed the REAL enriched completed-event payload, starts its downstream
# process exactly once with the CONVERGED business key (== the in-flow worker's own derivation)
# and a durable audit row; a re-delivery (and a bridge delivery AFTER the in-flow worker already
# started) never double-starts.
# =================================================================================================


async def test_pin_contas_recurso_armed_starts_with_converged_bk() -> None:
    """CONTAS ST_PublishEncaminhadaRecurso now emits numero_guia_tiss (T4 enrichment), so the
    RECURSO rule fires against the REAL payload and starts SP-OP-RECURSO-001 with the SAME
    business key `contas.start_recurso` derives — convergence, never a divergent double-start."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-REAL-001",
        "glosa_id": "GLOSA-REAL-001",
        "numero_guia_tiss": "GUIA-REAL-001",
        "desfecho": "encaminhada_recurso",
    }
    expected_bk = _contas_recurso_bk("amh", "GUIA-REAL-001", "GLOSA-REAL-001")

    results = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=real_payload)
    matched = [r for r in results if r.target_process == "SP-OP-RECURSO-001"]
    assert len(matched) == 1 and matched[0].handoff_triggered is True
    recurso_starts = _starts_for(transport, "SP-OP-RECURSO-001")
    assert len(recurso_starts) == 1
    assert recurso_starts[0][1] == expected_bk  # bridge BK == in-flow worker BK (convergence)
    assert len(audit_sink.calls) == 1

    # Re-delivery of the SAME event: no second start (idempotent).
    await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=real_payload)
    assert len(_starts_for(transport, "SP-OP-RECURSO-001")) == 1


async def test_pin_contas_recurso_convergence_inflow_first_then_bridge_no_double_start() -> None:
    """In-flow `start_recurso` starts RECURSO-001 first (seeded here under the converged BK); the
    bridge's later completed-event delivery finds the ACTIVE instance and returns it — ZERO
    double-start (the L0 convergence guarantee, in the real ordering)."""
    bridge, transport, audit_sink = _fenced_bridge()
    bk = _contas_recurso_bk("amh", "GUIA-CONV", "GLOSA-CONV")
    transport.seed_instance(
        ProcessInstance(
            instance_id="inflow-recurso",
            process_key="SP-OP-RECURSO-001",
            business_key=bk,
            state="ACTIVE",
            already_existed=True,
        )
    )
    results = await bridge.on_event(
        event_type=CONTAS_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-CONV",
            "glosa_id": "GLOSA-CONV",
            "numero_guia_tiss": "GUIA-CONV",
            "desfecho": "encaminhada_recurso",
        },
    )
    matched = [r for r in results if r.target_process == "SP-OP-RECURSO-001"]
    assert len(matched) == 1 and matched[0].process_instance_id == "inflow-recurso"
    assert _starts_for(transport, "SP-OP-RECURSO-001") == []  # never re-started


async def test_pin_contas_fraude_armed_starts_with_converged_bk() -> None:
    """T4 Phase-3 leg: CONTAS now emits desfecho=encaminhada_fraude (+ prestador_id anchor), so
    the CONTAS→FRAUDE rule fires and starts SP-OP-FRAUDE-001 with the SAME business key the new
    in-flow `contas.start_fraude` worker derives (FRAUDE-{tenant}-{prestador_id} when no
    numero_caso) — convergence."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-REAL-002",
        "prestador_id": "prov:real-0001",
        "desfecho": "encaminhada_fraude",
    }
    expected_bk = _contas_fraude_bk("amh", "prov:real-0001")

    results = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=real_payload)
    matched = [r for r in results if r.target_process == "SP-OP-FRAUDE-001"]
    assert len(matched) == 1 and matched[0].handoff_triggered is True
    fraude_starts = _starts_for(transport, "SP-OP-FRAUDE-001")
    assert len(fraude_starts) == 1
    assert fraude_starts[0][1] == expected_bk  # converges with contas.start_fraude
    assert len(audit_sink.calls) == 1

    await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=real_payload)
    assert len(_starts_for(transport, "SP-OP-FRAUDE-001")) == 1


async def test_pin_fraude_cred_armed_starts_with_converged_bk() -> None:
    """FRAUDE ST_PublishEncaminhadoCredenciamento now emits prestador_id (T4 enrichment), so the
    CRED rule fires and starts SP-OP-CRED-001 with the SAME business key
    `fraude.start_credenciamento` derives."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_caso": "CASO-REAL-001",
        "investigator_id": "INV-001",
        "tier": "alto",
        "prestador_id": "PREST-REAL-001",
        "desfecho": "encaminhado_credenciamento",
    }
    expected_bk = _fraude_cred_bk("amh", "PREST-REAL-001")

    results = await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=real_payload)
    matched = [r for r in results if r.target_process == "SP-OP-CRED-001"]
    assert len(matched) == 1 and matched[0].handoff_triggered is True
    cred_starts = _starts_for(transport, "SP-OP-CRED-001")
    assert len(cred_starts) == 1
    assert cred_starts[0][1] == expected_bk
    assert len(audit_sink.calls) == 1

    await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=real_payload)
    assert len(_starts_for(transport, "SP-OP-CRED-001")) == 1


async def test_pin_fraude_contratual_armed_starts_cancel_and_inadimplencia_for_contrato() -> None:
    """FRAUDE ST_PublishEncaminhadoContratual now emits numero_contrato + entidade_tipo (T4). For
    a `contrato` case BOTH the CANCEL rule and the INADIMPLENCIA rule fire — each with its own
    converged business key — mirroring the in-flow `start_contratual` worker (which starts BOTH
    for a contrato). Distinct BK prefixes (CANCEL- vs INAD-) => two DISTINCT instances, never one
    double-started."""
    bridge, transport, audit_sink = _fenced_bridge()
    real_payload = {
        "tenant_id": "amh",
        "numero_caso": "CASO-REAL-002",
        "investigator_id": "INV-001",
        "tier": "alto",
        "numero_contrato": "CTR-REAL-001",
        "entidade_tipo": "contrato",
        "desfecho": "encaminhado_contratual",
    }
    results = await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=real_payload)
    targets = {r.target_process for r in results if r.handoff_triggered}
    assert targets == {"SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}

    cancel_starts = _starts_for(transport, "SP-OP-CANCEL-001")
    inad_starts = _starts_for(transport, "SP-OP-INADIMPLENCIA-001")
    assert len(cancel_starts) == 1 and cancel_starts[0][1] == _fraude_cancel_bk("amh", "CTR-REAL-001")
    assert len(inad_starts) == 1 and inad_starts[0][1] == _fraude_inad_bk("amh", "CTR-REAL-001")
    assert cancel_starts[0][1] != inad_starts[0][1]  # coordinated via distinct prefixes, not shared BK
    assert len(audit_sink.calls) == 2

    await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=real_payload)
    assert len(_starts_for(transport, "SP-OP-CANCEL-001")) == 1
    assert len(_starts_for(transport, "SP-OP-INADIMPLENCIA-001")) == 1


async def test_pin_fraude_contratual_beneficiario_arms_cancel_only_inadimplencia_stays_dormant() -> None:
    """For a `beneficiario` contratual case (numero_contrato present, entidade_tipo=beneficiario),
    ONLY the CANCEL rule fires; INADIMPLENCIA stays DORMANT (it requires entidade_tipo=='contrato')
    — the residual, entity-type-scoped dormancy, matching the in-flow `start_contratual` worker
    which starts INADIMPLENCIA only for a contrato."""
    bridge, transport, audit_sink = _fenced_bridge()
    results = await bridge.on_event(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "numero_caso": "CASO-REAL-003",
            "investigator_id": "INV-001",
            "tier": "alto",
            "numero_contrato": "CTR-REAL-002",
            "entidade_tipo": "beneficiario",
            "desfecho": "encaminhado_contratual",
        },
    )
    targets = {r.target_process for r in results if r.handoff_triggered}
    assert targets == {"SP-OP-CANCEL-001"}
    assert _starts_for(transport, "SP-OP-INADIMPLENCIA-001") == []


# =================================================================================================
# PIN 2 — Fail-closed residual: the predicates never widened into tautologies. A payload MISSING
# the anchor still REFUSES to start (proving the armed pins above are not passing "vacuously"
# because a predicate broke open).
# =================================================================================================


@pytest.mark.parametrize(
    "event_type,payload,forbidden_target",
    [
        (
            CONTAS_COMPLETED_EVENT,
            {
                "tenant_id": "amh",
                "numero_lote_tiss": "L1",
                "glosa_id": "G1",
                "desfecho": "encaminhada_recurso",
            },
            "SP-OP-RECURSO-001",
        ),
        (
            CONTAS_COMPLETED_EVENT,
            {"tenant_id": "amh", "numero_lote_tiss": "L1", "desfecho": "encaminhada_fraude"},
            "SP-OP-FRAUDE-001",
        ),
        (
            FRAUDE_COMPLETED_EVENT,
            {
                "tenant_id": "amh",
                "numero_caso": "C1",
                "tier": "alto",
                "desfecho": "encaminhado_credenciamento",
            },
            "SP-OP-CRED-001",
        ),
        (
            FRAUDE_COMPLETED_EVENT,
            {"tenant_id": "amh", "numero_caso": "C1", "tier": "alto", "desfecho": "encaminhado_contratual"},
            "SP-OP-CANCEL-001",
        ),
        # t2-notify-integrity item 3 — TENANT anchor refusals: every per-rule anchor present but
        # tenant_id absent (or None). The armed pins above all carry tenant_id="amh"; these prove
        # a tenant-less real-shaped payload never mints the degenerate `{PREFIX}--…` key the
        # in-flow workers' own tenant guards (ERR_*_SEM_ALVO / shared non_blank) refuse in-flow —
        # fail-closed SYMMETRY, bridge-side.
        (
            CONTAS_COMPLETED_EVENT,
            {
                "numero_lote_tiss": "L1",
                "glosa_id": "G1",
                "numero_guia_tiss": "GU1",
                "desfecho": "encaminhada_recurso",
            },
            "SP-OP-RECURSO-001",
        ),
        (
            CONTAS_COMPLETED_EVENT,
            {"numero_lote_tiss": "L1", "prestador_id": "P1", "desfecho": "encaminhada_fraude"},
            "SP-OP-FRAUDE-001",
        ),
        (
            FRAUDE_COMPLETED_EVENT,
            {
                "numero_caso": "C1",
                "tier": "alto",
                "prestador_id": "P1",
                "desfecho": "encaminhado_credenciamento",
            },
            "SP-OP-CRED-001",
        ),
        (
            FRAUDE_COMPLETED_EVENT,
            {
                "numero_caso": "C1",
                "tier": "alto",
                "numero_contrato": "CTR-1",
                "entidade_tipo": "beneficiario",
                "desfecho": "encaminhado_contratual",
            },
            "SP-OP-CANCEL-001",
        ),
        (
            FRAUDE_COMPLETED_EVENT,
            {
                "numero_caso": "C1",
                "tier": "alto",
                "numero_contrato": "CTR-1",
                "entidade_tipo": "contrato",
                "desfecho": "encaminhado_contratual",
            },
            "SP-OP-INADIMPLENCIA-001",
        ),
        (
            CONTAS_COMPLETED_EVENT,
            {
                "tenant_id": None,
                "numero_lote_tiss": "L1",
                "prestador_id": "P1",
                "desfecho": "encaminhada_fraude",
            },
            "SP-OP-FRAUDE-001",
        ),
    ],
    ids=[
        "recurso-no-guia",
        "fraude-no-prestador",
        "cred-no-prestador",
        "cancel-no-contrato",
        "recurso-no-tenant",
        "fraude-no-tenant",
        "cred-no-tenant",
        "cancel-no-tenant",
        "inadimplencia-no-tenant",
        "fraude-tenant-none",
    ],
)
async def test_pin_anchor_absent_still_refuses(
    event_type: str, payload: dict[str, Any], forbidden_target: str
) -> None:
    """A completed-event payload missing a fail-closed anchor (business-key anchor OR tenant_id)
    never starts its downstream process — the armed predicates are still anchor-gated, not
    tautologies."""
    bridge, transport, audit_sink = _fenced_bridge()
    results = await bridge.on_event(event_type=event_type, payload=payload)
    assert all(r.target_process != forbidden_target for r in results)
    assert _starts_for(transport, forbidden_target) == []
    assert audit_sink.calls == []


async def test_pin_contas_non_fraude_desfechos_never_start_fraude() -> None:
    """The 4 non-fraude CONTAS desfechos (sem_glosa/encaminhada_recurso/glosa_aceita_humano/
    reenviada) must never start FRAUDE — only desfecho==encaminhada_fraude (+ prestador_id) does."""
    bridge, transport, _ = _fenced_bridge()
    for payload in (
        {"desfecho": "sem_glosa", "tenant_id": "amh", "numero_lote_tiss": "L1", "prestador_id": "P1"},
        {
            "desfecho": "encaminhada_recurso",
            "tenant_id": "amh",
            "numero_lote_tiss": "L1",
            "glosa_id": "G1",
            "numero_guia_tiss": "GU1",
        },
        {
            "desfecho": "glosa_aceita_humano",
            "tenant_id": "amh",
            "numero_lote_tiss": "L1",
            "codigo_glosa_aceito": "C1",
            "analista_id": "A1",
        },
        {"desfecho": "reenviada", "tenant_id": "amh", "numero_lote_tiss": "L1", "prestador_id": "P1"},
    ):
        await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=payload)
    assert _starts_for(transport, "SP-OP-FRAUDE-001") == []


# =================================================================================================
# PIN 3 — Structural fence: no raw/unfenced engine-start path anywhere in the bridge module or the
# now-FOUR in-flow handoff workers (start_recurso, start_fraude, start_credenciamento,
# start_contratual all live in contas.py / fraude.py). The ONLY sanctioned start-effect call is
# `start_process_idempotent`.
# =================================================================================================

_GUARDED_MODULES = {
    "notification_bridge": _REPO_ROOT / "src/maezo/platform/notification_bridge.py",
    "contas": _REPO_ROOT / "src/maezo/tools/workers/contas.py",
    "fraude": _REPO_ROOT / "src/maezo/tools/workers/fraude.py",
}

_FENCED_CHOKEPOINT_CALL = "start_process_idempotent"
_FORBIDDEN_CALL_NAMES = {"start_process_instance"}
_FORBIDDEN_PATH_SUBSTRING = "process-definition/key"


def _call_func_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _module_call_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_func_name(node)
            if name:
                names.add(name)
    return names


def _module_string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return literals


@pytest.mark.parametrize("module_label", sorted(_GUARDED_MODULES))
def test_pin_no_raw_unfenced_start_call_in_guarded_module(module_label: str) -> None:
    """Static AST scan: none of `_FORBIDDEN_CALL_NAMES` appear as a called function/attribute
    name anywhere in the module — the ONLY way any of these files can start a CIB Seven process is
    through `start_process_idempotent` (ADR-0007/T-C2 chokepoint). Now covers the T4
    `start_fraude` worker too (it lives in the already-guarded contas.py)."""
    path = _GUARDED_MODULES[module_label]
    call_names = _module_call_names(path)
    forbidden_found = call_names & _FORBIDDEN_CALL_NAMES
    assert not forbidden_found, (
        f"{module_label} ({path}) calls {forbidden_found} directly — a RAW, unfenced engine-start "
        "path bypassing start_process_idempotent has been reintroduced (ADR-0007/T-C2 violation); "
        "update A3 certification only if this is a deliberate, reviewed exception"
    )


@pytest.mark.parametrize("module_label", sorted(_GUARDED_MODULES))
def test_pin_no_raw_rest_path_literal_in_guarded_module(module_label: str) -> None:
    """Static scan: no string literal in the module contains the raw REST start path — a
    hand-rolled `/process-definition/key/{key}/start` POST would bypass the fenced chokepoint."""
    path = _GUARDED_MODULES[module_label]
    literals = _module_string_literals(path)
    offending = {lit for lit in literals if _FORBIDDEN_PATH_SUBSTRING in lit}
    assert not offending, (
        f"{module_label} ({path}) contains a raw REST start path literal {offending} — bypasses "
        "the fenced start_process_idempotent chokepoint"
    )


@pytest.mark.parametrize("module_label", sorted(_GUARDED_MODULES))
def test_pin_guard_is_non_vacuous_fenced_call_actually_present(module_label: str) -> None:
    """Sanity: `start_process_idempotent` IS actually called in the module — proves the two guards
    above are not passing merely because the module calls nothing at all."""
    path = _GUARDED_MODULES[module_label]
    call_names = _module_call_names(path)
    assert _FENCED_CHOKEPOINT_CALL in call_names, (
        f"{module_label} ({path}) does not call {_FENCED_CHOKEPOINT_CALL} at all — the "
        "no-unfenced-start guard above would be checking an empty surface"
    )


# =================================================================================================
# Spec-drift guard: the exact `event_payload_vars` literals the armed pins above hard-code as "the
# real production payload shape" must still match the spec/ BPMN text verbatim — if the spec/
# author later touches these lines, this fails LOUDLY instead of letting the armed pins silently
# test a shape that no longer reflects production.
# =================================================================================================


_EP = '<camunda:inputParameter name="event_payload_vars">{vars}</camunda:inputParameter>'
_ED = '<camunda:inputParameter name="event_desfecho">{desfecho}</camunda:inputParameter>'


@pytest.mark.parametrize(
    "expected_line",
    [
        _EP.format(vars="tenant_id,numero_lote_tiss,prestador_id"),
        _EP.format(vars="tenant_id,numero_lote_tiss,glosa_id,numero_guia_tiss"),  # T4: RECURSO armed
        _EP.format(vars="tenant_id,numero_lote_tiss,codigo_glosa_aceito,analista_id"),
        _ED.format(desfecho="encaminhada_fraude"),  # T4: CONTAS→FRAUDE branch landed
    ],
    ids=["sem_glosa/reenviada", "encaminhada_recurso_armed", "glosa_aceita_humano", "fraude_desfecho"],
)
def test_pin_contas_payload_var_literals_match_spec_verbatim(expected_line: str) -> None:
    """The exact `event_payload_vars`/`event_desfecho` literals every CONTAS armed pin above
    assumes must be present verbatim in the spec/ BPMN — never invented, per task requirement #1."""
    xml = _CONTAS_BPMN.read_text(encoding="utf-8")
    assert expected_line in xml, (
        f"CONTAS bpmn no longer contains the literal {expected_line!r} — the real payload shape "
        "this A3 certification hard-codes has drifted from spec/; update A3 certification"
    )


@pytest.mark.parametrize(
    "expected_line",
    [
        _EP.format(
            vars="tenant_id,numero_caso,investigator_id,tier"
        ),  # confirmada/juridico/arquivado/monitorar
        _EP.format(vars="tenant_id,numero_caso,investigator_id,tier,prestador_id"),  # T4: CRED armed
        _EP.format(
            vars="tenant_id,numero_caso,investigator_id,tier,numero_contrato,entidade_tipo"
        ),  # T4: CANCEL/INAD armed
        _ED.format(desfecho="encaminhado_credenciamento"),
        _ED.format(desfecho="encaminhado_contratual"),
    ],
    ids=["base_shape", "cred_armed", "contratual_armed", "cred_desfecho", "contratual_desfecho"],
)
def test_pin_fraude_payload_var_literals_match_spec_verbatim(expected_line: str) -> None:
    """The exact `event_payload_vars`/`event_desfecho` literals every FRAUDE armed pin above
    assumes must be present verbatim in the spec/ BPMN — never invented, per task requirement #1."""
    xml = _FRAUDE_BPMN.read_text(encoding="utf-8")
    assert expected_line in xml, (
        f"FRAUDE bpmn no longer contains the literal {expected_line!r} — the real payload shape "
        "this A3 certification hard-codes has drifted from spec/; update A3 certification"
    )


# =================================================================================================
# PIN 5 — CONTAS→FRAUDE Phase-3 leg is LANDED (T4): the CONTAS spec now DOES emit an
# `encaminhada_fraude` desfecho AND DOES have an in-flow `operadora.contas.start_fraude` worker
# topic. (Flipped from the T3.3 dormancy twins, which asserted their ABSENCE.)
# =================================================================================================


def test_pin_contas_now_emits_encaminhada_fraude_desfecho() -> None:
    """Source-level twin of the CONTAS→FRAUDE armed pin: the CONTAS BPMN's own XML now contains an
    `event_desfecho` of `encaminhada_fraude` (Phase-3 landed). If it ever disappears, the leg was
    reverted and this MUST fail loudly."""
    xml = _CONTAS_BPMN.read_text(encoding="utf-8")
    assert "encaminhada_fraude" in xml, (
        "CONTAS bpmn no longer emits an 'encaminhada_fraude' desfecho — the Phase-3 CONTAS→FRAUDE "
        "handoff was reverted; update A3 certification (the bridge's CONTAS→FRAUDE rule and this "
        "pin's armed assumption are now stale)"
    )


def test_pin_in_flow_start_fraude_worker_topic_exists_in_contas_spec() -> None:
    """The in-flow `camunda:topic="operadora.contas.start_fraude"` worker now exists in the CONTAS
    spec (Phase-3 counterpart to RECURSO/CRED/CANCEL/INADIMPLENCIA)."""
    xml = _CONTAS_BPMN.read_text(encoding="utf-8")
    assert 'camunda:topic="operadora.contas.start_fraude"' in xml, (
        "the in-flow start_fraude worker topic is missing from the CONTAS spec — the Phase-3 "
        "CONTAS→FRAUDE handoff was reverted; update A3 certification"
    )


# =================================================================================================
# PIN 6 (t8-audit-low-cluster) — BYTE-IDENTICAL numero_caso derivation across BOTH call sites, for
# EVERY input type, not just the string case.
#
# `contas._fraude_numero_caso_for_handoff` (the in-flow `start_fraude` worker) and
# `notification_bridge._fraude_numero_caso_for_contas_handoff` (the decoupled Kafka-mirror rule)
# both derive the FRAUDE-001 business-key `numero_caso` anchor. Before this pin, the two were
# independently re-implemented and had DRIFTED for non-string input: the worker used the shared
# `non_blank` (stringifies-then-checks, so an int/float/bool `numero_caso` is accepted), while the
# bridge required `isinstance(numero_caso, str)` and fell back to `prestador_id` for anything else
# — a latent divergent-double-start hazard (`numero_caso` is not a CONTAS process variable today,
# so unreachable in production, but a real defense-in-depth erosion). Both now delegate to the
# SAME shared `tools.workers.base.resolve_fraude_numero_caso`, so this test is a real regression
# fence: it fails RED the moment either call site stops delegating and re-diverges (verified by
# hand — see the R2 delivery report's mutation-proof — one resolver's body was temporarily reverted
# to its pre-fix `isinstance(..., str)`-only form and this test went RED; reverted after).
# =================================================================================================

_NUMERO_CASO_PARITY_CASES: list[tuple[str, Any]] = [
    ("string", "CASO-777"),
    ("string-with-whitespace-padding", "  CASO-PAD  "),
    ("int", 777),
    ("zero-int", 0),
    ("float", 3.14),
    ("bool-true", True),
    ("bool-false", False),
    ("none", None),
    ("absent", "__ABSENT__"),  # sentinel: key omitted from the payload entirely
    ("empty-string", ""),
    ("whitespace-only", "   "),
]


@pytest.mark.parametrize(
    "numero_caso",
    [case for _, case in _NUMERO_CASO_PARITY_CASES],
    ids=[label for label, _ in _NUMERO_CASO_PARITY_CASES],
)
def test_numero_caso_derivation_byte_identical_across_both_call_sites(numero_caso: Any) -> None:
    """For every input type/shape, `contas._fraude_numero_caso_for_handoff` and
    `notification_bridge._fraude_numero_caso_for_contas_handoff` must derive the EXACT SAME
    string — the business-key anchor both paths feed into `FRAUDE-{tenant}-{numero_caso}` — so
    the in-flow worker and the bridge's decoupled Kafka-mirror rule always converge on the same
    FRAUDE-001 instance, never a divergent double-start."""
    payload: dict[str, Any] = {"prestador_id": "PREST-PARITY-001"}
    if numero_caso != "__ABSENT__":
        payload["numero_caso"] = numero_caso

    worker_result = _worker_numero_caso(payload)
    bridge_result = _bridge_numero_caso(payload)

    assert worker_result == bridge_result, (
        f"numero_caso derivation DIVERGED for input {numero_caso!r}: "
        f"contas._fraude_numero_caso_for_handoff={worker_result!r} != "
        f"notification_bridge._fraude_numero_caso_for_contas_handoff={bridge_result!r} — this is "
        "exactly the class of drift that produces a DIFFERENT FRAUDE-001 business key on each "
        "path (divergent double-start hazard)."
    )
    assert isinstance(worker_result, str)
    assert isinstance(bridge_result, str)


def test_numero_caso_derivation_non_string_prefers_numero_caso_not_fallback() -> None:
    """Non-vacuousness proof: a non-string, non-blank `numero_caso` (e.g. an int) must be USED
    (stringified), not silently discarded in favor of the `prestador_id` fallback — otherwise the
    parity test above would pass "vacuously" merely because BOTH sides fell back to the same
    `prestador_id` for every non-string case, without ever actually exercising the numero_caso-wins
    branch on both sides."""
    payload = {"numero_caso": 777, "prestador_id": "PREST-SHOULD-NOT-WIN"}
    assert _worker_numero_caso(payload) == "777"
    assert _bridge_numero_caso(payload) == "777"
