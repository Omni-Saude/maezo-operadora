"""F-124 — a business key de SP-OP-INADIMPLENCIA-001 exige `numero_contrato` (DPO, #1082 opcao a).

Decisao do DPO de 31/10/2026: em plano sem `numero_contrato` o start de INAD e' RECUSADO (exige
saneamento cadastral); a chave NUNCA cai em `matricula_beneficiario` (nome `PHI_PROCESS_VARS`).
Onda 3: o comportamento novo vive atras da flag dark `MAEZO_INAD_CHAVE_EXIGE_CONTRATO` (OFF).

A recusa passa pelo padrao de recusa JA EXISTENTE do chokepoint de start (a mesma forma de
`require_start_tenant` / `CibSevenTenantError`): `start_process_idempotent(...,
require_contract_key=True)` levanta `CibSevenStartKeyError` (subclasse de `CibSevenError`) ANTES do
scrub, do claim duravel e do POST, e o grafo do Fernando cai no seu `except CibSevenError` de
sempre -> `start_failed_state` -> `notify_start_failure` (fail-notify ADR-0045). Nenhum caminho
ad-hoc novo.

Cada teste abaixo e' uma guarda negativa (PHI): sob a flag a matricula nao aparece na chave, nas
variaveis de start, no audit, no `task_id` A2A nem no log da recusa.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from maezo.agents.fernando.delegation import build_arrears_followup_envelope, make_fernando_handler
from maezo.agents.fernando.graph import (
    DMN_SLA,
    PROCESS_KEY,
    FernandoGraph,
    FernandoState,
    _business_key,
    build,
)
from maezo.runtime.start_outcome import DESFECHO_ERRO_INICIO_PROCESSO, StartProcessFailedError
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenError,
    CibSevenStartKeyError,
    FakeCibSevenTransport,
    require_contract_anchored_start_key,
    start_process_idempotent,
)
from maezo.tools.workers.base import INADIMPLENCIA_KEY_FAMILY, is_contract_anchored_key
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: Matricula sintetica, propositalmente reconhecivel: nenhum artefato da recusa pode carrega-la.
_MATRICULA = "MAT-SINTETICA-7731"
_CONTRATO = "000000042"


class _FakeInference:
    model_id = "fake"

    async def generate(self, prompt: str, **_kw: Any) -> str:
        return "texto sintetico"


class _FakeWhatsApp:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return {"ok": True}


def _state(**overrides: Any) -> FernandoState:
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "intencao": "analise_inadimplencia",
        "canal": "a2a",
        "numero_contrato": "",
        "matricula_beneficiario": _MATRICULA,
        "tipo_plano": "individual",
        "meses_inadimplencia": 2,
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(DMN_SLA, [{"sla_analise": "P10D", "sla_alerta": "P7D", "fonte_regulatoria": "RN 593"}])
    return dmn


def _graph(*, cibseven: FakeCibSevenTransport, audit_sink: FakeStartAuditSink, flag: bool) -> FernandoGraph:
    return FernandoGraph(
        inference=_FakeInference(),  # type: ignore[arg-type]
        dmn=_dmn(),
        cibseven=cibseven,
        audit_sink=audit_sink,
        whatsapp=_FakeWhatsApp(),
        chave_exige_contrato=flag,
    )


def _provenance() -> AgentDecisionProvenance:
    return AgentDecisionProvenance(agent_id="fernando", agent_version="t", tenant_id="amh", decision_basis={})


# --- 1. guarda do chokepoint ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("business_key", "variables"),
    [
        ("INAD-amh-", {"numero_contrato": ""}),  # chave degenerada
        ("INAD-amh-", {}),  # variavel ausente
        (f"INAD-amh-{_MATRICULA}", {"numero_contrato": ""}),  # fallback da matricula
        (f"INAD-amh-{_MATRICULA}", {"numero_contrato": _CONTRATO}),  # chave diverge do contrato
        ("INAD-amh-   ", {"numero_contrato": "   "}),  # so' espacos
        ("INAD-amh-None", {"numero_contrato": None}),  # None explicito
        (f"INAD-outro-{_CONTRATO}", {"numero_contrato": _CONTRATO}),  # tenant diverge
    ],
)
def test_guarda_recusa_chave_nao_ancorada_no_contrato(business_key: str, variables: dict[str, Any]) -> None:
    with pytest.raises(CibSevenStartKeyError) as info:
        require_contract_anchored_start_key(PROCESS_KEY, business_key, variables, tenant_id="amh")
    assert isinstance(info.value, CibSevenError)  # cai no `except CibSevenError` dos grafos
    assert _MATRICULA not in str(info.value)  # mensagem constante, nunca ecoa o valor


def test_guarda_aceita_a_forma_de_contrato() -> None:
    require_contract_anchored_start_key(
        PROCESS_KEY, f"INAD-amh-{_CONTRATO}", {"numero_contrato": _CONTRATO}, tenant_id="amh"
    )
    assert is_contract_anchored_key(INADIMPLENCIA_KEY_FAMILY, "amh", f"INAD-amh-{_CONTRATO}", _CONTRATO)


def test_guarda_pedida_para_processo_fora_do_mapa_e_erro_de_composicao() -> None:
    with pytest.raises(ValueError, match="nao se aplica"):
        require_contract_anchored_start_key("SP-OP-AUTH-001", "AUTH-amh-1", {}, tenant_id="amh")


async def test_chokepoint_recusa_antes_do_claim_e_do_post() -> None:
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(CibSevenStartKeyError):
        await start_process_idempotent(
            engine,
            process_key=PROCESS_KEY,
            business_key=f"INAD-amh-{_MATRICULA}",
            variables={"numero_contrato": ""},
            audit_sink=sink,
            provenance=_provenance(),
            require_contract_key=True,
        )
    assert sink.calls == []  # nenhuma linha de audit, nenhuma chave presa
    assert await engine.find_active_instance(f"INAD-amh-{_MATRICULA}") is None


async def test_chokepoint_sem_a_flag_mantem_o_comportamento_de_hoje() -> None:
    """Dark: OFF (o default) => o start com a chave legada continua acontecendo."""
    engine = FakeCibSevenTransport()
    instance = await start_process_idempotent(
        engine,
        process_key=PROCESS_KEY,
        business_key=f"INAD-amh-{_MATRICULA}",
        variables={"numero_contrato": _MATRICULA},
        audit_sink=FakeStartAuditSink(),
        provenance=_provenance(),
    )
    assert instance.already_existed is False


async def test_chokepoint_so_liga_com_true_de_verdade() -> None:
    """Um valor truthy que nao e' `True` (ex.: string "false" mal tipada) nunca liga a guarda."""
    engine = FakeCibSevenTransport()
    await start_process_idempotent(
        engine,
        process_key=PROCESS_KEY,
        business_key="INAD-amh-",
        variables={},
        audit_sink=FakeStartAuditSink(),
        provenance=_provenance(),
        require_contract_key="false",  # type: ignore[arg-type]
    )
    assert await engine.find_active_instance("INAD-amh-") is not None


# --- 2. Fernando --------------------------------------------------------------------------------


def test_business_key_sob_a_flag_nao_usa_matricula_nem_pseudo() -> None:
    state = _state(beneficiario_pseudo_id="pseudo-1")
    assert _business_key(state) == f"INAD-amh-{_MATRICULA}"  # OFF: legado byte-identico
    assert _business_key(state, exige_contrato=True) == "INAD-amh-"
    assert _business_key(_state(numero_contrato=_CONTRATO), exige_contrato=True) == f"INAD-amh-{_CONTRATO}"


async def test_receive_sob_a_flag_exige_o_numero_do_contrato() -> None:
    graph = _graph(cibseven=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink(), flag=True)
    result = await graph.receive(_state())
    assert result["error"] == "missing_contract_key"
    # OFF: a matricula ainda satisfaz o `receive` (comportamento de hoje).
    legado = _graph(cibseven=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink(), flag=False)
    assert not (await legado.receive(_state())).get("error")


async def test_start_sem_contrato_e_recusado_pelo_fail_notify_existente() -> None:
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    compiled = _graph(cibseven=engine, audit_sink=sink, flag=True).compile_graph().compile()

    with structlog.testing.capture_logs() as logs:
        result = await compiled.ainvoke(_state())

    assert result["start_failed"] is True
    assert result["process_started"] is False
    assert result["desfecho"] == DESFECHO_ERRO_INICIO_PROCESSO
    assert result["business_key"] == "INAD-amh-"
    assert sink.calls == []
    assert await engine.find_active_instance(f"INAD-amh-{_MATRICULA}") is None
    assert await engine.find_active_instance("INAD-amh-") is None
    (aviso,) = [e for e in logs if e.get("event") == "agent_process_start_failed"]
    assert aviso["process_key"] == PROCESS_KEY
    assert _MATRICULA not in repr(logs)
    assert _MATRICULA not in repr(result.get("error"))


async def test_start_com_contrato_sob_a_flag_inicia_e_nao_copia_a_matricula() -> None:
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    graph = _graph(cibseven=engine, audit_sink=sink, flag=True)
    result = await graph.compile_graph().compile().ainvoke(_state(numero_contrato=_CONTRATO))

    assert result["process_started"] is True
    assert result["business_key"] == f"INAD-amh-{_CONTRATO}"
    assert await engine.find_active_instance(f"INAD-amh-{_CONTRATO}") is not None
    assert graph._inadimplencia_variables(_state())["numero_contrato"] == ""
    legado = _graph(cibseven=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink(), flag=False)
    assert legado._inadimplencia_variables(_state())["numero_contrato"] == _MATRICULA  # OFF


def test_flag_do_grafo_exige_bool() -> None:
    with pytest.raises(TypeError, match="bool"):
        _graph(cibseven=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink(), flag="true")  # type: ignore[arg-type]


async def test_build_repassa_a_flag() -> None:
    engine = FakeCibSevenTransport()
    cfg = {
        "inference": _FakeInference(),
        "dmn": _dmn(),
        "cibseven": engine,
        "audit_sink": FakeStartAuditSink(),
        "whatsapp": _FakeWhatsApp(),
    }
    ligado = await build({**cfg, "inad_chave_exige_contrato": True}).compile().ainvoke(_state())
    assert ligado["start_failed"] is True
    desligado = await build(cfg).compile().ainvoke(_state())
    assert desligado["process_started"] is True  # OFF: o start legado sobre a matricula segue


async def test_handler_a2a_repassa_a_flag_e_falha_tipado() -> None:
    def _handler(flag: bool) -> Any:
        return make_fernando_handler(
            _FakeInference(),  # type: ignore[arg-type]
            dmn=_dmn(),
            cibseven=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
            whatsapp=_FakeWhatsApp(),
            inad_chave_exige_contrato=flag,
        )

    envelope = build_arrears_followup_envelope(
        tenant="amh",
        case_meta={"tipo_plano": "individual", "meses_inadimplencia": 2},
        matricula_beneficiario=_MATRICULA,
    )
    with pytest.raises(StartProcessFailedError) as info:
        await _handler(True)(envelope)
    assert _MATRICULA not in str(info.value)
    assert (await _handler(False)(envelope)).output_ref == f"process://INAD-amh-{_MATRICULA}"
