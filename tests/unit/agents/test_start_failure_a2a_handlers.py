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
    # A2A-RETRY-REEMITS-REQUESTED-FACT: o handler REEXECUTA (retentabilidade preservada acima),
    # mas o fato `requested` NAO deve ser reemitido para a mesma reentrega — uma unica delegacao
    # logica gera um unico `requested`, nao um por tentativa. Mutacao: remover o guard de
    # `_delegate_inflight`/`_execute` (`skip_requested_fact`) -> este assert vai para RED (2
    # ocorrencias de TOPIC_REQUESTED em vez de 1).
    assert producer.topics() == [TOPIC_REQUESTED], (
        f"reentrega do mesmo task_id reemitiu {TOPIC_REQUESTED!r} uma segunda vez "
        f"(topics={producer.topics()!r}) -- 2 fatos requested para 1 delegacao logica"
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


#: O conjunto GUARDADO: todo agente cujo `delegation.py` roda um grafo (`ainvoke`) E cujo
#: `graph.py` registra um no `start_process` — isto e', todo handler A2A capaz de devolver um
#: estado com `start_failed=True`. Curado a mao aqui e conferido contra um walk AST independente
#: por `test_the_guarded_set_is_exactly_the_handlers_whose_graph_starts_a_process` abaixo, de modo
#: que um agente novo (ou um que ganhe um no `start_process`) nao possa entrar calado.
#:
#: `beatriz` fica de fora POR ESTRUTURA, nao por esquecimento: seu grafo nao registra no
#: `start_process` nenhum (`agents/beatriz/graph.py` so' tem `receive`/`gather`/
#: `instruct_investigation`/`finalize`; o proprio `agents/beatriz/graph.py` declara
#: "never calls `start_process` (L0 structural — no conditional edge exists)"), entao
#: `start_failed` nunca pode aparecer no estado que o handler dela recebe e nao ha' o que guardar.
#: E' o mesmo motivo pelo qual ela e' pulada pela cerca estrutural irma
#: (`test_start_failure_routing.py::test_every_start_process_node_routes_to_notify_start_failure`).
#: `helena` fica de fora por outro motivo estrutural: ela ORIGINA delegacoes e seu `delegation.py`
#: nao roda grafo algum (zero chamadas a `ainvoke`).
_GUARDED_DELEGATION_AGENT_IDS = [
    "andre",
    "carolina",
    "fernando",
    "gustavo",
    "marina",
    "rafael",
    "valentina",
]


def _delegation_tree(agent_id: str) -> Any:
    import ast
    import importlib
    from pathlib import Path

    modulo = importlib.import_module(f"maezo.agents.{agent_id}.delegation")
    return ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))


@pytest.mark.parametrize("agent_id", _GUARDED_DELEGATION_AGENT_IDS)
def test_every_live_delegation_handler_refuses_to_report_a_failed_start(agent_id: str) -> None:
    """Cerca duravel: todo handler A2A VIVO (o que roda um grafo por `ainvoke`) tem de levantar
    `StartProcessFailedError`. Um handler novo que devolva `HandlerOutput` sobre um estado
    marcado com `start_failed` reintroduz RAF-02 e quebra aqui.
    """
    import ast

    tree = _delegation_tree(agent_id)
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


def test_the_guarded_set_is_exactly_the_handlers_whose_graph_starts_a_process() -> None:
    """Inventario FECHADO — a cerca que impede o defeito de voltar por um agente novo.

    Sem ela, `_GUARDED_DELEGATION_AGENT_IDS` seria uma lista curada a mao: um handler novo (ou um
    agente que GANHE um no `start_process`) simplesmente nao seria parametrizado, e a lacuna
    RAF-02-GUARD-MISSING-GUSTAVO-MARINA-VALENTINA — quatro handlers guardados e tres nao, todos
    verdes — se reproduziria identica. O conjunto e' reapurado aqui de forma independente, por AST:
    `delegation.py` que chama `ainvoke` (roda um grafo) X `graph.py` que registra
    `add_node("start_process", ...)` (pode devolver `start_failed=True`).
    """
    import ast
    from pathlib import Path

    import maezo.agents as agents_pkg

    raiz = Path(agents_pkg.__file__).parent
    com_start_node: set[str] = set()
    for graph_path in sorted(raiz.glob("*/graph.py")):
        arvore = ast.parse(graph_path.read_text(encoding="utf-8"), filename=str(graph_path))
        nos = {
            arg.value
            for chamada in ast.walk(arvore)
            if isinstance(chamada, ast.Call)
            and isinstance(chamada.func, ast.Attribute)
            and chamada.func.attr == "add_node"
            for arg in chamada.args[:1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        }
        if "start_process" in nos:
            com_start_node.add(graph_path.parent.name)

    roda_grafo: set[str] = set()
    for delegation_path in sorted(raiz.glob("*/delegation.py")):
        arvore = ast.parse(delegation_path.read_text(encoding="utf-8"), filename=str(delegation_path))
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "ainvoke"
            for n in ast.walk(arvore)
        ):
            roda_grafo.add(delegation_path.parent.name)

    esperado = com_start_node & roda_grafo
    assert esperado == set(_GUARDED_DELEGATION_AGENT_IDS), (
        "o conjunto de handlers A2A que PODEM receber `start_failed=True` (delegation.py com "
        f"`ainvoke` X graph.py com no `start_process`) e' {sorted(esperado)}, mas "
        f"`_GUARDED_DELEGATION_AGENT_IDS` diz {sorted(_GUARDED_DELEGATION_AGENT_IDS)}. Um agente "
        "novo entrou ou saiu: acrescente/remova a guarda RAF-02 no handler dele E atualize esta "
        "lista — nunca so' a lista."
    )
    # Nao-vacuidade: a intersecao acima seria satisfeita por dois conjuntos vazios.
    assert "beatriz" in roda_grafo and "beatriz" not in com_start_node, (
        "beatriz deveria continuar sendo o caso de exclusao ESTRUTURAL (roda grafo, mas o grafo "
        f"nao tem no `start_process`); roda_grafo={sorted(roda_grafo)}, "
        f"com_start_node={sorted(com_start_node)}"
    )
    assert "helena" not in roda_grafo, (
        "helena ORIGINA delegacoes e nao roda grafo no proprio delegation.py — se passou a rodar, "
        "ela entra no escopo desta cerca e precisa da guarda RAF-02 tambem"
    )
