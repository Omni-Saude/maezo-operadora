"""RAF-02: um start falho NUNCA vira `HandlerOutput` de sucesso — nem, pior, um sucesso SELADO.

O AGRAVANTE que separa RAF-02 de CC-01 puro. O handler A2A (`agents/rafael/delegation.py::
make_rafael_handler`) devolvia `HandlerOutput(output_ref=f"process://{business_key}", meta={...
"process_started": "False"})` mesmo quando o start havia falhado. `HandlerOutput` NAO tem campo
`success`, entao para `a2a/dispatcher.py::DelegationDispatcher._execute` todo retorno normal do
handler e sucesso: ele grava o audit terminal `_DECISION_COMPLETED`, emite o fato `COMPLETED` e
devolve `DelegationResult.ok`. Esse resultado e entao SELADO pela idempotencia por `task_id`
(`_delegate_inflight` guarda `entry.result`; `_delegate_durable` chama `store.complete`), o que
torna o falso sucesso IRRETENTAVEL: uma reentrega do mesmo `task_id` devolve o sucesso fabricado
sem reexecutar coisa alguma. E `cibseven_start_claim_orphaned` nao socorre — ele so cobre as
familias `gated`, e AUTH e NON_STRICT (D3-02).

A CORRECAO e uma excecao TIPADA (`runtime.start_outcome.StartProcessFailedError`) levantada pelo
handler quando o estado devolvido pelo grafo carrega o marcador `start_failed`. Ela NAO herda de
`a2a.delegation.DelegationError` de proposito: aquele ramo do dispatcher e uma REJEICAO terminal,
que tambem sela o `task_id`. Uma indisponibilidade de engine e transitoria e tem de continuar
retentavel — entao a excecao PROPAGA, exatamente como `AuditPersistenceError` ja propaga hoje.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.a2a import TOPIC_COMPLETED, TOPIC_REQUESTED
from maezo.agents.helena.delegation import build_auth_analysis_envelope
from maezo.agents.rafael.delegation import make_rafael_handler
from maezo.runtime.start_outcome import StartProcessFailedError
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import build_test_dispatcher, make_card

_CASE_META = {
    "beneficiario_pseudo_id": "pseudo-cc01-1",
    "prestador_id": "prestador-1",
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 500.0,
    "cid10": "Z00.0",
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": False,
    "rede_credenciada": True,
}


class _FakeInference:
    model_id = "claude-cc01-probe"

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return "dossie factual sintetico"


class _CountingFailingTransport(FakeCibSevenTransport):
    """O engine recusa o start, e CONTA quantas vezes o start foi de fato tentado.

    A contagem e o que prova a retentabilidade: se o dispatcher tivesse selado o resultado, a
    segunda entrega do mesmo `task_id` nao chegaria ao grafo e o contador ficaria em 1.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tentativas = 0

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.tentativas += 1
        raise CibSevenError(f"engine indisponivel (probe RAF-02): start de {process_key}")


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("auth_admissibility", [{"resultado": "SEGUE_ANALISE", "motivo": "test"}])
    dmn.register("auth_sla", [{"sla_analise": "P5D", "sla_alerta": "P3D", "fonte_regulatoria": "RN 395"}])
    dmn.register("auth_auto_approval", [{"recomendacao": "ANALISE_HUMANA", "motivo": "test"}])
    return dmn


def _envelope() -> Any:
    return build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-CC01-1",
        coverage_ref="fhir://Coverage/cc01-1",
        case_meta=_CASE_META,
    )


def _handler(transport: FakeCibSevenTransport) -> Any:
    return make_rafael_handler(
        _FakeInference(),
        dmn=_dmn(),
        cibseven=transport,
        audit_sink=FakeStartAuditSink(),
    )


async def test_handler_raises_typed_error_instead_of_returning_a_fabricated_success() -> None:
    """`CibSevenError` no start => `StartProcessFailedError`, nunca um `HandlerOutput`."""
    with pytest.raises(StartProcessFailedError) as exc:
        await _handler(_CountingFailingTransport())(_envelope())

    # A mensagem carrega os tokens de classe que um operador precisa (agente, processo, chave
    # idempotente) e NADA de PHI: nem o pseudo id do beneficiario, nem o CID.
    texto = str(exc.value)
    assert "rafael" in texto and "SP-OP-AUTH-001" in texto
    assert "Z00.0" not in texto and "pseudo-cc01-1" not in texto


async def test_handler_still_returns_success_when_the_start_works() -> None:
    """Simetria: sem falha de start, o contrato do handler e byte-a-byte o de antes."""
    output = await _handler(FakeCibSevenTransport())(_envelope())
    assert output.output_ref.startswith("process://AUTH-amh-")
    assert output.meta["process_started"] == "True"


async def test_dispatcher_neither_seals_nor_completes_a_failed_start() -> None:
    """O dispatcher NAO grava `_DECISION_COMPLETED`, NAO emite o fato `COMPLETED` e NAO cacheia.

    A prova de que nada foi selado e operacional, nao estrutural: a MESMA entrega (`task_id`
    identico, que `auth_task_id` deriva de tenant+guia) e reexecutada ate o start, em vez de
    devolver um resultado guardado.
    """
    transport = _CountingFailingTransport()
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": _handler(transport)}
    )

    envelope = _envelope()
    with pytest.raises(StartProcessFailedError):
        await dispatcher.delegate(envelope)

    assert transport.tentativas == 1
    # A admissao (REQUESTED/ALLOW) e legitima e permanece — o que nao pode existir e o TERMINAL.
    assert producer.topics() == [TOPIC_REQUESTED]
    assert TOPIC_COMPLETED not in producer.topics()
    decisoes = [record.decision for (record, _) in sink.emitted]
    assert "COMPLETED" not in decisoes, (
        "o dispatcher gravou um audit terminal COMPLETED para um processo que nunca nasceu "
        f"(decisoes: {decisoes})"
    )

    # REENTREGA do MESMO task_id: o handler roda de novo (nada foi selado por idempotencia).
    with pytest.raises(StartProcessFailedError):
        await dispatcher.delegate(_envelope())
    assert transport.tentativas == 2, (
        "a reentrega nao reexecutou o handler — o falso sucesso teria ficado irretentavel por "
        "task_id, que e exatamente o agravante RAF-02"
    )


async def test_dispatcher_still_completes_a_successful_start() -> None:
    """Simetria: com o engine de pe, o caminho terminal COMPLETED continua identico."""
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": _handler(FakeCibSevenTransport())}
    )
    result = await dispatcher.delegate(_envelope())
    assert result.success
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]
    assert "COMPLETED" in [record.decision for (record, _) in sink.emitted]


@pytest.mark.parametrize("agent_id", ["rafael", "carolina", "andre", "fernando"])
def test_every_live_delegation_handler_refuses_to_report_a_failed_start(agent_id: str) -> None:
    """Cerca duravel: todo handler A2A VIVO (o que roda um grafo por `ainvoke`) tem de levantar
    `StartProcessFailedError`. Um handler novo que devolva `HandlerOutput` sobre um estado
    marcado com `start_failed` reintroduz RAF-02 e quebra aqui.
    """
    import ast
    import importlib
    from pathlib import Path

    modulo = importlib.import_module(f"maezo.agents.{agent_id}.delegation")
    tree = ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "ainvoke"
        for n in ast.walk(tree)
    ), f"{agent_id}: este teste so vale para handlers que rodam um grafo"
    levantadas = {
        n.exc.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and isinstance(n.exc.func, ast.Name)
    }
    assert "StartProcessFailedError" in levantadas, (
        f"{agent_id}/delegation.py nao levanta StartProcessFailedError — um start falho voltaria "
        "ao dispatcher como sucesso e seria selado por task_id (RAF-02)"
    )
