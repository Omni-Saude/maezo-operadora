"""Texto do campo `error` de um grafo de agente — UMA definicao, nunca N copias (LUC-06/NEW-01).

O DEFEITO QUE ESTE MODULO FECHA (LUC-06 / NEW-01, auditoria de frota 2026-09-04; CC-10 fechou a
metade `notes` da mesma familia e deixou esta intacta)
-------------------------------------------------------------------------------------------------
Dezesseis sitios em `src/maezo/agents/*/graph.py` interpolavam o `str()` CRU da excecao ligada
dentro do campo de estado `error`/`dmn_error`, em duas familias uniformes::

    except CibSevenError as exc:
        return start_failed_state(business_key=key, error=f"start_process indisponivel: {exc}")

    except (DmnEvaluationError, DmnNoResultError) as exc:
        return {"error": f"DMN `{table}` indisponivel: {exc}"}

O `str()` de um `CibSevenError` e' TEXTO ARBITRARIO DE OUTRO SISTEMA, e ja' se provou que ele ecoa
identificador: a triagem viva reproduziu, em 7 dos 9 agentes,
``error == 'start_process indisponivel: engine unreachable at http://internal/cases/CPF-<cpf>'``.
O mesmo vale para a DMN, cujo `400` costuma devolver o payload submetido de volta
(``engine 400: payload echo [CPF <cpf>] for `<tabela>` ``).

POR QUE ISSO IMPORTA MESMO SEM VIRAR VARIAVEL DE PROCESSO
---------------------------------------------------------
Nenhum dos dois campos embarca no engine — `phi_vars.PHI_FREE_TEXT_VARS` documenta que `dmn_error`
"so' e' estado de grafo, nunca embarca", e quatro testes de agente asseguram que nem `error` nem
`dmn_error` aparecem em `_contract_variables`. A exposicao residual e' o CHECKPOINT: o grafo e'
compilado COM saver duravel na dispatch viva (`platform/webhooks/whatsapp/dispatch.py`), entao o
texto cru fica PERSISTIDO, e e' lido num turno POSTERIOR ao que o escreveu
(`helena/graph.py::_start_escalation` costura `state["error"]` no sufixo `[falha tecnica: ...]`).
Um campo persistido que aceita texto arbitrario de outro sistema e' exatamente a superficie que a
ADR-0006/0017 nao admite.

A POSTURA (a que helena ja' praticava sozinha)
-----------------------------------------------
TOKEN DE CLASSE + TEXTO REDIGIDO. A redacao NAO e' reimplementada aqui: os tres construtores
delegam a `maezo.tools.workers.phi_vars.redact_error_message`, o MESMO simbolo que
`start_outcome.notify_start_failure` ja' chamava no seu log e que helena inlinava nos seus dois
sitios — e que, por sua vez, delega a rede de identificadores a `redact_free_text`, a mesma rede
do chokepoint agente->engine (CC-06). Uma so' rede, uma so' definicao: as duas bordas nao podem
derivar.

`redact_error_message` preserva o nome da CLASSE como prefixo estavel (`"CibSevenError: ..."`),
entao a redacao NAO custa o diagnostico — ops continua sabendo QUE TIPO de falha ocorreu.

POR QUE UM MODULO PROPRIO EM `runtime/`
----------------------------------------
* NAO em `platform/error_types.py`: aquele modulo se declara FOLHA e nao importa NADA de `maezo`
  (a cerca `tests/unit/platform/test_alert_metrics_fence.py` mede isso num interpretador novo);
  importar `phi_vars` la' fecharia justamente o acoplamento que ele existe para impedir.
* NAO so' em `runtime/start_outcome.py`: aquele modulo e' o padrao de ROTEAMENTO da falha de start
  (CC-01). A indisponibilidade de uma DMN acontece em `assess`, sem nenhum `start_process`
  envolvido — pendura-la ali seria um desencontro de escopo.
* `runtime/` e' a camada que ja' hospeda os ajudantes COMPARTILHADOS por todos os grafos
  (`start_outcome.py`, `prompt_format.py`, `turn_telemetry.py`); este e' o irmao natural deles.

DISCIPLINA DE ESCOPO (C3 — nenhuma regra de negocio)
-----------------------------------------------------
Nada aqui decide cobertura, valor, negativa nem prazo. Sao textos de DIAGNOSTICO TECNICO; a rota
que cada agente toma diante da falha continua sendo dele (e continua fail-safe: humano).
"""

from __future__ import annotations

from maezo.tools.workers.phi_vars import redact_error_message

#: Prefixo canonico da falha de start. Um agente NAO inventa outro literal: os testes de sete
#: agentes asseriam `"start_process indisponivel" in result["error"]` antes deste modulo existir,
#: e um alerta operacional so' agrega a classe de falha se o prefixo for o mesmo em todo lugar.
START_PROCESS_INDISPONIVEL: str = "start_process indisponivel"


def start_unavailable_error(exc: BaseException, *, process_key: str | None = None) -> str:
    """Texto do campo `error` quando o start do processo falha tecnicamente.

    INVARIANTE: o resultado e' `<prefixo>: <NomeDaClasse>: <texto ja' redigido>` — NUNCA o
    `str(exc)` cru. O corpo passa por `phi_vars.redact_error_message`, que aplica a rede de
    identificadores de CC-06 (CPF/CNPJ separado ou colado, corridas de 11+ digitos, e-mail,
    telefone BR) e limita o comprimento; o nome da classe sobrevive como prefixo estavel para nao
    custar o diagnostico.

    `process_key` e' OPCIONAL e e' um TOKEN DE CLASSE (`SP-OP-*`, o identificador do processo
    BPMN), nunca PHI: existe porque `_template/graph.py` — o scaffold do contrato canonico, sem
    fluxo proprio — se identifica pela sua `PROCESS_KEY`, e essa informacao e' util a quem le o
    checkpoint de um agente derivado.
    """
    corpo = redact_error_message(exc)
    if process_key:
        return f"start de {process_key} indisponivel: {corpo}"
    return f"{START_PROCESS_INDISPONIVEL}: {corpo}"


def dmn_unavailable_error(table: str, exc: BaseException) -> str:
    """Texto do campo `error`/`dmn_error` quando a avaliacao de uma DMN falha.

    Mesma invariante de :func:`start_unavailable_error`. `table` e' o nome da decisao DMN — um
    token de classe do vocabulario declarado em `spec/processes/dmn/**`, nunca um valor de
    dominio — e por isso viaja verbatim: sem ele o diagnostico nao diz QUAL decisao caiu.
    """
    return f"DMN `{table}` indisponivel: {redact_error_message(exc)}"


def redact_error_field(error: str) -> str:
    """Backstop: redige um texto que ja' vem montado, a caminho do campo `error`.

    Existe para :func:`maezo.runtime.start_outcome.start_failed_state`, a UNICA fabrica do
    marcador `start_failed`. A cerca AST
    (`tests/unit/agents/test_error_field_no_raw_exception.py`) ja' proibe o call site montar texto
    cru; este e' a SEGUNDA linha, para o caso de um caminho futuro escapar dela.

    E' IDEMPOTENTE sobre a saida dos dois construtores acima (o texto redigido nao tem mais
    identificador para a rede encontrar, e o teto de comprimento e' o mesmo), entao aplica-lo por
    cima deles nao muda nada — e' de proposito: defesa em profundidade nao pode custar o
    diagnostico. Constantes de token de classe (ex.:
    `andre/graph.py::ERROR_START_PROCESS_ENGINE_UNAVAILABLE`) atravessam byte a byte.

    Recebe `str` (nao `BaseException`): quem tem o objeto da excecao em maos deve usar os
    construtores acima, que preservam o token de classe.
    """
    return redact_error_message(error)
