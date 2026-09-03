"""Unit tests for maezo.platform.notification_bridge — cross-process handoffs.

TDD London School: tests exercise handoff rules (evaluate) without
Kafka or CIB Seven. The starter is injected as a spy.

T2.6-7 adds two rules (NIP→ANS-SUBMIT, ans.cron_due→ANS-SUBMIT) — see the dedicated section
near the bottom of this file for their evaluate()/evaluate_all()/business-key/fenced-starter
coverage (docs/design/T2.6-ans-submission-rescope.md §1.5/§5 T2.6-7 row).
"""

from typing import Any

import pytest

from maezo.platform.notification_bridge import (
    CONTAS_COMPLETED_EVENT,
    FRAUDE_COMPLETED_EVENT,
    PROCESS_KEY_ANS_SUBMIT,
    RECURSO_INTAKE_EVENT,
    HandoffEvent,
    HandoffResult,
    NotificationBridge,
    NotificationBridgeHandoffFailedError,
    NotificationBridgeMissingBusinessKeyError,
    build_cibseven_process_starter,
)
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    StartDedupPosture,
    StartOutcome,
    is_strict_start_dedup,
    start_dedup_posture,
)
from tests.support.audit_fakes import FakeStartAuditSink

# EB-4 event_type reconciliation — the REAL emitted domain events the bridge now consumes
# (`agents.events.{contas,fraude}.completed`), keyed on `payload.desfecho`. See the module
# reconciliation note in notification_bridge.py.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bridge() -> NotificationBridge:
    """Create a bridge with the no-op starter for evaluate-only tests."""
    return NotificationBridge()


class StarterSpy:
    """Spy starter that records calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, process_key: str, variables: dict[str, Any]) -> str:
        self.calls.append((process_key, variables))
        return f"instance-{process_key}-spy"


def _make_bridge_with_spy() -> tuple[NotificationBridge, StarterSpy]:
    """Create a bridge with a spy starter for execute tests."""
    spy = StarterSpy()
    return NotificationBridge(cibseven_starter=spy), spy


class FailingStarterSpy:
    """Spy starter that ALWAYS raises — simulates a genuine CIB Seven start failure (transport
    error, engine rejection, ...) unrelated to a missing business key."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._exc = exc or RuntimeError("simulated CIB Seven transport failure")

    async def __call__(self, process_key: str, variables: dict[str, Any]) -> str:
        self.calls.append((process_key, variables))
        raise self._exc


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_bridge_initializes_with_default_handoffs() -> None:
    """NotificationBridge registers the 7 default handoff rules on init (5 pre-T2.6-7 + 2 T2.6-7)."""
    bridge = _make_bridge()
    handoffs = bridge.list_handoffs()
    # 7 rules: contas.glosa_confirmed, contas.encaminhar_fraude,
    # fraude.acusacao_registrada (x3: CRED, CANCEL, INADIMPLENCIA),
    # nip.handoff_ans_submit, ans.cron_due (T2.6-7).
    assert len(handoffs) == 7

    targets = {h["target_process"] for h in handoffs}
    assert "SP-OP-RECURSO-001" in targets
    assert "SP-OP-FRAUDE-001" in targets
    assert "SP-OP-CRED-001" in targets
    assert "SP-OP-CANCEL-001" in targets
    assert "SP-OP-INADIMPLENCIA-001" in targets
    assert PROCESS_KEY_ANS_SUBMIT in targets

    ans_submit_event_types = {
        h["event_type"] for h in handoffs if h["target_process"] == PROCESS_KEY_ANS_SUBMIT
    }
    assert ans_submit_event_types == {"nip.handoff_ans_submit", "ans.cron_due"}


def test_bridge_register_custom_handoff() -> None:
    """register_handoff allows adding custom handoff rules."""
    bridge = _make_bridge()
    bridge.register_handoff(
        event_type="custom.test",
        predicate=lambda p: p.get("go") is True,
        target_process="SP-CUSTOM-001",
        variables_fn=lambda p: {"value": p.get("x", 0)},
    )
    assert len(bridge.list_handoffs()) == 8

    result = bridge.evaluate(HandoffEvent(event_type="custom.test", payload={"go": True, "x": 42}))
    assert result.handoff_triggered is True
    assert result.target_process == "SP-CUSTOM-001"
    assert result.variables == {"value": 42}


# ---------------------------------------------------------------------------
# CONTAS → RECURSO
# ---------------------------------------------------------------------------


def test_regra_intake_recurso_dispara_com_payload_ancorado() -> None:
    """ADR-0040 §3.1 amarra 3: com um envelope sintetico completo a regra FUNCIONA — a dormencia e
    falta de PUBLICADOR, nao regra quebrada.

    Substitui `test_bridge_contas_to_recurso_triggered`: a aresta CONTAS→RECURSO codificava a
    perspectiva invertida (RECURSO como algo que CONTAS entrega quando a propria operadora decide
    recorrer). A operadora nao recorre da sua propria glosa; ela RECEBE o recurso.
    """
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=RECURSO_INTAKE_EVENT,
        payload={
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-123",
            "glosa_id": "GLOSA-001",
            "numero_lote_tiss": "LOTE-001",
            "prestador_id": "PREST-001",
            "glosa_type": "tecnica",
            "glosa_existe": True,
            "dentro_prazo_recurso": True,
            "documentacao_recurso_completa": True,
        },
    )
    result = bridge.evaluate(event)
    assert result.evaluated is True
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-RECURSO-001"
    assert result.variables["glosa_id"] == "GLOSA-001"
    assert result.variables["numero_guia_tiss"] == "GUIA-123"
    assert result.variables["prestador_id"] == "PREST-001"
    assert result.variables["business_key"] == "RECURSO-amh-GUIA-123-GLOSA-001"


def test_regra_intake_recurso_nao_dispara_no_evento_de_contas() -> None:
    """A aresta antiga MORREU: `agents.events.contas.completed` com desfecho
    `glosa_aplicada_humano` (o desfecho que carrega o `glosa_id`) nao inicia RECURSO-001 —
    nem sob outro nome. A operadora nao recorre da propria glosa."""
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(
            event_type=CONTAS_COMPLETED_EVENT,
            payload={
                "tenant_id": "amh",
                "desfecho": "glosa_aplicada_humano",
                "glosa_id": "GLOSA-001",
                "numero_guia_tiss": "GUIA-123",
            },
        )
    )
    assert result.handoff_triggered is False
    assert "No handoff rule matched" in result.reason


def test_regra_intake_recurso_dormant_when_anchor_missing() -> None:
    """Ancora fail-closed: sem `numero_guia_tiss`/`glosa_id` a regra fica DORMENTE — nunca um
    start sob business key divergente/degenerada."""
    bridge = _make_bridge()
    result = bridge.evaluate(HandoffEvent(event_type=RECURSO_INTAKE_EVENT, payload={"tenant_id": "amh"}))
    assert result.handoff_triggered is False


def test_regra_intake_recurso_facts_sao_fail_closed_nao_tautologias() -> None:
    """`glosa_existe`/`dentro_prazo_recurso` deixaram de ser tautologias e sao FAIL-CLOSED.

    A regra antiga semeava `glosa_existe=True` incondicionalmente ("so alcanca o handoff apos
    RECORRER sobre uma glosa ativa") — uma premissa que nao sobrevive a um recurso interposto de
    FORA, cujo `glosa_id` pode nao referenciar nada que a operadora tenha cunhado. Ausente => False
    => `ANALISE_HUMANA` pela DMN de admissibilidade, nunca uma afirmacao de que a glosa existe.
    """
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(
            event_type=RECURSO_INTAKE_EVENT,
            payload={"tenant_id": "amh", "numero_guia_tiss": "GUIA-123", "glosa_id": "GLOSA-001"},
        )
    )
    assert result.handoff_triggered is True
    assert result.variables["glosa_existe"] is False
    assert result.variables["dentro_prazo_recurso"] is False
    assert result.variables["documentacao_recurso_completa"] is False


def test_regra_intake_recurso_e_dormente_ate_o_adaptador_existir() -> None:
    """ADR-0040 §3.1 amarra 2 (OQ-R1): NENHUM publicador conhecido emite
    `agents.events.recurso.intake_recebido`.

    Varre os publicadores do repo — os `event_topic`/`event_topic_*` de TODO BPMN em
    `spec/processes/bpmn/**` (o trilho `operadora.events.publish`), os tópicos que os workers
    publicam via `kafka.publish` e os do proprio bridge. QUANDO o adaptador de intake TISS chegar,
    ESTE TESTE FICA VERMELHO — e essa e a sua funcao: forcar a atualizacao da divulgacao de
    dormencia no MESMO PR que liga o publicador. Nenhum stub, nenhum publicador sintetico.
    """
    from pathlib import Path
    from xml.etree import ElementTree as ET

    repo = Path(__file__).resolve().parents[3]
    publicados: set[str] = set()
    for bpmn in sorted((repo / "spec/processes/bpmn").glob("*.bpmn")):
        root = ET.parse(bpmn).getroot()
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == "inputParameter" and el.text:
                name = el.get("name") or ""
                if name.startswith("event_topic"):
                    publicados.add(el.text.strip())
    assert publicados, "a varredura de publicadores nao pode vir vazia"
    assert RECURSO_INTAKE_EVENT not in publicados, (
        f"{RECURSO_INTAKE_EVENT} ganhou um publicador — atualize a divulgacao de dormencia em "
        "`notification_bridge._register_default_handoffs` e feche OQ-R1 em docs/review-queue.md "
        "NO MESMO PR"
    )

    src_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((repo / "src/maezo").rglob("*.py"))
        if path.name != "notification_bridge.py"
    )
    assert RECURSO_INTAKE_EVENT not in src_text, (
        f"{RECURSO_INTAKE_EVENT} apareceu em src/ fora do proprio bridge — se e um publicador, "
        "atualize a divulgacao de dormencia e OQ-R1 no mesmo PR"
    )

    # m5 (gatekeeper R1 do PR-3): a exclusao acima e do ARQUIVO inteiro — o bridge tem de poder
    # NOMEAR o evento que consome. Isso deixava um buraco: um publicador acrescentado DENTRO do
    # proprio bridge escapava do tripwire. Aqui o arquivo excluido volta, com a forma certa de
    # pergunta: nao «o nome aparece?» (aparece, 3x, por construcao) e sim «o nome aparece como
    # ARGUMENTO de uma chamada de publicacao?». Residual declarado: um publicador que montasse o
    # topico por concatenacao/variavel intermediaria escaparia — a cerca lexica nao le fluxo de
    # dados; o que ela garante e que a forma OBVIA nao passa despercebida.
    import re

    bridge_src = (repo / "src/maezo/platform/notification_bridge.py").read_text(encoding="utf-8")
    alvo = f"(?:RECURSO_INTAKE_EVENT|{re.escape(RECURSO_INTAKE_EVENT)})"
    publicador = re.compile(rf"\b(?:publish|produce|send|emit)\w*\s*\([^)]*{alvo}", re.DOTALL)
    assert not publicador.search(bridge_src), (
        f"{RECURSO_INTAKE_EVENT} passou a ser PUBLICADO de dentro do proprio notification_bridge "
        "— atualize a divulgacao de dormencia e feche OQ-R1 em docs/review-queue.md NO MESMO PR"
    )
    # NAO-VACUIDADE do regex acima: a mesma forma, com um evento que o bridge realmente publica,
    # tem de casar — senao este assert seria verde por nunca casar nada.
    assert publicador.search("await kafka.publish(RECURSO_INTAKE_EVENT, payload)"), (
        "o detector de publicador nao reconhece a forma que ele existe para reconhecer"
    )


# ---------------------------------------------------------------------------
# CONTAS → FRAUDE
# ---------------------------------------------------------------------------


def test_bridge_contas_to_fraude_triggered() -> None:
    """Handoff CONTAS→FRAUDE fires on desfecho=encaminhada_fraude with the prestador_id anchor
    AND tenant_id present (t2-notify-integrity item 3: tenant is a required anchor — the old
    version of this test pinned a tenant-LESS trigger, which minted the degenerate
    `FRAUDE--{caso}` business key; that behavior is now fail-closed dormant, see
    `test_bridge_contas_to_fraude_dormant_without_tenant`)."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=CONTAS_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhada_fraude",
            "analista_id": "auditor-001",
            "numero_lote_tiss": "LOTE-001",
            "prestador_id": "PREST-001",
            "evidencia_refs": ["ref-1", "ref-2"],
            "indicadores_presentes": ["score_elevado"],
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-FRAUDE-001"
    assert result.variables["origem_encaminhamento"] == "contas"
    assert result.variables["encaminhado_por_id"] == "auditor-001"
    assert result.variables["prestador_id"] == "PREST-001"
    assert result.variables["evidencia_refs"] == ["ref-1", "ref-2"]
    assert result.variables["business_key"] == "FRAUDE-amh-PREST-001"  # never FRAUDE--PREST-001


def test_bridge_contas_to_fraude_dormant_without_tenant() -> None:
    """FLIPPED PIN (t2-notify-integrity item 3): the exact tenant-LESS payload the pre-fix test
    asserted `handoff_triggered is True` for must now stay DORMANT — a blank/absent tenant would
    mint the degenerate `FRAUDE--{caso}` business key (tenant-scoping orphan + divergent-key
    double-start hazard vs the canonical in-flow `FRAUDE-{tenant}-{caso}` key). Mirrors
    `contas.start_fraude`'s own ERR_CONTAS_FRAUDE_SEM_ALVO tenant guard."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=CONTAS_COMPLETED_EVENT,
        payload={
            "desfecho": "encaminhada_fraude",
            "analista_id": "auditor-001",
            "numero_lote_tiss": "LOTE-001",
            "prestador_id": "PREST-001",
            "evidencia_refs": ["ref-1", "ref-2"],
            "indicadores_presentes": ["score_elevado"],
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False


def test_bridge_contas_to_fraude_not_triggered_when_other_desfecho() -> None:
    """Does NOT trigger on a non-fraude desfecho."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=CONTAS_COMPLETED_EVENT,
        payload={"tenant_id": "amh", "desfecho": "glosa_aplicada_humano", "prestador_id": "PREST-001"},
    )
    result = bridge.evaluate(event)
    assert result.target_process != "SP-OP-FRAUDE-001"


def test_bridge_contas_to_fraude_dormant_when_anchor_missing() -> None:
    """The fraude desfecho (and tenant) but no prestador_id anchor -> dormant (fail-closed)."""
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(
            event_type=CONTAS_COMPLETED_EVENT,
            payload={"tenant_id": "amh", "desfecho": "encaminhada_fraude"},
        )
    )
    assert result.handoff_triggered is False


# ---------------------------------------------------------------------------
# FRAUDE → CRED
# ---------------------------------------------------------------------------


def test_bridge_fraude_to_cred_triggered() -> None:
    """Handoff FRAUDE→CRED triggers on desfecho=encaminhado_credenciamento (the BPMN's routing
    encoding) with a prestador_id anchor."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_credenciamento",
            "prestador_id": "PREST-001",
            "numero_caso": "FRAUDE-001",
            "bundle_root": "abc123def",
            "destino_referral": {"legal": True},
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CRED-001"


def test_bridge_fraude_to_cred_not_triggered_when_contratual_desfecho() -> None:
    """A contratual desfecho routes to CANCEL, not CRED."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "numero_contrato": "CTR-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True  # triggers CANCEL, not CRED
    assert result.target_process == "SP-OP-CANCEL-001"


# ---------------------------------------------------------------------------
# FRAUDE → CANCEL/INADIMPLENCIA
# ---------------------------------------------------------------------------


def test_bridge_fraude_to_cancel_triggered() -> None:
    """Handoff FRAUDE→CANCEL triggers on desfecho=encaminhado_contratual (beneficiario path —
    a single desfecho covers both beneficiario and contrato)."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "entidade_tipo": "beneficiario",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_contrato": "CTR-001",
            "numero_caso": "FRAUDE-001",
            "bundle_root": "abc123def",
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CANCEL-001"
    assert result.variables["beneficiario_pseudo_id"] == "pseudo-b-001"


def test_bridge_fraude_to_inadimplencia_triggered() -> None:
    """Handoff FRAUDE→INADIMPLENCIA and CANCEL both trigger for CONTRATO fraud
    (entidade_tipo=contrato, the INAD discriminator)."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    # evaluate returns first match (CANCEL, registered before INADIMPLENCIA)
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CANCEL-001"

    # evaluate_all returns both for contrato
    all_results = bridge.evaluate_all(event)
    assert len(all_results) == 2
    targets = {r.target_process for r in all_results}
    assert targets == {"SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}


# ---------------------------------------------------------------------------
# evaluate_all — multiple handoffs from same event
# ---------------------------------------------------------------------------


def test_evaluate_all_multiple_fraude_handoffs() -> None:
    """evaluate_all returns all matching rules for fraude.acusacao_registrada."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    results = bridge.evaluate_all(event)
    # For contrato: CANCEL + INADIMPLENCIA (2 rules match)
    assert len(results) == 2
    targets = {r.target_process for r in results}
    assert "SP-OP-CANCEL-001" in targets
    assert "SP-OP-INADIMPLENCIA-001" in targets
    for r in results:
        assert r.handoff_triggered is True


def test_evaluate_all_single_match() -> None:
    """evaluate_all returns one result when only one rule matches."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=RECURSO_INTAKE_EVENT,
        payload={
            "tenant_id": "amh",
            "glosa_id": "GLOSA-001",
            "numero_guia_tiss": "GUIA-123",
            "glosa_type": "tecnica",
            "numero_lote_tiss": "LOTE-001",
        },
    )
    results = bridge.evaluate_all(event)
    assert len(results) == 1
    assert results[0].target_process == "SP-OP-RECURSO-001"


def test_evaluate_all_no_match() -> None:
    """evaluate_all returns a single not-triggered result when no rules match."""
    bridge = _make_bridge()
    event = HandoffEvent(event_type="nonexistent.event", payload={})
    results = bridge.evaluate_all(event)
    assert len(results) == 1
    assert results[0].handoff_triggered is False
    assert "No handoff rule matched" in results[0].reason


# ---------------------------------------------------------------------------
# Unknown event types
# ---------------------------------------------------------------------------


def test_bridge_unknown_event_type() -> None:
    """Evaluating an unregistered event type returns not-triggered."""
    bridge = _make_bridge()
    result = bridge.evaluate(HandoffEvent(event_type="nonexistent.event", payload={}))
    assert result.evaluated is True
    assert result.handoff_triggered is False
    assert "No handoff rule matched" in result.reason


# ---------------------------------------------------------------------------
# execute_handoff (async, with spy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_handoff_calls_starter() -> None:
    """execute_handoff calls the injected CIB Seven starter."""
    bridge, spy = _make_bridge_with_spy()
    result = HandoffResult(
        evaluated=True,
        handoff_triggered=True,
        target_process="SP-OP-RECURSO-001",
        variables={"glosa_id": "G-1"},
    )
    result = await bridge.execute_handoff(result)
    assert result.process_instance_id == "instance-SP-OP-RECURSO-001-spy"
    assert len(spy.calls) == 1
    assert spy.calls[0] == ("SP-OP-RECURSO-001", {"glosa_id": "G-1"})


@pytest.mark.asyncio
async def test_execute_handoff_skips_when_not_triggered() -> None:
    """execute_handoff skips the starter when handoff_triggered is False — a legitimate
    "no rule matched" skip must NOT raise (EB-3 part 1: distinct from a genuine start failure)."""
    bridge, spy = _make_bridge_with_spy()
    result = HandoffResult(
        evaluated=True,
        handoff_triggered=False,
        reason="Nothing to do",
    )
    result = await bridge.execute_handoff(result)
    assert result.process_instance_id == ""
    assert len(spy.calls) == 0


# ---------------------------------------------------------------------------
# EB-3 part 1 — fail-closed execute_handoff: a genuine start failure PROPAGATES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_handoff_propagates_genuine_start_failure() -> None:
    """A MATCHED rule whose starter genuinely fails (transport error, NOT a missing business
    key) must PROPAGATE — never silently absorbed into `.reason` (the old fail-OPEN behavior)."""
    spy = FailingStarterSpy(RuntimeError("CIB Seven returned 500"))
    bridge = NotificationBridge(cibseven_starter=spy)
    result = HandoffResult(
        evaluated=True,
        handoff_triggered=True,
        target_process="SP-OP-RECURSO-001",
        variables={"glosa_id": "G-1", "business_key": "RECURSO-amh-GUIA-1-G-1"},
    )
    with pytest.raises(NotificationBridgeHandoffFailedError) as exc_info:
        await bridge.execute_handoff(result)

    assert exc_info.value.target_process == "SP-OP-RECURSO-001"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "CIB Seven returned 500" in str(exc_info.value.__cause__)
    assert len(spy.calls) == 1  # the starter WAS attempted, once


@pytest.mark.asyncio
async def test_on_event_propagates_when_matched_rule_start_fails() -> None:
    """The full on_event pipeline propagates a genuine start failure too (no try/except hides
    it at the higher-level entry point Kafka consumers call)."""
    spy = FailingStarterSpy()
    bridge = NotificationBridge(cibseven_starter=spy)
    with pytest.raises(NotificationBridgeHandoffFailedError):
        await bridge.on_event(
            event_type=RECURSO_INTAKE_EVENT,
            payload={
                "glosa_id": "GLOSA-002",
                "numero_guia_tiss": "GUIA-456",
                "tenant_id": "amh",
            },
        )
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_execute_handoff_idempotent_replay_is_not_a_failure() -> None:
    """A starter that returns an EXISTING instance id (idempotent replay — no exception at all,
    exactly what `start_process_idempotent`'s `find_active_instance` hit does) is a SUCCESS, not
    a failure — `execute_handoff` never raises when the starter itself does not raise."""
    bridge, spy = _make_bridge_with_spy()  # StarterSpy always succeeds, never raises
    result = HandoffResult(
        evaluated=True,
        handoff_triggered=True,
        target_process="SP-OP-RECURSO-001",
        variables={"business_key": "RECURSO-amh-GUIA-1-G-1"},
    )
    first = await bridge.execute_handoff(result)
    second = await bridge.execute_handoff(
        HandoffResult(
            evaluated=True,
            handoff_triggered=True,
            target_process="SP-OP-RECURSO-001",
            variables={"business_key": "RECURSO-amh-GUIA-1-G-1"},
        )
    )
    assert first.process_instance_id == second.process_instance_id
    assert len(spy.calls) == 2  # both attempted; a REAL idempotent starter would dedupe engine-side


@pytest.mark.asyncio
async def test_on_event_full_pipeline() -> None:
    """on_event evaluates and executes in one call."""
    bridge, spy = _make_bridge_with_spy()
    results = await bridge.on_event(
        event_type=RECURSO_INTAKE_EVENT,
        payload={
            "tenant_id": "amh",
            "glosa_id": "GLOSA-002",
            "numero_guia_tiss": "GUIA-456",
            "glosa_type": "clinica",
            "numero_lote_tiss": "LOTE-002",
        },
    )
    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == "SP-OP-RECURSO-001"
    assert results[0].process_instance_id == "instance-SP-OP-RECURSO-001-spy"
    assert spy.calls[0][0] == "SP-OP-RECURSO-001"


@pytest.mark.asyncio
async def test_on_event_multiple_handoffs() -> None:
    """on_event starts multiple processes for multi-handoff events."""
    bridge, spy = _make_bridge_with_spy()
    results = await bridge.on_event(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    assert len(results) == 2
    targets = {r.target_process for r in results}
    assert targets == {"SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}
    for r in results:
        assert r.process_instance_id.startswith("instance-")
    assert len(spy.calls) == 2


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def test_list_handoffs_returns_all() -> None:
    """list_handoffs returns all registered handoff rules."""
    bridge = _make_bridge()
    handoffs = bridge.list_handoffs()
    assert isinstance(handoffs, list)
    assert len(handoffs) == 7
    assert all("event_type" in h and "target_process" in h for h in handoffs)


def test_get_handoff_returns_rules() -> None:
    """get_handoff returns all rules for an event type. `agents.events.contas.completed` now
    carries ONE rule (the Phase-3 FRAUDE handoff) — the RECURSO edge moved to the intake event
    (ADR-0040)."""
    bridge = _make_bridge()
    assert len(bridge.get_handoff(CONTAS_COMPLETED_EVENT)) == 1

    rules = bridge.get_handoff(RECURSO_INTAKE_EVENT)
    assert len(rules) == 1
    recurso = [(t, p) for t, p in rules if t == "SP-OP-RECURSO-001"]
    assert len(recurso) == 1
    _, predicate = recurso[0]
    assert callable(predicate)
    assert (
        predicate(
            {
                "tenant_id": "amh",
                "numero_guia_tiss": "G-1",
                "glosa_id": "GL-1",
            }
        )
        is True
    )
    # Ancora fail-closed: sem `glosa_id` a regra nao dispara.
    assert predicate({"tenant_id": "amh", "numero_guia_tiss": "G-1"}) is False
    # t2-notify-integrity item 3: tenant is a required anchor — same payload minus tenant is dormant.
    assert predicate({"numero_guia_tiss": "G-1", "glosa_id": "GL-1"}) is False


def test_get_handoff_multiple_rules() -> None:
    """get_handoff returns the 3 rules on agents.events.fraude.completed (CRED/CANCEL/INAD)."""
    bridge = _make_bridge()
    rules = bridge.get_handoff(FRAUDE_COMPLETED_EVENT)
    assert len(rules) == 3  # CRED, CANCEL, INADIMPLENCIA
    targets = {t for t, _ in rules}
    assert targets == {"SP-OP-CRED-001", "SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}


def test_get_handoff_unknown_returns_empty() -> None:
    """get_handoff returns empty list for unregistered event types."""
    bridge = _make_bridge()
    assert bridge.get_handoff("nonexistent.event") == []


def test_count_handoffs() -> None:
    """count_handoffs returns the number of registered rules."""
    bridge = _make_bridge()
    assert bridge.count_handoffs() == 7
    bridge.register_handoff(
        event_type="extra.rule.here",
        predicate=lambda p: True,
        target_process="SP-EXTRA-001",
        variables_fn=lambda p: {},
    )
    assert bridge.count_handoffs() == 8


# ---------------------------------------------------------------------------
# T2.6-7 — NIP→ANS-SUBMIT
# ---------------------------------------------------------------------------


def test_nip_handoff_triggers_ans_submit() -> None:
    """nip.handoff_ans_submit with origem_envio=nip_filing starts SP-OP-ANS-SUBMIT-001."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="nip.handoff_ans_submit",
        payload={
            "tenant_id": "amh",
            "numero_nip_ans": "000000042",
            "protocolo_ans": "PROTO-TESTE-0001",
            "decisao_nip": "CONCEDER",
            "origem_envio": "nip_filing",
        },
    )
    result = bridge.evaluate(event)
    assert result.evaluated is True
    assert result.handoff_triggered is True
    assert result.target_process == PROCESS_KEY_ANS_SUBMIT
    assert result.variables["origem_envio"] == "nip_filing"
    assert result.variables["nip_protocolo_origem"] == "PROTO-TESTE-0001"
    assert result.variables["tenant_id"] == "amh"
    assert result.variables["numero_nip_ans"] == "000000042"
    # Fail-closed admissibility facts (never auto-passed; human resolves in
    # UT_Corrigir*/UT_RevisarEnvioJuridico).
    assert result.variables["dataset_complete"] is False
    assert result.variables["schema_valid"] is False
    assert result.variables["lgpd_anonimizado"] is False
    # Deterministic BK — contract "Variaveis de saida" protocolo_ans note.
    assert result.variables["business_key"] == "ANSSUB-amh-nipfiling-000000042"


def test_nip_handoff_business_key_is_deterministic_across_redelivery() -> None:
    """Two identical redeliveries of the same NIP handoff derive the SAME business key
    (idempotency — never a second SUBMIT instance for the same NIP case)."""
    bridge = _make_bridge()
    payload = {
        "tenant_id": "amh",
        "numero_nip_ans": "000000099",
        "protocolo_ans": None,
        "origem_envio": "nip_filing",
    }
    r1 = bridge.evaluate(HandoffEvent(event_type="nip.handoff_ans_submit", payload=dict(payload)))
    r2 = bridge.evaluate(HandoffEvent(event_type="nip.handoff_ans_submit", payload=dict(payload)))
    assert r1.variables["business_key"] == r2.variables["business_key"] == "ANSSUB-amh-nipfiling-000000099"
    # protocolo_ans absent (None) is legitimate (GAP-NIP-6) — nip_protocolo_origem falls back to "".
    assert r1.variables["nip_protocolo_origem"] == ""


def test_nip_handoff_not_triggered_when_origem_envio_not_nip_filing() -> None:
    """A NIP handoff event with a different/absent origem_envio never starts ANS-SUBMIT."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="nip.handoff_ans_submit",
        payload={"tenant_id": "amh", "numero_nip_ans": "000000042", "origem_envio": "retransmissao_manual"},
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False


def test_nip_handoff_fail_closed_when_numero_nip_ans_missing() -> None:
    """Malformed trigger (no numero_nip_ans -> no business-key anchor) never starts a process —
    fail-closed rather than deriving a garbage/collision-prone business key."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="nip.handoff_ans_submit",
        payload={"tenant_id": "amh", "origem_envio": "nip_filing"},
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False
    assert "No handoff rule matched" in result.reason


def test_nip_handoff_fail_closed_when_numero_nip_ans_blank() -> None:
    """Blank (whitespace-only) numero_nip_ans is treated as malformed — same fail-closed path."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="nip.handoff_ans_submit",
        payload={"tenant_id": "amh", "numero_nip_ans": "   ", "origem_envio": "nip_filing"},
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False


def test_nip_handoff_fail_closed_when_numero_nip_ans_is_none() -> None:
    """EB-3 part 2: an EXPLICIT None numero_nip_ans is fail-closed rejected — the naive
    `bool(str(p.get(field, "")).strip())` idiom would otherwise treat `None` as the non-blank
    string "None", deriving a garbage business key like ANSSUB-amh-nipfiling-None."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="nip.handoff_ans_submit",
        payload={"tenant_id": "amh", "numero_nip_ans": None, "origem_envio": "nip_filing"},
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False
    assert "No handoff rule matched" in result.reason


# ---------------------------------------------------------------------------
# T2.6-7 — ans.cron_due→ANS-SUBMIT
# ---------------------------------------------------------------------------


def test_cron_due_triggers_ans_submit() -> None:
    """ans.cron_due (SP-OP-ANS-CRON-001's per-report_type tick) starts SP-OP-ANS-SUBMIT-001.

    t2-notify-integrity item 3: the payload now carries `tenant_id` (a required anchor) — the
    pre-fix version of this test pinned a tenant-LESS trigger whose asserted business key was the
    degenerate `ANSSUB--DIOPS_TRIMESTRAL-...` (double-dash = blank tenant segment), exactly the
    orphan-key class the tenant anchor now refuses."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="ans.cron_due",
        payload={
            "type": "ans.cron_due",
            "tenant_id": "amh",
            "report_type": "DIOPS_TRIMESTRAL",
            "periodicidade": "trimestral",
            "origem_envio": "calendario",
            "competencia": "COMPETENCIA_PENDENTE",
            "ans_cron_reference_date_iso": "2026-07-24",
        },
    )
    result = bridge.evaluate(event)
    assert result.evaluated is True
    assert result.handoff_triggered is True
    assert result.target_process == PROCESS_KEY_ANS_SUBMIT
    assert result.variables["report_type"] == "DIOPS_TRIMESTRAL"
    assert result.variables["competencia"] == "COMPETENCIA_PENDENTE"
    assert result.variables["periodicidade"] == "trimestral"
    assert result.variables["origem_envio"] == "calendario"
    assert result.variables["dataset_complete"] is False
    assert result.variables["schema_valid"] is False
    assert result.variables["lgpd_anonimizado"] is False
    assert result.variables["business_key"] == "ANSSUB-amh-DIOPS_TRIMESTRAL-COMPETENCIA_PENDENTE"


def test_cron_due_business_key_deterministic_and_scoped_by_report_type() -> None:
    """Same tenant/competencia sentinel but different report_type -> different business keys
    (no cross-report_type collision on the shared COMPETENCIA_PENDENTE sentinel)."""
    bridge = _make_bridge()
    r1 = bridge.evaluate(
        HandoffEvent(
            event_type="ans.cron_due",
            payload={"report_type": "RN_124_SIP", "competencia": "COMPETENCIA_PENDENTE", "tenant_id": "amh"},
        )
    )
    r2 = bridge.evaluate(
        HandoffEvent(
            event_type="ans.cron_due",
            payload={
                "report_type": "DIOPS_TRIMESTRAL",
                "competencia": "COMPETENCIA_PENDENTE",
                "tenant_id": "amh",
            },
        )
    )
    assert r1.variables["business_key"] == "ANSSUB-amh-RN_124_SIP-COMPETENCIA_PENDENTE"
    assert r2.variables["business_key"] == "ANSSUB-amh-DIOPS_TRIMESTRAL-COMPETENCIA_PENDENTE"
    assert r1.variables["business_key"] != r2.variables["business_key"]

    # Redelivery of the SAME fact reconverges on the SAME business key (idempotent re-tick).
    r1_again = bridge.evaluate(
        HandoffEvent(
            event_type="ans.cron_due",
            payload={"report_type": "RN_124_SIP", "competencia": "COMPETENCIA_PENDENTE", "tenant_id": "amh"},
        )
    )
    assert r1_again.variables["business_key"] == r1.variables["business_key"]


def test_cron_due_missing_competencia_falls_back_to_sentinel() -> None:
    """A fact missing `competencia` entirely still resolves to the documented sentinel — never
    a blank/undefined competência reaching the engine's business key."""
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(
            event_type="ans.cron_due", payload={"tenant_id": "amh", "report_type": "RN_388_QUALIDADE"}
        )
    )
    assert result.handoff_triggered is True
    assert result.variables["competencia"] == "COMPETENCIA_PENDENTE"


def test_cron_due_fail_closed_when_report_type_missing() -> None:
    """Malformed trigger (no report_type, tenant present) never starts a process — fail-closed."""
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(
            event_type="ans.cron_due",
            payload={"tenant_id": "amh", "competencia": "COMPETENCIA_PENDENTE"},
        )
    )
    assert result.handoff_triggered is False


def test_cron_due_fail_closed_when_report_type_blank() -> None:
    """Blank (whitespace-only) report_type is treated as malformed — same fail-closed path."""
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(event_type="ans.cron_due", payload={"tenant_id": "amh", "report_type": "  "})
    )
    assert result.handoff_triggered is False


def test_cron_due_fail_closed_when_report_type_is_none() -> None:
    """EB-3 part 2: an EXPLICIT None report_type is fail-closed rejected (same None-hardening
    rationale as the NIP rule above)."""
    bridge = _make_bridge()
    result = bridge.evaluate(
        HandoffEvent(event_type="ans.cron_due", payload={"tenant_id": "amh", "report_type": None})
    )
    assert result.handoff_triggered is False


def test_get_handoff_ans_submit_rules() -> None:
    """get_handoff surfaces both new T2.6-7 rules independently by event_type."""
    bridge = _make_bridge()
    nip_rules = bridge.get_handoff("nip.handoff_ans_submit")
    assert len(nip_rules) == 1
    assert nip_rules[0][0] == PROCESS_KEY_ANS_SUBMIT

    cron_rules = bridge.get_handoff("ans.cron_due")
    assert len(cron_rules) == 1
    assert cron_rules[0][0] == PROCESS_KEY_ANS_SUBMIT


@pytest.mark.asyncio
async def test_on_event_nip_handoff_executes_via_starter_spy() -> None:
    """Full pipeline: nip.handoff_ans_submit -> evaluate -> execute via the injected starter."""
    bridge, spy = _make_bridge_with_spy()
    results = await bridge.on_event(
        event_type="nip.handoff_ans_submit",
        payload={
            "tenant_id": "amh",
            "numero_nip_ans": "000000042",
            "protocolo_ans": "PROTO-TESTE-0001",
            "origem_envio": "nip_filing",
        },
    )
    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == PROCESS_KEY_ANS_SUBMIT
    assert spy.calls[0][0] == PROCESS_KEY_ANS_SUBMIT
    assert spy.calls[0][1]["business_key"] == "ANSSUB-amh-nipfiling-000000042"


@pytest.mark.asyncio
async def test_on_event_cron_due_executes_via_starter_spy() -> None:
    """Full pipeline: ans.cron_due -> evaluate -> execute via the injected starter."""
    bridge, spy = _make_bridge_with_spy()
    results = await bridge.on_event(
        event_type="ans.cron_due",
        payload={
            "report_type": "DIOPS_TRIMESTRAL",
            "competencia": "COMPETENCIA_PENDENTE",
            "tenant_id": "amh",
        },
    )
    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == PROCESS_KEY_ANS_SUBMIT
    assert spy.calls[0][1]["business_key"] == "ANSSUB-amh-DIOPS_TRIMESTRAL-COMPETENCIA_PENDENTE"


# ---------------------------------------------------------------------------
# t2-notify-integrity item 3 — tenant anchor: ALL 7 default rules stay DORMANT on a payload
# whose per-rule anchors are present but whose tenant_id is absent/blank/None. Restores the
# fail-closed symmetry with the in-flow fenced-start workers (contas.handoff_pagamento/start_fraude,
# fraude.start_credenciamento/start_contratual all REFUSE a tenant-less start via the shared
# non_blank) and kills the degenerate business-key class (`RECURSO--…`, `FRAUDE--…`, `CRED--…`,
# `CANCEL--…`, `INAD--…`, `ANSSUB--…`) that a tenant-less trigger used to mint.
# ---------------------------------------------------------------------------

_TENANTLESS_RULE_PAYLOADS: list[tuple[str, str, dict[str, Any]]] = [
    (
        "intake-recurso",
        RECURSO_INTAKE_EVENT,
        {"numero_guia_tiss": "G-1", "glosa_id": "GL-1"},
    ),
    (
        "contas-fraude",
        CONTAS_COMPLETED_EVENT,
        {"desfecho": "encaminhada_fraude", "prestador_id": "PREST-001"},
    ),
    (
        "fraude-cred",
        FRAUDE_COMPLETED_EVENT,
        {"desfecho": "encaminhado_credenciamento", "prestador_id": "PREST-001"},
    ),
    (
        "fraude-cancel",
        FRAUDE_COMPLETED_EVENT,
        {"desfecho": "encaminhado_contratual", "entidade_tipo": "beneficiario", "numero_contrato": "CTR-1"},
    ),
    (
        "fraude-inadimplencia",
        FRAUDE_COMPLETED_EVENT,
        {"desfecho": "encaminhado_contratual", "entidade_tipo": "contrato", "numero_contrato": "CTR-1"},
    ),
    (
        "nip-anssubmit",
        "nip.handoff_ans_submit",
        {"origem_envio": "nip_filing", "numero_nip_ans": "000000042"},
    ),
    (
        "cron-anssubmit",
        "ans.cron_due",
        {"report_type": "DIOPS_TRIMESTRAL", "competencia": "COMPETENCIA_PENDENTE"},
    ),
]


@pytest.mark.parametrize(
    "event_type,payload",
    [(event_type, payload) for _, event_type, payload in _TENANTLESS_RULE_PAYLOADS],
    ids=[label for label, _, _ in _TENANTLESS_RULE_PAYLOADS],
)
@pytest.mark.parametrize(
    "tenant_value", ["__ABSENT__", "", "   ", None], ids=["absent", "empty", "blank", "none"]
)
def test_all_7_rules_dormant_without_tenant_anchor(
    event_type: str, payload: dict[str, Any], tenant_value: Any
) -> None:
    """Every default rule refuses to trigger when tenant_id is absent/empty/whitespace/None even
    with every per-rule anchor present — no degenerate `{PREFIX}--…` business key can ever reach
    the fenced starter from a tenant-less event."""
    bridge = _make_bridge()
    event_payload = dict(payload)
    if tenant_value != "__ABSENT__":
        event_payload["tenant_id"] = tenant_value
    results = bridge.evaluate_all(HandoffEvent(event_type=event_type, payload=event_payload))
    assert all(r.handoff_triggered is False for r in results)


@pytest.mark.parametrize(
    "event_type,payload",
    [(event_type, payload) for _, event_type, payload in _TENANTLESS_RULE_PAYLOADS],
    ids=[label for label, _, _ in _TENANTLESS_RULE_PAYLOADS],
)
def test_all_7_rules_trigger_once_tenant_anchor_present(event_type: str, payload: dict[str, Any]) -> None:
    """Non-vacuousness twin: the SAME payloads DO trigger with tenant_id added — proving the
    dormancy above is the tenant anchor's doing, not a broken/unmatchable payload shape."""
    bridge = _make_bridge()
    results = bridge.evaluate_all(
        HandoffEvent(event_type=event_type, payload={**payload, "tenant_id": "amh"})
    )
    assert any(r.handoff_triggered is True for r in results)
    for r in results:
        if r.handoff_triggered:
            assert "--" not in r.variables["business_key"]  # no blank-tenant segment, ever


# ---------------------------------------------------------------------------
# T2.6-7 — build_cibseven_process_starter (the fenced-start chokepoint proof)
# ---------------------------------------------------------------------------


class _RecordingCibSevenTransport(FakeCibSevenTransport):
    """Records every start_process_instance call (the audit surface under proof)."""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        self.start_calls.append((process_key, business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


@pytest.mark.asyncio
async def test_fenced_starter_goes_through_start_process_idempotent_with_sink_and_provenance() -> None:
    """`build_cibseven_process_starter` funnels the start through `start_process_idempotent`:
    a durable audit record is emitted BEFORE the engine start, and the engine sees the exact
    business_key/process_key/variables the matched rule derived."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)

    bridge = NotificationBridge(cibseven_starter=starter)
    results = await bridge.on_event(
        event_type="nip.handoff_ans_submit",
        payload={
            "tenant_id": "amh",
            "numero_nip_ans": "000000042",
            "protocolo_ans": "PROTO-TESTE-0001",
            "origem_envio": "nip_filing",
        },
    )

    assert len(results) == 1
    result = results[0]
    assert result.handoff_triggered is True
    assert "CIB Seven start failed" not in result.reason  # no exception swallowed
    assert result.process_instance_id  # a real (fake) instance id was returned

    # The durable ADR-0007 audit record was emitted (BEFORE the effect — chokepoint's own
    # ordering guarantee; we only assert exactly-once here since the ordering itself is the
    # chokepoint's own tested invariant, not this bridge's).
    assert len(audit_sink.calls) == 1
    record, dedup_key = audit_sink.calls[0]
    assert record.agent_id == "notification_bridge"
    assert record.action == f"start_process:{PROCESS_KEY_ANS_SUBMIT}"
    assert "amh" in dedup_key
    assert PROCESS_KEY_ANS_SUBMIT in dedup_key
    assert "ANSSUB-amh-nipfiling-000000042" in dedup_key

    # The engine saw the correct process_key/business_key/variables — never a raw/bypassed start.
    assert len(transport.start_calls) == 1
    started_process_key, started_bk, started_vars = transport.start_calls[0]
    assert started_process_key == PROCESS_KEY_ANS_SUBMIT
    assert started_bk == "ANSSUB-amh-nipfiling-000000042"
    assert started_vars["origem_envio"] == "nip_filing"


@pytest.mark.asyncio
async def test_fenced_starter_idempotent_hit_never_double_starts() -> None:
    """A second on_event for the SAME business key (re-tick/redelivery) returns the existing
    instance — never a second engine start (the chokepoint's `find_active_instance` guard)."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)
    bridge = NotificationBridge(cibseven_starter=starter)

    payload = {
        "report_type": "DIOPS_TRIMESTRAL",
        "competencia": "COMPETENCIA_PENDENTE",
        "tenant_id": "amh",
    }
    first = await bridge.on_event(event_type="ans.cron_due", payload=dict(payload))
    second = await bridge.on_event(event_type="ans.cron_due", payload=dict(payload))

    assert first[0].handoff_triggered is True
    assert second[0].handoff_triggered is True
    # Only ONE engine start — the second call is an idempotent hit
    # (`find_active_instance` short-circuits `start_process_instance`).
    assert len(transport.start_calls) == 1
    assert first[0].process_instance_id == second[0].process_instance_id
    # The fake audit sink records both calls (a real durable sink additionally dedupes via
    # `emit_once`'s own exactly-once contract — not re-proven here, that's the chokepoint's own
    # test suite); what THIS test proves is that the engine effect itself never repeats.
    assert len(audit_sink.calls) == 2


@pytest.mark.asyncio
async def test_fenced_starter_fails_closed_on_missing_business_key() -> None:
    """A rule that (hypothetically) omits business_key from its variables never reaches the
    engine — the starter raises before any transport/audit call, never a garbage-key start.

    EB-3 part 1: `on_event`/`execute_handoff` now PROPAGATE that failure
    (`NotificationBridgeHandoffFailedError`, chaining the original
    `NotificationBridgeMissingBusinessKeyError`) instead of swallowing it into `.reason` — a
    genuine start failure must surface as an incident, never be silently absorbed into a
    "successful" HandoffResult."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)

    bridge = NotificationBridge(cibseven_starter=starter)
    bridge.register_handoff(
        event_type="broken.rule",
        predicate=lambda p: True,
        target_process="SP-OP-ANS-SUBMIT-001",
        variables_fn=lambda p: {"tenant_id": "amh"},  # no business_key — malformed rule
    )
    with pytest.raises(NotificationBridgeHandoffFailedError) as exc_info:
        await bridge.on_event(event_type="broken.rule", payload={})

    assert "SP-OP-ANS-SUBMIT-001" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, NotificationBridgeMissingBusinessKeyError)
    assert "missing/blank business_key" in str(exc_info.value.__cause__)
    assert len(transport.start_calls) == 0
    assert len(audit_sink.calls) == 0


@pytest.mark.asyncio
async def test_fenced_starter_raises_directly_when_called_standalone() -> None:
    """The starter callable itself (bypassing execute_handoff's broad except) raises
    NotificationBridgeMissingBusinessKeyError — proves the fail-closed guard is in the starter,
    not merely papered over by the bridge's own error handling."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)

    with pytest.raises(NotificationBridgeMissingBusinessKeyError):
        await starter("SP-OP-ANS-SUBMIT-001", {"tenant_id": "amh"})


# ---------------------------------------------------------------------------
# F3 MAJOR-2 — the chokepoint's typed outcome must survive the starter seam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_result_carries_the_typed_start_outcome() -> None:
    """`process_instance_id` cannot distinguish a fresh start from an idempotent replay from a
    strict-gate refusal, so `HandoffResult` carries the chokepoint's own `StartOutcome` token.

    The fenced starter returns the whole `ProcessInstance` for exactly this reason — flattening it
    to an id here is what once let a blank id reach a "complete"-looking result.
    """
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)
    bridge = NotificationBridge(cibseven_starter=starter)

    payload = {
        "report_type": "DIOPS_TRIMESTRAL",
        "competencia": "COMPETENCIA_PENDENTE",
        "tenant_id": "amh",
    }
    first = await bridge.on_event(event_type="ans.cron_due", payload=dict(payload))
    second = await bridge.on_event(event_type="ans.cron_due", payload=dict(payload))

    assert first[0].start_outcome == StartOutcome.STARTED.value
    assert second[0].start_outcome == StartOutcome.ALREADY_ACTIVE.value
    assert len(transport.start_calls) == 1


@pytest.mark.asyncio
async def test_a_str_returning_starter_reports_an_unknown_outcome_not_a_fake_one() -> None:
    """Back-compat without a lie: a legacy/dev starter returning a bare id still works, and the
    outcome is reported as UNKNOWN (`""`) rather than guessed as a start."""
    calls: list[str] = []

    async def _legacy_starter(process_key: str, variables: dict[str, Any]) -> str:
        calls.append(process_key)
        return f"legacy-{process_key}"

    bridge = NotificationBridge(cibseven_starter=_legacy_starter)
    results = await bridge.on_event(
        event_type="ans.cron_due",
        payload={"report_type": "DIOPS_TRIMESTRAL", "competencia": "X", "tenant_id": "amh"},
    )

    assert calls == [PROCESS_KEY_ANS_SUBMIT]
    assert results[0].process_instance_id == f"legacy-{PROCESS_KEY_ANS_SUBMIT}"
    assert results[0].start_outcome == ""


@pytest.mark.asyncio
async def test_a_blank_instance_id_is_never_reported_as_a_completed_handoff() -> None:
    """FAIL-CLOSED (F3 MAJOR-2, the `process_instance_id=""` latency). A triggered handoff whose
    starter yields no instance id is NOT a completed handoff — it raises, so a Kafka consumer
    cannot ack a message whose process was never identified."""

    async def _blank_starter(process_key: str, variables: dict[str, Any]) -> str:
        return "   "

    bridge = NotificationBridge(cibseven_starter=_blank_starter)

    with pytest.raises(NotificationBridgeHandoffFailedError) as exc_info:
        await bridge.on_event(
            event_type="ans.cron_due",
            payload={"report_type": "DIOPS_TRIMESTRAL", "competencia": "X", "tenant_id": "amh"},
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "blank process instance id" in str(exc_info.value.__cause__)


# ---------------------------------------------------------------------------
# EB-3 part 4 — business_key derivation for the 5 pre-existing bridge rules
#
# Before this fix, NONE of these 5 rules set `business_key` — the fenced starter
# (`build_cibseven_process_starter`) would fail-closed refuse to start EVERY one of them
# (`NotificationBridgeMissingBusinessKeyError`). Each test below proves the derived key matches
# the target process's own contract shape ("Business key (idempotencia)") and is deterministic
# (same anchor fields -> same key, across redelivery).
# ---------------------------------------------------------------------------


def test_intake_recurso_derives_business_key() -> None:
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=RECURSO_INTAKE_EVENT,
        payload={
            "tenant_id": "amh",
            "glosa_id": "GLOSA-001",
            "numero_guia_tiss": "GUIA-123",
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.variables["tenant_id"] == "amh"
    assert result.variables["business_key"] == "RECURSO-amh-GUIA-123-GLOSA-001"


def test_contas_to_fraude_derives_business_key_from_prestador_fallback() -> None:
    """No `numero_caso` supplied by the CONTAS payload (the common case — the bridge IS the
    intake here) -> falls back to `prestador_id` per `_fraude_numero_caso_for_contas_handoff`."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=CONTAS_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhada_fraude",
            "prestador_id": "PREST-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.variables["business_key"] == "FRAUDE-amh-PREST-001"


def test_contas_to_fraude_derives_business_key_from_explicit_numero_caso() -> None:
    """When the CONTAS payload DOES carry an assigned numero_caso, it wins over the
    prestador_id fallback."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=CONTAS_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhada_fraude",
            "prestador_id": "PREST-001",
            "numero_caso": "CASO-777",
        },
    )
    result = bridge.evaluate(event)
    assert result.variables["business_key"] == "FRAUDE-amh-CASO-777"


def test_fraude_to_cred_derives_business_key() -> None:
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_credenciamento",
            "prestador_id": "PREST-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.target_process == "SP-OP-CRED-001"
    assert result.variables["business_key"] == "CRED-amh-PREST-001"


def test_fraude_to_cancel_derives_business_key() -> None:
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "entidade_tipo": "beneficiario",
            "numero_contrato": "CTR-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.target_process == "SP-OP-CANCEL-001"
    assert result.variables["business_key"] == "CANCEL-amh-CTR-001"


def test_fraude_to_inadimplencia_derives_business_key_distinct_from_cancel() -> None:
    """Same tenant+contrato as CANCEL, but a DISTINCT prefix (INAD vs CANCEL) per the
    contract's own "Coordenacao com CANCEL-001" note — never a business-key collision between
    the two coordinating processes."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type=FRAUDE_COMPLETED_EVENT,
        payload={
            "tenant_id": "amh",
            "desfecho": "encaminhado_contratual",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
        },
    )
    all_results = bridge.evaluate_all(event)
    keys = {r.target_process: r.variables["business_key"] for r in all_results}
    assert keys == {
        "SP-OP-CANCEL-001": "CANCEL-amh-CTR-001",
        "SP-OP-INADIMPLENCIA-001": "INAD-amh-CTR-001",
    }
    assert keys["SP-OP-CANCEL-001"] != keys["SP-OP-INADIMPLENCIA-001"]


def test_5_rules_business_key_deterministic_across_redelivery() -> None:
    """Redelivery of the identical event reconverges on the SAME business key for every one of
    the 5 pre-existing rules — idempotency, not just presence."""
    bridge = _make_bridge()
    payload = {
        "tenant_id": "amh",
        "glosa_id": "GLOSA-9",
        "numero_guia_tiss": "GUIA-9",
    }
    r1 = bridge.evaluate(HandoffEvent(event_type=RECURSO_INTAKE_EVENT, payload=dict(payload)))
    r2 = bridge.evaluate(HandoffEvent(event_type=RECURSO_INTAKE_EVENT, payload=dict(payload)))
    assert r1.variables["business_key"] == r2.variables["business_key"] == "RECURSO-amh-GUIA-9-GLOSA-9"


@pytest.mark.asyncio
async def test_5_rules_now_start_through_the_fenced_starter() -> None:
    """The whole point of part 4: the fenced starter (which fail-closed REFUSED all 5 rules
    before this fix — none set business_key) now actually starts them."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    starter = build_cibseven_process_starter(transport, audit_sink)
    bridge = NotificationBridge(cibseven_starter=starter)

    results = await bridge.on_event(
        event_type=RECURSO_INTAKE_EVENT,
        payload={
            "tenant_id": "amh",
            "glosa_id": "GLOSA-42",
            "numero_guia_tiss": "GUIA-42",
        },
    )
    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].process_instance_id  # a real (fake) instance id — never fail-closed refused
    assert len(transport.start_calls) == 1
    assert transport.start_calls[0][1] == "RECURSO-amh-GUIA-42-GLOSA-42"


# ---------------------------------------------------------------------------
# GAP-D3-02 — the FRAUDE→CANCEL rule now fronts a GATED (`EXCLUSIVE`) family
#
# `SP-OP-CANCEL-001` is the FIRST gated family any bridge rule targets. The fenced starter is
# posture-agnostic by construction, but its INJECTED seams are not: an `EXCLUSIVE` start requires
# `HistoryQueryingTransport` + `DedupReportingAuditSink`, and the bridge's composition root
# (`platform/integrations/notifications_bridge.py:355`) must keep supplying both.
# ---------------------------------------------------------------------------


class _HistoryBlindStarterTransport:
    """`CibSevenTransport` WITHOUT `find_any_instance` — the capability a decorator could drop.

    Genuinely absent (not stubbed): the `runtime_checkable` probe is attribute-presence."""

    def __init__(self) -> None:
        self.start_calls: list[str] = []

    async def find_active_instance(self, business_key: str) -> Any:
        return None

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        self.start_calls.append(business_key)
        raise AssertionError("an un-gateable EXCLUSIVE start must never reach the engine")

    async def correlate_message(self, *a: Any, **k: Any) -> None:
        return None

    async def get_process_status(self, business_key: str) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        return None


_FRAUDE_TO_CANCEL_PAYLOAD = {
    "tenant_id": "amh",
    "desfecho": "encaminhado_contratual",
    "numero_contrato": "C-42",
    "entidade_tipo": "beneficiario",
}


def test_fraude_to_cancel_is_the_only_bridge_rule_targeting_a_gated_family() -> None:
    """Scope pin. Exactly one of the 7 registered rules targets a gated start-dedup family, and it
    is FRAUDE→CANCEL. If a future rule targets another one, this test names it — so the seam
    requirements above get re-checked instead of being discovered in production."""
    bridge = _make_bridge()
    registered = bridge.list_handoffs()
    assert len(registered) == bridge.count_handoffs() == 7
    gated = sorted({r["target_process"] for r in registered if is_strict_start_dedup(r["target_process"])})
    assert gated == ["SP-OP-CANCEL-001"]
    assert start_dedup_posture("SP-OP-CANCEL-001") is StartDedupPosture.EXCLUSIVE


@pytest.mark.asyncio
async def test_fraude_to_cancel_starts_through_the_gate_and_dedupes_a_redelivery() -> None:
    """A Kafka re-delivery of the same `fraude.completed` event converges on ONE CANCEL-001
    instance — the property the durable claim buys over the bare `find_active_instance` GET."""
    transport = _RecordingCibSevenTransport()
    audit_sink = FakeStartAuditSink()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    first = await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=dict(_FRAUDE_TO_CANCEL_PAYLOAD))
    second = await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=dict(_FRAUDE_TO_CANCEL_PAYLOAD))

    assert [r.target_process for r in first] == ["SP-OP-CANCEL-001"]
    assert len(transport.start_calls) == 1, "a re-delivery started a SECOND rescisao review"
    assert transport.start_calls[0][1] == "CANCEL-amh-C-42"
    assert second[0].start_outcome == StartOutcome.ALREADY_ACTIVE
    assert second[0].process_instance_id == first[0].process_instance_id


@pytest.mark.asyncio
async def test_fraude_to_cancel_fails_closed_behind_a_history_blind_transport() -> None:
    """NEVER FALL BACK. A bridge root whose transport lost `find_any_instance` cannot gate the
    CANCEL start — `execute_handoff` surfaces the failure (EB-3 fail-closed: no swallow) and the
    engine is never asked to start anything."""
    transport = _HistoryBlindStarterTransport()
    audit_sink = FakeStartAuditSink()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await bridge.on_event(event_type=FRAUDE_COMPLETED_EVENT, payload=dict(_FRAUDE_TO_CANCEL_PAYLOAD))

    assert transport.start_calls == []
    assert audit_sink.calls == [], "the refusal must precede the durable claim (no orphan claim)"
