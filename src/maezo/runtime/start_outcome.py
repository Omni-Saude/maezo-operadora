"""Roteamento fail-notify de falha de start de processo — o padrao CC-01, no ponto mais baixo.

O DEFEITO QUE ESTE MODULO FECHA (CC-01 / RAF-02 / LUC-05 da auditoria de frota, 2026-09-04)
--------------------------------------------------------------------------------------------
Todo agente que abre um processo BPMN faz isto no seu no `start_process`::

    try:
        instance = await start_process_idempotent(...)
    except CibSevenError as exc:
        return {"process_started": False, "business_key": ..., "error": ...}

e, ate CC-01, a aresta seguinte era INCONDICIONAL para um terminal no-op (`complete`/`finalize`/
`END`). O `desfecho` de sucesso ja gravado a montante (`encaminhado_auditor`, `escalado_humano`,
...) SOBREVIVIA a falha. O resultado era um FATO FABRICADO — o estado afirma que o caso foi
encaminhado quando nenhuma instancia nasceu — e, porque o timer de SLA vive na instancia BPMN que
nao existe, o caso se perdia em silencio: sem prazo, sem alerta, sem retry.
`record_agent_error` tambem nunca disparava, porque o unico sitio que o chama e o `except` do
`ainvoke` — e a excecao havia sido engolida dentro do grafo.

O PADRAO (uma definicao, adotada por todos — nunca N copias)
------------------------------------------------------------
1. O `except CibSevenError` do no de start devolve :func:`start_failed_state`, que acrescenta o
   MARCADOR EXPLICITO ``start_failed=True`` ao dicionario que ja devolvia.
2. A aresta que sai de `start_process` vira CONDICIONAL, roteada por :func:`route_after_start`.
3. O destino de falha e o no `notify_start_failure` do grafo, que delega a
   :func:`notify_start_failure` deste modulo.

POR QUE UM MARCADOR PROPRIO, E NAO ``process_started is not True``
------------------------------------------------------------------
Porque `process_started=False` NAO significa "falhou" em todo agente. Ele tambem significa, de
forma legitima e desejada:
  * `andre`: o fluxo nao e `pagto_dossier`; ou a delegacao veio de DENTRO da instancia que ja
    roda (`ORIGIN_PAGTO_WORKER`); ou o chokepoint devolveu `ALREADY_COMPLETED` (uma instancia
    anterior ja liquidou a ordem — nada foi iniciado E nada deveria ser);
  * `lucas`: a rota e `respond_member` (a jornada informativa nunca abre processo).
Rotear esses casos para o alerta de falha tecnica seria um alarme falso — e um alarme falso e
como um `cibseven_start_claim_orphaned` real acaba ignorado. O marcador e escrito por EXATAMENTE
um caminho de codigo: o `except CibSevenError` do no de start.

DISCIPLINA DE ESCOPO (C3 — nenhuma regra de negocio nova)
----------------------------------------------------------
Isto e ROTEAMENTO DE FALHA TECNICA, nao decisao. Nada aqui decide cobertura, valor, negativa nem
prazo regulatorio (isso e DMN/BPMN/policy, nunca Python — AGENTS.md regra 5). O unico valor de
dominio que este modulo escreve e o desfecho TECNICO
:data:`DESFECHO_ERRO_INICIO_PROCESSO`, declarado spec-first na secao "Desfecho de agente" de cada
contrato SP-OP afetado.

DISCIPLINA PHI (ADR-0006/0017)
------------------------------
O log estruturado carrega SO tokens de classe: `agent_id`, `process_key`, `business_key` (a chave
idempotente que o proprio engine ja ve), `tenant_id` e o desfecho. A mensagem da excecao passa por
`redact_error_message` — um `CibSevenError` traz texto arbitrario de outro sistema, e esse texto ja
provou carregar identificador (CC-06/HEL-05).
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from maezo.platform import observability
from maezo.platform.error_types import AGENT_ERROR_TYPE_OUTRO
from maezo.runtime.error_text import redact_error_field
from maezo.runtime.turn_telemetry import emit_turn_desfecho
from maezo.tools.workers.phi_vars import redact_error_message

logger = structlog.get_logger(__name__)

#: Desfecho canonico de falha de start. Um agente NAO inventa outro literal: o valor tem de ser o
#: mesmo em todo lugar para que um alerta operacional consiga agrega-lo, e e este o literal
#: declarado nos contratos SP-OP (secao "Desfecho de agente: falha de start (CC-01)").
DESFECHO_ERRO_INICIO_PROCESSO: str = "erro_inicio_processo"

#: Nome do no de falha em TODO grafo. A cerca `tests/unit/agents/test_start_failure_routing.py`
#: exige literalmente este nome como destino da aresta condicional que sai de `start_process`.
NODE_NOTIFY_START_FAILURE: str = "notify_start_failure"

#: Rotulo do ramo de SUCESSO devolvido por :func:`route_after_start`. Cada grafo o mapeia para o
#: SEU proprio terminal (`complete`, `finalize`, `send_escalation_ack`, `END`), porque o nome do
#: terminal varia por agente enquanto o predicado de roteamento nao varia.
ROUTE_CONTINUE: str = "continue"

#: `error_type` (ALERT-COUNTER-LABELS / R-063) do turno contado por :func:`notify_start_failure`.
#: Este no' NAO tem objeto de excecao em escopo — quando ele roda, a excecao ja' foi convertida em
#: string por :func:`start_failed_state` (que e' chamada EXCLUSIVAMENTE de blocos
#: `except CibSevenError as exc` nos nos de `start_process`, cerca
#: `tests/unit/agents/test_start_failure_routing.py`). Colocar a excecao no `state` (um `TypedDict`
#: fechado que e' CHECKPOINTADO) para poder classifica-la aqui seria pior que o problema.
#: O valor e' o MESMO que `classify_agent_error_type(exc)` devolveria em todo caminho que chega
#: aqui: `CibSevenError` (e qualquer subclasse dela) NAO esta' em
#: `_AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS`, cuja busca e' por NOME EXATO de classe, sem caminhar a
#: MRO — logo cai sempre em `AGENT_ERROR_TYPE_OUTRO`.
START_FAILURE_ERROR_TYPE: str = AGENT_ERROR_TYPE_OUTRO

#: Chave de estado (OUTPUT-ONLY) que marca "o start foi TENTADO e FALHOU tecnicamente".
STATE_KEY_START_FAILED: str = "start_failed"


class StartProcessFailedError(RuntimeError):
    """O turno terminou em falha de start — um handler A2A NAO pode devolver sucesso (RAF-02).

    Levantada pelos handlers de delegacao (`agents/*/delegation.py`) DEPOIS que o grafo devolveu
    um estado marcado com ``start_failed``. A razao de ser uma EXCECAO, e nao um `HandlerOutput`
    com metadado de falha, e o dispatcher:

      * `a2a/dispatcher.py::DelegationDispatcher._execute` grava o audit terminal
        `_DECISION_COMPLETED` e emite o fato `COMPLETED` para TODO retorno normal do handler —
        `HandlerOutput` nao tem campo `success`, entao um retorno e, por construcao, um sucesso;
      * e o resultado e SELADO pela idempotencia por `task_id` (`_delegate_inflight` guarda
        `entry.result`; `_delegate_durable` chama `store.complete`), tornando o falso sucesso
        IRRETENTAVEL — uma reentrega do mesmo `task_id` devolve o sucesso fabricado sem reexecutar
        nada.

    NAO herda de `a2a.delegation.DelegationError` DE PROPOSITO: aquele ramo do dispatcher e
    tratado como uma REJEICAO terminal (`_DECISION_FAILED` + `DelegationResult.rejected`), que
    tambem sela o `task_id`. Uma indisponibilidade do engine e transitoria e TEM de continuar
    retentavel, entao esta excecao PROPAGA — o dispatcher nao a converte em resultado, nao grava
    audit terminal e nao cacheia. Mesmo caminho que `AuditPersistenceError` ja percorre hoje
    (`tests/unit/a2a/test_dispatcher.py`).
    """


def start_failed_state(*, business_key: str, error: str) -> dict[str, Any]:
    """O dicionario que o `except CibSevenError` do no de start devolve.

    Preserva EXATAMENTE as tres chaves que os nos ja devolviam (`process_started`,
    `business_key`, `error`) e acrescenta o marcador `start_failed`. E a UNICA fabrica do
    marcador — um grep por `start_failed=True` fora deste modulo e um desvio do padrao.

    INVARIANTE DO CAMPO `error` (LUC-06/NEW-01): a celula NUNCA carrega o `str()` cru de uma
    excecao — so' token de classe e texto ja' passado pela rede de identificadores. O call site
    monta o texto com `runtime/error_text.py::start_unavailable_error` (proibicao fixada pela
    cerca AST `tests/unit/agents/test_error_field_no_raw_exception.py`), e esta fabrica aplica
    `redact_error_field` como SEGUNDA linha — porque ela e' a unica fabrica do marcador, e um
    caminho futuro que escape da cerca ainda assim nao consegue persistir texto cru no
    checkpoint. A rede de identificadores e' idempotente sobre a saida do construtor (nada de PHI
    sobra para a segunda passada encontrar); o teto de comprimento NAO e' — ele pode cortar mais
    alguns caracteres de cauda quando o texto ja' esta perto do teto ou acima dele (ver o
    docstring de `redact_error_field`; nunca PHI). Um token de classe constante
    (`andre/graph.py::ERROR_START_PROCESS_ENGINE_UNAVAILABLE`), bem abaixo do teto, atravessa
    intacto.
    """
    return {
        "process_started": False,
        STATE_KEY_START_FAILED: True,
        "business_key": business_key,
        "error": redact_error_field(error),
    }


def route_after_start(state: dict[str, Any]) -> Literal["notify_start_failure", "continue"]:
    """Predicado da aresta condicional que sai de `start_process`, para TODO agente.

    Devolve o nome do no de falha quando (e SOMENTE quando) o marcador `start_failed` esta
    presente; caso contrario o rotulo do ramo de sucesso, que cada grafo mapeia para o seu
    proprio terminal. Nao le `process_started`: veja a secao "POR QUE UM MARCADOR PROPRIO" no
    docstring do modulo.
    """
    if state.get(STATE_KEY_START_FAILED) is True:
        return "notify_start_failure"
    return "continue"


def notify_start_failure(
    state: dict[str, Any],
    *,
    agent_id: str,
    process_key: str,
    extra: dict[str, Any] | None = None,
    route: str | None = None,
    motivo_categoria: str | None = None,
) -> dict[str, Any]:
    """Grava o desfecho de erro e ALERTA — o corpo do no `notify_start_failure` de todo grafo.

    Faz QUATRO coisas, nesta ordem:

    1. Conta o turno falho em `maezo_agent_errors_total`
       (`platform.observability.record_agent_error`) — o contador que a regra de alerta
       `MaezoAgentCrashLoop` le. Ate CC-01 nenhum contador subia nesta classe de falha, porque a
       excecao morria dentro do grafo e o `except` do `ainvoke` nunca via nada. O modulo
       `observability` e chamado por ATRIBUTO (nao por simbolo importado) para que um teste
       consiga espiona-lo com `monkeypatch.setattr`.
    2. Emite `agent_process_start_failed` com a business key — enquanto a instancia nao nasce, o
       timer de SLA do BPMN nao existe, e este evento e o substituto operacional do prazo.
    3. Devolve o desfecho tecnico, sobrescrevendo qualquer desfecho de sucesso gravado a montante
       (que e exatamente o fato fabricado que CC-01 descreve), mais o que o agente passar em
       `extra` (por exemplo `mensagem_enviada=False`, `ack_pending=True`).
    4. CC-09: emite UM `maezo_agent_desfecho_total{agent_id,desfecho="erro_inicio_processo",...}`
       via `turn_telemetry.emit_turn_desfecho` — o UNICO site de emissao do desfecho de falha de
       start para os 9 agentes que chamam este helper (todos exceto Beatriz, que nunca abre
       processo). Por padrao `route`/`motivo_categoria` sao lidos do `state` de ENTRADA (o que
       `assess`/`human_review`/`escalate` ja gravaram antes de `start_process` tentar e falhar) —
       CORRETO para os 8 agentes cujo estado usa a chave `route` (o valor de roteamento
       pre-falha sobrevive no label). `route=`/`motivo_categoria=` (F1, VERIFY-CC09) sao
       overrides OPCIONAIS e GENERICOS, so' repassados a `emit_turn_desfecho` quando nao-`None`
       — este modulo permanece agnostico de agente, nao le nenhum campo especifico de Helena.
       Existem para o unico agente cujo estado NAO tem `route` (Helena usa `response_kind`): o
       call site especifico de Helena (`helena/graph.py::_start_failure_outcome`) passa
       `route=RESPONSE_KIND_FALHA_TECNICA_START`, o literal que `_ROUTE_VOCAB["helena"]` ja
       declara para este caso — sem o override o `state.get("route")` generico devolve `None` e
       o label fica vazio, apesar do vocabulario ja aceitar o valor certo.
       `enviada` prefere o que `extra` acabou de sobrescrever (ex.: Lucas/Fernando zeram
       `mensagem_enviada` no proprio `extra`) e cai para o `state` quando `extra` nao o toca.

    `extra` existe porque a neutralizacao especifica varia por agente e o estado de cada agente e
    um `TypedDict` fechado: escrever `mensagem_enviada` num agente que nao tem esse campo seria
    uma chave estranha ao estado. O helper nunca adivinha o vocabulario do agente.
    """
    business_key = str(state.get("business_key") or state.get("escalation_business_key") or "")
    logger.error(
        "agent_process_start_failed",
        agent_id=agent_id,
        process_key=process_key,
        business_key=business_key,
        tenant_id=str(state.get("tenant_id") or ""),
        desfecho=DESFECHO_ERRO_INICIO_PROCESSO,
        # Texto arbitrario vindo do transport — passa pelo mesmo net de identificadores do
        # chokepoint (CC-06) antes de tocar um log.
        error=redact_error_message(str(state.get("error") or "")),
    )
    observability.record_agent_error(agent=agent_id, error_type=START_FAILURE_ERROR_TYPE)
    outcome: dict[str, Any] = {
        "desfecho": DESFECHO_ERRO_INICIO_PROCESSO,
        "process_started": False,
    }
    if extra:
        outcome.update(extra)
    overrides: dict[str, Any] = {}
    if route is not None:
        overrides["route"] = route
    if motivo_categoria is not None:
        overrides["motivo_categoria"] = motivo_categoria
    emit_turn_desfecho(
        state,
        agent_id=agent_id,
        desfecho=DESFECHO_ERRO_INICIO_PROCESSO,
        enviada=outcome.get("mensagem_enviada", state.get("mensagem_enviada")),
        **overrides,
    )
    return outcome
