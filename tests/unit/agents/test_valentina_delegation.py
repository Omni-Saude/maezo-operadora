"""Unit tests for `agents.valentina.delegation` — the inbound `care.stratify`/`care.enroll` edge
(VAL-01).

Mirrors `tests/unit/agents/test_fernando_delegation.py` (engine-free, PG-free — `make_valentina_
handler` runs the REAL `valentina.graph.build(config)` graph against fakes, no I/O). The origin
side (`delegate_care_task`) is NOT called from `tools/workers/programa.py` (see
`agents/valentina/delegation.py`'s module docstring — that call site is an owner decision outside
this work package); these tests cover the TARGET side end to end plus the origin helpers in
isolation.

The consent tests are the load-bearing ones: this seam RELAYS worker-resolved consent FACTS and
never decides consent — the verdict is `graph.consent_gate`'s, and the binding chokepoint is the
engine worker `operadora.programa.check_consent` (`ERR_PROGRAMA_NO_CONSENT`).
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.valentina.delegation import (
    ORIGIN_WORKER,
    TARGET_AGENT,
    TASK_TYPE_CARE_ENROLL,
    TASK_TYPE_CARE_STRATIFY,
    TASK_TYPES,
    build_care_envelope,
    care_task_id,
    make_valentina_handler,
    state_from_envelope,
)
from maezo.agents.valentina.graph import (
    _CALLER_INPUT_FIELDS,
    CONSENT_SCOPE_PROGRAMA,
    _business_key,
    build,
)
from maezo.runtime.start_outcome import StartProcessFailedError
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# Free text / clinical content planted in `case_meta`: none may ride the seam (ADR-0006, Zona
# PHI — the risk BAND is a class token; a diagnosis or a narrative is not).
_PHI_PLANTS: dict[str, Any] = {
    "narrativa_clinica": "paciente diabetico tipo 2 com HbA1c 9.2 em 2026-06",
    "cid10": "E11",
    "diagnostico": "diabetes mellitus",
}

_CASE_META: dict[str, Any] = {
    "gatilho": "estratificacao_populacional",
    "proactive_trigger_ref": "trigger://pop-2026-07",
    "consent_scope": CONSENT_SCOPE_PROGRAMA,
    "consent_event_ref": "consent://c1",
    "risco_estratificado": "ALTO",
    "patient_summary_ref": "Patient/b1",
    "consentimento_ativo": True,
    "consent_checked": True,
    "consent_revoked": False,
    "elegibilidade_criterios_atendidos": True,
    "criterio_alta_aparente": False,
    **_PHI_PLANTS,
}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["plano de cuidado sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


def _envelope(task_type: str = TASK_TYPE_CARE_STRATIFY, case_meta: dict[str, Any] | None = None) -> Any:
    return build_care_envelope(
        tenant="amh",
        task_type=task_type,
        programa_id="p1",
        beneficiario_pseudo_id="b1",
        ciclo="c1",
        case_meta=case_meta if case_meta is not None else _CASE_META,
    )


def _dmn_programa() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("programa_routing", [{"elegivel_programa": "ELEGIVEL", "motivo": "criterios atendidos"}])
    dmn.register(
        "programa_sla",
        [{"sla_decisao": "P7D", "sla_alerta": "P5D", "fonte": "politica clinica interna (DRAFT)"}],
    )
    return dmn


def _handler(dmn: FakeDmnTransport, *, cibseven: Any = None) -> Any:
    return make_valentina_handler(
        _FakeInference(),
        dmn=dmn,
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


# --- task types / task_id / envelope builder ----------------------------------------------------


def test_task_types_match_the_agent_card_contract() -> None:
    assert {"care.stratify", "care.enroll"} == TASK_TYPES


def test_task_id_is_the_process_business_key() -> None:
    """MUTATION PROBE (dispatcher Guard 4): a `task_id` that is not
    `PROG-{tenant}-{programa}-{benef}-{ciclo}` breaks idempotent replay."""
    assert (
        care_task_id("amh", programa_id="p1", beneficiario_pseudo_id="b1", ciclo="c1") == "PROG-amh-p1-b1-c1"
    )


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_envelope_contract_matches_valentina_card(task_type: str) -> None:
    envelope = _envelope(task_type)
    assert envelope.task_type == task_type
    assert envelope.origin == ORIGIN_WORKER == "programa-worker"
    assert envelope.target == TARGET_AGENT == "valentina"
    assert envelope.task_id == "PROG-amh-p1-b1-c1"
    assert envelope.payload_ref == "process://PROG-amh-p1-b1-c1"
    assert envelope.delegation_chain == ("programa-worker", "valentina")


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_envelope_task_id_equals_the_graphs_own_business_key(task_type: str) -> None:
    envelope = _envelope(task_type)
    assert envelope.task_id == _business_key(state_from_envelope(envelope))


def test_unknown_task_type_fails_closed_at_the_envelope_builder() -> None:
    with pytest.raises(ValueError, match="accepted_task_types"):
        build_care_envelope(
            tenant="amh",
            task_type="care.discharge",
            programa_id="p1",
            beneficiario_pseudo_id="b1",
            ciclo="c1",
            case_meta=_CASE_META,
        )


# --- payload_meta: strict non-PHI allowlist -----------------------------------------------------


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_payload_meta_never_carries_clinical_content(task_type: str) -> None:
    """MUTATION PROBE (ADR-0006): adding `cid10`/`diagnostico`/`narrativa_clinica` to
    `_STRING_META_KEYS` turns this RED. The risk BAND is a class token and DOES ride; clinical
    content never does."""
    meta = dict(_envelope(task_type).payload_meta)
    for forbidden in _PHI_PLANTS:
        assert forbidden not in meta
    assert meta["risco_estratificado"] == "ALTO"
    assert all(isinstance(value, str) for value in meta.values())


def test_payload_meta_never_carries_the_consent_verdict() -> None:
    """THE structural line (module docstring §CONSENT IS NOT DECIDED HERE): a caller may assert
    consent FACTS, never the VERDICT. `consent_status` is absent from every allowlist tuple."""
    meta = dict(_envelope().payload_meta)
    assert "consent_status" not in meta
    assert meta["consentimento_ativo"] == "true"
    assert meta["consent_checked"] == "true"
    assert meta["consent_revoked"] == "false"


# --- state_from_envelope ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task_type", "task"), [(TASK_TYPE_CARE_STRATIFY, "stratify"), (TASK_TYPE_CARE_ENROLL, "enroll")]
)
def test_state_from_envelope_sets_the_task_from_the_task_type(task_type: str, task: str) -> None:
    """Never left to `graph._task`'s missing-key default (`stratify`) — a `care.enroll`
    delegation must not assemble a stratification dossier under an enroll audit trail."""
    state = state_from_envelope(_envelope(task_type))
    assert state["task"] == task
    assert state["tenant_id"] == "amh"
    assert state["canal"] == "a2a"
    assert set(state) <= _CALLER_INPUT_FIELDS


def test_state_from_envelope_normalizes_consent_facts_to_real_booleans() -> None:
    """`consent_gate` demands `is True` (exact booleans — a string `"true"` is AMBIGUOUS and
    therefore NO consent), so the `Mapping[str, str]` values MUST be normalized here."""
    state = state_from_envelope(_envelope())
    assert state["consentimento_ativo"] is True
    assert state["consent_checked"] is True
    assert state["consent_revoked"] is False


def test_state_from_envelope_can_never_seed_the_consent_verdict() -> None:
    """MUTATION PROBE (LGPD): a producer planting `consent_status="ativo"` — the PHI-gate bypass
    Valentina's graph calls "the highest-stakes plant" — cannot reach the state at all."""
    envelope = _envelope()
    planted = {**dict(envelope.payload_meta), "consent_status": "ativo"}
    object.__setattr__(envelope, "payload_meta", planted)
    state = state_from_envelope(envelope)
    assert "consent_status" not in state


def test_state_from_envelope_never_seeds_clinical_content() -> None:
    state = state_from_envelope(_envelope())
    assert "cid10" not in state
    assert "diagnostico" not in state
    assert state["risco_estratificado"] == "ALTO"


@pytest.mark.parametrize(
    "meta",
    [
        {"beneficiario_pseudo_id": "b1", "ciclo": "c1"},  # programa_id missing
        {"programa_id": "p1", "ciclo": "c1"},  # beneficiario missing
        {"programa_id": "p1", "beneficiario_pseudo_id": "b1"},  # ciclo missing
    ],
)
def test_state_from_envelope_fails_closed_without_the_business_key_identity(
    meta: dict[str, str],
) -> None:
    """The graph would fail-closed to the NEUTRAL no-consent terminal, which is safe but
    indistinguishable from a genuine consent absence — a producer bug must be loud HERE."""
    envelope = _envelope()
    object.__setattr__(envelope, "payload_meta", meta)
    with pytest.raises(ValueError, match="business key cannot be derived"):
        state_from_envelope(envelope)


def test_state_from_envelope_rejects_a_task_type_outside_the_card() -> None:
    envelope = _envelope()
    object.__setattr__(envelope, "task_type", "care.discharge")
    with pytest.raises(ValueError, match="accepted_task_types"):
        state_from_envelope(envelope)


def test_state_from_envelope_raises_if_a_meta_key_ever_escapes_the_input_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REG-06: the `unknown` guard at the end of `state_from_envelope` is a REAL, fail-closed
    check, not a `# pragma: no cover` promise — proven by making it fire for real instead of
    trusting the docstring's "structural, currently-unreachable" claim. `_STRING_META_KEYS` is
    monkeypatched to add a name outside `graph._CALLER_INPUT_FIELDS`, the same name is planted in
    `payload_meta` (the only way `raw` ever gains a key), and the guard is asserted to raise
    instead of silently widening the state.
    """
    import maezo.agents.valentina.delegation as delegation_module

    bogus_key = "reg06_mutation_probe_unknown_key"
    assert bogus_key not in _CALLER_INPUT_FIELDS
    monkeypatch.setattr(
        delegation_module, "_STRING_META_KEYS", (*delegation_module._STRING_META_KEYS, bogus_key)
    )
    envelope = _envelope()
    planted = {**dict(envelope.payload_meta), bogus_key: "valor-fora-do-limite"}
    object.__setattr__(envelope, "payload_meta", planted)

    with pytest.raises(ValueError, match="non-input keys for Valentina"):
        state_from_envelope(envelope)


# --- make_valentina_handler ---------------------------------------------------------------------


async def test_handler_with_active_consent_stratifies_and_starts_the_process() -> None:
    output = await _handler(_dmn_programa())(_envelope())

    assert output.output_ref == "process://PROG-amh-p1-b1-c1"
    assert output.meta["task"] == "stratify"
    assert output.meta["consent_status"] == "ativo"
    assert output.meta["process_started"] == "True"


async def test_handler_without_consent_produces_the_neutral_terminal_and_no_process() -> None:
    """LGPD chokepoint: the graph decides, not the handler. Facts saying "no consent" must
    produce zero PHI, zero DMN and zero process — never an adverse outcome."""
    case_meta = {**_CASE_META, "consentimento_ativo": False, "consent_checked": False}
    output = await _handler(_dmn_programa())(_envelope(TASK_TYPE_CARE_STRATIFY, case_meta))

    assert output.meta["consent_status"] == "ausente"
    assert output.meta["desfecho"] == "sem_consentimento"
    assert output.meta["process_started"] == "False"


async def test_handler_with_revoked_consent_stops_processing() -> None:
    case_meta = {**_CASE_META, "consent_revoked": True}
    output = await _handler(_dmn_programa())(_envelope(TASK_TYPE_CARE_STRATIFY, case_meta))

    assert output.meta["consent_status"] == "revogado"
    assert output.meta["process_started"] == "False"


async def test_handler_enroll_task_assembles_the_care_plan_dossier() -> None:
    output = await _handler(_dmn_programa())(_envelope(TASK_TYPE_CARE_ENROLL))
    assert output.meta["task"] == "enroll"


async def test_handler_never_forwards_the_dossier_content() -> None:
    """A CLOSED key set — adding `dossier`/`summary_facts` turns this RED. The adverse clinical
    decision fields (`decisao_programa`, ...) can never appear here (L0 hard)."""
    output = await _handler(_dmn_programa())(_envelope())

    assert set(output.meta) == {
        "task",
        "route",
        "desfecho",
        "motivo_humano",
        "grupo_destino",
        "consent_status",
        "process_started",
    }
    assert all(isinstance(v, str) and len(v) <= 40 for v in output.meta.values())
    assert "decisao_programa" not in output.meta
    assert "summary_facts" not in output.meta


async def test_handler_uses_the_real_fail_closed_build_contract() -> None:
    state = state_from_envelope(_envelope())
    direct = build(
        {
            "inference": _FakeInference(),
            "dmn": _dmn_programa(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        }
    ).compile()
    direct_result: dict[str, Any] = await direct.ainvoke(state)

    handler_output = await _handler(_dmn_programa())(_envelope())

    assert direct_result["consent_status"] == "ativo"
    assert handler_output.meta["consent_status"] == "ativo"


# --- RAF-02: um start falho NUNCA vira `HandlerOutput` de sucesso -------------------------------


class _FailingStartTransport(FakeCibSevenTransport):
    """O engine recusa o start, e CONTA quantas vezes o start foi tentado.

    A contagem e' o que prova que o turno REALMENTE chegou ao `start_process` (um teste que so'
    afirmasse a excecao passaria tambem se o grafo tivesse parado antes, por outro motivo).
    """

    def __init__(self) -> None:
        super().__init__()
        self.tentativas = 0

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.tentativas += 1
        raise CibSevenError(f"engine indisponivel (probe RAF-02): start de {process_key}")


async def test_handler_raises_instead_of_reporting_a_failed_start_as_success() -> None:
    """RAF-02 (gap `RAF-02-GUARD-MISSING-GUSTAVO-MARINA-VALENTINA`, 2026-09-05): com
    `start_failed=True` no estado devolvido pelo grafo, o handler levanta
    `StartProcessFailedError` em vez de devolver `HandlerOutput`.

    Antes da guarda este handler devolvia `HandlerOutput(output_ref="process://<business_key>",
    meta={"process_started": "False", ...})` — e, como `HandlerOutput` nao tem campo `success`,
    o `DelegationDispatcher` tratava esse retorno como sucesso: gravava o audit terminal
    `_DECISION_COMPLETED`, emitia o fato `COMPLETED` e SELAVA o resultado por `task_id`, tornando
    o falso sucesso IRRETENTAVEL. A cerca comum do dispatcher esta' em
    `tests/unit/agents/test_start_failure_a2a_handlers.py`; aqui se prova o lado deste agente.
    """
    transporte = _FailingStartTransport()
    with pytest.raises(StartProcessFailedError) as exc:
        await _handler(_dmn_programa(), cibseven=transporte)(_envelope())

    assert transporte.tentativas == 1, "o turno nem chegou ao `start_process` — teste vacuo"
    # A mensagem carrega os tokens de classe que um operador precisa (agente, processo, chave
    # idempotente) e NADA de PHI nem texto livre.
    texto = str(exc.value)
    assert "valentina" in texto and "SP-OP-PROGRAMA-001" in texto
    assert "E11" not in texto and "diabetes" not in texto
