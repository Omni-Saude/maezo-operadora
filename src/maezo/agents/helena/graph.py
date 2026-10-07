"""Helena Moreira — Health Navigator Agent (Phase 0, AGJ-HELENA-TRIAGE, T1.11/defect B6).

Journey map (mirrors the v1 donor's `AGJ-HELENA-TRIAGE`, READ-ONLY reference
`Maezo-Healthcare-Plan src/maezo/agents/helena/graph.py`, adapted to v2's flatter seam set —
v2 has no `ToolRegistry`/PEP-gateway wiring for agent tool calls yet, so this graph's nodes call
the seams already on `main` DIRECTLY: `DmnTransport.evaluate` (ADR-0028/T1.5), `InferenceProvider.
generate(phi=True)` (ADR-0009/T1.7), and the new `CibSevenTransport` (ADR-0001, T1.11 —
`tools/mcp_cibseven/transport.py`)):

    receive -> classify -> {inform | schedule | escalate} -> respond
    resume                                               (GAP-XHITL-4: retomada pos-humano)

`resume` is the SECOND entry door (GAP-XHITL-4). It is taken ONLY when the turn was assembled by
`new_helena_resume_state` (the agent-resume consumer, after a human concluded SP-OP-ESCALATION-001
as `devolvido_agente`): it relays the human's instructions to the beneficiary through the SAME
output fences every other route uses, sends, and records its own desfecho. Human text is
UNTRUSTED content here exactly like the beneficiary's own message.

`classify` ALWAYS evaluates the red-flag DMN when the message describes a symptom (ADR-0012:
the LLM extracts + normalizes `sintoma_codigo`/`intensidade`/a population field; the DMN decides
`red_flag`/`conduta`/`prioridade` — the LLM never decides). `conduta=ESCALATE_*` or `red_flag`
true -> `escalate`, which starts SP-OP-ESCALATION-001 (idempotent, business key
`ESC-{tenant}-{conversation_id}`) via the CibSeven transport.

Six escalation triggers (each maps to a distinct `motivo_categoria`, contract
SP-OP-ESCALATION-001):
1. DMN red_flag=true                       -> red_flag_clinico (or risco_psicossocial if the
                                               mental_health table fired)
2. intent = clinical question              -> intencao_clinica (L0 hard — Helena never answers)
3. explicit request for a human            -> solicitacao_humano
4. intent = scheduling                     -> solicitacao_humano (GAP 9.2 — direct scheduling is
                                               OUT OF SCOPE this phase; the `schedule` node drafts
                                               its own honest, scheduling-specific reply, then
                                               hands off through the SAME SP-OP-ESCALATION-001
                                               start `escalate` uses, via the shared
                                               `_start_escalation` helper — the beneficiary is
                                               actually routed to a human, not just told one will
                                               follow up. See `spec/agents/helena/agent.yaml`'s
                                               `scope`/`out_of_scope` block for the phase-boundary
                                               declaration this trigger implements.)
5. technical failure                       -> falha_tecnica — DMN down/no-result, OR any
                                               classify-LLM failure: exception, unparseable
                                               JSON, or schema-invalid JSON (unknown intent,
                                               invalid population, non-allow-listed
                                               sintoma_codigo, out-of-domain intensidade).
                                               R1 cycle-1 blocking fix: pre-fix, a classify
                                               failure silently defaulted to intent=
                                               "information" -> inform (fail-OPEN,
                                               live-reproduced by the verifier); it now
                                               escalates, symmetric with every other failure
                                               path in this graph.
6. psychosocial risk in ANY message        -> risco_psicossocial (always evaluated, highest
                                               priority — never gated behind `intent`)

NOT a trigger, and deliberately so: `intent = greeting` (11/09/2026) ends in `inform` and opens
NOTHING. A "oi"/"bom dia" with no request attached is not work for anybody. Before this intent
existed the model had no slot for a greeting and reached for the nearest neighbour — "Opa" came
out `human_request` and opened a P3 human-queue item with a 4h deadline, while "Ola, bom dia"
came out `information` and resolved itself. A greeting carrying any request is NOT a greeting
(normalized in `classify`, never trusted to the prompt alone).

L0 HARD INVARIANT: Helena NEVER resolves a clinical concern herself. Every path ends in either
a human task (`escalate`/`schedule` -> SP-OP-ESCALATION-001's `UT_TratarEscalonamento`) or an
explicit, non-clinical response (`inform`) drafted by an LLM that is instructed to never give
clinical guidance (see `prompts.py`).

Since HEL-06/HEL-03 that invariant no longer rests on a prompt instruction alone. `inform` has a
DETERMINISTIC precondition (`_inform_recusado`), applied in two layers — inside `classify`
(`_rota_informativa`, which produces the honest motivo/severidade) and again on the conditional
edge (`_route`, the structural backstop). A reported `sintoma_codigo` can never end in an
automatic answer without a DMN verdict, whatever `intent` the classify output declared: that
declaration is the ONE thing a prompt injection can still choose inside the closed schema, and
this is what makes choosing it useless.

PHI discipline (ADR-0006/ADR-0017, T1.7's gate): every LLM call in this module passes
`phi=True` — inbound WhatsApp free text is treated as PHI-adjacent content even after the
webhook-edge phone-number pseudonymization, so it may ONLY be served by a `phi_capable`
provider (`phi_zone_mock` in dev; a real BR-resident endpoint is blocked(external), T1.7
charter). A non-PHI-capable provider raises `PhiZoneRoutingError` — this graph does NOT catch
that error specially: in `_classify_llm` it is a classify failure -> escalate `falha_tecnica`
(trigger 4, human takes over); in `_respond_llm`/`_resumo_contexto` (pure text drafting, the
route is already decided) it degrades to a safe canned text — which since 21/09/2026 means
`RESPOSTA_FALHA_DE_REDACAO`, an honest constant that promises NOTHING and goes through the same
output fences as a model draft (the previous canned text promised a human and bypassed them).
Neither path ever silently downgrades to a general-zone provider.

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- FHIR patient/coverage enrichment is NOT wired in this graph, and since the WP
  FHIR-TOOL-SURFACE-PARITY (NEW-09/GAP-TRIAGE-5) the SPEC no longer pretends otherwise:
  `mcp-fhir.read_patient_summary` and `mcp-fhir.search_coverage` were REMOVED from
  `spec/agents/helena/agent.yaml`, together with the `read_phi_data` autonomy action, because no
  node here has an `_fhir` field at all and helena is absent from
  `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT`. The contract SP-OP-ESCALATION-001 build
  steps in the T1.11 charter do not require the enrichment. What SURVIVES in the yaml is
  `mcp-fhir.read_coverage` alone, and NOT because anything uses it: helena is the only agent.yaml
  declaring that id, and `test_every_catalogued_tool_id_is_declared_by_some_agent` requires every
  `effect_classes.CATALOGUED_TOOL_IDS` entry to be declared by at least one agent — dropping it
  from the catalogue would mean editing the CODEOWNED
  `spec/policies/autonomy/action-approvals.yaml` (exact round-trip, fence §8.5 item 3). That
  residue is an OWNER-GATED pin, recorded as such in the yaml comment and in
  `tests/unit/gateway/test_fhir_tool_surface_parity.py::_DECLARED_WITHOUT_CALL_SITE` — not a
  capability this graph holds. The correction to v2's `FhirServer` claim: it is a generic HAPI
  client, but it IS reached through the PEP/ToolRegistry gateway for the agents that actually
  read (`gateway/seams/fhir.py::GatedFhirReader`); helena simply is not one of them.
- Episodic memory write (`mcp-memory.read_write`, ADR-0002) is NOT wired in this graph. The
  schema is NOT what is missing — migration `0001` creates `agent_memory`;
  `MemoryServer.store_episodic` refuses because its `(agent_id, event)` signature carries neither
  `tenant_id` nor `thread_id` (both `text NOT NULL`), which is GAP-DU-01-a, an owner decision. The semantic
  column that once sat in the same table was dropped by `0009_drop_pgvector` and ADR-0002 §3 is
  SUSPENDED pending a consumer (ADR-0047, DRAFT) — so there is no semantic write to follow up
  on at all.
- Free-text WhatsApp message content is NOT scanned for embedded PHI patterns (e.g. a
  beneficiary typing their own CPF into the message) before reaching the LLM — mitigated by the
  mandatory `phi=True` routing (content never reaches a general-zone cloud provider). Since HEL-06
  it IS demarcated: all three prompts carry the message inside a
  `runtime.prompt_format.render_untrusted_block("message_body", ...)` block (fixed preamble, a
  delimiter the text cannot forge, a length cap). That is a PROMPT boundary, not a PHI scrub — the
  content stays in-zone by design, and scrubbing it here would destroy what the classifier must
  read. What DOES
  exist since CC-06/HEL-05 is the EGRESS scrub: `_start_escalation` runs
  `phi_vars.redact_free_text` over the `resumo_contexto` it produces (and
  `redact_error_message` over the `[falha tecnica: ...]` suffix), and the shared start chokepoint
  `start_process_idempotent` runs the same net over every process-start variable. That is an
  identifier net (CPF/CNPJ/e-mail/BR phone/long digit runs), NOT a PHI classifier: clinical
  content in prose is still carried, in-zone, by design.
- No multi-turn conversation checkpointing WITHIN this module: this graph is compiled here
  without a checkpointer. The LIVE webhook path is NOT stateless, though:
  `platform/webhooks/whatsapp/dispatch.py::HelenaDispatcher.dispatch` compiles it WITH a durable
  LangGraph checkpointer (T4b, `runtime.checkpoint.Checkpointer`) and invokes it under a PHI-safe
  per-conversation thread config, so cross-turn state does persist in production. The remaining
  follow-up is ADR-0002's episodic/semantic memory layers, not the checkpointer.

A2A / DELEGATION (ADR-0003, CC-04/FENCE-PINS disclosure): THIS graph does not change to gain
that capability — `agents/helena/delegation.py` is a SIBLING module (not a node of the graph
above) that lets a harness/authorization journey ORIGINATE a prior-authorization-analysis
sub-task and delegate it to Rafael via `DelegationDispatcher.delegate`. Helena is not a
delegation TARGET in Phase 1 (`spec/agents/helena/agent.yaml`'s `accepted_task_types: []`
confirms it); `agents/helena/delegation.py` only originates, never receives, and this graph's own
routing (`receive -> classify -> {...} -> respond`) is unaffected either way.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Hashable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypedDict, cast, get_args

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.platform.observability import record_resposta_recusada, record_sintoma_fora_da_tabela
from maezo.runtime.caso_clinico import (
    MOTIVOS_DE_ALERTA_CLINICO,
    SUFIXO_CASO_CLINICO,
    chave_do_escalonamento,
)
from maezo.runtime.competencia import competencia_valida
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.error_text import (
    dmn_unavailable_error,
    start_unavailable_error,
)
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.prompt_format import render_untrusted_block
from maezo.runtime.start_outcome import notify_start_failure as emit_start_failure_notice
from maezo.runtime.turn_telemetry import emit_turn_desfecho
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    StartOutcome,
    start_process_idempotent,
)
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    first_row,
)
from maezo.tools.workers.phi_vars import redact_error_message, redact_free_text

from .consultas_plano import (
    ADENDO_CLASSIFY_CONSULTAS,
    CLASSIFY_CONSULTAS_ADENDO_VERSION,
    CONSULTA_PLANO_PROMPT_VERSION,
    CONSULTA_SUBTIPOS,
    INTENT_CONSULTA_PLANO,
    RESPOSTA_CONSULTA_SEM_IDENTIDADE,
    SUBTIPOS_REDIGIDOS,
    FonteDeFatosDoPlano,
    consulta_prompt,
    fatos_da_consulta,
    literais_dos_fatos,
    motivo_de_recusa_da_consulta,
    resposta_deterministica,
    texto_para_cerca,
)
from .historico import (
    CHAVE_RESPOSTA_COM_DADOS_DO_PLANO,
    HISTORICO_JANELA_HORAS,
    MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO,
    acrescentar_turno,
    historico_em_texto,
    historico_valido,
)
from .prompts import (
    ALLOWED_SINTOMA_CODIGOS,
    CLASSIFY_HISTORICO_VERSION,
    CLASSIFY_PROMPT_VERSION,
    CLASSIFY_PROMPT_VERSION_ROTEADOR,
    COLETA_PROMPT_VERSION,
    RECUSA_DE_SAIDA_VERSION,
    RECUSA_ESCALONAMENTO_JA_ABERTO,
    RECUSA_HANDOFF_SEM_MENCAO,
    RECUSA_NEGATIVA_CLINICA,
    RESPONSE_HISTORICO_VERSION,
    RESPONSE_PROMPT_VERSION,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    SINTOMA_CODIGOS_BY_POPULATION,
    SYSTEM_PROMPT_VERSION,
    classify_historico_adendo,
    classify_prompt,
    coleta_prompt,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
    response_historico_adendo,
    response_prompt,
    sintoma_em_palavras,
)
from .prompts import (
    _normalizar as _normalizar_texto,
)

logger = structlog.get_logger(__name__)

# --- Domain enums (mirror the SP-OP-ESCALATION-001 contract + DMN schema) -------------------

Intent = Literal[
    "symptom",
    "scheduling",
    "information",
    "human_request",
    "clinical_question",
    "greeting",
    "outside_channel",
    # NUMERO UNICO (onda e, ADR-0062): so' existe no dominio VALIDADO com o roteador ligado
    # (`_intents_validos`). Desligado, um `cobranca` vindo do modelo e' `invalid_intent`.
    "cobranca",
    # CONSULTA DO PLANO (DL de 07/10/2026): so' existe no dominio VALIDADO com as consultas da AMH
    # ligadas (`MAEZO_HELENA_CONSULTAS_AMH`). Desligado, e' `invalid_intent` como sempre.
    "consulta_plano",
]
Population = Literal["adult", "pediatric", "gestante", "mental_health", "none"]
#: `collect` (COLETA, 09/09/2026): o turno termina numa PERGUNTA ao beneficiario, nao numa
#: resposta — a mensagem descreveu um sintoma, a tabela de red flag NAO acusou bandeira, e a
#: tabela de suficiencia (`SUFFICIENCY_DMN_KEY`) disse que faltava um dado para decidir com
#: honestidade. Nunca substitui um escalonamento: `classify` so' chega a ele DEPOIS de a DMN de
#: red flag ter dito `false` — uma emergencia nao espera pergunta.
#: `handoff` (onda e do numero unico): a Helena PASSA a conversa ao Lucas — sem LLM, sem processo,
#: no maximo a frase fixa `FRASE_PASSAGEM_COBRANCA`. So' alcancavel com o roteador ligado.
ResponseKind = Literal["inform", "schedule", "escalate", "collect", "handoff"]
#: CC-01: o vocabulario do `response_kind` EMITIDO e um superconjunto do de ROTEAMENTO. Helena
#: pode responder um `falha_tecnica_start` (a resposta honesta quando a escalacao nao abriu), mas
#: nunca ROTEIA para ele — `next_kind` continua sendo `ResponseKind`, com os tres destinos que
#: `_route` sabe mapear. Alargar o tipo de roteamento aqui criaria um valor que nenhuma aresta
#: conhece; alargar so o de saida nao cria destino nenhum.
ResponseKindOut = Literal[
    "inform", "schedule", "escalate", "collect", "falha_tecnica_start", "retomada", "handoff"
]

# --- COLETA (passo 4 do fluxo de triagem — 09/09/2026) -------------------------------------
#
# O DEFEITO QUE ISTO FECHA, medido ao vivo em 09/09/2026 nas quatro tabelas de red flag:
# "estou com dor de cabeca" -> `sintoma_codigo=None` (nada casa na allowlist), `intensidade`
# nao dita -> `"desconhecida"` (:796). A regra fail-safe "sintoma nao mapeado" das tabelas exige
# intensidade GRAVE e nao dispara; o catch-all devolve `red_flag=false`; `_rota_informativa`
# libera a resposta automatica. Uma hemorragia lida como "sem urgencia" — sem que ninguem
# tenha decidido isso: a tabela respondeu com confianca sobre dados que nao tinha.
#
# A CORRECAO NAO E' NA TABELA DE RED FLAG. `tests/unit/spec/test_triage_redflag_shadow_candidates
# .py::test_an_explicitly_unknown_intensity_never_raises_a_red_flag` FIXA que `desconhecida`
# nunca levanta bandeira — decisao deliberada: intensidade que nao foi dita nao e' grave nem
# leve, e' DESCONHECIDA, e o que se faz com o desconhecido e' PERGUNTAR. Esta e' a peca que
# pergunta.
#
# QUEM DECIDE SE OS DADOS BASTAM E' UMA TABELA (`triage_sufficiency`), nao este codigo — pelo
# mesmo motivo das tabelas clinicas: escrita pelo negocio, ratificada por medico, versionada.
# A proposta da tabela esta' em `docs/design/triage-suficiencia-coleta.md` (DRAFT, nao
# ratificada, NAO implantada). Enquanto ela nao existir no motor, `coleta_enabled=True` falha
# FECHADO (DMN indisponivel -> `falha_tecnica` -> humano); com `coleta_enabled=False` (default)
# o grafo e' byte-a-byte o de antes. Nenhum conteudo clinico entra em vigor por este commit.
#
# TRES REGRAS que o desenho impoe independentemente do conteudo da tabela:
#   1. FALHA PARA O LADO SEGURO — so' pergunta quando a red flag ja' disse `false`; nunca
#      adivinha um codigo; tabela indisponivel escala.
#   2. TEM FIM — `COLETA_MAX_RODADAS` perguntas; depois disso escala (`MOTIVO_COLETA_ESGOTADA`).
#      Um beneficiario com dor no peito nao fica num interrogatorio.
#   3. NAO PERGUNTA O QUE JA' SABE — a tabela recebe o que ESTA' no estado; campo presente nao
#      vira pergunta.
SUFFICIENCY_DMN_KEY: str = "triage_sufficiency"
COLETA_MAX_RODADAS: int = 2

#: CLASSIFICADOR: quantas vezes o modelo e' chamado quando devolve JSON ilegivel ou fora do schema.
#: Medido em 02/10/2026 (bateria de 118 casos): ~2% das classificacoes falhavam por JSON ilegivel ou
#: schema invalido, e cada falha PERDIA a leitura de saude ("o boleto venceu e estou com dor no peito"
#: virava `falha_tecnica` P3 em vez de P1). A segunda chamada converte falha transitoria em acerto e
#: NUNCA aceita JSON invalido: o que o validador recusa continua recusado, e a falha da ultima
#: tentativa segue para `falha_tecnica` como antes (fail-closed). Excecao do provedor NAO e' repetida.
CLASSIFY_TENTATIVAS: int = 2
#: Decisao do documento do diretor (08/09/2026): "se duas rodadas de pergunta nao resolverem,
#: escalar como pedido de humano". `solicitacao_humano` roteia P3 / atendimento-humano / ack 4h
#: (`escalation_routing` r5). ALTERNATIVA NAO ADOTADA, registrada para a ratificacao: um sintoma
#: que nao se conseguiu caracterizar em duas rodadas talvez mereca `intencao_clinica`
#: (P2 / enfermagem / 30 min). Nao e' decisao de engenharia — e' do medico auditor.
MOTIVO_COLETA_ESGOTADA: MotivoCategoria = "solicitacao_humano"
#: Vereditos que a tabela de suficiencia pode emitir. Tudo fora disto e' tratado como falha
#: tecnica (fail-closed) — uma tabela que devolve um token desconhecido nao e' confiavel.
COLETA_VEREDITOS_PERGUNTA: frozenset[str] = frozenset(
    {"PERGUNTAR_INTENSIDADE", "PERGUNTAR_CARACTERIZACAO", "PERGUNTAR_IDADE", "PERGUNTAR_IDADE_GESTACIONAL"}
)
COLETA_VEREDITO_SUFICIENTE: str = "SUFICIENTE"
COLETA_VEREDITO_ESCALAR: str = "ESCALAR"


def _rodadas_de_coleta(valor: object) -> int:
    """Le `coleta_rodadas` sem o idioma `or 0` (cerca NONE-GUARDRAIL): um valor que NAO e' um
    inteiro nao-negativo nao foi escrito por este grafo — e a leitura fail-closed e' ESGOTADO
    (`COLETA_MAX_RODADAS`), que so' consegue escalar mais cedo. `bool` e' recusado explicitamente:
    `True` e' `int` em Python e "uma rodada" nao pode nascer de um flag."""
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return COLETA_MAX_RODADAS
    return min(valor, COLETA_MAX_RODADAS)


# Classify-output schema domains (classify-v1's own contract) — the R1 cycle-1 fail-closed
# validator (`_validate_extraction`) checks membership against these. Kept as explicit
# frozensets (not `typing.get_args` derivations) so the validation surface is self-contained
# and greppable next to the Literal types it mirrors.
_VALID_INTENTS: frozenset[str] = frozenset(
    {
        "symptom",
        "scheduling",
        "information",
        "human_request",
        "clinical_question",
        "greeting",
        "outside_channel",
        "cobranca",
        "consulta_plano",
    },
)
#: A intencao de cobranca (onda e). Membro de `_VALID_INTENTS` (o espelho do `Literal`), mas so'
#: aceita pelo validador com o roteador ligado — `_intents_validos` decide, por chamada.
INTENT_COBRANCA: str = "cobranca"
#: Dominio FECHADO de `cobranca_subtipo` (§2.4 do plano). O MESMO de
#: `platform/webhooks/whatsapp/roteamento.py::COBRANCA_SUBTIPOS` e do CHECK da migration 0017; o
#: grafo nao importa a plataforma, entao a copia e' conferida por
#: `tests/unit/agents/test_helena_passagem_cobranca.py`.
CobrancaSubtipo = Literal[
    "boleto_2via",
    "vencimento",
    "confirmacao_pagamento",
    "contestacao",
    "cobranca_recebida",
    "cancelamento",
    "outro",
]
_VALID_COBRANCA_SUBTIPOS: frozenset[str] = frozenset(get_args(CobrancaSubtipo))


def _intents_validos(*, cobranca_habilitada: bool, consultas_habilitadas: bool = False) -> frozenset[str]:
    """O dominio de `intent` que o validador aceita NESTE grafo. Sem o roteador, `cobranca` fica
    fora; sem as consultas do plano, `consulta_plano` fica fora — e o classify desligado continua
    byte a byte o de antes (`invalid_intent`)."""
    fora: set[str] = set()
    if not cobranca_habilitada:
        fora.add(INTENT_COBRANCA)
    if not consultas_habilitadas:
        fora.add(INTENT_CONSULTA_PLANO)
    return _VALID_INTENTS - fora


_VALID_POPULATIONS: frozenset[str] = frozenset({"adult", "pediatric", "gestante", "mental_health", "none"})
_VALID_INTENSIDADES: frozenset[str] = frozenset({"leve", "moderada", "grave", "desconhecida"})


def _em_dominio(valor: object, dominio: frozenset[str]) -> bool:
    """`valor` pertence a este vocabulario FECHADO? Recusa tudo que nao for `str`.

    POR QUE O `isinstance` NAO E' REDUNDANTE (21/09/2026, segunda rodada). Todo `x in frozenset`
    deste modulo le' valor de JSON de modelo ou de memoria checkpointada — valor NAO validado, que
    e' justamente o que estas fronteiras existem para julgar. Um valor nao-hashavel (`"intensidade":
    {"valor": "grave"}`, `"sintoma_codigo": ["febre", "dispneia"]` para quem relatou dois sintomas)
    levanta `TypeError: unhashable type` no proprio teste de pertinencia; `TypeError` esta' em
    `PROGRAMMING_ERRORS` e PROPAGA DE PROPOSITO, entao o no' morria alto: o beneficiario nao recebia
    nada, nenhuma escalacao nascia e nenhum desfecho era contado — o oposto exato da postura
    declarada ("schema-invalid JSON -> escalate `falha_tecnica`, NEVER a silent inform default").
    E' a mesma guarda que `_coerce_age` (que devolve `(False, None)` para lista/dict) ja fazia nos
    tres campos de IDADE do mesmo validador, agora nos campos de dominio fechado.

    UMA FUNCAO, e nao o `isinstance` repetido em cada sitio, porque a lista de sitios cresce: cada
    campo de vocabulario fechado que nascer neste modulo tem de passar por aqui.
    """
    return isinstance(valor, str) and valor in dominio


MotivoCategoria = Literal[
    "red_flag_clinico",
    "risco_psicossocial",
    "intencao_clinica",
    "solicitacao_humano",
    "falha_tecnica",
    "outro",
]
Severidade = Literal["grave", "moderada", "leve"]

PROCESS_KEY = "SP-OP-ESCALATION-001"

#: CC-01: a resposta HONESTA quando a escalacao nao pode ser aberta. Constante, nunca um draft de
#: LLM (ver `_respond_start_failure`). Nao promete atendente, nao promete prazo, nao cita
#: identificador nenhum — diz o que houve e o que o beneficiario pode fazer agora.
#: 21/09/2026: a primeira oracao tinha 21 palavras contra o limite de 20 da propria regua de
#: clareza da Helena (`EVL-HELENA-CLAREZA-*.clarity.max_words_per_sentence`) — quebrada em duas,
#: sem mudar o conteudo. Ver a nota igual em `RESPOSTA_HANDOFF_RECUSADA`.
RESPOSTA_FALHA_TECNICA_START: str = (
    "Nao consegui registrar seu atendimento agora por uma falha tecnica no nosso sistema. "
    "Por isso nenhum atendente foi acionado ainda. Por favor, envie sua mensagem novamente "
    "em alguns minutos. Se voce estiver passando por uma emergencia, procure o servico de "
    "emergencia mais proximo."
)

#: 21/09/2026 (segunda rodada, achado CRITICO): o texto quando a REDACAO nao aconteceu — o
#: provedor de inferencia caiu na chamada de `_respond_llm` (`except EXTERNAL_DEPENDENCY_FAILURES`).
#:
#: O QUE ELA SUBSTITUI, e por que aquilo era um defeito e nao um detalhe. O fallback anterior era
#: *"Recebemos sua mensagem. Um profissional humano vai continuar o atendimento em breve."* — uma
#: PROMESSA DE HUMANO, devolvida por um `return` que ficava ANTES do bloco de recusa. Nem a cerca
#: de saida nem a de canal a viam, e num turno `inform` (onde ninguem foi acionado, nenhuma fila
#: existe e ninguem vai ligar) ela e' exatamente o dano do `C1`: uma pessoa esperando um telefonema
#: que ninguem ia dar. Com a diferenca de que aqui nao havia modelo no meio — a frase era do
#: repositorio.
#:
#: ALCANCE, medido e nao suposto: uma queda TOTAL do provedor falha primeiro no `classify` (mesma
#: porta, `phi=True`) e o turno vira gatilho 4 -> escalate, que e' correto. Este caminho e' a falha
#: da SEGUNDA chamada depois de a primeira ter passado — timeout, 429 ou recusa de zona na redacao,
#: que e' uma requisicao separada e muito maior (prefixo cacheavel + bloco nao confiavel).
#:
#: O QUE ELA DIZ, e o que ela NAO diz: diz que a resposta nao foi preparada e pede a mensagem de
#: novo. Nao promete humano, nao promete prazo e nao cita canal nenhum — passa nas quatro cercas
#: (negativa clinica, capacidade, promessa de humano em qualquer rota COM `start_aconteceu=False`,
#: e canal nao confirmado), o que a torna segura por construcao em vez de por sorte. Nas rotas que
#: ABREM processo ela nao e' a ultima palavra: `respond` ve' que um humano foi acionado e que o
#: texto nao menciona o encaminhamento, e a troca por `RESPOSTA_HANDOFF_RECUSADA` acontece la'.
RESPOSTA_FALHA_DE_REDACAO: str = (
    "Recebemos sua mensagem. Nao consegui preparar a resposta agora por uma falha tecnica. "
    "Por favor, envie sua mensagem novamente em alguns minutos. Se voce estiver passando por "
    "uma emergencia, procure o servico de emergencia mais proximo."
)

#: RECUSA DE SAIDA (13/09/2026): o texto de handoff quando o proprio rascunho de `escalate` foi
#: barrado. CONSTANTE, nunca um segundo rascunho: um humano FOI acionado neste ponto (o processo
#: esta' sendo aberto), entao a promessa aqui e' verdadeira, e a frase nao contem nenhum dos
#: padroes proibidos — o que a torna a saida segura por construcao, nao por sorte do modelo.
#: 21/09/2026 — A FRASE FOI QUEBRADA EM TRES, e a razao e' uma medicao, nao estilo. A cerca TEXTO
#: x FATO tornou esta constante um substituto ALCANCAVEL na rota que os goldens de CLAREZA medem, e
#: la' ela reprovou: a primeira oracao tinha 22 palavras contra o limite de 20 declarado por
#: `EVL-HELENA-CLAREZA-0{1,3}.clarity.max_words_per_sentence`. Uma constante que a propria regua da
#: Helena reprova nao pode ser a saida honesta dela. O CONTEUDO nao mudou.
RESPOSTA_HANDOFF_RECUSADA: str = (
    "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe de saude. "
    "Um profissional vai dar continuidade ao seu atendimento. Se voce estiver passando por uma "
    "emergencia, procure o servico de emergencia mais proximo."
)

#: CRITICO 1 DA BATERIA DE 22/09/2026: o texto de um turno `inform` cujo RASCUNHO foi barrado por
#: um grupo que NAO e' a negativa clinica (promessa de humano, promessa de capacidade, canal nao
#: confirmado). A troca e' de TEXTO, e o turno segue `inform`.
#:
#: O QUE FOI MEDIDO, e por que a troca e' aqui. `C1` ("estou com dor de cabeca") e `B5` ("estou com
#: um pouco de dor de garganta desde ontem") tinham estado clinico IDENTICO — extracao sem sintoma,
#: DMN no catch-all, `red_flag=false` — e desfechos opostos: o C1 abriu chamado P3
#: `atendimento-humano` rotulado *falha tecnica* porque o rascunho do modelo dizia "a equipe entre
#: em contato" (`helena_resposta_recusada ... response_kind=inform`), e o B5 nao abriu nada. A fila
#: humana dependia da frase que o modelo sorteou, nao de criterio clinico nenhum.
#:
#: ELA NAO PROMETE, NAO CITA CANAL E NAO OPINA SOBRE O CORPO DE NINGUEM — passa nas quatro cercas
#: com `response_kind="inform"` e sem start, o que a torna segura por CONSTRUCAO (o corpus
#: `corpus_cercas_de_saida.json` fixa o veredito). E ela nao traz a abertura do cartao, para nao
#: acender `apresentacao_ja_feita` num turno que nao apresentou nada.
#: FORA DO CANAL (01/10/2026, DL-0052): a Helena e' navegadora de SAUDE — triagem, roteamento e
#: escalonamento. Qualquer outro assunto (cobranca, boleto, preco de plano, agendamento, reembolso,
#: cancelamento, conversa sem relacao com saude) recebe ESTE texto fixo: nao se oferece para
#: orientar o assunto, nao convida a seguir conversando e NAO abre processo. A unica porta aberta e'
#: a de sempre: quem pede uma pessoa cai no gatilho 3 de `classify`, em qualquer assunto.
RESPOSTA_FORA_DO_CANAL: str = (
    "Sou a Helena, navegadora de saúde. Neste canal eu cuido de sintomas e de encaminhar você "
    "para a equipe de saúde. Esse assunto não é tratado aqui. Se quiser falar com uma pessoa "
    "da equipe, é só me pedir. Se você estiver passando por uma emergência, procure o serviço "
    "de emergência mais próximo."
)

#: SINTOMA SEM BANDEIRA (01/10/2026): o texto fixo de um turno `inform` cujo sintoma a tabela de red
#: flag NAO marcou. TEXTO PROVISORIO — redacao de engenharia, aguarda aprovacao de produto e do dono
#: clinico (Plano G4.3 / G1.6).
#:
#: POR QUE NAO HA MODELO AQUI. Na bateria de 01/10 o modelo, que redigia esta resposta, (a) listou
#: sinais de alarme e limiares clinicos que nenhuma tabela nem medico aprovou ("febre por mais de
#: 48 horas", "aplicar gelo por 15 minutos a cada 2 horas"), (b) disse "a tabela de regras nao
#: identificou sinais de alerta" (jargao interno e, ditas pelo modelo, palavras que a cerca de
#: negativa clinica barra e que viravam P3 `falha_tecnica` por sorteio) e (c) reabriu o cartao de
#: apresentacao. Conduta clinica e' exatamente o que o prompt proibe e a cerca lexical nao pega:
#: ela so' barra a NEGACAO ("nao e grave"), nunca a afirmacao ("aplique gelo").
#:
#: O QUE ELA FAZ: nao avalia, nao lista sinal, nao da conduta, nao opina sobre o corpo de ninguem,
#: orienta procurar atendimento se piorar e deixa a porta de um humano aberta (gatilho 3 de
#: `classify`). Fala de "o que foi relatado", e nao de "o que voce sente", porque o relato pode ser
#: sobre um filho ou um pai. Cada frase tem ate' 20 palavras (regua de CLAREZA).
RESPOSTA_SINTOMA_SEM_ALERTA: str = (
    "Recebemos o seu relato. Este canal não avalia o que foi relatado e não substitui uma "
    "avaliação profissional. Se os sintomas persistirem ou piorarem, procure atendimento "
    "presencial. Se você estiver passando por uma emergência, procure o serviço de emergência "
    "mais próximo. Se quiser falar com uma pessoa da equipe, é só me pedir."
)

#: ESCALONAMENTO COM TEXTO FIXO POR PRIORIDADE (01/10/2026). TEXTOS PROVISORIOS — redacao de
#: engenharia, aguardam aprovacao de produto e do dono clinico (Plano G4.3 / G1.6).
#:
#: POR QUE NAO HA MODELO AQUI. Na bateria de 01/10 o modelo que redigia o aviso de encaminhamento
#: (a) declarou a gravidade ("Este e' um quadro grave", "Como a situacao e' grave"), (b) deu conduta
#: ("evite esforcos fisicos", "pressione o ferimento com um pano limpo"), (c) expôs o mecanismo e a
#: classificacao ("a tabela de regras identificou...", "classificada como leve, o contato pode levar
#: algumas horas") e (d) nomeou o diagnostico suspeito ("sindrome coronariana aguda", "suspeita de
#: AVC"). O prompt diz que a Helena NUNCA decide se um caso e' grave; a unica garantia disso e' nao
#: dar a ela a caneta.
#:
#: A PRIORIDADE ESCOLHE O TEXTO, e nao a falar dela: urgente (P1 clinico) manda procurar a emergencia
#: sem esperar o contato; psicossocial acolhe antes; o resto usa `RESPOSTA_HANDOFF_RECUSADA`. Nenhum
#: promete prazo (o relogio e' do processo, e o SLA do motor nao e' exato) nem canal (nao ha ligacao).
#: O resumo clinico vai ao ATENDENTE (`_resumo_contexto`), nao a pessoa. Cada frase tem ate' 20 palavras.
RESPOSTA_ESCALONAMENTO_URGENTE: str = (
    "Recebemos o seu relato e encaminhamos o seu caso para a nossa equipe de saúde. "
    "Um profissional vai dar continuidade ao seu atendimento. Se você estiver com sintomas agora, "
    "procure o serviço de emergência mais próximo sem esperar o nosso contato."
)
RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL: str = (
    "Sinto muito que você esteja passando por isso. Encaminhamos o seu caso para a nossa equipe de "
    "saúde, e um profissional vai dar continuidade ao seu atendimento. Se você estiver em risco "
    "agora, procure o serviço de emergência mais próximo sem esperar o nosso contato."
)


#: SAUDACAO E DESPEDIDA COM TEXTO FIXO (01/10/2026). TEXTOS PROVISORIOS — redacao de engenharia,
#: aguardam aprovacao de produto (Plano G4.3: "nao existe texto de boas-vindas aprovado").
#:
#: O DEFEITO, medido em 6 de 6 execucoes (A02, A03, A10): o modelo, que redigia a resposta a "oi",
#: "tudo bem?" e "obrigado, era so isso", REABRIA o cartao de apresentacao a cada turno, depois de
#: ja' ter se apresentado — o que o `response_prompt` proibe em letras maiusculas. Instrucao de
#: prompt nao segura uma regra que depende de memoria da conversa; `apresentacao_ja_feita` ja' e'
#: mantido pelo grafo (e' aceso quando o texto ENVIADO traz "Helena"), entao a decisao passa a ser
#: dele: primeira vez, o cartao (que diz o que o canal faz — o mesmo escopo do DL-0052); depois, uma
#: frase curta. A frase de abertura NAO diz que o canal "orienta duvidas administrativas": desde o
#: DL-0052 esse assunto recebe `RESPOSTA_FORA_DO_CANAL`, e o cartao antigo prometia o contrario.
RESPOSTA_SAUDACAO_ABERTURA: str = (
    "Sou a Helena, navegadora de saúde. Neste canal eu cuido de sintomas e de encaminhar você "
    "para a equipe de saúde. Como posso ajudar?"
)
RESPOSTA_SAUDACAO_CURTA: str = "Olá! Como posso te ajudar?"
RESPOSTA_DESPEDIDA: str = "De nada! Se precisar de algo, é só me chamar."

#: A DESPEDIDA E' DETECTADA POR VOCABULARIO FECHADO, e e' conservadora de proposito: a mensagem
#: inteira tem de ser agradecimento ou adeus. Um falso positivo engoliria um pedido ("obrigado, e
#: agora me liga?" tem tokens fora da lista e nao casa); um falso negativo so' devolve o turno ao
#: modelo, que e' o comportamento de antes. "ok" sozinho fica de fora: costuma responder a uma
#: pergunta da propria Helena, e "de nada" ali seria estranho.
_DESPEDIDA_NUCLEO = frozenset({"obrigado", "obrigada", "brigado", "brigada", "valeu", "vlw", "tchau", "xau"})
_DESPEDIDA_FRASES = ("era so isso", "so isso", "ate mais", "ate logo")
_DESPEDIDA_ACOMPANHANTES = frozenset(
    {
        *("muito", "era", "so", "isso", "ate", "mais", "logo", "tudo", "certo", "bom", "ta"),
        *("show", "beleza", "por", "pela", "ajuda", "atencao", "e", "de", "nada", "ok"),
    }
)


def _e_despedida(texto: str) -> bool:
    """`True` quando a mensagem INTEIRA e' agradecimento ou adeus (vocabulario fechado acima)."""
    plano = re.sub(r"[^\w\s]", " ", _normalizar_texto(texto or ""))
    palavras = plano.split()
    if not palavras or len(palavras) > 8:
        return False
    if not all(p in _DESPEDIDA_NUCLEO or p in _DESPEDIDA_ACOMPANHANTES for p in palavras):
        return False
    return any(p in _DESPEDIDA_NUCLEO for p in palavras) or any(
        f in " ".join(palavras) for f in _DESPEDIDA_FRASES
    )


def _texto_de_escalonamento(motivo: MotivoCategoria | None, severidade: Severidade | None) -> str:
    """O texto fixo do aviso de encaminhamento, escolhido pelo MOTIVO e pela severidade do processo
    que esta' sendo aberto (que derivam da tabela, nunca de redacao)."""
    if motivo == "risco_psicossocial":
        return RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL
    if motivo == "red_flag_clinico" and severidade == "grave":
        return RESPOSTA_ESCALONAMENTO_URGENTE
    return RESPOSTA_HANDOFF_RECUSADA


RESPOSTA_INFORM_RECUSADA: str = (
    "Recebemos sua mensagem. Nao consegui preparar uma resposta para ela agora. "
    "Se quiser, me conte com mais detalhes o que esta acontecendo. Se voce quiser falar com uma "
    "pessoa, escreva isso na proxima mensagem. Se voce estiver passando por uma emergencia, "
    "procure o servico de emergencia mais proximo."
)

#: RECUSA DE SAIDA (13/09/2026): o `error` do turno em que o texto redigido pelo modelo foi
#: BARRADO antes de sair. O sufixo e' o GRUPO do padrao (`negativa_clinica` /
#: `promessa_de_humano`), nunca o texto recusado — que e' output de modelo sobre a mensagem do
#: beneficiario e nao tem por que entrar num campo que viaja para o handoff.
ERRO_RESPOSTA_RECUSADA: str = "resposta recusada na saida"

# --- CERCA TEXTO x FATO (21/09/2026 — F1/F2 da bateria do diretor) --------------------------
#
# O ACHADO, e por que ele e' UM achado e nao dois. Dois casos da bateria, opostos na aparencia:
#
#   C1  "estou com dor de cabeca"      -> "Um profissional de saude entrara em contato (...)
#                                          Aguarde nosso contato."  E NENHUM processo novo.
#   E4  "quanto eu devo de mensalidade?" -> processo ABERTO (`solicitacao_humano`, P3, fila
#                                          `atendimento-humano`) e a resposta mandou a pessoa
#                                          procurar no aplicativo, sem citar o encaminhamento.
#
# Os dois sao a MESMA ausencia: nada no repositorio verificava que o que a Helena DIZ bate com o
# que ela FEZ. A cerca de saida (`prompts.py::motivo_de_recusa`) liberava a promessa de humano
# pela ROTA — `escalate`/`schedule` —, e a rota e' a INTENCAO do grafo, nao o fato.
#
# A CAUSA DO C1, e a razao de `start_failed` nao ter pego. A business key e'
# `ESC-{tenant}-{conversation_id}` e o canal de teste reusa uma faixa de 99 telefones; havia 27
# escalonamentos ABERTOS de baterias anteriores. Com a conversa colidindo numa dessas chaves,
# `start_process_idempotent` faz o que foi desenhado para fazer: `find_active_instance` encontra a
# instancia VIVA e a devolve com `start_outcome=ALREADY_ACTIVE`, sem abrir outra e SEM ERRO. Nada
# falhou — entao `except CibSevenError` nunca correu, `start_failed` ficou `False`, e o grafo,
# que ignorava `start_outcome`, anunciou um encaminhamento novo que nao existiu.
#
# O QUE MUDA: o estado passa a carregar o DESFECHO REAL do start, derivado do `StartOutcome` que
# o chokepoint ja' devolvia e que ninguem lia, e `respond` decide o texto pelo FATO.

#: O start de SP-OP-ESCALATION-001 nem foi tentado neste turno (rota `inform`/`collect`). Valor
#: NEUTRO do campo — e' por isso que ele nao pode ser confundido com `falhou`.
START_DESFECHO_NAO_TENTADO: str = "nao_tentado"
#: ESTE turno abriu a instancia (`StartOutcome.STARTED`). Um humano foi acionado AGORA.
START_DESFECHO_NOVO: str = "novo"
#: Ja' havia instancia VIVA para esta conversa (`StartOutcome.ALREADY_ACTIVE`): nada foi aberto, e
#: um humano esta' com o caso. A promessa de atendimento e' verdadeira; a de um encaminhamento
#: NOVO nao e'.
START_DESFECHO_JA_ATIVO: str = "ja_ativo"

#: Anotada no `resumo_contexto` do caso clinico PARALELO (DL-0072): quem abre o caso no portal precisa
#: saber que a conversa tem outro caso aberto, de outra classe, e que este e' o alerta clinico.
NOTA_CASO_CLINICO_PARALELO: str = "[alerta clinico novo; ha outro caso aberto nesta conversa]"
#: Uma instancia desta chave ja' RODOU e terminou (`StartOutcome.ALREADY_COMPLETED`): nada foi
#: aberto e ninguem esta' com o caso agora. Inalcancavel para esta agente hoje —
#: `SP-OP-ESCALATION-001` e' `NON_STRICT` em `_START_DEDUP_POLICY`, e so' as posturas GATED
#: produzem este veredito —, declarado assim mesmo porque a alternativa e' um `.get()` sem
#: resposta no dia em que a postura mudar. Fica FORA de `_START_DESFECHOS_COM_HUMANO`.
START_DESFECHO_JA_CONCLUIDO: str = "ja_concluido"
#: O chokepoint devolveu instancia sem veredito (`StartOutcome.UNREPORTED`). Tambem inalcancavel
#: por construcao — `start_process_idempotent` ESTAMPA um dos tres valores acima em todo retorno,
#: a partir do proprio fluxo de controle. Leitura fail-closed de um valor que nao se reconhece:
#: sem veredito nao se afirma que um humano foi acionado.
START_DESFECHO_NAO_REPORTADO: str = "nao_reportado"
#: O start foi tentado e FALHOU (`except CibSevenError`). Espelha `start_failed=True`, que segue
#: sendo o marcador que roteia o ramo de falha (CC-01); este campo diz a MESMA coisa no
#: vocabulario unico do desfecho de start, para que `respond` tenha UMA fonte para consultar.
START_DESFECHO_FALHOU: str = "falhou"

#: Traducao do veredito do chokepoint. DICIONARIO e nao `if`/`elif` de proposito: `StartOutcome`
#: e' um enum fechado, e um membro novo la' vira `START_DESFECHO_NAO_REPORTADO` aqui (fail-closed)
#: em vez de cair silenciosamente no ramo do sucesso.
_START_DESFECHO_POR_OUTCOME: dict[str, str] = {
    StartOutcome.STARTED.value: START_DESFECHO_NOVO,
    StartOutcome.ALREADY_ACTIVE.value: START_DESFECHO_JA_ATIVO,
    StartOutcome.ALREADY_COMPLETED.value: START_DESFECHO_JA_CONCLUIDO,
    StartOutcome.UNREPORTED.value: START_DESFECHO_NAO_REPORTADO,
}

#: Os UNICOS desfechos em que ha' um humano com o caso — e portanto os unicos em que a promessa de
#: atendimento humano e' verdadeira. Allowlist FECHADA: um desfecho novo (ou desconhecido) e'
#: recusado por OMISSAO, que e' o lado seguro.
_START_DESFECHOS_COM_HUMANO: frozenset[str] = frozenset({START_DESFECHO_NOVO, START_DESFECHO_JA_ATIVO})


def _start_desfecho_de(outcome: object) -> str:
    """O desfecho de start a partir do `StartOutcome` que o chokepoint devolveu.

    Le' o `.value` quando ha' um (o enum) e a propria string caso contrario, porque `StartOutcome`
    e' um `StrEnum` e um duplo de transporte pode devolver o literal. Valor desconhecido ->
    `START_DESFECHO_NAO_REPORTADO`, nunca o ramo do sucesso.
    """
    bruto = getattr(outcome, "value", outcome)
    return _START_DESFECHO_POR_OUTCOME.get(str(bruto), START_DESFECHO_NAO_REPORTADO)


def _texto_tem_negativa_clinica(texto: str) -> bool:
    """O texto afirma AUSENCIA de alerta clinico? (21/09/2026, terceira rodada)

    A pergunta existe para o no' `collect` decidir entre perguntar de novo e escalar, e ela e'
    feita ao TEXTO em vez de ao grupo da excecao — ver o comentario no proprio `except` de
    `collect`, que e' onde o porque importa.

    `response_kind` e' irrelevante para esta categoria (a negativa clinica e' proibida em TODA
    rota, com qualquer fato), e por isso a chamada usa `"collect"` sem que a rota influencie o
    resultado: a lista e' consultada antes de qualquer ramo por rota em `motivo_de_recusa`.
    """
    recusa = motivo_de_recusa(texto, "collect")
    return recusa is not None and recusa[0] == RECUSA_NEGATIVA_CLINICA


def _humano_acionado(estado: Mapping[str, Any]) -> bool:
    """HA' um humano com este caso? A pergunta que a cerca TEXTO x FATO responde.

    FAIL-CLOSED POR OMISSAO, e a escolha e' o coracao desta cerca: um estado que nao declara
    `start_desfecho` NAO afirma que um humano foi acionado. Nunca se le' `escalation_started` como
    substituto — era exatamente o sinal que o C1 tinha ligado (uma instancia existia, so' que nao
    era desta conversa-turno) enquanto a promessa era falsa.

    Quem escreve o campo e' `_start_escalation`, no MESMO turno em que `respond` o le' — os dois
    nos correm na mesma invocacao do grafo, entao nao existe janela em que o campo esteja atrasado.
    """
    return str(estado.get("start_desfecho") or "") in _START_DESFECHOS_COM_HUMANO


#: F1: o texto honesto quando a escalacao desta conversa JA estava aberta. CONSTANTE, e nunca um
#: segundo rascunho — pelo mesmo motivo de `RESPOSTA_FALHA_TECNICA_START`: pedir a um modelo para
#: "explicar que nada foi aberto porque ja' havia" convida a inventar um protocolo, um prazo ou um
#: encaminhamento novo. Ela nao cita identificador nenhum e nao promete prazo (o SLA vive na
#: instancia que ja' existe, e este turno nao sabe quanto dela ja' correu).
RESPOSTA_HANDOFF_JA_ABERTO: str = (
    "Recebemos sua mensagem. Seu atendimento com a nossa equipe de saude ja esta aberto e "
    "continua em andamento. Por isso nao abri outro atendimento. Se voce estiver passando por "
    "uma emergencia, procure o servico de emergencia mais proximo."
)

#: F1, o outro lado: o texto quando o rascunho prometeu um humano e NINGUEM foi acionado neste
#: turno. Nao ha como escalar daqui — `respond` e' o no' terminal —, entao o que resta e' nao
#: mentir: diz o que NAO aconteceu e como a pessoa consegue um humano na proxima mensagem (que e'
#: verdade: `intent=human_request` e' o gatilho 3 de `classify`).
RESPOSTA_SEM_ENCAMINHAMENTO: str = (
    "Recebemos sua mensagem. Nao abri atendimento com a nossa equipe neste momento. Se voce "
    "quiser falar com uma pessoa, escreva isso na proxima mensagem. Se voce estiver passando por "
    "uma emergencia, procure o servico de emergencia mais proximo."
)

#: F1: o `error` do turno em que o texto prometia um humano que este turno nao acionou. TOKEN DE
#: CLASSE, como todos os `error` deste modulo (LUC-06/NEW-01) — o padrao exato fica no log.
ERRO_PROMESSA_SEM_START: str = "promessa de humano sem start"
#: F2: o `error` do turno em que o start aconteceu e o texto nao mencionou o encaminhamento.
ERRO_HANDOFF_SEM_MENCAO: str = "handoff sem mencao ao encaminhamento"

#: F1, item 3 do diretor ("toda recusa/nao-start deixa rastro"): o desfecho do turno em que a
#: escalacao JA estava aberta. Token PROPRIO, e a razao e' a mesma de `DESFECHO_RESPOSTA_VAZIA`:
#: contado como `escalado_humano` este turno ficaria indistinguivel de um encaminhamento novo, e a
#: taxa de escalonamento da Helena contaria como trabalho criado uma fila que ela nao criou — que
#: e' precisamente o que tornou os 27 escalonamentos reusados da bateria INVISIVEIS. Precisa estar
#: em `turn_telemetry._DESFECHO_VOCAB["helena"]`, senao e' normalizado para `"outro"`.
DESFECHO_ESCALONAMENTO_JA_ABERTO: str = "escalonamento_ja_aberto"


class RespostaRecusadaError(RuntimeError):
    """O texto redigido violou `motivo_de_recusa` e NAO vai ser enviado.

    Excecao, e nao um valor de retorno, de proposito: `_respond_llm` devolve `str` para tres
    chamadores e um deles (`_start_escalation`) usa o texto no meio de uma sequencia que tambem
    inicia processo. Um sentinela de string obrigaria cada chamador a lembrar de compara-lo, e
    esquecer a comparacao enviaria o sentinela ao beneficiario. A excecao nao tem como ser
    ignorada por esquecimento.
    """

    def __init__(self, grupo: str, padrao: str, response_kind: str) -> None:
        super().__init__(f"resposta recusada ({grupo}) na rota {response_kind}")
        self.grupo = grupo
        self.padrao = padrao
        self.response_kind = response_kind


#: HEL-07: o `error` do turno em que o rascunho de resposta voltou VAZIO. Token de classe
#: (nao carrega o texto, que e' justamente o que nao existe), para um alerta poder distinguir
#: "nada foi enviado" de "enviei e o transporte falhou".
ERRO_RESPOSTA_VAZIA: str = "resposta vazia: nada enviado ao beneficiario"

#: HEL-07: desfecho do mesmo turno. Precisa estar em `turn_telemetry._DESFECHO_VOCAB["helena"]` —
#: um valor fora do vocabulario e' normalizado para `"outro"`, o que apagaria exatamente a
#: distincao que este achado cria.
DESFECHO_RESPOSTA_VAZIA: str = "resposta_vazia_nao_enviada"

#: HEL-03: prefixo do `error` quando a precondicao deterministica recusou a rota `inform`. O
#: sufixo e' o TOKEN DE CLASSE da precondicao violada (`_inform_recusado`), nunca texto de
#: terceiro — este `error` viaja para `resumo_contexto` e dali para variaveis de processo.
ERRO_INFORM_RECUSADO: str = "inform recusado"
#: CONSULTA DO PLANO: token de classe da recusa da precondicao de `consultar_plano` (vira `falha_tecnica`).
ERRO_CONSULTA_RECUSADA: str = "consulta recusada"
#: O `next_kind` (e o nome da aresta) da consulta do plano. So' existe com as consultas ligadas.
NEXT_KIND_CONSULTA_PLANO: str = "consulta_plano"
#: Teto do tempo que a consulta a AMH soma ao turno (cada leitura ja' e' limitada pelo port).
CONSULTA_PRAZO_S: float = 6.0
#: Onda (e): o prefixo do `error` quando `_handoff_recusado` barrou a passagem ao Lucas. Mesmo
#: formato do de cima: o sufixo e' o TOKEN DE CLASSE da precondicao violada, nunca texto.
ERRO_HANDOFF_RECUSADO: str = "handoff recusado"

#: NUMERO UNICO (onda e, ADR-0062; plano §2.4). A frase que a Helena manda na PRIMEIRA passagem da
#: conversa para o Lucas, e so' nela: com o Lucas ja' atendendo, a Helena nao envia nada. Texto
#: FIXO, sem modelo, sujeito a revisao do Diretor de Tecnologia (plano §7, pergunta 1). Nao promete
#: humano, nao cita canal e nao opina sobre saude — o veredito das cercas de saida esta' fixado em
#: `tests/unit/agents/corpus_cercas_de_saida.json`.
FRASE_PASSAGEM_COBRANCA: str = "Vou te passar para o atendimento de cobrança."
#: `response_kind` do turno que passou a conversa ao Lucas.
RESPONSE_KIND_HANDOFF: str = "handoff"
#: Desfechos do turno de passagem. Declarados tambem em `runtime/turn_telemetry.py` (vocabulario da
#: helena); `test_helena_passagem_cobranca.py` impede a divergencia. Dois porque sao dois fatos: a
#: frase saiu (primeira passagem) ou nada saiu (o Lucas ja' estava com a conversa).
DESFECHO_PASSAGEM_COBRANCA: str = "passagem_cobranca"
DESFECHO_PASSAGEM_SEM_FRASE: str = "passagem_cobranca_sem_frase"
#: Quem esta' com a conversa, como o despachante le' da tabela `conversa_agente_ativo`.
AGENTES_DA_CONVERSA: frozenset[str] = frozenset({"helena", "lucas"})

#: IDENTIDADE DO BENEFICIARIO (DL-0077). Quem escreve, resolvido pela AMH a partir do telefone (hash) e
#: entregue pelo despachante SO' com `MAEZO_HELENA_IDENTIDADE_AMH` ligada e SO' quando o telefone aponta
#: para UMA pessoa. E' CONTEXTO, nunca insumo de decisao: nenhum no' da Helena le' este campo para
#: classificar, tabelar, escalar ou redigir (nao entra no prompt nem na extracao); ele viaja no estado e
#: na telemetria para personalizacao futura e roteamento correto. Vocabulario FECHADO (Zona Geral,
#: ADR-0006): referencia opaca, faixa etaria grossa (nunca idade exata nem nascimento), e os fatos de
#: plano do contrato. Nunca telefone, CPF ou nome. `None` = sem identidade = a Helena de sempre.
#: UNICA EXCECAO DECLARADA (DL de 07/10/2026): o no' `consultar_plano`, so' com
#: `MAEZO_HELENA_CONSULTAS_AMH` ligada, le' a REFERENCIA OPACA para consultar os fatos do plano na AMH —
#: a referencia nunca entra no prompt, e o resto da identidade continua sem leitor.
IDENTIDADE_FAIXAS_ETARIAS: frozenset[str] = frozenset(
    {"lactente", "crianca", "adolescente", "adulto", "idoso"}
)
IDENTIDADE_CHAVES: frozenset[str] = frozenset(
    {
        "portable_subject_ref",
        "faixa_etaria",
        "plano_ativo",
        "vigencia_inicio",
        "vigencia_fim",
        "carencia_vigente",
        "titular_ref",
    }
)
_IDENTIDADE_REF: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._:~-]{1,256}$")
_IDENTIDADE_DATA: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalizar_identidade(valor: object) -> dict[str, Any] | None:
    """A identidade em forma canonica, ou `None` quando ela nao pode ser confiada.

    Estrita e fail-closed: mapeamento com exatamente `IDENTIDADE_CHAVES`, referencia opaca no formato
    esperado, faixa dentro do vocabulario, booleanos/`None` e datas ISO (`YYYY-MM-DD`). Qualquer desvio
    devolve `None` — nunca uma identidade "parcial" ou corrigida. Nao levanta.
    """
    if not isinstance(valor, Mapping) or frozenset(valor) != IDENTIDADE_CHAVES:
        return None
    ref = valor["portable_subject_ref"]
    titular = valor["titular_ref"]
    faixa = valor["faixa_etaria"]
    if not isinstance(ref, str) or not _IDENTIDADE_REF.fullmatch(ref):
        return None
    if titular is not None and (not isinstance(titular, str) or not _IDENTIDADE_REF.fullmatch(titular)):
        return None
    if faixa is not None and faixa not in IDENTIDADE_FAIXAS_ETARIAS:
        return None
    for chave in ("plano_ativo", "carencia_vigente"):
        if valor[chave] is not None and not isinstance(valor[chave], bool):
            return None
    for chave in ("vigencia_inicio", "vigencia_fim"):
        data = valor[chave]
        if data is not None and (not isinstance(data, str) or not _IDENTIDADE_DATA.fullmatch(data)):
            return None
    return {chave: valor[chave] for chave in sorted(IDENTIDADE_CHAVES)}


#: AVISO DE IDENTIDADE (DL-0078, decisao do dono de 07/10/2026). O DESFECHO da resolucao pela AMH, em
#: vocabulario FECHADO, entregue pelo despachante ao lado da identidade (campo de ENTRADA
#: `identidade_desfecho`):
#:   * `reconhecido`    — candidato UNICO e identidade de vocabulario fechado montada;
#:   * `nao_encontrado` — a AMH respondeu DEFINITIVAMENTE que nao ha' candidato (`nenhum`, conjunto vazio);
#:   * `indeterminado`  — todo o resto (telefone compartilhado, falha, prazo, sem base legal, perfil fora
#:     do vocabulario). Indeterminado NAO fala nada sobre identidade: a Helena de sempre.
#: `None` = flag desligada (ou entrada sem despachante) = a Helena de sempre, byte a byte.
#:
#: SO' O FATO "reconheci / nao encontrei" chega a pessoa. NENHUM dado do plano (ativo, vigencia,
#: carencia, faixa etaria, titular) e' dito, nunca: identificar pelo telefone nao e' autenticar, e a
#: pessoa do outro lado pode nao ser o beneficiario. A identidade continua fora de todo prompt.
IDENTIDADE_RECONHECIDA: str = "reconhecido"
IDENTIDADE_NAO_ENCONTRADA: str = "nao_encontrado"
IDENTIDADE_INDETERMINADA: str = "indeterminado"
IDENTIDADE_DESFECHOS: frozenset[str] = frozenset(
    {IDENTIDADE_RECONHECIDA, IDENTIDADE_NAO_ENCONTRADA, IDENTIDADE_INDETERMINADA}
)
#: O nome de exibicao da operadora nos textos de identidade. O despachante passa o valor da settings
#: `helena_identidade_nome_operadora` (`MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA`); este e' o default.
NOME_OPERADORA_PADRAO: str = "Austa Clínicas"
#: Os textos FIXOS do dono (DL-0078), sem modelo. Modelos com `{operadora}`; com o nome padrao o texto
#: renderizado e' EXATAMENTE o ditado pelo dono (o teste fixa os tres).
#:   * primeiro contato, numero reconhecido -> `RESPOSTA_AVISO_IDENTIDADE_RECONHECIDA_MODELO`;
#:   * pergunta "sabe quem sou eu?", numero reconhecido -> `RESPOSTA_PERGUNTA_IDENTIDADE_RECONHECIDA_MODELO`;
#:   * numero nao encontrado (primeiro contato E pergunta) -> `RESPOSTA_IDENTIDADE_NAO_ENCONTRADA`.
RESPOSTA_AVISO_IDENTIDADE_RECONHECIDA_MODELO: str = (
    "Reconheci este número no cadastro de beneficiários da {operadora}. "
    "Por segurança, não mostro dados pessoais por aqui."
)
RESPOSTA_PERGUNTA_IDENTIDADE_RECONHECIDA_MODELO: str = (
    "Este número está cadastrado para um beneficiário da {operadora}. "
    "Por segurança, não mostro nome nem dados pelo WhatsApp."
)
RESPOSTA_IDENTIDADE_NAO_ENCONTRADA: str = "Não encontrei este número no cadastro de beneficiários."


def texto_aviso_de_identidade(desfecho: object, *, nome_operadora: str = NOME_OPERADORA_PADRAO) -> str | None:
    """O aviso de PRIMEIRO CONTATO para o desfecho, ou `None` (indeterminado/desligado = silencio)."""
    if desfecho == IDENTIDADE_RECONHECIDA:
        return RESPOSTA_AVISO_IDENTIDADE_RECONHECIDA_MODELO.format(operadora=nome_operadora)
    if desfecho == IDENTIDADE_NAO_ENCONTRADA:
        return RESPOSTA_IDENTIDADE_NAO_ENCONTRADA
    return None


def texto_pergunta_de_identidade(
    desfecho: object, *, nome_operadora: str = NOME_OPERADORA_PADRAO
) -> str | None:
    """A resposta a "sabe quem sou eu?" para o desfecho, ou `None` (indeterminado = fluxo normal)."""
    if desfecho == IDENTIDADE_RECONHECIDA:
        return RESPOSTA_PERGUNTA_IDENTIDADE_RECONHECIDA_MODELO.format(operadora=nome_operadora)
    if desfecho == IDENTIDADE_NAO_ENCONTRADA:
        return RESPOSTA_IDENTIDADE_NAO_ENCONTRADA
    return None


#: COLETA: desfecho de um turno que terminou em PERGUNTA. Declarado tambem em
#: `runtime/turn_telemetry.py` (vocabulario da helena) — o teste de coleta impede a divergencia.
DESFECHO_PERGUNTA_COLETA: str = "pergunta_coleta"
#: Perguntas de fallback quando o modelo falha — PERGUNTAS, nunca orientacao.
_PERGUNTA_FALLBACK: dict[str, str] = {
    "PERGUNTAR_INTENSIDADE": (
        "Para eu encaminhar do jeito certo: esse sintoma esta' leve, moderado ou forte agora?"
    ),
    "PERGUNTAR_CARACTERIZACAO": (
        "Entendi. Pode me dizer com mais detalhe o que voce esta' sentindo e desde quando?"
    ),
    "PERGUNTAR_IDADE": (
        "Para eu encaminhar do jeito certo: qual e' a idade da pessoa que esta' com esse sintoma?"
    ),
    "PERGUNTAR_IDADE_GESTACIONAL": (
        "Para eu encaminhar do jeito certo: com quantas semanas de gestacao voce esta'?"
    ),
    "default": "Pode me contar um pouco mais sobre o que voce esta' sentindo?",
}

#: `response_kind` do turno de falha de start. Token de classe fechado, como os demais — o que
#: permite a um golden/alerta distinguir esta resposta de um handoff de verdade.
RESPONSE_KIND_FALHA_TECNICA_START: str = "falha_tecnica_start"

# DMN table per population (contract SP-OP-ESCALATION-001 + spec/processes/dmn/triage_redflag_*).
_DMN_BY_POPULATION: dict[str, str] = {
    "adult": "triage_redflag_adult",
    "pediatric": "triage_redflag_pediatric",
    "gestante": "triage_redflag_gestante",
    "mental_health": "triage_redflag_mental_health",
}


# --- RETOMADA POS-HUMANO (GAP-XHITL-4) ---------------------------------------------------------
#
# A segunda porta de entrada do grafo. Um humano concluiu SP-OP-ESCALATION-001 como
# `devolvido_agente` e escreveu `notas_resolucao`; o consumidor de retomada
# (`platform/integrations/agent_resume.py`) leu a nota do HISTORICO do motor (Zona PHI — ela NUNCA
# viaja no evento, ADR-0006) e invoca este grafo sob o MESMO thread de checkpoint da conversa.
#
# O TEXTO HUMANO E' CONTEUDO NAO CONFIAVEL. O contrato ja' o trata assim, e a razao e' concreta:
# a nota e' texto livre digitado numa tela, por alguem que nao conhece as cercas desta agente, e o
# que sai daqui chega ao beneficiario com a voz da Helena. Entao ela passa pelas MESMAS cercas de
# saida das outras rotas (`motivo_de_canal_nao_confirmado` + `motivo_de_recusa`), com
# `start_aconteceu=False`: o caso acabou de ser devolvido — nenhum humano esta' com ele agora, e
# prometer um seria a promessa sem lastro que CC-01 removeu.

#: `origem_do_turno` de um turno normal (mensagem do beneficiario). Todo construtor de estado de
#: entrada o grava explicitamente, entao um valor velho no checkpoint nunca decide a rota.
ORIGEM_BENEFICIARIO: str = "beneficiario"
#: `origem_do_turno` de um turno de retomada. SO' `new_helena_resume_state` o grava.
ORIGEM_RETOMADA: str = "retomada"

#: `response_kind` (e rota de telemetria) do turno de retomada.
RESPONSE_KIND_RETOMADA: str = "retomada"

#: Desfechos do turno de retomada — declarados tambem em `runtime/turn_telemetry.py` (vocabulario
#: da helena); `test_helena_retomada.py` impede a divergencia.
DESFECHO_RETOMADA_ENVIADA: str = "retomada_enviada"
DESFECHO_RETOMADA_RECUSADA: str = "retomada_recusada"
DESFECHO_RETOMADA_SEM_INSTRUCOES: str = "retomada_sem_instrucoes"
DESFECHO_RETOMADA_FALHA_ENVIO: str = "retomada_falha_envio"
#: Emitido pelo CONSUMIDOR (nao por este grafo): a janela de 24h da Meta fechou, nada foi enviado
#: e a equipe foi avisada (`agent_resume.NotifyTeamAlerter`).
DESFECHO_RETOMADA_FORA_DA_JANELA: str = "retomada_fora_da_janela"

#: `error` do turno de retomada que chegou sem instrucao utilizavel. Token de classe.
ERRO_RETOMADA_SEM_INSTRUCOES: str = "retomada sem instrucoes humanas"
#: `error` do turno de retomada cujas instrucoes excedem o teto de envio. Token de classe.
ERRO_RETOMADA_INSTRUCOES_LONGAS: str = "retomada: instrucoes excedem o limite de envio"

#: Teto das instrucoes aceitas para envio. O portal ja' limita `notas_resolucao` a 2000
#: (`portal/contracts/completions.py::MAX_NOTES`) e a Cloud API do WhatsApp aceita 4096 no corpo;
#: o teto aqui fica entre os dois para que o template nunca empurre a mensagem para uma recusa do
#: provedor. Instrucao maior e' RECUSADA (nunca truncada em silencio: cortar a instrucao de um
#: humano no meio mudaria o que ela diz).
RETOMADA_MAX_INSTRUCOES: int = 3000

#: REDACAO APROVADA PELO DONO (opcao B, 25/09/2026 — spec da diretoria, secao 6). A instrucao do
#: humano vai ENTRE ASPAS e atribuida a "um profissional da nossa equipe": quem le sabe que a
#: orientacao e' de uma pessoa, e que a Helena so' a repassa. Trocar a redacao e' editar ESTA
#: constante (e o teste que a fixa).
RETOMADA_TEMPLATE: str = (
    "Olá, aqui é a Helena, a assistente virtual do seu plano. Um profissional da nossa equipe "
    'revisou o seu caso e pediu que eu repassasse: "{instrucoes}". Posso ajudar com mais alguma coisa?'
)

#: AGUARDA APROVACAO DE PRODUTO — o texto enviado quando as instrucoes humanas foram BARRADAS pela cerca
#: de saida. Nao repete nada da nota (foi ela que a cerca barrou), nao promete humano (o caso
#: acabou de ser devolvido), nao cita canal. Passa nas cercas por construcao; o teste o fixa.
RETOMADA_RECUSADA_PLACEHOLDER: str = (
    "[RASCUNHO - redacao pendente de produto] Recebemos o retorno sobre o seu atendimento. "
    "Se quiser continuar, responda esta mensagem. Se voce estiver passando por uma emergencia, "
    "procure o servico de emergencia mais proximo."
)

#: Caracteres de controle (menos quebra de linha e tab) removidos da nota antes do template: nao
#: tem significado numa mensagem e sao o veiculo classico de texto que se disfarca na tela.
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class RetomadaEnvioFalhouError(RuntimeError):
    """O envio da retomada falhou. PROPAGA para o consumidor, que nao confirma o offset.

    Diferente dos turnos do beneficiario (que registram `error` e seguem): ali quem espera a
    resposta e' a pessoa que acabou de escrever, e o webhook ja' respondeu a Meta. Aqui quem espera
    e' um evento Kafka — engolir a falha confirmaria o offset de uma retomada que nunca chegou, e a
    instrucao do humano se perderia sem rastro. Falha fechada: o evento volta e o envio e' tentado
    de novo (a chave de idempotencia do envio e' por instancia de escalonamento).
    """


def compor_mensagem_de_retomada(instrucoes: str) -> str:
    """A mensagem ao beneficiario a partir das instrucoes humanas. PURA (template aprovado, opcao B).

    Deterministica de proposito: a instrucao do humano chega ao beneficiario COMO ESCRITA (menos
    caracteres de controle), sem um modelo no meio para parafrasear, omitir ou "melhorar" uma
    orientacao que outra pessoa assinou. Se produto preferir uma redacao pelo modelo, e' uma troca
    local aqui — as cercas continuam depois, em `HelenaGraph.resume`.
    """
    limpo = _CONTROLE.sub("", instrucoes).strip()
    return RETOMADA_TEMPLATE.format(instrucoes=limpo)


def motivo_de_recusa_da_retomada(texto: str) -> tuple[str, str] | None:
    """As cercas de saida aplicadas ao texto de retomada. `(grupo, padrao)` ou `None`. PURA.

    As MESMAS duas cercas de `HelenaGraph._cercar_saida`, na mesma ordem (canal primeiro), com o
    fato de start declarado: `start_aconteceu=False`, porque neste turno nenhum humano esta' com o
    caso — ele acabou de ser devolvido. Assim a promessa de humano e' barrada em qualquer forma,
    inclusive a mencao de encaminhamento que o humano tenha escrito na nota.
    """
    return motivo_de_canal_nao_confirmado(texto) or motivo_de_recusa(
        texto, RESPONSE_KIND_RETOMADA, start_aconteceu=False
    )


class WhatsAppSender(Protocol):
    """Outbound WhatsApp send seam. Operates on a phone HASH, never a raw number — Helena's
    state is pseudonymized end to end (ADR-0006); resolving the hash back to a real number for
    actual delivery is the DISPATCH layer's job (it has the raw number in-hand for the inbound
    turn it is currently handling), never this graph's."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]: ...


class HandoffCobranca(TypedDict):
    """A saida TIPADA com que a Helena passa a conversa ao Lucas (onda e; plano §2.4).

    SAIDA-ONLY do `HelenaState`, e so' o no' `handoff_cobranca` deste modulo a constroi. Nenhum
    campo carrega texto do beneficiario: o Lucas recebe enums, a competencia e a referencia
    pseudonimizada da mensagem de entrada (`message_ref`), nunca o `message_body` (§3).
    """

    para: Literal["lucas"]
    cobranca_subtipo: CobrancaSubtipo
    competencia: str | None
    message_ref: str


# --- Graph state (working memory; ADR-0002 working-memory layer) ---------------------------


class HelenaState(TypedDict, total=False):
    """Conversation state. Every field here is pseudonymized (Zona Geral, ADR-0006) —
    `beneficiario_pseudo_id` NEVER carries a raw CPF/name/phone number."""

    # Runtime identifiers (injected by the dispatch layer at turn start).
    tenant_id: str
    conversation_id: str
    canal: str  # whatsapp | portal | telefone
    beneficiario_pseudo_id: str

    # Turn input (free text already pseudonymized at the webhook edge — sender identity only;
    # see module docstring's labeled boundary on embedded-PHI-in-free-text scanning).
    message_body: str

    # NUMERO UNICO (onda e, ADR-0062) — ENTRADAS que o despachante preenche com o roteador ligado.
    # `new_helena_state` grava as quatro em TODO turno (com o neutro quando o roteador esta'
    # desligado), para que um valor de um turno anterior nunca sobreviva no checkpoint.
    #   * `agente_ativo_conversa`: quem estava com a conversa ANTES deste turno (`helena`/`lucas`);
    #     decide so' se a frase de passagem sai.
    #   * `pedido_humano_lexico` / `sinal_saude_lexico`: os lexicos deterministicos de §2.3. O
    #     primeiro forca o gatilho 3; o segundo so' BLOQUEIA a passagem ao Lucas.
    #   * `message_ref`: o pseudonimo keyed do wamid de entrada (`log_safe_message_id`), que vai no
    #     `handoff` para o Lucas amarrar o turno dele a ESTA mensagem.
    agente_ativo_conversa: str
    pedido_humano_lexico: bool
    sinal_saude_lexico: bool
    message_ref: str
    #: IDENTIDADE DO BENEFICIARIO (DL-0077): contexto pseudonimo, ver `IDENTIDADE_CHAVES`. ENTRADA do
    #: despachante, gravada em TODO turno (`None` quando a fonte esta' desligada ou nao resolveu uma
    #: pessoa so'). Nenhum no' decide nada a partir dela.
    identidade_beneficiario: dict[str, Any] | None
    #: AVISO DE IDENTIDADE (DL-0078): o desfecho FECHADO da resolucao (`IDENTIDADE_DESFECHOS`) ou `None`
    #: com a flag desligada. ENTRADA do despachante, gravada em TODO turno como a identidade. So' decide
    #: QUAL texto fixo de identidade sai (aviso de primeiro contato / resposta a "sabe quem sou eu?").
    identidade_desfecho: str | None

    # Filled by `classify`.
    intent: Intent
    population: Population
    psychosocial_risk: bool
    sintoma_codigo: str | None
    intensidade: str
    idade_anos: int | None
    idade_meses: int | None
    idade_gestacional_semanas: int | None
    risco_imediato: bool | None
    dmn_table: str
    dmn_decision: dict[str, Any]
    dmn_decision_ref: str
    # Onda (e): o subtipo e a competencia de uma mensagem de COBRANCA, ja' validados contra o
    # dominio fechado. `None` em todo turno que nao e' `intent="cobranca"`.
    cobranca_subtipo: CobrancaSubtipo | None
    cobranca_competencia: str | None
    #: Onda (e): a passagem ao Lucas, escrita SO' pelo no' `handoff_cobranca`.
    handoff: HandoffCobranca | None
    #: CONSULTA DO PLANO (DL de 07/10/2026): o subtipo validado (`None` fora de `consulta_plano`) e os
    #: literais do cadastro que a resposta deste turno carrega — as cercas de saida os leem APAGADOS
    #: (`consultas_plano.texto_para_cerca`): um status "em analise" do cadastro e' fato, nao invencao.
    consulta_subtipo: str | None
    consulta_literais: tuple[str, ...] | None
    #: `True` so' no turno cuja resposta carrega fatos do plano (o historico da onda 0 grava um marcador).
    resposta_com_dados_do_plano: bool

    # COLETA (passo 4). Os tres primeiros sao MEMORIA DE CONVERSA: sobrevivem ao `receive` do
    # turno seguinte (ver `_HELENA_MEMORIA_DE_CONVERSA`) porque a pergunta feita num turno so'
    # tem sentido se o turno seguinte souber que a fez.
    coleta_rodadas: int
    coleta_pendente: str | None
    coleta_contexto: str
    # Estes dois sao do turno corrente, zerados como qualquer saida.
    coleta_veredito: str | None
    coleta_pergunta: str | None

    # MEMORIA CLINICA ENTRE TURNOS (Frente 2.1). Tambem e' MEMORIA DE CONVERSA: sobrevive ao
    # `receive` do turno seguinte. Guarda QUEM E' O PACIENTE — populacao e idades —, que e' o
    # unico grupo de campos cuja ausencia no turno seguinte muda a TABELA consultada.
    memoria_clinica: dict[str, Any] | None
    # Do turno corrente, zerado como qualquer saida: o texto que a Helena deve confirmar quando um
    # dado LEMBRADO entra numa decisao clinica pela primeira vez.
    memoria_a_confirmar: str | None

    # Routing.
    next_kind: ResponseKind
    escalation_motivo: MotivoCategoria | None
    escalation_severidade: Severidade | None

    # `escalate` outputs.
    escalation_started: bool
    escalation_business_key: str
    escalation_process_ref: dict[str, Any]
    #: CC-01: o start de SP-OP-ESCALATION-001 foi TENTADO e FALHOU tecnicamente
    #: (`_start_escalation`'s `except CibSevenError`). Distinto de `escalation_started is False`,
    #: que tambem e o valor NEUTRO de um turno informativo que nunca tentou escalar.
    start_failed: bool
    #: CERCA TEXTO x FATO (21/09/2026, F1): o DESFECHO REAL do start deste turno, no vocabulario
    #: fechado `START_DESFECHO_*`, derivado do `StartOutcome` que `start_process_idempotent`
    #: devolve. `start_failed` responde "falhou?" (um booleano); este responde "o que ACONTECEU?"
    #: — e e' a diferenca entre `novo` e `ja_ativo` que o C1 da bateria expos: nos dois
    #: `escalation_started` e' `True` e nenhum erro existe, mas so' num deles ha' um
    #: encaminhamento novo para anunciar.
    start_desfecho: str
    #: F6 (21/09/2026): o cartao de apresentacao ja foi enviado NESTA conversa. Preservado entre
    #: turnos por `receive` (como `memoria_clinica`) e lido por `response_prompt` via contexto —
    #: com ele ligado, repetir a apresentacao esta' PROIBIDO no prompt.
    apresentacao_ja_feita: bool
    #: ISO-8601 UTC do ULTIMO turno em que a pessoa escreveu. Memoria de conversa: `receive` o compara
    #: com o agora para decidir se o cartao de apresentacao ainda vale (DL-0076).
    ultima_mensagem_em: str | None
    #: DL-0078: o aviso de identidade (ou a resposta a "sabe quem sou eu?") JA' chegou a pessoa nesta
    #: conversa. Memoria de conversa: `receive` o preserva (so' `True` literal atravessa), e so' `respond`
    #: o acende, depois de um envio bem-sucedido. Vale para a conversa inteira (o thread do checkpoint).
    aviso_identidade_dado: bool
    #: HISTORICO CURTO DA CONVERSA (DL-0080, `MAEZO_HELENA_HISTORICO`, default off): as ultimas
    #: `HISTORICO_MAX_TROCAS` mensagens `{papel, texto, em}` (ver `historico.py`). Memoria de conversa:
    #: `receive` o preserva SO' com a flag ligada e dentro da janela de 6 h desde `ultima_mensagem_em`;
    #: so' `respond` o estende, com a mensagem do turno e o texto que SAIU. Nunca telefone/identidade.
    historico_conversa: list[dict[str, str]] | None

    # Turn output.
    response_text: str
    response_kind: ResponseKindOut
    #: CC-01/NEW-10: desfecho do turno. Escrito exclusivamente por `respond`, nos DOIS ramos:
    #: `erro_inicio_processo` no ramo de falha de start (via `notify_start_failure`) e
    #: `escalado_humano`/`resolvido_automatico` no ramo de sucesso (mesmo valor rotulado na
    #: telemetria CC-09, gravado tambem aqui desde NEW-10 — antes ficava "" ate a proxima falha).
    desfecho: str
    error: str

    # RETOMADA (GAP-XHITL-4). `origem_do_turno` e' INPUT (todo construtor de entrada o grava:
    # `new_helena_state`/`gate_inbound_state` sempre com `beneficiario`, e SO'
    # `new_helena_resume_state` com `retomada`). `retomada_instrucoes` e' OUTPUT-ONLY para a
    # particao: `gate_inbound_state` o descarta e `receive` o zera — o unico jeito de ele chegar ao
    # grafo e' o construtor tipado da retomada.
    origem_do_turno: str
    retomada_instrucoes: str | None


# --- Input/output field split + input-boundary gate (T1.11 caller-planted read-through fix) ---
#
# HelenaState carries TWO disjoint classes of key:
#   * INPUT-ONLY  (`HELENA_INPUT_FIELDS`): the ONLY keys a caller/upstream/dispatch seam may set.
#   * OUTPUT-ONLY (`_HELENA_NEUTRAL_OUTPUTS`): keys OWNED by this graph's nodes. A caller must
#     NEVER set one — a planted output field is an injection (forged routing, forged escalation
#     motivo/severidade, forged ADR-0007 `dmn_decision_ref` provenance, or an anti-escalation
#     `next_kind`/`error` that suppresses a red flag).
#
# TWO defenses, both fail-closed:
#   1. Per-graph entry sanitization — `receive` resets EVERY output-only field to its neutral
#      default before any downstream node runs, so a planted value cannot be read even if it
#      reached the state dict (see `HelenaGraph.receive`).
#   2. Input-boundary gate — the production construction seam(s) assemble state ONLY through the
#      typed `new_helena_state` constructor or the `gate_inbound_state` allowlist filter, so an
#      output-only key can never enter the state dict in the first place. This is the durable,
#      class-killing layer: the read-throughs stay unreachable even if a future node regresses.
#
# The `_completeness` guard below fails at import time if a newly added HelenaState field is not
# classified into exactly one of the two sets — "any missed key is a hole".

HELENA_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "conversation_id",
        "canal",
        "beneficiario_pseudo_id",
        "message_body",
        "origem_do_turno",
        # Onda (e) do numero unico — ver o bloco no `HelenaState`.
        "agente_ativo_conversa",
        "pedido_humano_lexico",
        "sinal_saude_lexico",
        "message_ref",
        "identidade_beneficiario",
        "identidade_desfecho",
    }
)

# Neutral default for every OUTPUT-ONLY field. `receive` writes a copy of this over the incoming
# state so nothing a caller planted survives to a downstream read. Every value here is immutable
# (scalars / None) — safe to share across turns via a shallow copy.
_HELENA_NEUTRAL_OUTPUTS: dict[str, Any] = {
    "intent": None,
    "population": None,
    "psychosocial_risk": False,
    "sintoma_codigo": None,
    "intensidade": None,
    "idade_anos": None,
    "idade_meses": None,
    "idade_gestacional_semanas": None,
    "risco_imediato": None,
    "dmn_table": None,
    "dmn_decision": None,
    "dmn_decision_ref": None,
    "cobranca_subtipo": None,
    "cobranca_competencia": None,
    "handoff": None,
    "consulta_subtipo": None,
    "consulta_literais": None,
    "resposta_com_dados_do_plano": False,
    # COLETA — os tres de memoria tem default neutro aqui (para a particao de campos e para o
    # PRIMEIRO turno de uma conversa), mas `receive` os PRESERVA em vez de zerar; ver
    # `_HELENA_MEMORIA_DE_CONVERSA` logo abaixo.
    "coleta_rodadas": 0,
    "coleta_pendente": None,
    "coleta_contexto": "",
    "coleta_veredito": None,
    "coleta_pergunta": None,
    # MEMORIA CLINICA (Frente 2.1): default neutro aqui — como a memoria de coleta, `receive` a
    # PRESERVA em vez de zerar quando a feature esta ligada.
    "memoria_clinica": None,
    "memoria_a_confirmar": None,
    "next_kind": "inform",
    "escalation_motivo": None,
    # HELENA-SEVERIDADE-DEFAULT: `None`, never `"leve"` — a clinical severity that was never
    # determined is not the mildest one, it is UNKNOWN (same principle GAP-ESC-SEVERITY-GROUP
    # fixed one layer down, in the worker). The reachable path this guards is `receive`'s own
    # missing-runtime-context escalate (`next_kind="escalate"` set WITHOUT running `classify`,
    # which is the only place that assigns a REAL severidade per gatilho) — before this fix that
    # path silently announced every such case as `leve`. `escalate` forwards whatever this is
    # (including `None`) verbatim.
    # §Delta-3 (regressao P-12): o historico Fleet registrou `no user task ever appeared`
    # nas duas provas de classificador. A fixture antiga nao tinha allowlist BPMN nem Kafka;
    # nao provou falha dos DOIS canais de producao, nem entrega depois do reparo.
    # O contrato HEL-04 e o worker aceitam `null` somente em `falha_tecnica`; a DMN r6
    # escolhe grupo/prioridade sem usar severidade (`-`). A instancia deve chegar a
    # `UT_TratarEscalonamento`. Sem produtor, o status e `teams_notification_skipped_no_producer`:
    # progresso no fluxo nao significa humano paginado. Nunca fabricar `leve`.
    "escalation_severidade": None,
    "escalation_started": False,
    "escalation_business_key": None,
    "escalation_process_ref": None,
    "start_failed": False,
    # CERCA TEXTO x FATO: o neutro e' `nao_tentado`, NUNCA `""` nem `None`. Uma string vazia
    # obrigaria cada leitor a decidir o que a ausencia significa, e foi essa ambiguidade
    # (`escalation_started is False` querendo dizer "falhou" OU "nem tentei") que CC-01 ja teve de
    # desfazer uma vez com `start_failed`. Aqui a ausencia tem NOME.
    "start_desfecho": START_DESFECHO_NAO_TENTADO,
    "apresentacao_ja_feita": False,
    "ultima_mensagem_em": None,
    # DL-0078: memoria de conversa, como `apresentacao_ja_feita` (ver `_HELENA_MEMORIA_DE_CONVERSA`).
    "aviso_identidade_dado": False,
    # DL-0080: memoria de conversa com portao proprio (a flag do historico) — ver `receive`.
    "historico_conversa": None,
    "response_text": None,
    "response_kind": None,
    "desfecho": "",
    "error": None,
    # GAP-XHITL-4: so' `new_helena_resume_state` o preenche; `receive` o zera em todo turno do
    # beneficiario, e `resume` o zera ao terminar.
    "retomada_instrucoes": None,
}

_HELENA_ALL_FIELDS = HELENA_INPUT_FIELDS | frozenset(_HELENA_NEUTRAL_OUTPUTS)

#: F6 (21/09/2026, segunda rodada): A MARCA DA APRESENTACAO no texto enviado.
#:
#: POR QUE ELA EXISTE. `apresentacao_ja_feita` significa "a pessoa JA LEU o cartao", e o efeito de
#: liga-lo e' PROIBIR a apresentacao no prompt pelo resto da conversa. Ate' aqui ele acendia em
#: QUALQUER envio bem-sucedido — inclusive nos turnos em que a cerca TEXTO x FATO trocou o texto
#: por uma constante que nao tem cartao nenhum ("Recebemos sua mensagem. Seu atendimento (...) ja
#: esta aberto"). Nesses turnos a pessoa leu uma mensagem, nao o cartao, e a Helena nunca mais se
#: apresentaria naquela conversa — o oposto do F6, que existe para ela nao REPETIR o cartao.
#:
#: POR QUE UMA MARCA, E POR QUE ELA E' O NOME. O cartao e' redigido pelo modelo, entao nao ha texto
#: fixo para comparar. O `response-v8` passou a mandar que a apresentacao, quando acontece, se
#: IDENTIFIQUE PELO NOME ("Sou Helena") — que e' uma boa ideia por si (quem pergunta "quem e' voce?"
#: merece o nome) e e' o que torna o sinal verificavel. A cerca e' `_apresentou_se`, e o
#: `response_prompt` e a lista aqui sao as duas metades: editar uma sem a outra e' o descompasso
#: que o teste torna visivel.
#:
#: A MARCA E' O NOME, E NAO A FRASE (21/09/2026, TERCEIRA RODADA). Ate' aqui a lista tinha duas
#: frases LITERAIS ("sou helena" / "sou a helena") contra um cartao que o modelo redige — e
#: parafrase nao e' excecao, e' o caso comum: *"Oi, aqui e a Helena"*, *"Eu me chamo Helena"*,
#: *"Helena falando"*. Nenhuma das tres acendia o sinal, entao `apresentacao_ja_feita` ficava
#: apagado e o F6 voltava SEM TETO — a Helena repetindo o cartao a cada turno, que e' o defeito
#: medido em 21/09 (tres turnos seguidos iguais).
#:
#: A marca passou a ser `\bhelena\b` no texto normalizado ENVIADO, e a premissa e' verificavel: a
#: Helena so' escreve o proprio nome quando se identifica. Nenhuma das nove constantes que este
#: modulo e o `prompts` enviam ao beneficiario contem o nome dela
#: (`test_helena_adv_rodada3.py::test_o_texto_sem_o_nome_nao_acende_a_flag` fixa as nove).
#:
#: A CORRECAO DO CUSTO, que o comentario anterior errava: ele dizia que um falso negativo "no
#: maximo repete a apresentacao uma vez", e isso e' FALSO — o sinal e' consultado a cada turno, e
#: enquanto nenhum texto o acender ele fica apagado para sempre; a repeticao e' de TODOS os turnos,
#: nao de um. Os dois lados custam, e por isso o lado escolhido nao e' "o seguro", e' o
#: VERIFICAVEL: o falso positivo (um texto com o nome dela sem cartao nenhum) exigiria que o prompt
#: pedisse assinatura, e ele nao pede — se um dia pedir, esta lista e' o lugar de reagir.
#: A SEGUNDA MARCA E' O PROPRIO CARTAO (22/09/2026, item [b] do diretor). A pergunta dele era qual
#: das duas metades falhava — o sinal nao acendendo ou o prompt nao obedecendo —, e a medicao
#: responde: o SINAL. O cartao que saiu nos tres turnos do `A1` ("Este canal pode te orientar sobre
#: o plano (...) Se quiser, descreva melhor o que voce esta sentindo") nao tem o nome dela em lugar
#: nenhum; o `response-v9` pede "comece por Sou Helena" QUANDO VOCE SE APRESENTAR, e o modelo nao
#: trata a frase de abertura como apresentacao. Com a marca sendo so' o nome, `apresentacao_ja_feita`
#: ficava apagado para sempre e a proibicao de repetir nunca valia — a repeticao e' de TODOS os
#: turnos, nao de um.
#:
#: O FALSO POSITIVO ACEITO, declarado em vez de descoberto: um `inform` que responda uma duvida
#: administrativa comecando por "este canal pode te orientar sobre X" tambem acende o sinal. E'
#: aceitavel porque esse texto JA E' o cartao — a pessoa leu o que este canal faz, com ou sem o
#: nome —, e porque o que a flag proibe e' SO' repetir a frase de abertura: "quem e voce?" e "voce
#: e um robo?" continuam sendo respondidas SEMPRE, por regra propria do `response_prompt`, que esta
#: flag nao desliga. Nenhuma das constantes que este modulo e o `prompts` enviam ao beneficiario
#: contem esta abertura (`test_helena_bateria_22_09.py` e o corpus fixam isso).
#: A NEGACAO INTERPOSTA ENTROU EM 22/09/2026 (REVISAO DO PROPRIO CRITICO). A marca do cartao
#: nascia sem ela, e por isso casava tambem a forma NEGADA — medido: `_apresentou_se("Este canal
#: nao pode agendar consultas.")` e `_apresentou_se("Este canal nao consegue consultar o status da
#: guia em tempo real.")` davam `True`. E' o falso positivo CARO, nao um raro: o `response-v9`
#: ENSINA exatamente essa familia de frase ("Este canal NAO emite boleto, NAO atualiza cadastro,
#: NAO consulta status de guia em tempo real"), entao bastava o primeiro turno recusar uma
#: capacidade para `apresentacao_ja_feita` acender SEM cartao nenhum — e a Helena nao se
#: apresentava mais naquela conversa, que e' o lado que o comentario acima diz evitar.
#:
#: O LUGAR ONDE A NEGACAO E' TESTADA E' A ACAO, E NAO A JANELA — o mesmo idioma (e a mesma licao)
#: do item 2 de `prompts.padrao_de_encaminhamento`, quinta rodada: um lookbehind `(?<!nao )`
#: colado no verbo. A alternativa medida aqui foi a janela com lookahead
#: (`\beste canal\b(?![^.;!?]{0,40}\bnao\b)...`), e ela ERRA PARA O OUTRO LADO justamente nos
#: cartoes que o prompt manda redigir, porque apaga a marca por um `nao` que nega OUTRA coisa:
#:
#:     "Este canal pode te orientar sobre o plano, mas nao emite boleto."      -> False
#:     "Este canal nao emite boleto, mas pode te orientar sobre o plano."      -> False
#:     "Este canal, que nao substitui atendimento medico, pode te orientar..." -> False
#:
#: Com o lookbehind os tres voltam a `True`, as duas frases negadas ficam em `False` e o cartao do
#: `A1` continua `True` (o corpus de `test_helena_bateria_22_09.py` fixa os cinco casos).
#:
#: RESIDUAL DECLARADO, e a direcao e' deliberada: a negacao que NAO encosta no verbo ("Este canal
#: nunca pode...", "Este canal nao vai poder...") nao e' vista, e uma recusa isolada sem nenhum
#: verbo afirmativo depois ("Este canal nao pode agendar consultas, mas te oriento sobre o plano")
#: deixa de acender esta segunda marca. Nos dois casos sobra a PRIMEIRA marca, `\bhelena\b`, que e'
#: o que o `response-v9` pede na frase de apresentacao de verdade ("comece por Sou Helena").
MARCAS_DE_APRESENTACAO: tuple[str, ...] = (
    r"\bhelena\b",
    r"\beste canal\b[^.;!?]{0,40}(?<!nao )\b(?:pode|posso|consigo|consegue)\b",
)


def _apresentou_se(texto: str) -> bool:
    """O texto ENVIADO contem a apresentacao? (F6, 21/09/2026 segunda rodada)

    Comparacao normalizada pela mesma funcao das cercas de saida (`prompts._normalizar`): caixa,
    acento e caractere invisivel nao podem decidir se a Helena volta a se apresentar.

    REGEX de palavra inteira, e nao substring, pela mesma razao que a cerca de canal usa `\\b`:
    "helena" solto casaria dentro de outra palavra, e a lista existe para ser ampliada por quem
    for preciso sem reabrir esse buraco.
    """
    plano = _normalizar_texto(texto)
    return any(re.search(marca, plano) is not None for marca in MARCAS_DE_APRESENTACAO)


def _confirmacao_chegou(texto: str, frase: object) -> bool:
    """O texto ENVIADO trouxe a frase de confirmacao do dado lembrado? (21/09/2026, quarta rodada)

    O DEFEITO QUE ISTO FECHA. `memoria_clinica.confirmada` era gravada em `classify`, no mesmo
    `update` que CRIA a frase — isto e', quando ela e' GERADA. Entre gerar e enviar existe a cerca
    TEXTO x FATO (`_texto_bate_com_o_fato`), e ela troca o rascunho por uma constante em tres
    casos; num `ja_ativo` com rascunho que anuncia handoff a troca e' INTEGRAL. O resultado
    medido: a pessoa nunca le' a pergunta, `confirmada` fica `True` para sempre, e a unica
    confirmacao que a conversa tinha (`confirmar_agora` exige `not ja_confirmada`) nunca mais e'
    feita. O dado lembrado passa a decidir a tabela de red flag sem que ninguem possa corrigi-lo,
    que e' exatamente o que "MOSTRAR ANTES DE USAR" existe para impedir.

    POR QUE ESTE LADO DO CONSERTO, e nao devolver `memoria_a_confirmar` ao proximo turno: o SPLIT
    INPUT/OUTPUT de `HelenaState`. `memoria_a_confirmar` e' campo de OUTPUT com default neutro, ou
    seja `receive` o zera em todo turno; faze-lo atravessar exigiria promove-lo a
    `_HELENA_MEMORIA_DE_CONVERSA` e passar a limpa-lo a mao nos turnos que nao confirmam — campo
    novo na excecao do reset, por um fato que ja' tem casa. `memoria_clinica` JA e' memoria de
    conversa, JA atravessa o reset pela excecao declarada e JA carrega `confirmada` pela fronteira
    validada (`_memoria_clinica_valida`). O fato "a pergunta saiu" mora onde a memoria mora.

    SUBSTRING NORMALIZADA, pela mesma razao de `_apresentou_se`: caixa, acento e caractere
    invisivel nao podem decidir se a Helena volta a confirmar. A frase entra no prompt com ordem
    de ser copiada como veio ("comece a resposta por essa frase, exatamente como ela veio").

    RESIDUAL DECLARADO: se o modelo PARAFRASEAR a frase, `confirmada` fica `False` e a pergunta
    volta no proximo turno clinico. E' ruido, e e' o lado certo do erro — o avesso (dar por
    confirmado o que a pessoa nunca leu) e' o defeito que esta funcao conserta.
    """
    if not isinstance(frase, str) or not frase.strip():
        return False
    return _normalizar_texto(frase) in _normalizar_texto(texto)


#: MEMORIA DE CONVERSA — a UNICA excecao ao reset de `receive`, e por que ela e' segura.
#:
#: O reset existe para que valor plantado por quem chama nao seja lido a jusante. Estes tres
#: campos so' entram no estado por DUAS vias: o proprio grafo (num turno anterior) ou o
#: checkpointer que o devolve. O portao de entrada (`gate_inbound_state` / `new_helena_state`)
#: continua recusando-os vindos de fora — nada muda ali.
#:
#: E se, mesmo assim, um valor plantado chegasse? O PIOR que ele consegue e' na direcao segura:
#: `coleta_rodadas` alto -> escala mais cedo (humano); `coleta_pendente` -> a proxima
#: classificacao considera que ha' pergunta em aberto; `coleta_contexto` -> texto extra dentro
#: do bloco NAO CONFIAVEL do prompt, que ja' e' onde a mensagem do beneficiario vive. Nenhum
#: deles suprime uma red flag, forja um `dmn_decision_ref` ou desvia um escalonamento — os
#: campos que fazem isso continuam zerados em todo turno.
#:
#: `apresentacao_ja_feita` ENTROU NO CONJUNTO EM 21/09/2026 (segunda rodada), e a declaracao e' a
#: correcao: `receive` ja' o preservava desde a entrega do F6, mas ele nao estava DECLARADO aqui —
#: entao a cerca que prova "so' a memoria de conversa sobrevive ao reset"
#: (`test_helena_coleta.py::test_receive_preserva_so_a_memoria_de_conversa_e_zera_o_resto`) nao o
#: enxergava, e um campo preservado sem declaracao e' exatamente o tipo de excecao que a defesa
#: T1.11 existe para nao ter.
#:
#: NOTA IMPORTANTE, que e' a razao de `_MEMORIA_DE_COLETA` existir logo abaixo: este campo e'
#: preservado SEM o portao de coleta, ao contrario dos tres primeiros. Ele e' um bool que so' anda
#: para True e o unico efeito dele e' o prompt nao repetir a apresentacao; amarra-lo ao portao da
#: tabela de suficiencia faria a Helena repetir o cartao a cada turno ate' aquela tabela ser
#: ratificada, que e' um prazo sem relacao nenhuma com o defeito. E `receive` o le' com `is True`,
#: entao um valor plantado truthy nao-bool nao atravessa.
_HELENA_MEMORIA_DE_CONVERSA: frozenset[str] = frozenset(
    {
        "coleta_rodadas",
        "coleta_pendente",
        "coleta_contexto",
        "memoria_clinica",
        "apresentacao_ja_feita",
        "ultima_mensagem_em",
        # DL-0078: o aviso de identidade e' uma vez por conversa; preservado sem portao, como o cartao.
        "aviso_identidade_dado",
        # DL-0080: preservado SO' com `historico_enabled` e dentro da janela (`historico_valido`).
        "historico_conversa",
    }
)

#: Os campos de memoria que o PORTAO DA COLETA governa. Subconjunto explicito de
#: `_HELENA_MEMORIA_DE_CONVERSA` porque os outros dois membros daquele conjunto tem cada um a sua
#: propria condicao de preservacao (`memoria_clinica` -> portao da memoria clinica;
#: `apresentacao_ja_feita` -> nenhum portao, ver a nota acima). Derivar este conjunto por
#: subtracao, como era antes, fazia todo membro NOVO do conjunto maior herdar silenciosamente o
#: portao da coleta.
_MEMORIA_DE_COLETA: frozenset[str] = frozenset({"coleta_rodadas", "coleta_pendente", "coleta_contexto"})

#: MEMORIA CLINICA ENTRE TURNOS (Frente 2.1) — os campos que dizem QUEM E' O PACIENTE.
#:
#: O DEFEITO QUE ISTO CORRIGE, reproduzido tres vezes em 13/09/2026: um bebe de 11 meses foi
#: triado pela tabela de ADULTO. A mae disse a idade no primeiro turno e descreveu o sintoma no
#: segundo; `receive` zera toda saida entre turnos, entao `population` voltou a `none` e o sintoma
#: caiu em `triage_redflag_adult`. Nao e' um bug de extracao — e' a memoria desenhada estreita de
#: proposito (defesa T1.11), que resolveu seguranca e nao resolveu continuidade.
#:
#: POR QUE SO' ESTES QUATRO, e nao "o estado do turno anterior": sao os unicos cuja ausencia muda
#: a TABELA consultada. Lembrar `sintoma_codigo` seria pior que nao lembrar — o sintoma e' o que a
#: pessoa esta dizendo AGORA, e carregar o anterior faria a Helena responder a mensagem errada.
_MEMORIA_CLINICA_CAMPOS: tuple[str, ...] = (
    "population",
    "idade_anos",
    "idade_meses",
    "idade_gestacional_semanas",
)

#: Quanto tempo um dado lembrado vale, em horas. O documento pede janela "de horas, nao de
#: minutos": beneficiario que volta depois do almoco e' o caso comum, nao a excecao. Seis horas
#: cobrem a volta na mesma parte do dia; alem disso o quadro clinico pode ter mudado o bastante
#: para que lembrar seja pior que perguntar de novo.
MEMORIA_CLINICA_JANELA_HORAS: float = 6.0
# DL-0080: o historico curto expira junto com a memoria clinica — uma janela so'.
assert HISTORICO_JANELA_HORAS == MEMORIA_CLINICA_JANELA_HORAS

#: O carimbo de quando a memoria foi gravada. Chave separada dos campos clinicos de proposito: a
#: validacao rejeita a memoria inteira quando ele falta ou nao parseia, e uma memoria sem relogio
#: e' indistinguivel de uma memoria eterna.
_MEMORIA_GRAVADA_EM: str = "gravado_em"

#: Se a Helena JA' disse a pessoa o que lembrou. A confirmacao acontece UMA vez por conversa, nao
#: por turno: repeti-la a cada mensagem viraria ruido e a pessoa pararia de ler.
_MEMORIA_CONFIRMADA: str = "confirmada"

#: F5 (21/09/2026): o sintoma que uma avaliacao de red flag DESTA conversa efetivamente usou, e a
#: intensidade com que o usou.
#:
#: POR QUE ISTO NAO CONTRADIZ `_MEMORIA_CLINICA_CAMPOS`. Aquele bloco registra, com razao, que
#: lembrar `sintoma_codigo` seria PIOR que nao lembrar: o sintoma e' o que a pessoa esta dizendo
#: AGORA, e carregar o anterior faria a Helena responder a mensagem errada — com a agravante de
#: disparar a DMN num turno em que ninguem o mencionou. Estas duas chaves nao fazem isso, e a
#: diferenca e' o GATILHO, nao o dado: elas sao lidas SO' quando a mensagem CORRIGE um dado que
#: aquela avaliacao usou (`_correcao_de_dado_avaliado`). Fora desse caso nenhum turno as consulta,
#: e nenhum turno vira sintoma por causa delas.
#:
#: O DEFEITO QUE FECHAM, caso `D3` de 21/09/2026: "tenho 30 anos e estou com febre" avaliou a
#: tabela de adulto; "me enganei, tenho 70 anos" terminou SEM TABELA e com o sintoma perdido — e
#: 70 anos + febre e' exatamente o limiar da regra `r8`. A pessoa corrigiu o dado que decidia a
#: regra, e ninguem reavaliou.
_MEMORIA_SINTOMA_AVALIADO: str = "sintoma_avaliado"
_MEMORIA_INTENSIDADE_AVALIADA: str = "intensidade_avaliada"

#: F4 (21/09/2026): a POPULACAO a que cada campo de idade pertence — o que permite dizer que
#: `population="adult"` ao lado de `idade_meses=36` e' um par INCOERENTE, e nao dois fatos.
#:
#: E' o par que a bateria mediu no turno 3 do `D2`: a resposta disse "seu bebe de 36 meses"
#: (`_frase_de_confirmacao` le' `idade_meses` primeiro) enquanto a consulta foi a
#: `triage_redflag_adult` com `idade_anos=None` (a tabela de adulto nem le' `idade_meses`). Uma
#: crianca de 3 anos triada pela tabela de adulto, com a frase provando que a memoria sabia.
_POPULACAO_DA_IDADE: dict[str, str] = {
    "idade_anos": "adult",
    "idade_meses": "pediatric",
    "idade_gestacional_semanas": "gestante",
}

#: As populacoes que TEM um campo de idade proprio. `none` nao e' populacao nenhuma e
#: `mental_health` decide por `risco_imediato` (a tabela dela nao le' idade), entao em nenhuma das
#: duas uma idade lembrada conflita com a populacao final.
_POPULACOES_COM_IDADE: frozenset[str] = frozenset(_POPULACAO_DA_IDADE.values())

#: O caminho inverso: qual campo de idade uma populacao PRECISA para ser uma afirmacao, e nao um
#: campo obrigatorio preenchido por falta de opcao.
_IDADE_DA_POPULACAO: dict[str, str] = {v: k for k, v in _POPULACAO_DA_IDADE.items()}

#: TETO SANO DE IDADE nas fronteiras de idade (21/09/2026, segunda rodada; estendido a extracao na
#: terceira).
#:
#: O QUE ELE FECHA: a fronteira recusava negativo, `bool`, float e string, e nao tinha limite
#: SUPERIOR nenhum. `idade_meses=1200` (100 anos em meses) era uma memoria valida, atravessava a
#: fusao e ia para a tabela PEDIATRICA — que le' `idade_meses` — como se fosse um lactente. E
#: `idade_anos=900` idem na de adulto. Um valor desses nao vem de ninguem falando da propria
#: idade; vem de extracao torta ou de valor plantado, e a leitura honesta e' "isto nao e' idade".
#:
#: OS NUMEROS, e por que eles NAO sao conteudo clinico: 120 anos e' o limite demografico (o recorde
#: humano documentado e' 122), e 288 meses e' 24 anos — o proprio limiar que a frase de confirmacao
#: ja' usa para deixar de falar em meses. Nenhum dos dois decide conduta: decidem se um valor E'
#: uma idade.
#:
#: `idade_gestacional_semanas` fica FORA, e isso e' declarado e nao esquecido: qual semana deixa de
#: ser uma gestacao possivel e' julgamento clinico (pos-termo existe, e o numero exato pertence ao
#: medico revisor), e inventar o teto aqui seria a unica afirmacao clinica deste bloco. A ausencia
#: esta' registrada em `docs/review-queue.md` (dono: medico auditor), e
#: `test_helena_adv_rodada3.py::test_a_pendencia_clinica_esta_registrada_na_fila_de_revisao` e' o
#: que impede a prosa de prometer um registro que nao existe.
#:
#: AS DUAS FRONTEIRAS, DESDE 21/09/2026 (TERCEIRA RODADA), e a correcao e' do raciocinio anterior.
#: Este teto nasceu SO' na memoria, com o argumento de que "a extracao entrega o dado que a pessoa
#: DISSE neste turno" e de que um teto so' na memoria mantem `memoria ⊆ extracao`. O argumento
#: estava errado na premissa: a extracao NAO e' uma fronteira mais fraca que pode ser larga — ela
#: e' a que ALIMENTA A DMN. `_validate_extraction` aceitava `idade_meses=1200` e a tabela
#: PEDIATRICA (que le' `idade_meses`) triava um lactente de 100 anos, enquanto a memoria recusava
#: o mesmo valor: duas reguas para o mesmo conceito, e a permissiva era a que decide.
#:
#: O mesmo dict serve as duas, entao `memoria ⊆ extracao` deixa de depender de coincidencia (288
#: passa nas duas, 289 e' recusado nas duas). E ele NAO desceu para `_coerce_age`: aquela funcao e'
#: generica sobre um valor (nao sabe QUAL campo), e o teto e' por campo. A recusa mora no laco de
#: `_validate_extraction`, que ja' e' onde a decisao por campo acontece.
_TETO_DE_IDADE: dict[str, int] = {"idade_anos": 120, "idade_meses": 24 * 12}


def _populacao_tem_lastro(extraction: Mapping[str, Any], nova: object, lembrada: object) -> bool:
    """A `population` que a mensagem trouxe e' uma AFIRMACAO de que o paciente mudou? (F4)

    A REGRA, e o defeito que ela fecha. A decisao 3 do documento da memoria clinica diz "a
    informacao NOVA vence, EXCETO quando a nova e' AUSENCIA", e definiu ausencia como `None` (para
    as idades) ou o literal `"none"` (para a populacao). No turno 3 do `D2` o modelo devolveu
    `population="adult"` para a mensagem "desde ontem" — que nao e' `"none"`, e portanto contava
    como afirmacao, mas tambem nao diz absolutamente nada sobre um adulto. O campo e' obrigatorio
    no schema fechado; sem ninguem na mensagem, o modelo preenche com o valor mais comum.

    LASTRO e' o campo de idade que aquela populacao usa para decidir (`_IDADE_DA_POPULACAO`). Com
    ele na mensagem, a troca e' uma afirmacao real ("na verdade e' pra mim mesma, tenho 34 anos") e
    continua vencendo — que e' o caso que `test_informacao_nova_vence_a_lembrada` ja' fixava. Sem
    ele, a populacao lembrada permanece.

    `mental_health` e' a excecao DELIBERADA: ela nao tem campo de idade, entao exigir lastro seria
    exigir o impossivel. E ela nao chega sozinha — vem junto do `psychosocial_risk` que `classify`
    ja' trata como gatilho de prioridade maxima, forcando a tabela por outro caminho.
    """
    if nova == lembrada:
        return True
    campo = _IDADE_DA_POPULACAO.get(str(nova))
    if campo is None:
        return True
    return extraction.get(campo) is not None


def _memoria_clinica_valida(
    bruta: Any, *, agora: datetime, janela_horas: float = MEMORIA_CLINICA_JANELA_HORAS
) -> dict[str, Any] | None:
    """A memoria lembrada, ou `None` quando ela nao pode ser confiada.

    FALHA PARA `None`, NUNCA PARA UM VALOR PARCIAL, e essa escolha e' o coracao da seguranca aqui:
    `None` degrada exatamente para o comportamento de hoje (cada turno comeca do zero), que e'
    conhecido e ja' esta em producao. Um valor parcialmente aceito seria um paciente parcialmente
    lembrado — o modo de falha que esta frente existe para acabar.

    Recusa, em ordem: o que nao e' mapa; o que nao tem carimbo de tempo parseavel; o que esta fora
    da janela; populacao fora do vocabulario fechado; idade que nao e' inteiro nao-negativo.
    """
    if not isinstance(bruta, dict):
        return None
    carimbo = bruta.get(_MEMORIA_GRAVADA_EM)
    if not isinstance(carimbo, str):
        return None
    try:
        gravado = datetime.fromisoformat(carimbo)
    except ValueError:
        return None
    if gravado.tzinfo is None:
        gravado = gravado.replace(tzinfo=UTC)
    idade_da_memoria = (agora - gravado).total_seconds()
    if idade_da_memoria < 0 or idade_da_memoria > janela_horas * 3600:
        return None

    limpa: dict[str, Any] = {}
    populacao = bruta.get("population")
    if populacao is not None:
        if not _em_dominio(populacao, _VALID_POPULATIONS):
            return None
        limpa["population"] = populacao
    for campo in ("idade_anos", "idade_meses", "idade_gestacional_semanas"):
        valor = bruta.get(campo)
        if valor is None:
            continue
        # `bool` e' subclasse de `int` em Python: `True` passaria por `isinstance(v, int)` e
        # viraria idade 1. Recusado explicitamente, como o helper de rodadas de coleta ja' faz.
        if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
            return None
        teto = _TETO_DE_IDADE.get(campo)
        if teto is not None and valor > teto:
            return None
        limpa[campo] = valor
    if not limpa:
        return None
    # F5: a marca da avaliacao passa pela MESMA fronteira, com o mesmo fail-closed para `None`.
    # `sintoma_avaliado` nao e' texto livre — e' um codigo da allowlist que as tabelas de red flag
    # casam (`ALLOWED_SINTOMA_CODIGOS`), e um codigo fora dela nao casaria regra nenhuma: aceitar
    # um valor invalido aqui reabriria a triagem com um sintoma que a DMN nunca reconhece, o que e'
    # pior que nao reabrir.
    avaliado = bruta.get(_MEMORIA_SINTOMA_AVALIADO)
    if avaliado is not None:
        if not isinstance(avaliado, str) or avaliado not in ALLOWED_SINTOMA_CODIGOS:
            return None
        limpa[_MEMORIA_SINTOMA_AVALIADO] = avaliado
        intensidade_avaliada = bruta.get(_MEMORIA_INTENSIDADE_AVALIADA)
        if intensidade_avaliada is not None:
            # `isinstance(..., str)` ANTES do `in`, como a linha do `sintoma_avaliado` logo acima
            # (21/09/2026, segunda rodada). Sem ela, um valor nao-hashavel (`["grave"]`,
            # `{"valor": "grave"}`) levanta `TypeError` no teste de pertinencia ao frozenset — e
            # `TypeError` esta' em `PROGRAMMING_ERRORS`, ou seja PROPAGA: esta fronteira, cujo
            # docstring promete "FALHA PARA `None`, NUNCA PARA UM VALOR PARCIAL", derrubava o turno
            # inteiro em vez de recusar a memoria. E ela e' chamada em `receive` sobre
            # `state["memoria_clinica"]`, que e' exatamente o valor em que nao se confia (T1.11).
            if not _em_dominio(intensidade_avaliada, _VALID_INTENSIDADES):
                return None
            limpa[_MEMORIA_INTENSIDADE_AVALIADA] = intensidade_avaliada
    limpa[_MEMORIA_GRAVADA_EM] = gravado.isoformat()
    limpa[_MEMORIA_CONFIRMADA] = bruta.get(_MEMORIA_CONFIRMADA) is True
    return limpa


#: DL-0076 (05/10/2026, PRAZO PROVISORIO — decisao de produto): depois de quantas horas SEM a pessoa escrever
#: a Helena se apresenta de novo. Antes disso o cartao era uma vez por conversa, e a conversa de um numero
#: nao expira: quem voltava dias depois nao era reapresentado. 12 h por sugestao de engenharia; a janela
#: de 24 h da Meta (`last_inbound_at`) seria o outro candidato.
APRESENTACAO_VALIDADE_HORAS: float = 12.0


def _apresentacao_continua_valida(
    ultima_mensagem_em: Any,
    *,
    agora: datetime,
    janela_horas: float = APRESENTACAO_VALIDADE_HORAS,
) -> bool:
    """`True` so' quando a ultima mensagem tem carimbo legivel, nao e' futura e e' mais nova que a janela.

    Ausente, ilegivel, no futuro ou velho => `False` (reapresenta). Fail-safe: o pior caso e' o cartao
    aparecer uma vez a mais.
    """
    if not isinstance(ultima_mensagem_em, str):
        return False
    try:
        quando = datetime.fromisoformat(ultima_mensagem_em)
    except ValueError:
        return False
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    decorrido = (agora - quando).total_seconds()
    return 0 <= decorrido < janela_horas * 3600


def _fundir_memoria_clinica(
    extraction: dict[str, Any], memoria: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Funde o que a mensagem trouxe com o que a conversa lembrava.

    Devolve `(extracao_fundida, o_que_veio_da_memoria)`. A extracao NAO e' mutada: o segundo
    elemento e' o que decide se ha' algo a confirmar em voz alta, e confundir "o modelo extraiu"
    com "a conversa lembrou" tiraria justamente essa distincao.

    A REGRA, decisao 3 do documento: a informacao NOVA vence, EXCETO quando a nova e' AUSENCIA.
    Nao repetir "meu bebe" nao apaga o bebe. So' afirmacao explicita em contrario troca a
    populacao.

    O QUE CONTA COMO AUSENCIA, e e' onde mora a sutileza: para as idades e' `None`, direto. Para
    `population` e' o literal `"none"` — o vocabulario fechado nao tem `None`, e o modelo devolve
    `"none"` tanto para "nao consegui determinar" quanto para "a mensagem nao e' sobre ninguem em
    particular". As duas leituras levam a mesma conduta aqui: nenhuma delas e' uma afirmacao de
    que o paciente MUDOU, entao nenhuma das duas apaga o que ja' se sabia.

    F4 (21/09/2026) — DUAS REGRAS NOVAS, e as duas nascem do MESMO turno medido (`D2`, turno 3,
    "desde ontem" -> `population="adult"` + `idade_meses=36` lembrado -> tabela de ADULTO com
    `idade_anos=None`, e a resposta dizendo "seu bebe de 36 meses"):

      1. LASTRO (`_populacao_tem_lastro`). A definicao de ausencia acima era estreita: cobria
         `"none"` e nao cobria `"adult"` sem idade nenhuma, que e' o campo obrigatorio preenchido
         por falta de opcao, nao uma afirmacao. Uma populacao sem o campo de idade que ela usa nao
         troca a populacao lembrada. COM lastro, a informacao nova continua vencendo.
      2. COERENCIA. Uma idade lembrada so' atravessa se pertencer a populacao FINAL. Carregar
         `idade_meses` para um turno adulto produzia um par que nenhuma das duas leituras sabia
         interpretar: a tabela de adulto nao le' `idade_meses` (triava com idade vazia) e
         `_frase_de_confirmacao` le' `idade_meses` PRIMEIRO (falava de um bebe). Foi essa
         incoerencia, e nao "um caminho que ignora a memoria", a causa do que a bateria mediu.

    A ORDEM IMPORTA: a populacao e' decidida ANTES das idades, porque e' ela que diz qual idade e'
    coerente. Inverter faria a coerencia ser avaliada contra uma populacao que ainda ia mudar.
    """
    if not memoria:
        return extraction, {}

    fundida = dict(extraction)
    veio_da_memoria: dict[str, Any] = {}

    # PASSO 1 — QUEM E' O PACIENTE.
    lembrada = memoria.get("population")
    atual = fundida.get("population")
    if lembrada is not None:
        ausente = atual is None or atual == "none"
        if ausente or not _populacao_tem_lastro(fundida, atual, lembrada):
            fundida["population"] = lembrada
            veio_da_memoria["population"] = lembrada
    populacao_final = str(fundida.get("population") or "none")

    # PASSO 2 — A IDADE DELE, e SO' a que combina com a populacao acima.
    #
    # A MENSAGEM FALOU DE IDADE? Calculado ANTES do laco de proposito: o laco escreve em `fundida`,
    # e ler a pergunta dentro dele faria a idade que a memoria acabou de inserir contar como "a
    # mensagem trouxe".
    mensagem_falou_de_idade = any(extraction.get(c) is not None for c in _POPULACAO_DA_IDADE)
    for campo in _POPULACAO_DA_IDADE:
        lembrado = memoria.get(campo)
        if lembrado is None or fundida.get(campo) is not None:
            continue
        if populacao_final in _POPULACOES_COM_IDADE and _POPULACAO_DA_IDADE[campo] != populacao_final:
            continue
        if mensagem_falou_de_idade:
            # COERENCIA NOS DOIS SENTIDOS (21/09/2026, segunda rodada). A regra de coerencia acima
            # e' de mao unica: ela filtra a idade que vem da MEMORIA contra a populacao final, e
            # nunca pergunta se a mensagem ja' trouxe aquela idade em OUTRO campo.
            #
            # O TURNO QUE EXPOS ISSO e' o `D2` um passo adiante. A conversa lembra
            # `population=pediatric` + `idade_meses=36`; a mae escreve "na verdade ele tem 5 anos"
            # e o modelo devolve `population=pediatric` com a idade em `idade_anos=5` (o
            # `classify_prompt` pede `idade_meses` para pediatric, mas "5 anos" e' como a frase
            # chega, e `_validate_extraction` valida cada campo ISOLADAMENTE, sem cruzar idade com
            # populacao). `idade_meses` da mensagem e' `None`, entao a idade LEMBRADA atravessava:
            # a extracao fundida ficava com 5 anos E 36 meses ao mesmo tempo, `_evaluate_dmn` na
            # tabela pediatrica le' `idade_meses` — a idade de dois turnos atras decidia a regra —,
            # e a frase de confirmacao ainda dizia "sua crianca de 3 anos", contradizendo o que a
            # pessoa escreveu no mesmo turno.
            #
            # A REGRA, e por que ela e' a de menor superficie: quando a pessoa esta' FALANDO DE
            # IDADE agora, nenhuma idade lembrada atravessa. Nao ha como saber qual campo o modelo
            # deveria ter usado — isso e' julgamento clinico sobre a frase —, e o lado seguro e'
            # a tabela decidir com o dado que a pessoa acabou de dar (ou com idade ausente, que o
            # catch-all das tabelas trata) em vez de com um dado velho que ela acabou de corrigir.
            logger.info(
                "helena_idade_lembrada_descartada",
                node="classify",
                campo=campo,  # so' o NOME do campo, nunca o valor
                motivo="mensagem_trouxe_idade_em_outro_campo",
            )
            continue
        fundida[campo] = lembrado
        veio_da_memoria[campo] = lembrado
    return fundida, veio_da_memoria


#: POPULACAO -> o vocabulario de `sintoma_codigo` que a tabela DAQUELA populacao conhece. Derivado
#: da mesma declaracao que o `classify_prompt` lista, e `test_helena_codigos_casam_com_a_dmn.py` ja'
#: fixa que cada tupla e' IDENTICA ao conjunto de literais da `spec/processes/dmn/triage_redflag_*`
#: correspondente — e' isso que torna a pergunta abaixo uma pergunta sobre a TABELA, e nao sobre o
#: prompt.
_VOCABULARIO_DA_POPULACAO: dict[str, frozenset[str]] = {
    populacao: frozenset(codigos) for populacao, codigos in SINTOMA_CODIGOS_BY_POPULATION.items()
}


def _sintoma_coerente_com_a_populacao(codigo: object, population: object) -> bool:
    """O `sintoma_codigo` existe na tabela que ESTE turno vai consultar? (CRITICO 2, 22/09/2026)

    O QUE FOI MEDIDO. Mesmo telefone, tres turnos: "meu filho esta com febre" -> "3 anos" ->
    "desde ontem". No turno 3 a decision-instance trouxe `sintoma_codigo=cefaleia_subita_intensa`
    — a `r4` da tabela ADULTA, P1 com prazo de cinco minutos — numa conversa cujo unico sintoma
    relatado foi FEBRE, e cuja populacao e' `pediatric`. `cefaleia_subita_intensa` NAO EXISTE na
    tabela pediatrica.

    POR QUE A REGRA DO QUALIFICADOR (rodada 3) NAO ALCANCAVA ISTO. Ela mora no texto do prompt, e o
    prompt so' ve' a MENSAGEM do turno — "desde ontem". Quem produziu a incoerencia foi a FUSAO: o
    modelo devolveu `population="adult"` (o campo e' obrigatorio, e sem ninguem na mensagem ele
    preenche com o valor mais comum), a memoria corretamente impos `pediatric` por falta de lastro
    (F4), e o codigo de adulto sobreviveu a troca. O par saiu do modelo COERENTE e ficou incoerente
    depois — por isso a pergunta e' feita aqui, DEPOIS da fusao, e nao em `_validate_extraction`,
    que valida cada campo isoladamente contra a UNIAO das quatro allowlists.

    ESTA NAO E' A CERCA LITERAL QUE `prompts.py` RECUSOU A ESCREVER, e a diferenca e' o ponto: uma
    cerca que exigisse as palavras "subita"/"intensa" na mensagem reprovaria o `C4` ("comecou de
    repente e e' a pior da minha vida"), que diz o qualificador com OUTRAS palavras e tem de
    continuar produzindo o codigo. Esta aqui nao le' a mensagem: ela pergunta se o codigo casa
    alguma regra da tabela que vai ser consultada. `adult` + `cefaleia_subita_intensa` continua
    passando, em qualquer redacao.

    AUSENCIA E' COERENTE (`None` -> `True`): um turno sem sintoma e' o caso comum, e as tabelas tem
    catch-all para ele. E uma populacao fora do mapa cai no vocabulario de ADULTO, que e' exatamente
    o default de `_evaluate_dmn` (`_DMN_BY_POPULATION.get(population, "triage_redflag_adult")`) —
    as duas leituras tem de olhar a MESMA tabela, senao a cerca mede outra coisa.
    """
    if codigo is None:
        return True
    vocabulario = _VOCABULARIO_DA_POPULACAO.get(str(population), _VOCABULARIO_DA_POPULACAO["adult"])
    return codigo in vocabulario


def _memoria_a_gravar(
    extraction: dict[str, Any], memoria: dict[str, Any] | None, *, agora: datetime, confirmada: bool
) -> dict[str, Any] | None:
    """A memoria do PROXIMO turno, ou `None` quando nao ha' nada que valha lembrar.

    Grava a extracao JA' FUNDIDA, entao um dado lembrado no turno 2 continua lembrado no turno 3
    sem a pessoa ter de repeti-lo — que e' a diferenca entre lembrar e ter memoria de um turno so'.

    O CARIMBO E' SEMPRE O DE AGORA, e nao o da gravacao original: a janela mede desde a ULTIMA vez
    que o dado foi usado, nao desde a primeira vez que foi dito. Uma conversa ativa nao deveria
    esquecer quem e' o paciente no meio so' porque ela comecou ha' seis horas.
    """
    lembravel = {
        campo: extraction.get(campo)
        for campo in _MEMORIA_CLINICA_CAMPOS
        if extraction.get(campo) is not None and extraction.get(campo) != "none"
    }
    if not lembravel:
        # NADA A LEMBRAR nao apaga o que ja' se lembrava: um turno administrativo no meio de uma
        # conversa clinica ("qual o telefone da central?") nao pode fazer a Helena esquecer o bebe.
        return memoria
    # F5: a marca da avaliacao atravessa o turno junto de quem e' o paciente. Sem esta linha ela
    # morreria no turno seguinte — o mesmo modo de falha que a gravacao da extracao JA' FUNDIDA
    # fechou para as idades ("a diferenca entre lembrar e ter memoria de um turno so'").
    for chave in (_MEMORIA_SINTOMA_AVALIADO, _MEMORIA_INTENSIDADE_AVALIADA):
        if memoria and memoria.get(chave) is not None:
            lembravel[chave] = memoria[chave]
    lembravel[_MEMORIA_GRAVADA_EM] = agora.isoformat()
    lembravel[_MEMORIA_CONFIRMADA] = confirmada
    return lembravel


def _marcar_avaliacao(
    memoria: dict[str, Any] | None, *, sintoma_codigo: object, intensidade: object
) -> dict[str, Any] | None:
    """Registra na memoria que uma avaliacao de red flag ACONTECEU, e com que dado (F5).

    Chamada em `classify` DEPOIS de a DMN responder — e' o unico momento em que "houve avaliacao"
    e' um fato, e nao uma intencao. NAO muta a memoria recebida: devolve copia, pela mesma razao
    que `_fundir_memoria_clinica` nao muta a extracao.

    `None` entra e `None` sai: uma conversa que nao sabe quem e' o paciente nao tem dado que possa
    ser corrigido depois, entao nao ha o que marcar.
    """
    if not memoria or not isinstance(sintoma_codigo, str) or not sintoma_codigo:
        return memoria
    marcada = dict(memoria)
    marcada[_MEMORIA_SINTOMA_AVALIADO] = sintoma_codigo
    marcada[_MEMORIA_INTENSIDADE_AVALIADA] = (
        intensidade if _em_dominio(intensidade, _VALID_INTENSIDADES) else "desconhecida"
    )
    return marcada


def _correcao_de_dado_avaliado(
    extraction: Mapping[str, Any], memoria: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """A mensagem CORRIGE um dado que uma avaliacao desta conversa ja' usou? (F5)

    Devolve o que a reavaliacao precisa (`sintoma_codigo`, `intensidade` e QUAIS campos foram
    corrigidos) ou `None` quando nao ha correcao.

    O SINAL E' DETERMINISTICO, e isso e' o ponto: nao depende de o modelo rotular a intencao como
    "correcao" — nao existe esse rotulo no schema fechado, e inventar um seria dar ao LLM mais uma
    decisao de rota. As tres condicoes sao todas necessarias:

      1. uma AVALIACAO aconteceu nesta conversa (`sintoma_avaliado` na memoria). Sem ela nao ha o
         que refazer, e nenhuma mensagem vira sintoma;
      2. a mensagem NAO traz sintoma proprio. Se traz, o caminho normal ja' resolve, e usar o
         lembrado seria responder a mensagem errada — exatamente o que a decisao de nao lembrar
         sintoma existe para evitar;
      3. a mensagem traz, para um campo que a avaliacao usou, um valor DIFERENTE do lembrado.
         Ausencia nao conta (a mesma regra 3 do documento, do outro lado), e repetir o mesmo dado
         nao e' correcao.

    A DIRECAO DO ERRO. Um gatilho largo aqui faria cada pergunta de cadastro no meio de uma
    conversa clinica reabrir a triagem, com a DMN consultada sobre um sintoma que a pessoa nao
    mencionou naquele turno. Um gatilho estreito deixa a correcao passar como turno administrativo
    — que e' o comportamento de hoje, medido e conhecido. Por isso as tres condicoes, e nao uma.
    """
    if not memoria:
        return None
    avaliado = memoria.get(_MEMORIA_SINTOMA_AVALIADO)
    if not avaliado:
        return None
    if extraction.get("sintoma_codigo") is not None:
        return None
    corrigidos: dict[str, Any] = {}
    for campo in _MEMORIA_CLINICA_CAMPOS:
        novo = extraction.get(campo)
        antigo = memoria.get(campo)
        if novo is None or antigo is None:
            continue
        if campo == "population" and novo == "none":
            continue
        if novo != antigo:
            corrigidos[campo] = novo
    if not corrigidos:
        return None
    intensidade = memoria.get(_MEMORIA_INTENSIDADE_AVALIADA)
    return {
        "sintoma_codigo": avaliado,
        "intensidade": intensidade if _em_dominio(intensidade, _VALID_INTENSIDADES) else "desconhecida",
        "corrigidos": corrigidos,
    }


def _frase_de_confirmacao(lembrados: dict[str, Any]) -> str:
    """O que a Helena diz ao usar um dado lembrado pela primeira vez.

    UMA FRASE, NAO UM FORMULARIO — a decisao 4 do documento e' explicita. E ela e' uma PERGUNTA,
    nao um aviso: a pessoa precisa poder corrigir, porque o custo de uma populacao errada lembrada
    e' a tabela errada consultada em silencio.

    F4, defeito de redacao medido em 21/09/2026: *"seu bebe de 36 meses"* para uma crianca de 3
    anos. Nenhuma mae fala assim, e chamar de bebe quem ja anda soa a erro — o que importa porque
    esta frase existe PARA a pessoa corrigir, e uma frase que soa errada e' uma frase que ela para
    de ler. Acima de 24 meses a idade e' dita em ANOS, com divisao inteira (e' como se fala: 30
    meses e' "2 anos", nao "2 anos e meio"). O limiar e' INCLUSIVE em 24: dois anos ainda e' idade
    de bebe no uso comum, e a `idade_meses` continua sendo o que a DMN pediatrica le' em todos os
    casos — a conversao e' so' da FRASE.

    21/09/2026 (segunda rodada), DUAS ADICOES:

      * `idade_meses=0` sai "seu RECEM-NASCIDO", nao "seu bebe de 0 meses". Ninguem fala assim, e
        vale o mesmo argumento do "bebe de 36 meses": a frase existe para a pessoa corrigir, e uma
        frase que soa como sistema quebrado e' uma frase que ela para de ler. O zero continua
        atravessando toda a cadeia (validacao, fusao, gravacao) — o que muda e' so' a FRASE, e e'
        exatamente o paciente com mais red flag na tabela pediatrica.
      * O RAMO DO SINTOMA (F5). Quando a reavaliacao acontece com o sintoma LEMBRADO, o que a
        pessoa precisa poder corrigir nao e' quem e' o paciente — e' SOBRE O QUE ela esta' falando.
        A frase e' outra ("entendi que ainda e' sobre ..."), e por isso este ramo vem PRIMEIRO: num
        turno de correcao ele e' a confirmacao que importa.
    """
    if lembrados.get(_MEMORIA_SINTOMA_AVALIADO) is not None:
        sobre = sintoma_em_palavras(lembrados[_MEMORIA_SINTOMA_AVALIADO])
        return f"entendi que ainda e' sobre {sobre} que voce contou, certo?"
    if lembrados.get("idade_meses") is not None:
        meses = int(lembrados["idade_meses"])
        if meses == 0:
            quem = "seu recem-nascido"
        elif meses > 24:
            quem = f"sua crianca de {meses // 12} anos"
        else:
            # 21/09/2026 (terceira rodada): "seu bebe de 1 meses". Mesmo argumento das duas
            # adicoes acima — a frase existe PARA a pessoa corrigir, e uma frase que soa a sistema
            # quebrado e' uma frase que ela para de ler.
            quem = f"seu bebe de {meses} {'mes' if meses == 1 else 'meses'}"
    elif lembrados.get("idade_anos") is not None:
        # 21/09/2026 (QUARTA RODADA): "a pessoa de 1 anos" e "a gestacao de 1 semanas". O
        # comentario do ramo do mes afirmava que "o mes e' o unico numero desta funcao que chega a
        # 1", e a afirmacao era FALSA nos dois ramos seguintes: `idade_anos` vem da EXTRACAO (uma
        # pessoa de 1 ano e' um paciente pediatrico comum, e o teto e' o unico limite) e
        # `idade_gestacional_semanas` tambem ("estou de 1 semana"). Divisao inteira so' explica o
        # ramo `meses > 24`, que nao e' nenhum destes dois.
        anos = lembrados["idade_anos"]
        quem = f"a pessoa de {anos} {'ano' if anos == 1 else 'anos'}"
    elif lembrados.get("idade_gestacional_semanas") is not None:
        semanas = lembrados["idade_gestacional_semanas"]
        quem = f"a gestacao de {semanas} {'semana' if semanas == 1 else 'semanas'}"
    elif lembrados.get("population") == "pediatric":
        quem = "sua crianca"
    elif lembrados.get("population") == "gestante":
        quem = "a gestacao"
    else:
        quem = "a mesma pessoa de antes"
    return f"entendi que voce esta falando sobre {quem}, certo?"


assert frozenset(_HELENA_NEUTRAL_OUTPUTS) >= _HELENA_MEMORIA_DE_CONVERSA
if frozenset(HelenaState.__annotations__) != _HELENA_ALL_FIELDS:
    _missing = frozenset(HelenaState.__annotations__) - _HELENA_ALL_FIELDS
    _extra = _HELENA_ALL_FIELDS - frozenset(HelenaState.__annotations__)
    raise RuntimeError(
        "HelenaState input/output field split is incomplete (T1.11 input-boundary gate): "
        f"unclassified fields={sorted(_missing)} stale entries={sorted(_extra)} — every "
        "HelenaState key MUST be either an INPUT field or carry a neutral output default."
    )


def new_helena_state(
    *,
    tenant_id: str,
    conversation_id: str,
    canal: str,
    beneficiario_pseudo_id: str,
    message_body: str,
    agente_ativo_conversa: str = "helena",
    pedido_humano_lexico: bool = False,
    sinal_saude_lexico: bool = False,
    message_ref: str = "",
    identidade_beneficiario: Mapping[str, Any] | None = None,
    identidade_desfecho: str | None = None,
) -> HelenaState:
    """Typed input-boundary constructor for a fresh Helena turn (T1.11).

    This is the production construction seam's ONLY sanctioned way to build a `HelenaState`: its
    explicit keyword-only signature makes it STRUCTURALLY impossible to pass an output-only key
    through it (a forged `next_kind`/`error`/`escalation_*`/`dmn_decision_ref`). Every accepted
    argument is an `HELENA_INPUT_FIELDS` member.

    NUMERO UNICO (onda e): os quatro ultimos argumentos so' sao passados pelo despachante com o
    roteador ligado. Os defaults sao o NEUTRO — `helena`, nenhum sinal lexico, sem referencia — e
    sao GRAVADOS mesmo assim: com checkpoint, a entrada deste turno e' mesclada sobre o estado
    salvo, e um `lucas` ou um `True` de um turno anterior decidiria este turno se nao fosse
    reescrito aqui (o mesmo argumento de `origem_do_turno`). Valor fora do dominio e' recusado:
    quem monta a entrada e' codigo, e um tipo errado aqui e' defeito de composicao.
    """
    if agente_ativo_conversa not in AGENTES_DA_CONVERSA:
        raise ValueError("new_helena_state: agente_ativo_conversa fora do dominio")
    if not isinstance(pedido_humano_lexico, bool) or not isinstance(sinal_saude_lexico, bool):
        raise ValueError("new_helena_state: sinais lexicos devem ser booleanos")
    if not isinstance(message_ref, str):
        raise ValueError("new_helena_state: message_ref deve ser texto")
    identidade = None
    if identidade_beneficiario is not None:
        identidade = normalizar_identidade(identidade_beneficiario)
        if identidade is None:
            raise ValueError("new_helena_state: identidade_beneficiario fora do vocabulario fechado")
    # DL-0078: o desfecho e' fechado e COERENTE com a identidade — `reconhecido` sem identidade (ou
    # identidade com outro desfecho declarado) e' defeito de composicao, recusado aqui.
    if identidade_desfecho is not None and identidade_desfecho not in IDENTIDADE_DESFECHOS:
        raise ValueError("new_helena_state: identidade_desfecho fora do dominio")
    if identidade_desfecho is not None and (identidade_desfecho == IDENTIDADE_RECONHECIDA) != (
        identidade is not None
    ):
        raise ValueError("new_helena_state: identidade_desfecho incoerente com identidade_beneficiario")
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "canal": canal,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "message_body": message_body,
        # GAP-XHITL-4: SEMPRE gravado. Com checkpoint, a entrada deste turno e' mesclada sobre o
        # estado salvo — um `retomada` deixado por uma retomada que falhou no meio decidiria a
        # rota do proximo turno do beneficiario se este campo nao fosse reescrito aqui.
        "origem_do_turno": ORIGEM_BENEFICIARIO,
        "agente_ativo_conversa": agente_ativo_conversa,
        "pedido_humano_lexico": pedido_humano_lexico,
        "sinal_saude_lexico": sinal_saude_lexico,
        "message_ref": message_ref,
        # DL-0077: SEMPRE gravada (neutro `None`): com checkpoint a entrada e' mesclada sobre o estado
        # salvo, e a identidade de um turno anterior nao pode sobreviver a um turno sem ela.
        "identidade_beneficiario": identidade,
        # DL-0078: SEMPRE gravado, pela mesma razao (neutro `None` = nenhum texto de identidade).
        "identidade_desfecho": identidade_desfecho,
    }


def new_helena_resume_state(
    *,
    tenant_id: str,
    conversation_id: str,
    canal: str,
    instrucoes: str,
) -> HelenaState:
    """Construtor tipado do turno de RETOMADA (GAP-XHITL-4) — o unico que abre a porta `resume`.

    Nao leva `message_body` nem `beneficiario_pseudo_id`: o turno nao nasce de uma mensagem do
    beneficiario, e o checkpoint da conversa ja' tem os dois. `instrucoes` e' o `notas_resolucao`
    lido do historico do motor pelo consumidor — conteudo NAO CONFIAVEL, cercado em `resume`.
    """
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "canal": canal,
        "origem_do_turno": ORIGEM_RETOMADA,
        "retomada_instrucoes": instrucoes,
    }


def gate_inbound_state(raw: Mapping[str, Any]) -> HelenaState:
    """Fail-closed input allowlist for seams that receive a raw mapping (A2A/delegation, future).

    Only `HELENA_INPUT_FIELDS` keys survive; EVERY other key — i.e. any caller-planted output
    field — is DROPPED (never reaches a downstream node) and logged. Use this at any seam that
    assembles Helena state from an untrusted/upstream dict; use `new_helena_state` where the
    input scalars are already in hand (the dispatch path).
    """
    dropped = sorted(k for k in raw if k not in HELENA_INPUT_FIELDS)
    if dropped:
        logger.warning("helena_inbound_output_fields_dropped", dropped=dropped)
    gated = {k: raw[k] for k in HELENA_INPUT_FIELDS if k in raw}
    # GAP-XHITL-4: a origem NAO e' escolhida por quem chama por aqui. Um mapeamento cru (A2A,
    # delegacao) e' sempre um turno de beneficiario; a porta `resume` so' abre pelo construtor
    # tipado `new_helena_resume_state`.
    if raw.get("origem_do_turno", ORIGEM_BENEFICIARIO) != ORIGEM_BENEFICIARIO:
        logger.warning("helena_inbound_origem_forcada", origem_recebida_ignorada=True)
    gated["origem_do_turno"] = ORIGEM_BENEFICIARIO
    # NUMERO UNICO (onda e): um mapeamento cru nao escolhe quem esta' com a conversa — so' o
    # despachante, que le' a tabela do roteador, sabe isso. `helena` sempre (o pior caso e' a frase
    # de passagem sair de novo). Os sinais lexicos so' atravessam como `True` literal: os dois so'
    # empurram para o lado seguro (uma pessoa / bloquear a passagem), entao um `True` plantado nao
    # tira ninguem da triagem. Qualquer outro valor vira o neutro.
    gated["agente_ativo_conversa"] = "helena"
    gated["pedido_humano_lexico"] = raw.get("pedido_humano_lexico") is True
    gated["sinal_saude_lexico"] = raw.get("sinal_saude_lexico") is True
    ref = raw.get("message_ref")
    gated["message_ref"] = ref if isinstance(ref, str) else ""
    # DL-0077: um mapeamento cru nao carrega identidade — so' o despachante, que a resolve pela AMH,
    # chama `new_helena_state` com ela. Qualquer valor plantado aqui vira o neutro.
    gated["identidade_beneficiario"] = None
    # DL-0078: idem para o desfecho — sem despachante, nenhum texto de identidade.
    gated["identidade_desfecho"] = None
    return cast(HelenaState, gated)


# --- Helpers ---------------------------------------------------------------------------------


def _business_key(state: HelenaState) -> str:
    """Idempotent business key per contract: `ESC-{tenant_id}-{conversation_id}`."""
    return f"ESC-{state.get('tenant_id', '')}-{state.get('conversation_id', '')}"


def _to_hash_from_state(state: HelenaState) -> str:
    """Extract the phone hash from `conversation_id = wa:{tenant}:{phone_hash}`.

    Falls back to the raw `conversation_id` as an opaque destination handle when the format
    doesn't match (e.g. `canal != whatsapp`) — the sender treats it as an opaque key regardless.
    """
    conv = state.get("conversation_id", "")
    if conv.startswith("wa:"):
        parts = conv.split(":", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    return conv


#: PERGUNTA DE IDENTIDADE NAO E' PEDIDO DE HUMANO (bateria de 23/09/2026, caso `A2`).
#:
#: O QUE FOI MEDIDO. "quem e voce?" e "voce e uma pessoa ou um robo?" sairam do classificador como
#: `human_request` e abriram SP-OP-ESCALATION-001: `solicitacao_humano`, P3, fila
#: `atendimento-humano`, prazo de 4h — trabalho numa fila humana para uma pergunta que a propria
#: Helena responde, e responde por obrigacao (a transparencia de "sou um assistente virtual" e'
#: requisito, `response-v9`). O prompt ja' diz que `human_request` exige pedido EXPLICITO; a rota
#: nao pode depender da obediencia do modelo — o mesmo principio da "saudacao com sintoma" acima.
#:
#: A CERCA E' ESTREITA DE PROPOSITO, e o lado que ela protege e' o de quem QUER um humano. So'
#: converte quando a mensagem (normalizada) e' uma pergunta sobre o que a Helena e' E nao traz
#: pedido nenhum. "voce e um robo? quero falar com uma pessoa" continua `human_request`: o pedido
#: explicito vence. Um falso negativo aqui (deixar passar uma pergunta de identidade) custa um
#: chamado P3 a mais; um falso positivo (engolir um pedido de humano) custaria atendimento — por
#: isso o padrao de PEDIDO e' largo e o de IDENTIDADE e' estreito.
_PERGUNTA_DE_IDENTIDADE = re.compile(
    r"\bquem (?:e|eh|seria|esta falando|fala)\b"
    r"|\bcom quem (?:eu )?(?:estou|to|tou|falo)\b"
    r"|\b(?:voce|vc|tu|isso|isto) (?:e|eh|seria)(?: uma?)? (?:pessoa|humano|humana|gente|robo|bot|ia"
    r"|inteligencia artificial|maquina|sistema|assistente|atendente|real|de verdade)\b"
    r"|\b(?:e|eh) (?:uma? )?(?:pessoa|humano|humana|robo|bot|ia|maquina)\b"
    # 23/09/2026, caso `R6` da bateria v2: a pergunta sobre a identidade do BENEFICIARIO ("voce
    # sabe quem eu sou?", "voce me conhece?") tem a mesma resposta honesta — "por aqui eu nao
    # consigo te identificar" (F6) — e o mesmo defeito: saiu `human_request` e abriu P3.
    r"|\b(?:voce|vc|tu) (?:sabe|conhece|lembra) (?:quem (?:eu )?sou|meu nome|de mim|com quem)\b"
    r"|\b(?:voce|vc|tu) me (?:conhece|identifica|reconhece|conhecia)\b"
    r"|\bsabe (?:o )?meu nome\b"
)
_PEDIDO_DE_HUMANO = re.compile(
    r"\b(?:falar|conversar|atendimento|atender|atendente|transfer\w*|passa\w*|chama\w*|liga\w*"
    r"|ligue|operador\w*|enfermeir\w*|medic\w*|responsavel|gerente|supervisor\w*)\b"
    r"|\bquero (?:uma? )?(?:pessoa|humano|alguem)\b"
    r"|\bpreciso (?:de )?(?:uma? )?(?:pessoa|humano|alguem)\b"
)


# AMEACA DE RECLAMACAO A ORGAO REGULADOR (DL-0070, 02/10/2026, caso B13: "vou abrir reclamacao na
# ANS, ninguem resolve nada"). Antes recebia o texto fixo de fora do canal e a pessoa ficava sem
# ninguem; agora abre atendimento humano pela rota que ja' existe (`solicitacao_humano`, P3). Os
# dois lados do padrao sao exigidos — um orgao citado SEM verbo de reclamacao ("o que e a ANS?")
# nao escala. INTERINO: motivo e fila proprios (SP-OP-NIP-001) dependem do Diretor e do regulatorio.
_AMEACA_REGULATORIA = re.compile(
    r"\b(?:reclam\w+|denunci\w+|queix\w+|processar|aciona\w*|recorr\w+|registr\w+)\b"
    r"[^.!?\n]{0,40}\b(?:ans|procon|ouvidoria|justica|agencia nacional de saude)\b"
    r"|\b(?:ans|procon|ouvidoria|justica)\b[^.!?\n]{0,25}\b(?:reclam\w+|denunci\w+|queix\w+)\b"
)


def _ameaca_regulatoria(texto: str) -> bool:
    """`True` quando a mensagem ameaca ou anuncia reclamacao a ANS, Procon, ouvidoria ou justica."""
    return bool(_AMEACA_REGULATORIA.search(_normalizar_texto(texto or "")))


def _pergunta_de_identidade_sem_pedido(texto: str, *, incluir_quem_sou_eu: bool = False) -> bool:
    """`True` quando a mensagem pergunta O QUE a Helena e' e nao pede humano nenhum.

    `incluir_quem_sou_eu` (DL-0078) so' e' `True` com o desfecho de identidade DETERMINADO: ai as formas
    largas de "sabe quem sou eu?" (`_PERGUNTA_QUEM_SOU_EU`) tambem contam. Desligado, nada muda.
    """
    plano = _normalizar_texto(texto or "")
    # Os padroes estritos de baixo cobrem as duas perguntas que o prompt de resposta lista como
    # identidade e que `_PERGUNTA_DE_IDENTIDADE` nao casava ("isso e automatico?", "estou falando
    # com uma pessoa?"): classificadas `human_request`, abriam P3 sem que ninguem tivesse pedido.
    e_identidade = (
        _PERGUNTA_DE_IDENTIDADE.search(plano)
        or _PERGUNTA_SOBRE_A_HELENA.search(plano)
        or _PERGUNTA_SOBRE_O_BENEFICIARIO.search(plano)
        or (incluir_quem_sou_eu and _pergunta_quem_sou_eu(plano))
    )
    return bool(e_identidade) and not _PEDIDO_DE_HUMANO.search(plano)


# AS DUAS FRASES DE CONFORMIDADE (F6, 21/09/2026) NAO PODEM DEPENDER DA INTENCAO (01/10/2026).
#
# O DEFEITO, medido na bateria de 01/10 (A04 e A05) depois do DL-0052 (#577): "voce e uma pessoa ou
# um robo?" e "voce sabe quem eu sou?" passaram a sair do classificador como `outside_channel`
# ("conversa sem relacao com saude") e `inform` devolve `RESPOSTA_FORA_DO_CANAL` SEM chamar o
# modelo. O desvio de 23/09 (`classify`, acima) so' roda quando a intencao e' `human_request`, e
# as frases F6 so' existem no prompt de resposta, que esse caminho nao usa. A resposta que o dono
# exigiu — dizer que e' um sistema automatizado — deixou de ser dada, e nenhum teste pegou porque os
# de A2 forjam o classificador em `human_request` e nunca leem o texto enviado.
#
# A CORRECAO E' DETERMINISTICA E NAO TOCA O CLASSIFICADOR: a mesma pergunta recebe a mesma frase,
# qualquer que seja a intencao que o modelo atribuiu. Os padroes abaixo sao MAIS ESTREITOS que
# `_PERGUNTA_DE_IDENTIDADE` (que so' decide "nao e' pedido de humano" e por isso aceita "quem e"
# solto): aqui o custo de um falso positivo e' responder "sou um assistente virtual" a "quem e o
# titular do contrato?", entao so' casa a pergunta sobre a propria Helena.
_PERGUNTA_SOBRE_A_HELENA = re.compile(
    r"\bquem (?:e|eh|seria) (?:voce|vc|tu)\b"
    r"|\bquem (?:esta|ta) falando\b"
    r"|\bcom quem (?:eu )?(?:estou|to|tou|falo)\b"
    r"|\b(?:voce|vc|tu|isso|isto) (?:e|eh|seria)(?: uma?)? (?:pessoa|humano|humana|gente|robo|bot|ia"
    r"|inteligencia artificial|maquina|sistema|assistente|atendente|real|de verdade)\b"
    r"|\bfalando com (?:uma? )?(?:pessoa|humano|humana|robo|bot|maquina)\b"
    r"|\b(?:e|eh) (?:uma? )?(?:pessoa|humano|humana|robo|bot|maquina)\b"
    r"|\b(?:isso|isto|esse atendimento|este atendimento) (?:e|eh) automatic[oa]\b"
)
_PERGUNTA_SOBRE_O_BENEFICIARIO = re.compile(
    r"\b(?:voce|vc|tu) (?:sabe|conhece|lembra) (?:quem (?:eu )?sou|meu nome|de mim|com quem)\b"
    r"|\b(?:voce|vc|tu) me (?:conhece|identifica|reconhece|conhecia)\b"
    r"|\bsabe (?:o )?meu nome\b"
)

# AVISO DE IDENTIDADE (DL-0078, 07/10/2026): "sabe quem sou eu?" nas formas que o dono listou ("sabe quem
# sou eu?", "voce sabe quem eu sou", "quem sou eu", "me reconhece?", "sabe com quem esta falando?"). MAIS
# LARGO que `_PERGUNTA_SOBRE_O_BENEFICIARIO` (que exige o "voce") e por isso SO' consultado com o desfecho
# de identidade DETERMINADO (`reconhecido`/`nao_encontrado`): flag desligada ou indeterminado = nada muda.
# Aplicado ao texto normalizado SEM pontuacao (`_sem_pontuacao`): caixa, acento e pontuacao nao decidem.
_PERGUNTA_QUEM_SOU_EU = re.compile(
    r"\bquem (?:eu )?sou(?: eu)?\b"
    r"|\b(?:me|mi) (?:conhece|reconhece|identifica|reconheceu|identificou)\b"
    r"|\bsabe com quem (?:(?:voce|vc|tu) )?(?:esta|ta|tah|estah) falando\b"
    r"|\bsabe (?:o )?meu nome\b"
)
# "nao sei (mais) quem sou eu" NAO e' pergunta de identidade: pode ser sofrimento psiquico, e quem decide
# esse caminho e' a classificacao + a tabela de saude mental, nunca um texto fixo de cadastro.
_NAO_SEI_QUEM_SOU = re.compile(r"\bnao sei (?:mais )?quem (?:eu )?sou\b")
# "sabe com quem esta falando?" e' a expressao idiomatica sobre QUEM ESCREVE, nao sobre a Helena. Com o
# desfecho determinado ela e' retirada antes de `_PERGUNTA_SOBRE_A_HELENA`, que casaria "quem esta falando"
# e responderia "sou um assistente virtual" a uma pergunta que nao era sobre ela.
_SABE_COM_QUEM_FALA = re.compile(r"\bsabe com quem (?:(?:voce|vc|tu) )?(?:esta|ta|tah|estah) falando\b")
# Mencao de cobranca: com ela, a pergunta de identidade NAO e' a mensagem inteira — fluxo normal.
_MENCAO_DE_COBRANCA = re.compile(
    r"\b(?:boletos?|faturas?|cobrancas?|mensalidades?|pagamentos?|pagar|paguei|segunda via|2a via|pix"
    r"|debito|vencimento|vencid[oa]s?|atrasad[oa]s?|reembolso)\b"
)


def _sem_pontuacao(plano: str) -> str:
    """O texto ja' normalizado, com pontuacao virando espaco e espacos colapsados."""
    return " ".join(re.sub(r"[^\w\s]", " ", plano).split())


def _pergunta_quem_sou_eu(plano: str) -> bool:
    """`True` quando o texto normalizado pergunta se a Helena sabe QUEM ESCREVE (DL-0078)."""
    limpo = _sem_pontuacao(plano)
    if _NAO_SEI_QUEM_SOU.search(limpo):
        return False
    return bool(_PERGUNTA_SOBRE_O_BENEFICIARIO.search(plano) or _PERGUNTA_QUEM_SOU_EU.search(limpo))


def _desfecho_determinado(estado: Mapping[str, Any]) -> bool:
    """O desfecho deste turno permite texto de identidade (`reconhecido`/`nao_encontrado`)."""
    return estado.get("identidade_desfecho") in (IDENTIDADE_RECONHECIDA, IDENTIDADE_NAO_ENCONTRADA)


def _resposta_fixa_de_identidade(
    estado: Mapping[str, Any], *, nome_operadora: str = NOME_OPERADORA_PADRAO
) -> str | None:
    """As frases F6 EXATAS do dono (`prompts.RESPOSTA_*`), ou `None` se nao e' pergunta de identidade.

    So' vale para a mensagem que e' PERGUNTA de identidade e nada mais: sem codigo de sintoma, sem
    risco psicossocial e sem pedido de humano (esses tem rota propria e vencem — "voce e um robo?
    quero falar com uma pessoa" continua `human_request`). Pergunta sobre a Helena e sobre a pessoa
    na mesma mensagem recebe as duas frases, nessa ordem.

    DL-0078: com o desfecho de identidade DETERMINADO e a mensagem sendo SO' a pergunta "sabe quem sou
    eu?" (alem do acima: sem sinal lexico de saude e sem mencao de cobranca), a frase sobre a pessoa e' a
    do aviso de identidade (`texto_pergunta_de_identidade`) no lugar de `RESPOSTA_NAO_CONSIGO_IDENTIFICAR`.
    Qualquer outro caso cai exatamente no caminho de antes. Nenhum dado do plano e' dito, nunca.
    """
    if estado.get("sintoma_codigo") or estado.get("psychosocial_risk") is True:
        return None
    plano = _normalizar_texto(str(estado.get("message_body") or ""))
    if _PEDIDO_DE_HUMANO.search(plano):
        return None
    sobre_a_pessoa: str | None = None
    plano_helena = plano
    texto_identidade = texto_pergunta_de_identidade(
        estado.get("identidade_desfecho"), nome_operadora=nome_operadora
    )
    if (
        texto_identidade is not None
        and _pergunta_quem_sou_eu(plano)
        and estado.get("sinal_saude_lexico") is not True
        and estado.get("intent") != INTENT_COBRANCA
        and not _MENCAO_DE_COBRANCA.search(_sem_pontuacao(plano))
    ):
        sobre_a_pessoa = texto_identidade
        plano_helena = _SABE_COM_QUEM_FALA.sub(" ", _sem_pontuacao(plano))
    elif _PERGUNTA_SOBRE_O_BENEFICIARIO.search(plano):
        sobre_a_pessoa = RESPOSTA_NAO_CONSIGO_IDENTIFICAR
    partes: list[str] = []
    if _PERGUNTA_SOBRE_A_HELENA.search(plano_helena):
        partes.append(RESPOSTA_SOU_ASSISTENTE_VIRTUAL)
    if sobre_a_pessoa is not None:
        partes.append(sobre_a_pessoa)
    return " ".join(partes) or None


def _severidade_from_prioridade(prioridade: str) -> Severidade:
    """P1 -> grave; P2 -> moderada; anything else -> leve (contract SP-OP-ESCALATION-001)."""
    if prioridade == "P1":
        return "grave"
    if prioridade == "P2":
        return "moderada"
    return "leve"


def _severidade_de_intensidade(intensidade: object) -> Severidade:
    """HEL-04: severidade do gatilho 4 (`falha_tecnica`) a partir da `intensidade` JA VALIDADA.

    O achado, reproduzido na base `87b51a8`: a DMN caia sobre um sintoma classificado com
    `intensidade="grave"` e o caso chegava ao atendente rotulado `"leve"` — o literal estava
    escrito no codigo, sem consultar campo nenhum.

    TETO DELIBERADO EM `moderada`. O contrato SP-OP-ESCALATION-001 §Variaveis de entrada reserva
    `grave` para red flag P1 — um veredito que so' a DMN emite, e neste ramo a DMN nao emitiu
    nenhum. Anunciar `grave` aqui seria fabricar o veredito que faltou; anunciar `leve` seria
    fabricar o oposto.

    SO' UM `leve` EXPLICITO PRODUZ `leve` — `desconhecida`, ausente ou qualquer outro valor viram
    `moderada`. E' o mesmo idioma de `_is_explicitly_false` neste modulo, pela mesma razao: uma
    intensidade que ninguem apurou nao e' a mais branda, e o proprio HELENA-SEVERIDADE-DEFAULT (ja
    em main) fixou que um valor desconhecido nunca vira `leve`. A diferenca em relacao aquele gap
    e' que la' nao havia sintoma nenhum (contexto de runtime ausente) e aqui HA' um sintoma
    reportado — por isso aqui a leitura conservadora e' `moderada`, e nao `None`.
    """
    if isinstance(intensidade, str) and intensidade.strip().lower() == "leve":
        return "leve"
    return "moderada"


#: HEL-03: os UNICOS `intent` que podem terminar em `inform`. Allowlist FECHADA: um `intent` novo
#: (ou nenhum) e' recusado por omissao, em vez de cair na rota informativa por ser o `else` do
#: encadeamento de gatilhos.
#:
#: `greeting` entrou em 11/09/2026 e a admissao e' DELIBERADA, nao um efeito colateral de o
#: intent existir: uma saudacao sem pedido nao tem sintoma, entao a precondicao da DMN abaixo
#: nao se aplica a ela, e responder "bom dia" nao e' resolver preocupacao clinica nenhuma. Era
#: justamente esta allowlist que fazia um `greeting` recem-criado cair em `falha_tecnica` — o
#: comportamento correto ate' alguem decidir, que e' o que esta linha registra.
#:
#: `outside_channel` e `scheduling` entraram em 01/10/2026 (decisao do Diretor de Tecnologia, DL-0052):
#: assunto que nao e' saude recebe `RESPOSTA_FORA_DO_CANAL` e NAO abre processo. A admissao nao
#: afrouxa a precondicao da DMN: com `sintoma_codigo` preenchido ela continua exigindo o veredito.
_INTENTS_ADMISSIVEIS_INFORM: frozenset[str] = frozenset(
    {"information", "symptom", "greeting", "outside_channel", "scheduling"}
)

#: Os `intent` cuja resposta e' a constante `RESPOSTA_FORA_DO_CANAL`, sem passar pelo modelo.
_INTENTS_FORA_DO_CANAL: frozenset[str] = frozenset({"outside_channel", "scheduling"})


def _inform_recusado(estado: Mapping[str, Any]) -> str | None:
    """HEL-03: precondicao DETERMINISTICA da rota `inform`. Devolve o TOKEN DE CLASSE da condicao
    violada, ou `None` quando `inform` e' admissivel.

    O ACHADO. Ate' `87b51a8` a unica barreira contra a rota informativa era a instrucao de prompt
    (`prompts.py`) — e a saida do LLM sozinha escolhia a rota. A reproducao viva: uma extracao
    `{"intent":"information", "sintoma_codigo":"dor_toracica", "intensidade":"grave"}` — dentro do
    schema fechado, portanto ACEITA pelo validador — roteava para `inform` com
    `dmn_decision_ref = None`, ou seja, com a DMN de red flag nunca consultada. E' exatamente o que
    uma injecao pelo WhatsApp consegue pedir (HEL-06): nao inventar um campo, apenas escolher o
    valor valido que suprime o escalonamento.

    A REGRA, E POR QUE ELA NAO E' UMA REGRA DE NEGOCIO NOVA (C3). Nenhuma decisao clinica mora
    aqui: quem decide `red_flag`/`conduta` continua sendo a DMN (ADR-0012). Esta funcao so' exige
    que o veredito EXISTA antes de a conversa terminar em resposta automatica — `sintoma_codigo`
    preenchido (venha de que `intent` vier) obriga a haver `dmn_decision_ref`, e so' um
    `red_flag is False` EXPLICITO com `conduta` fora de `ESCALATE*` libera. As quatro tabelas
    `spec/processes/dmn/triage_redflag_*.dmn` emitem `red_flag` em TODA regra, catch-all inclusive
    (typeRef `boolean`), entao exigir o `False` explicito nao pede nada que as tabelas nao deem —
    e nenhuma DMN precisou mudar para isto.

    Chamada em DUAS camadas, como a barreira de entrada deste mesmo modulo (`receive` + o gate de
    construcao): `_rota_informativa` (dentro de `classify`) e `HelenaGraph._route` (a aresta
    condicional). A segunda e' o backstop estrutural — se um `classify` futuro regredir e devolver
    `next_kind="inform"` sem justificativa, a aresta nao entrega o turno ao no' `inform`.
    """
    if estado.get("error"):
        return "erro_do_turno"
    if estado.get("psychosocial_risk") is True:
        return "risco_psicossocial"
    if estado.get("intent") not in _INTENTS_ADMISSIVEIS_INFORM:
        return "intent_fora_da_allowlist"
    if not (estado.get("sintoma_codigo") or estado.get("intent") == "symptom"):
        return None
    if not estado.get("dmn_decision_ref"):
        return "sintoma_sem_veredito_da_dmn"
    decisao = estado.get("dmn_decision") or {}
    if decisao.get("red_flag") is not False:
        return "red_flag_da_dmn"
    if str(decisao.get("conduta", "")).startswith("ESCALATE"):
        return "conduta_de_escalonamento_da_dmn"
    return None


def _rota_informativa(update: dict[str, Any]) -> dict[str, Any]:
    """HEL-03, camada 1: so' roteia para `inform` se `_inform_recusado` deixar; caso contrario
    converte o turno em escalonamento `falha_tecnica`.

    `falha_tecnica` e' o rotulo honesto para toda recusa desta guarda: os demais tokens de
    `_inform_recusado` (`risco_psicossocial`, `red_flag_da_dmn`, ...) descrevem estados que os
    gatilhos de `classify` JA tratam antes de chegar aqui — ve-los significa que o grafo alcancou
    um estado que nao sabe justificar, e um estado injustificado e' uma falha da automacao, nao um
    veredito clinico que ela possa nomear. A severidade vem de `_severidade_de_intensidade`
    (HEL-04), nunca de um literal.
    """
    recusa = _inform_recusado(update)
    if recusa is None:
        update["next_kind"] = "inform"
        return update
    logger.warning("helena_inform_recusado", motivo=recusa, node="classify")
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "falha_tecnica"
    update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
    update["error"] = f"{ERRO_INFORM_RECUSADO}: {recusa}"
    return update


def _handoff_recusado(estado: Mapping[str, Any], *, roteador_ligado: bool) -> str | None:
    """NUMERO UNICO (onda e; plano §2.4): precondicao DETERMINISTICA da passagem ao Lucas. Devolve
    o TOKEN DE CLASSE da condicao violada, ou `None` quando a passagem e' admissivel.

    A REGRA ESTRUTURAL (§2.3): ir para o Lucas exige que NADA de saude, risco ou pedido de pessoa
    tenha aparecido neste turno — nem no classify (`psychosocial_risk`, `sintoma_codigo`, `intent`)
    nem nos lexicos deterministicos (`sinal_saude_lexico`, `pedido_humano_lexico`). Um classify que
    falhou (`error`) nunca vai ao Lucas. A ordem dos testes nao muda o desfecho (qualquer recusa e'
    recusa); ela so' escolhe QUAL token aparece no log.

    Chamada em DUAS camadas, como `_inform_recusado`: `_rota_de_cobranca` (dentro de `classify`,
    produz o rotulo honesto) e `HelenaGraph._route_com_roteador` (a aresta — o backstop estrutural, para o dia
    em que um `classify` futuro regredir e devolver `next_kind="handoff"` sem justificativa).
    """
    if estado.get("error"):
        return "erro_do_turno"
    if not roteador_ligado:
        return "roteador_desligado"
    if estado.get("intent") != INTENT_COBRANCA:
        return "intent_nao_e_cobranca"
    if estado.get("psychosocial_risk") is not False:
        return "risco_psicossocial"
    if estado.get("sintoma_codigo"):
        return "sintoma_reportado"
    if estado.get("sinal_saude_lexico") is not False:
        return "sinal_saude_lexico"
    if estado.get("pedido_humano_lexico") is not False:
        return "pedido_humano_lexico"
    if not _em_dominio(estado.get("cobranca_subtipo"), _VALID_COBRANCA_SUBTIPOS):
        return "subtipo_fora_do_dominio"
    competencia = estado.get("cobranca_competencia")
    if competencia is not None and not competencia_valida(competencia):
        return "competencia_invalida"
    ref = estado.get("message_ref")
    if not isinstance(ref, str) or not ref:
        return "sem_referencia_da_mensagem"
    return None


def _rota_de_cobranca(
    update: dict[str, Any], state: Mapping[str, Any], *, roteador_ligado: bool
) -> dict[str, Any]:
    """Onda (e), camada 1: so' roteia para `handoff` se `_handoff_recusado` deixar; caso contrario
    o turno vira escalonamento `falha_tecnica` — estado injustificado vai para humano (§2.4), o
    mesmo desenho de `_rota_informativa`. As entradas lexicas vem do `state` (o despachante as
    escreve); o resto vem do que `classify` acabou de decidir."""
    estado = {
        **update,
        "sinal_saude_lexico": state.get("sinal_saude_lexico", False),
        "pedido_humano_lexico": state.get("pedido_humano_lexico", False),
        "message_ref": state.get("message_ref", ""),
    }
    recusa = _handoff_recusado(estado, roteador_ligado=roteador_ligado)
    if recusa is None:
        update["next_kind"] = RESPONSE_KIND_HANDOFF
        return update
    logger.warning("helena_handoff_recusado", motivo=recusa, node="classify")
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "falha_tecnica"
    update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
    update["error"] = f"{ERRO_HANDOFF_RECUSADO}: {recusa}"
    return update


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from an LLM's free-text response.

    `InferenceProvider.generate` returns a plain string (no structured-output mode, unlike the
    v1 donor's `.complete(..., response_schema=...)`) — this defensively locates the first
    `{...}` span and parses it, tolerating stray prose/markdown fencing around the JSON. Returns
    `None` (never raises) on any parse failure — the caller (`_classify_llm`) treats `None` as
    a CLASSIFY FAILURE and routes to escalation (R1 cycle-1 fix), never a benign default.
    """
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# The three age signals the red-flag DMN tables consume, each DMN-typed `integer`
# (`idade_anos`->triage_redflag_adult, `idade_meses`->triage_redflag_pediatric,
# `idade_gestacional_semanas`->triage_redflag_gestante). They arrive from the classify LLM's
# free-text JSON and drive which pediatric/gestational/adult red-flag rows can fire — a wrong
# age could MISS a red flag, so they are validated (below) exactly like every other classify
# field before ever reaching the DMN.
_AGE_FIELDS: tuple[str, ...] = ("idade_anos", "idade_meses", "idade_gestacional_semanas")


def _coerce_age(value: Any) -> tuple[bool, int | None]:
    """Coerce an LLM-sourced age value to a non-negative `int`, fail-closed on ambiguity.

    Returns `(ok, coerced)`:
    - `None`/absent -> `(True, None)` — many turns carry no age; that is valid, the DMN's own
      catch-all handles the missing-age case;
    - a native `int` that is not a `bool` and `>= 0` -> `(True, value)`;
    - a clean non-negative integer STRING like `"5"` -> `(True, 5)` (the classify LLM emits JSON
      where a number may arrive quoted);
    - ANYTHING else -> `(False, None)`: a `bool`, a float / fractional or signed string
      (`5.9`, `"5.9"`, `"-5"`), a non-numeric string, a list/dict. We NEVER `int(float(...))` a
      `"5.9"` into `5` — a plausible-but-wrong age could mis-triage — the caller fails closed
      (escalate to a human) instead, never passing an ambiguous value to the DMN.

    The string guard is `str.isdecimal()`, NOT `str.isdigit()`: `isdigit()` is `True` for
    Unicode digit glyphs that `int()` cannot parse (superscripts like `"²"`, circled digits),
    so `isdigit()` + `int()` would RAISE on such input; `isdecimal()` admits only base-10 digits
    `int()` accepts, so a Unicode-digit glyph fails CLOSED to `(False, None)` here rather than
    raising up an unwrapped call site.
    """
    if value is None:
        return True, None
    if isinstance(value, bool):  # bool is an int subclass — a `true`/`false` age is malformed.
        return False, None
    if isinstance(value, int):
        return (True, value) if value >= 0 else (False, None)
    if isinstance(value, str) and value.strip().isdecimal():  # base-10 digits only -> int()-safe.
        return True, int(value.strip())
    return False, None


def _is_explicitly_false(value: Any) -> bool:
    """True only for an EXPLICIT boolean-false signal (native `False` or the string `"false"`).

    Mirrors this codebase's `_is_true` idiom (fraude/contas workers) but inverted for the
    conservative red-flag posture: any value that is NOT an explicit false — `None`, `"true"`,
    an unparseable string, a list — is treated as NOT-explicitly-false so the caller can fail
    SAFE toward immediate risk. Never coerces a truthiness (`bool("false") == True`), which is
    the very bug this replaces at the `risco_imediato` call site.
    """
    if isinstance(value, bool):
        return value is False
    if isinstance(value, str):
        return value.strip().lower() == "false"
    return False


def _validate_extraction(
    data: dict[str, Any], *, cobranca_habilitada: bool = False, consultas_habilitadas: bool = False
) -> str | None:
    """Validate the classify LLM's parsed JSON against classify-v1's own schema.

    Returns a CLASS TOKEN failure reason (e.g. `invalid_intent`,
    `non_allowlisted_sintoma_codigo`) or `None` when valid. Any non-`None` return is a
    CLASSIFY FAILURE: the caller routes to escalation `falha_tecnica` (R1 cycle-1 fix — an
    invalid classification must never be silently read as an administrative-info turn).

    R1 CYCLE-2 FIX (leak): the failure reason NEVER carries the offending FIELD VALUE. The
    cycle-1 version echoed `repr(value)[:80]` — raw LLM output — into the reason, which
    `escalate` appends to `resumo_contexto` and ships into ENGINE PROCESS VARIABLES (general
    zone); an LLM copying a beneficiary-typed identifier (e.g. a CPF) into any field would put
    PHI into the engine DB (live-proven by the verifier). Class tokens only — no field values,
    no reprs, no truncation-based mitigation. The offending value is not logged anywhere either
    (the class token alone is sufficient for triage; the beneficiary's own message reaches the
    human attendant through the normal escalation flow).

    Checks (classify-v1's required fields + domains):
    - `intent` present and in `_VALID_INTENTS` -> `invalid_intent`;
    - `population` present and in `_VALID_POPULATIONS` -> `invalid_population`;
    - `psychosocial_risk` present and a real boolean (the always-active gatilho-5 signal must
      never be silently absent/coerced) -> `missing_or_invalid_psychosocial_risk`;
    - `sintoma_codigo` either `null` or in `ALLOWED_SINTOMA_CODIGOS` (an invented code is a
      failure — the DMN tables cannot match it, which would silently bypass every symptom rule
      and land on the no-red-flag catch-all) -> `non_allowlisted_sintoma_codigo`;
    - `intensidade`, when present, in `_VALID_INTENSIDADES` (an out-of-domain intensity would
      miss the DMN's own `"grave"` fail-safe rows the same way an invented code would) ->
      `invalid_intensidade`;
    - each of `_AGE_FIELDS` (`idade_anos`/`idade_meses`/`idade_gestacional_semanas`) is
      int-or-None (`None`/absent is valid). A non-coercible age (list, non-numeric or fractional
      string, float, bool) -> `invalid_age`: these feed the red-flag DMN tables as typed
      `integer`s and a wrong/ambiguous age could MISS a red flag, so an un-validated value must
      never reach the DMN (previously a bad value only failed INCIDENTALLY via a downstream
      engine FEEL/400 error — this makes the escalation EXPLICIT and DETERMINISTIC). Valid ages
      are COERCED IN PLACE (a quoted `"5"` -> `5`) so the DMN receives the correct integer type.
      An age ABOVE the sane per-field ceiling (`_TETO_DE_IDADE`: 120 years / 288 months) is the
      same class of failure, added 21/09/2026 (third round): `idade_meses=1200` is not somebody
      talking about their own age, it is a skewed extraction, and the pediatric table reads
      `idade_meses`. `idade_gestacional_semanas` has no ceiling BY DECLARED DECISION (clinical
      judgement — see `_TETO_DE_IDADE`).

    NUMERO UNICO (onda e, `cobranca_habilitada=True` SO' com o roteador ligado — `classify-v6`):
    `intent="cobranca"` passa a existir no dominio, e com ele dois campos. `cobranca_subtipo` e'
    OBRIGATORIO em `cobranca` e tem dominio fechado (`_VALID_COBRANCA_SUBTIPOS`); fora de `cobranca`
    ele tem de ser nulo ou ausente. `competencia`, quando presente, e' `AAAA-MM`. Qualquer desvio e'
    `invalid_cobranca_subtipo`/`invalid_competencia`, ou seja `falha_tecnica`, nunca Lucas. Com
    `cobranca_habilitada=False` NADA disto e' lido: o validador e' o de antes, e `cobranca` e'
    `invalid_intent`.
    """
    dominio = _intents_validos(
        cobranca_habilitada=cobranca_habilitada, consultas_habilitadas=consultas_habilitadas
    )
    if not _em_dominio(data.get("intent"), dominio):
        return "invalid_intent"
    if not _em_dominio(data.get("population"), _VALID_POPULATIONS):
        return "invalid_population"
    if not isinstance(data.get("psychosocial_risk"), bool):
        return "missing_or_invalid_psychosocial_risk"
    codigo = data.get("sintoma_codigo")
    if codigo is not None and not _em_dominio(codigo, ALLOWED_SINTOMA_CODIGOS):
        return "non_allowlisted_sintoma_codigo"
    intensidade = data.get("intensidade")
    if intensidade is not None and not _em_dominio(intensidade, _VALID_INTENSIDADES):
        return "invalid_intensidade"
    for field in _AGE_FIELDS:
        ok, coerced = _coerce_age(data.get(field))
        if not ok:
            return "invalid_age"
        # TETO SANO POR CAMPO (21/09/2026, terceira rodada): o mesmo `_TETO_DE_IDADE` da fronteira
        # da memoria, aplicado aqui — que e' a fronteira que ALIMENTA A DMN. Sem ele,
        # `idade_meses=1200` era uma extracao valida e a tabela pediatrica triava um lactente de
        # 100 anos. Ver o raciocinio completo na declaracao de `_TETO_DE_IDADE`.
        teto = _TETO_DE_IDADE.get(field)
        if coerced is not None and teto is not None and coerced > teto:
            return "invalid_age"
        # Coerce in place (e.g. "5" -> 5) so `_evaluate_dmn` reads the same dict and hands the
        # DMN a correctly typed integer. Absent fields (coerced None) are left as-is.
        if coerced is not None:
            data[field] = coerced
    if cobranca_habilitada:
        subtipo = data.get("cobranca_subtipo")
        if data.get("intent") == INTENT_COBRANCA:
            if not _em_dominio(subtipo, _VALID_COBRANCA_SUBTIPOS):
                return "invalid_cobranca_subtipo"
        elif subtipo is not None:
            return "invalid_cobranca_subtipo"
        competencia = data.get("competencia")
        if competencia is not None and not competencia_valida(competencia):
            return "invalid_competencia"
    if consultas_habilitadas:
        # CONSULTA DO PLANO: `consulta_subtipo` OBRIGATORIO e de dominio fechado em `consulta_plano`,
        # nulo/ausente em qualquer outro intent — o mesmo desenho de `cobranca_subtipo`.
        subtipo_consulta = data.get("consulta_subtipo")
        if data.get("intent") == INTENT_CONSULTA_PLANO:
            if not _em_dominio(subtipo_consulta, CONSULTA_SUBTIPOS):
                return "invalid_consulta_subtipo"
        elif subtipo_consulta is not None:
            return "invalid_consulta_subtipo"
    return None


# --- Graph ------------------------------------------------------------------------------------


class HelenaGraph:
    """Wires Helena's injected dependencies into a compilable `StateGraph[HelenaState]`.

    The dispatch layer (webhook receiver) or the harness's `build()` contract instantiates this
    with concrete transports; tests inject `FakeDmnTransport`/`FakeCibSevenTransport`/a fake
    inference provider/a fake `WhatsAppSender`.
    """

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        whatsapp: WhatsAppSender,
        agent_version: str = "helena@v0",
        coleta_enabled: bool = False,
        memoria_clinica_enabled: bool = True,
        roteador_lucas_enabled: bool = False,
        nome_operadora: str = NOME_OPERADORA_PADRAO,
        historico_enabled: bool = False,
        consultas_plano: FonteDeFatosDoPlano | None = None,
    ) -> None:
        self._llm = inference
        # HISTORICO CURTO (DL-0080): DESLIGADO por default, e desligado e' o grafo de antes byte a
        # byte — `historico_conversa` fica `None` em todo turno e nenhum prompt muda. So' a composicao
        # do receptor com `MAEZO_HELENA_HISTORICO` ligada passa `True` (`dispatch.py`).
        self._historico_enabled = historico_enabled is True
        # CONSULTA DO PLANO (DL de 07/10/2026): DESLIGADA por default (`None`), e desligada e' o grafo
        # de antes byte a byte — sem adendo no classify, `consulta_plano` fora do dominio validado e
        # sem o no' `consultar_plano`. So' a composicao do receptor com `MAEZO_HELENA_CONSULTAS_AMH`
        # ligada passa a fonte (`platform/webhooks/whatsapp/helena_consultas.py`).
        self._consultas = consultas_plano if isinstance(consultas_plano, FonteDeFatosDoPlano) else None
        # DL-0078: o nome de exibicao da operadora nos textos fixos de identidade (settings
        # `helena_identidade_nome_operadora`). Vazio/nao-texto cai no padrao: o texto nunca sai sem nome.
        self._nome_operadora = (
            nome_operadora.strip()
            if isinstance(nome_operadora, str) and nome_operadora.strip()
            else NOME_OPERADORA_PADRAO
        )
        # NUMERO UNICO (onda e, ADR-0062): DESLIGADO por default, e desligado e' o grafo de antes
        # byte a byte — `classify-v5`, `cobranca` fora do dominio validado e nem o no'
        # `handoff_cobranca` existe. So' a composicao do receptor com `MAEZO_ROTEADOR_LUCAS` ligado
        # passa `True` (`platform/webhooks/whatsapp/dispatch.py`).
        self._roteador_lucas_enabled = roteador_lucas_enabled is True
        self._dmn = dmn
        self._cibseven = cibseven
        # COLETA (passo 4): DESLIGADA por default. Ligar exige que `triage_sufficiency` exista no
        # motor — ratificada pelo medico — senao todo sintoma sem red flag e sem dado suficiente
        # escala como `falha_tecnica` (fail-closed, nunca resposta automatica).
        self._coleta_enabled = bool(coleta_enabled)
        # MEMORIA CLINICA (Frente 2.1): LIGADA por default, ao contrario da coleta, e a diferenca
        # e' deliberada. A coleta desligada deixa o sistema no comportamento conhecido; a memoria
        # desligada deixa o sistema no comportamento MEDIDO COMO ERRADO — o bebe de 11 meses
        # triado pela tabela de adulto, tres vezes. Entre um default que preserva o defeito e um
        # que o corrige, o segundo e' o que precisa de justificativa para ser desligado.
        #
        # Desligar continua possivel (`memoria_clinica_enabled=False`) e faz cada turno comecar do
        # zero, exatamente como antes desta frente.
        self._memoria_clinica_enabled = bool(memoria_clinica_enabled)
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-ESCALATION-001 start
        # (audit-before-effect). This is the LIVE agent execution path (webhook dispatch).
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._whatsapp = whatsapp
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: HelenaState) -> dict[str, Any]:
        """Turn start: (1) ENTRY SANITIZATION — reset EVERY output-only field to its neutral
        default so no caller/upstream-planted value can be read by a downstream node (T1.11
        caller-planted read-through fix, layer 1); (2) defend against missing runtime identifiers
        (fail-closed -> escalate).

        The reset is what makes the `if state.get("error")` bails downstream (`classify`,
        `escalate`) safe: after `receive`, `error` reflects ONLY a failure THIS graph set this
        turn (missing context here; a classify/DMN/tool failure later) — never a caller-supplied
        one. In particular it defeats the anti-escalation probe (planted `error`+`next_kind=
        'inform'` on a red-flag message): the planted `error` is cleared, `classify` runs, and the
        existing fail-closed red-flag logic re-engages instead of being skipped.
        """
        reset: dict[str, Any] = dict(_HELENA_NEUTRAL_OUTPUTS)
        # Memoria de coleta so' e' preservada com a feature ligada. O helper estrito
        # recusa bool, negativos e valores malformados como rodadas esgotadas.
        if self._coleta_enabled:
            for chave in _MEMORIA_DE_COLETA:
                if chave in state:
                    reset[chave] = state[chave]  # type: ignore[literal-required]
            if "coleta_rodadas" in state:
                reset["coleta_rodadas"] = _rodadas_de_coleta(state["coleta_rodadas"])
        # F6 (21/09/2026): o cartao de apresentacao e' uma vez por CONVERSA. Preservado sem portao
        # de feature — e' um bool que so' anda para True, e o unico efeito e' o prompt nao repetir
        # "Sou Helena..." no segundo turno.
        #
        # DL-0076 (05/10/2026): o cartao vale por `APRESENTACAO_VALIDADE_HORAS` desde a ULTIMA mensagem
        # da pessoa. Quem volta depois de 12 h (ou numa conversa sem carimbo, de antes desta regra) e'
        # apresentado de novo. O carimbo do turno atual e' gravado SEMPRE, depois da decisao: o valor
        # lido aqui e' o do turno anterior.
        agora = datetime.now(UTC)
        if state.get("apresentacao_ja_feita") is True and _apresentacao_continua_valida(
            state.get("ultima_mensagem_em"), agora=agora
        ):
            reset["apresentacao_ja_feita"] = True
        # DL-0080: o historico vale pela janela da memoria clinica desde a ULTIMA mensagem da pessoa —
        # o carimbo lido aqui e' o do turno anterior (o deste turno e' gravado logo abaixo). Flag
        # desligada, expirado ou malformado = `None` (o neutro).
        if self._historico_enabled:
            reset["historico_conversa"] = historico_valido(
                state.get("historico_conversa"),
                ultima_mensagem_em=state.get("ultima_mensagem_em"),
                agora=agora,
            )
        reset["ultima_mensagem_em"] = agora.isoformat()
        # DL-0078: o aviso de identidade e' uma vez por CONVERSA, sem validade (ao contrario do cartao):
        # so' `True` literal atravessa, e so' `respond` o acende depois de um envio bem-sucedido.
        if state.get("aviso_identidade_dado") is True:
            reset["aviso_identidade_dado"] = True
        # MEMORIA CLINICA (Frente 2.1): preservada por uma chave PROPRIA, independente da coleta.
        # As duas memorias respondem a perguntas diferentes — "que pergunta ficou em aberto" e
        # "quem e' o paciente" — e amarrar a segunda ao portao da primeira deixaria a crianca
        # triada como adulto ate' a tabela de suficiencia ser ratificada, que e' um prazo que nao
        # tem relacao nenhuma com o defeito.
        #
        # A VALIDACAO ACONTECE AQUI, NA FRONTEIRA, e nao no ponto de uso: o que nao passa vira
        # `None`, e `None` degrada para o comportamento de hoje. Isto tambem e' o que mantem viva
        # a defesa T1.11 para este campo — um valor plantado por quem chama com forma errada,
        # carimbo ausente ou fora da janela nao atravessa, e um valor plantado com forma CERTA
        # produz no maximo a confirmacao em voz alta ("entendi que e' sobre seu bebe, certo?"),
        # que e' visivel ao beneficiario em vez de silenciosa.
        if self._memoria_clinica_enabled and "memoria_clinica" in state:
            reset["memoria_clinica"] = _memoria_clinica_valida(
                state["memoria_clinica"], agora=datetime.now(UTC)
            )
        if not state.get("conversation_id") or not state.get("tenant_id"):
            reset["next_kind"] = "escalate"
            reset["error"] = "missing runtime context (tenant_id/conversation_id)"
        return reset

    async def classify(self, state: HelenaState) -> dict[str, Any]:
        """Classify intent + normalize any symptom, then ALWAYS evaluate the red-flag DMN for a
        symptom (or for psychosocial risk, forced to the mental_health table). The LLM never
        decides red_flag/severity — only the DMN does (ADR-0012).

        FAIL-CLOSED on classifier failure (R1 cycle-1 blocking fix): an LLM exception,
        unparseable JSON, or schema-invalid JSON is gatilho 4 (`falha_tecnica`) -> escalate,
        NEVER a silent `inform` default — symmetric with the DMN-down and missing-context
        failure paths below/in `receive`.
        """
        if state.get("error"):
            # `receive` already reset every output-only field, so a surviving `error` here can
            # ONLY be one THIS graph set this turn (missing runtime context) — never a caller-
            # planted one. That genuine mid-turn failure was already routed to escalate by
            # `receive`; do not re-classify over it.
            return {}

        extraction, classify_failure = await self._classify_llm(state)
        if extraction is None:
            # Gatilho 4: classifier failure is a TECHNICAL failure -> human, NEVER read as a
            # benign administrative turn (fail-safe; see `_classify_llm`'s docstring).
            # HEL-04: a severidade e' genuinamente DESCONHECIDA — nao houve extracao alguma de
            # onde deriva-la (nem `intensidade`, nem `sintoma_codigo`). `None`, nunca `"leve"`:
            # e' o mesmo principio de HELENA-SEVERIDADE-DEFAULT (ja em main) para o outro caminho
            # sem classificacao.
            # §Delta-3 (regressao P-12) — CORRECAO: este bloco afirmava que o worker recusa
            # fail-closed uma severidade ausente e que "NENHUMA notificacao e' publicada", com o
            # caso ainda chegando ao HITL pela aresta `BE_NotifFallbackFailed`. No motor vivo ele
            # NAO chegava: as duas provas de falha do classificador
            # (`tests/integration/agents/test_helena_escalation.py`) falharam com "no user task
            # ever appeared". Trocar um `leve` desonesto por um processo parado nao e' um bom
            # negocio justamente aqui, no caminho que existe para entregar a um humano o caso que
            # a maquina nao conseguiu ler. Verdade atual: o contrato declara `null` para este
            # motivo e o worker implementa a excecao (`escalation.py::_exigir_severidade`) — a
            # `severidade` nula e' ACEITA em `motivo_categoria=falha_tecnica`, a notificacao SAI
            # para o grupo da regra `r6` da DMN `escalation_routing` (P3 / atendimento-humano;
            # aquela regra casa `severidade` no coringa `-`, entao nunca dependeu dela) e a
            # instancia segue por `Flow_Notificar_UT` -> `UT_TratarEscalonamento`. O que deixa de
            # existir e' o rotulo fabricado — nao a pagina.
            return {
                "next_kind": "escalate",
                "escalation_motivo": "falha_tecnica",
                "escalation_severidade": None,
                "error": classify_failure or "classify LLM failed",
            }
        intent = cast(Intent, extraction.get("intent", "information"))
        psychosocial = bool(extraction.get("psychosocial_risk", False))

        # MEMORIA CLINICA ENTRE TURNOS (Frente 2.1). A fusao acontece AQUI, antes de qualquer
        # decisao, porque e' `extraction` — nao `update` — que vai para `_evaluate_dmn`, e e' a
        # ESCOLHA DA TABELA que o defeito de 13/09 errava. Fundir depois seria consertar o que a
        # Helena diz e nao o que ela consulta.
        memoria = state.get("memoria_clinica") if self._memoria_clinica_enabled else None
        extraction, veio_da_memoria = _fundir_memoria_clinica(extraction, memoria)
        population = cast(Population, extraction.get("population", "none"))

        update: dict[str, Any] = {
            "intent": intent,
            "population": population,
            "psychosocial_risk": psychosocial,
            "sintoma_codigo": extraction.get("sintoma_codigo"),
            "intensidade": extraction.get("intensidade", "desconhecida"),
            "idade_anos": extraction.get("idade_anos"),
            "idade_meses": extraction.get("idade_meses"),
            "idade_gestacional_semanas": extraction.get("idade_gestacional_semanas"),
        }
        if intent == INTENT_COBRANCA:
            # Onda (e): so' existe com o roteador ligado (o validador recusa `cobranca` desligado),
            # e os dois valores ja' passaram pelo dominio fechado de `_validate_extraction`.
            update["cobranca_subtipo"] = extraction.get("cobranca_subtipo")
            update["cobranca_competencia"] = extraction.get("competencia")
        if intent == INTENT_CONSULTA_PLANO:
            # So' existe com as consultas ligadas (o validador recusa desligado); dominio ja' fechado.
            update["consulta_subtipo"] = extraction.get("consulta_subtipo")
            if extraction.get("sintoma_codigo"):
                # CONSULTA COM SINTOMA NAO E' CONSULTA: o mesmo idioma da cobranca com sintoma.
                logger.info("helena_consulta_com_sintoma", node="classify")
                intent = "symptom"
                update["intent"] = intent
                update["consulta_subtipo"] = None

        # CRITICO 2 (22/09/2026): O CODIGO TEM DE EXISTIR NA TABELA QUE ESTE TURNO VAI CONSULTAR.
        #
        # A POSICAO E' O PONTO: aqui, DEPOIS da fusao e ANTES de qualquer decisao. Quem produziu a
        # incoerencia medida no `D2` turno 3 foi a propria fusao — o modelo devolveu
        # `population="adult"` + `cefaleia_subita_intensa` (um par coerente) para a mensagem "desde
        # ontem", a memoria corretamente impos `pediatric` por falta de lastro (F4), e o codigo de
        # ADULTO sobreviveu a troca. `_validate_extraction` nao pega isso nem se quisesse: ela roda
        # antes da fusao e valida cada campo isoladamente contra a UNIAO das quatro allowlists.
        #
        # O QUE ACONTECE COM O CODIGO RECUSADO: ele e' DESCARTADO, e nada mais. Nao vira
        # `falha_tecnica` — transformar cada escorregao de redacao do modelo numa fila humana e' o
        # defeito do CRITICO 1, do outro lado — e nao vira o sintoma lembrado da conversa: lembrar
        # sintoma entre turnos e' uma decisao que esta agente tomou em sentido contrario (ver
        # `_correcao_de_dado_avaliado`), e reabri-la aqui responderia a mensagem errada. O turno
        # segue com `sintoma_codigo=None`, que e' exatamente o caso que as quatro tabelas tratam no
        # catch-all, e a conversa continua lembrando QUAL sintoma ela ja' avaliou.
        #
        # `psychosocial_risk` fica FORA: o gatilho 5 forca a tabela `mental_health` por outro
        # caminho e tem prioridade maxima; recortar o codigo dele aqui mudaria qual regra daquela
        # tabela casa, que e' decisao clinica e nao higiene de extracao.
        if not psychosocial and not _sintoma_coerente_com_a_populacao(
            extraction.get("sintoma_codigo"), population
        ):
            # O QUE A LINHA LEVA, e por que cada campo e' SEGURO (22/09/2026, revisao do proprio
            # critico). Ate' aqui ela levava so' `motivo="codigo_de_outra_populacao"`, e com isso
            # as DUAS causas do descarte ficavam indistinguiveis: (a) o modelo alucinou um par
            # incoerente — defeito de extracao — e (b) a FUSAO impos a populacao errada sobre um
            # codigo legitimo. A (b) e' real e e' a cara: conversa pediatrica estabelecida, a mae
            # passa a falar de SI ("eu estou com dor no peito"), a populacao pediatrica entra por
            # falta de lastro (F4) e um `dor_toracica` legitimo e' jogado fora — o turno segue sem
            # sintoma, a tabela cai no catch-all e ninguem consegue ver isso no agregado.
            #
            # OS TRES CAMPOS SAO VOCABULARIO FECHADO, e isso e' conferido e nao suposto:
            #   * `codigo` so' chega aqui depois de `_validate_extraction`, que recusa a extracao
            #     inteira quando ele nao esta em `ALLOWED_SINTOMA_CODIGOS`
            #     (`non_allowlisted_sintoma_codigo`), e a memoria clinica NAO lembra
            #     `sintoma_codigo` (ver `_MEMORIA_CLINICA_CAMPOS`), entao a fusao nao tem como
            #     introduzir um valor de fora. E' um literal da allowlist, nunca texto do modelo;
            #   * `populacao_final` e' um dos cinco de `_VALID_POPULATIONS`, pela mesma validacao;
            #   * `populacao_da_memoria` e' um booleano derivado de `veio_da_memoria`.
            # Nenhum deles carrega uma palavra escrita pelo beneficiario — que e' a linha que
            # `helena_memoria_clinica_usada` ja' traca ao logar so' os NOMES dos campos.
            #
            # O CODIGO VAI PARA O LOG E NAO PARA O CONTADOR: e' a disciplina de
            # `helena_texto_nao_bate_com_o_fato`/`record_resposta_recusada`, onde o padrao exato
            # fica na linha e o contador guarda so' os eixos de cardinalidade pequena.
            populacao_da_memoria = "population" in veio_da_memoria
            logger.warning(
                "helena_sintoma_fora_da_tabela_da_populacao",
                node="classify",
                motivo="codigo_de_outra_populacao",
                codigo=str(extraction.get("sintoma_codigo") or ""),
                populacao_final=population,
                populacao_veio_da_memoria=populacao_da_memoria,
            )
            record_sintoma_fora_da_tabela(
                populacao=population,
                origem_populacao="memoria" if populacao_da_memoria else "mensagem",
            )
            extraction["sintoma_codigo"] = None
            update["sintoma_codigo"] = None

        # MOSTRAR ANTES DE USAR (decisao 4 do documento), e as tres condicoes sao todas
        # necessarias:
        #   1. algum dado veio da MEMORIA, nao da mensagem — confirmar o que a pessoa acabou de
        #      dizer seria papagaio, nao transparencia;
        #   2. o turno e' CLINICO (`symptom` ou risco psicossocial) — e' onde o dado lembrado muda
        #      a tabela e a orientacao; num turno administrativo ele nao decide nada;
        #   3. a confirmacao ainda nao CHEGOU A PESSOA nesta conversa — uma vez, nao a cada
        #      mensagem, senao vira ruido e a pessoa para de ler. "Chegou" e' literal desde a
        #      quarta rodada: `confirmada` e' escrita em `respond`, contra o texto enviado.
        ja_confirmada = bool(memoria and memoria.get(_MEMORIA_CONFIRMADA))
        turno_clinico = psychosocial or intent == "symptom"
        confirmar_agora = bool(veio_da_memoria) and turno_clinico and not ja_confirmada
        if confirmar_agora:
            update["memoria_a_confirmar"] = _frase_de_confirmacao(veio_da_memoria)
            logger.info(
                "helena_memoria_clinica_usada",
                node="classify",
                campos=sorted(veio_da_memoria),  # so' os NOMES dos campos, nunca os valores
            )
        # `confirmada` NAO ACENDE AQUI (21/09/2026, QUARTA RODADA). Ate' esta rodada a linha era
        # `confirmada=ja_confirmada or confirmar_agora`, ou seja o flag acendia quando a frase era
        # GERADA. Entre gerar e enviar esta' a cerca TEXTO x FATO, que troca o rascunho por
        # constante — e num `ja_ativo` com rascunho que anuncia handoff a troca e' integral. A
        # pessoa nunca lia a pergunta e ela nunca mais era feita, porque `confirmar_agora` exige
        # `not ja_confirmada`. Quem acende o flag agora e' `respond`, contra o texto EFETIVAMENTE
        # ENVIADO (`_confirmacao_chegou`) — o mesmo ponto e o mesmo criterio de
        # `apresentacao_ja_feita`, que tem exatamente este modo de falha.
        update["memoria_clinica"] = _memoria_a_gravar(
            extraction,
            memoria,
            agora=datetime.now(UTC),
            confirmada=ja_confirmada,
        )

        # SAUDACAO COM PEDIDO NAO E' SAUDACAO (11/09/2026). A regra do prompt ja' diz isso, mas a
        # rota nao pode depender da obediencia do modelo: se vier `greeting` junto de um codigo de
        # sintoma, vale o SINTOMA, que e' o caminho que passa pela DMN. Sem esta linha, "bom dia,
        # dor no peito" classificado como saudacao terminaria em resposta automatica sem veredito
        # — o `_inform_recusado` ainda barraria (viraria `falha_tecnica`), mas transformar um
        # sintoma em falha tecnica e' desperdicar a classificacao que ja' foi feita.
        if intent == "greeting" and extraction.get("sintoma_codigo"):
            logger.info("helena_saudacao_com_sintoma", node="classify")
            intent = "symptom"
            update["intent"] = intent

        # COBRANCA COM SINTOMA NAO E' COBRANCA (onda e; §1.2, P2 vence P3) — o mesmo idioma da
        # saudacao acima, pela mesma razao: a rota nao depende da obediencia do modelo. "o boleto
        # venceu e estou com dor no peito" segue o caminho do SINTOMA (tabela de red flag, coleta,
        # resposta), nunca o Lucas. `_handoff_recusado` barraria de qualquer jeito, mas viraria
        # `falha_tecnica` — desperdicar a triagem que a mensagem pede.
        if intent == INTENT_COBRANCA and extraction.get("sintoma_codigo"):
            logger.info("helena_cobranca_com_sintoma", node="classify")
            intent = "symptom"
            update["intent"] = intent
            update["cobranca_subtipo"] = None
            update["cobranca_competencia"] = None

        # PERGUNTA DE IDENTIDADE NAO E' PEDIDO DE HUMANO (23/09/2026, caso `A2`) — ver
        # `_PERGUNTA_DE_IDENTIDADE`. Vira `information`: a resposta honesta ("sou um assistente
        # virtual") e' o atendimento. Sem codigo de sintoma, porque com sintoma o caminho certo e'
        # a tabela, e o gatilho 1 ja' a consulta antes de qualquer intencao.
        if (
            intent == "human_request"
            and not extraction.get("sintoma_codigo")
            and _pergunta_de_identidade_sem_pedido(
                str(state.get("message_body") or ""), incluir_quem_sou_eu=_desfecho_determinado(state)
            )
        ):
            logger.info("helena_identidade_nao_e_pedido_de_humano", node="classify")
            intent = "information"
            update["intent"] = intent

        # F5 (21/09/2026): CORRECAO DE UM DADO JA' AVALIADO RE-DISPARA A AVALIACAO.
        #
        # O caso `D3`: "tenho 30 anos e estou com febre" avaliou a tabela de adulto; "me enganei,
        # tenho 70 anos" saiu `intent=information`, SEM TABELA, sintoma perdido — e voltou ao
        # cartao administrativo para alguem que acabou de dizer febre e 70 anos, o limiar exato da
        # regra `r8`. A pessoa corrigiu o dado que decidia a regra e ninguem reavaliou.
        #
        # A CONVERSAO ACONTECE AQUI, ANTES dos gatilhos, e a posicao e' o ponto: `intent` e'
        # justamente o que decide se a DMN e' consultada, entao corrigir o `intent` depois de o
        # despacho ter acontecido nao consultaria tabela nenhuma.
        #
        # E ELA NAO ATROPELA GATILHO NENHUM. `psychosocial_risk` (gatilho 5, prioridade maxima)
        # exclui este caminho, e os intents que JA escalam por conta propria — `clinical_question`,
        # `human_request`, `scheduling` — ficam fora por construcao: a allowlist reutilizada e'
        # `_INTENTS_ADMISSIVEIS_INFORM`, ou seja EXATAMENTE os intents que poderiam terminar numa
        # resposta automatica. E' esse o risco que esta regra existe para remover: uma correcao de
        # dado clinico que termina em cartao administrativo.
        correcao = _correcao_de_dado_avaliado(extraction, memoria)
        if correcao is not None and not psychosocial and intent in _INTENTS_ADMISSIVEIS_INFORM:
            logger.info(
                "helena_reavaliacao_por_correcao",
                node="classify",
                # So' os NOMES dos campos corrigidos, nunca os valores — a mesma disciplina de
                # `helena_memoria_clinica_usada` logo acima.
                campos=sorted(correcao["corrigidos"]),
            )
            intent = "symptom"
            extraction["sintoma_codigo"] = correcao["sintoma_codigo"]
            extraction["intensidade"] = correcao["intensidade"]
            update["intent"] = intent
            update["sintoma_codigo"] = correcao["sintoma_codigo"]
            update["intensidade"] = correcao["intensidade"]
            # MOSTRAR ANTES DE USAR, TAMBEM AQUI (21/09/2026, segunda rodada). O `confirmar_agora`
            # acima e' calculado ANTES desta conversao e olha `veio_da_memoria`, que sao os campos
            # de QUEM E' O PACIENTE. Neste ramo o dado lembrado que entra na decisao clinica e'
            # outro — o SINTOMA (`sintoma_avaliado`) — e ele entrava sem ninguem dizer a pessoa: o
            # turno reabria a triagem com um sintoma que ela nao mencionou naquela mensagem, e a
            # unica coisa que ela leria seria o resultado.
            #
            # SOBRESCREVE a confirmacao de populacao quando as duas caem no mesmo turno, e a ordem
            # e' deliberada: `response_prompt` usa UMA frase de confirmacao, e num turno de
            # CORRECAO o que a pessoa precisa poder corrigir e' sobre o que ainda se esta' falando.
            # Nao mexe no `confirmada` gravado na memoria: aquele flag e' da confirmacao de
            # paciente, que acontece uma vez por conversa, e esta e' por reavaliacao.
            update["memoria_a_confirmar"] = _frase_de_confirmacao(
                {_MEMORIA_SINTOMA_AVALIADO: correcao["sintoma_codigo"]}
            )

        # Gatilho 5 (always evaluated, highest priority): psychosocial risk in ANY message.
        #
        # A SEVERIDADE VEM DA TABELA (22/09/2026, item [a] do diretor). Ate' aqui a linha era
        # `escalation_severidade = "grave"`, LITERAL, e a bateria mediu o que isso significa: "nao
        # estou bem" como primeira mensagem saia `grave` com a DMN `mental_health` tendo respondido
        # `r7` = `red_flag=false` / `CONTINUE`. O contrato SP-OP-ESCALATION-001 reserva `grave` para
        # red flag P1 — um veredito que so' a DMN emite —, e `_severidade_de_intensidade` ja' tinha
        # escrito esse raciocinio por extenso para o outro ramo: anunciar `grave` onde a tabela nao
        # emitiu veredito e' fabricar o veredito que faltou, e faz da tabela um carimbo.
        #
        # O QUE **NAO** MUDA, e esta' declarado de proposito: o ROTEAMENTO. A regra `r2` da
        # `escalation_routing` casa `risco_psicossocial` com `severidade` no coringa `-`, entao
        # este turno continua P1 / `plantao-clinico` / 5min, como continuava antes desta linha. Um
        # `psychosocial_risk=true` com a tabela dizendo `CONTINUE` seguir indo ao plantao clinico e'
        # CONDUTA, nao rotulo: mudar isso e' decisao do dono clinico (`owners.clinico` segue vazio,
        # e as tabelas seguem `CONTEUDO CLINICO: DRAFT`), nao de quem conserta o carimbo.
        #
        # COM A TABELA FORA DO AR O `grave` FICA, e a distincao e' toda a mudanca desta linha: o
        # que se para de fazer e' CONTRADIZER a tabela, nao deixar de ser conservador quando ela
        # nao falou. `EVL-HELENA-12` e' exatamente esse caso (o gemeo DMN-down do `EVL-HELENA-03`)
        # e e' expectativa ratificada: uma tabela de saude mental indisponivel nao pode rebaixar
        # uma revelacao de risco imediato. A propria tabela sustenta a leitura — a `r2` manda P1
        # para ideacao mesmo SEM risco imediato declarado e a `r6` e' um fail-safe explicito —,
        # entao a linha mais conservadora dela e' o substituto honesto da linha que nao veio.
        if psychosocial:
            dmn_out = await self._evaluate_dmn(extraction, force_population="mental_health")
            update.update(dmn_out)
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "risco_psicossocial"
            if dmn_out.get("error"):
                update["escalation_severidade"] = "grave"
            else:
                prioridade = str((dmn_out.get("dmn_decision") or {}).get("prioridade", ""))
                update["escalation_severidade"] = _severidade_from_prioridade(prioridade)
            return update

        # GATILHO 1 (22/09/2026, CRITICO 3): A TABELA ANTES DA INTENCAO — e uma bandeira encontrada
        # MANDA no roteamento.
        #
        # O QUE FOI MEDIDO. `E1` = "tenho 45 anos e estou com dor no peito. o que eu tenho? e
        # infarto?" -> P2 / `enfermagem-triagem` / 30min, SEM consultar tabela nenhuma: o gatilho
        # `intencao_clinica` (logo abaixo) cortava antes. A MESMA dor toracica sem a pergunta (`B1`,
        # `F2`) da P1 / `plantao-clinico` / 5min. Ou seja: FAZER UMA PERGUNTA CLINICA REBAIXAVA A
        # URGENCIA DE UMA RED FLAG — e a pessoa que pergunta "sera que e' infarto?" e' exatamente a
        # que esta' com medo de estar tendo um.
        #
        # A ORDEM CERTA, e por que ela e' esta. Os gatilhos 2/3 classificam o que a pessoa QUER
        # (uma opiniao clinica, um humano); a DMN decide o que o quadro E'. O que a pessoa quer nao
        # pode decidir a prioridade de um quadro que ninguem olhou — e nenhuma decisao clinica
        # mudou de dono: quem diz `red_flag`/`conduta` continua sendo a tabela (ADR-0012), e o que
        # esta linha faz e' garantir que ela seja PERGUNTADA antes de o roteamento ser decidido.
        #
        # O GATILHO 2 NAO MORREU: sem bandeira, a pergunta clinica continua sendo `intencao_clinica`
        # e continua sem resposta automatica (L0 hard — a Helena nunca responde uma). A diferenca e'
        # que agora "nao ha bandeira" e' um veredito da tabela, e nao um turno em que ninguem olhou.
        #
        # SEM `sintoma_codigo` NADA MUDA: uma pergunta clinica sem sintoma ("o plano cobre
        # fisioterapia?") nao tem o que triar, e uma tabela de red flag sobre um codigo nulo nao
        # responde nada que valha uma chamada ao motor.
        ja_triado = bool(extraction.get("sintoma_codigo"))
        if ja_triado and await self._triar_red_flag(extraction, update):
            return update

        # Gatilho 2: clinical question (L0 hard — Helena never answers one herself).
        if intent == "clinical_question":
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "intencao_clinica"
            update["escalation_severidade"] = "moderada"
            return update

        # Gatilho 3: explicit request for a human.
        #
        # NUMERO UNICO (onda e; §2.3, P5): o lexico deterministico de pedido de pessoa
        # (`pedido_humano_lexico`, so' preenchido com o roteador ligado) FORCA este gatilho depois
        # do 5 e do 1 (e do 2: saude vence pedido de pessoa, §1.2) — um falso positivo leva a uma
        # pessoa, que e' o lado seguro. A UNICA excecao e' a pergunta de identidade sem pedido
        # ("voce e humano?"), o caso `A2` de 23/09: o termo "humano" esta' no lexico, e forcar o
        # gatilho ali reabriria o chamado P3 que aquela cerca fechou. Um pedido explicito na mesma
        # mensagem ("voce e um robo? quero falar com uma pessoa") continua vencendo.
        pedido_lexico = state.get("pedido_humano_lexico") is True and not _pergunta_de_identidade_sem_pedido(
            str(state.get("message_body") or ""), incluir_quem_sou_eu=_desfecho_determinado(state)
        )
        if pedido_lexico and intent != "human_request":
            logger.info("helena_pedido_humano_lexico", node="classify", intent=intent)
        if intent == "human_request" or pedido_lexico:
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "solicitacao_humano"
            update["escalation_severidade"] = "leve"
            return update

        # Gatilho 3b (DL-0070): ameaca de reclamacao a orgao regulador. Vence a rota de fora do canal,
        # perde para sintoma, risco psicossocial e pergunta clinica (todos acima).
        if intent != "greeting" and _ameaca_regulatoria(str(state.get("message_body") or "")):
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "solicitacao_humano"
            update["escalation_severidade"] = "leve"
            return update

        # SAUDACAO: responde e encerra, SEM abrir processo. NAO e' um gatilho de escalonamento —
        # e' a ausencia de um.
        #
        # O DEFEITO QUE ISTO CORRIGE, medido em 11/09/2026: "Opa" foi classificado
        # `human_request` e abriu SP-OP-ESCALATION-001 com `solicitacao_humano`, P3, fila
        # `atendimento-humano`, prazo de 4h — enquanto "Ola, bom dia" saiu `information` e
        # resolveu sozinha. Duas saudacoes, dois desfechos. A causa nao era o modelo desobedecer:
        # nenhuma das cinco intencoes do prompt cabia numa saudacao, e a instrucao de
        # `human_request` exige pedido EXPLICITO. Sem lugar para por, o modelo escolheu o vizinho
        # mais proximo. Em operacao, todo "oi" podia virar trabalho numa fila humana.
        # CONSULTA DO PLANO: chegar aqui ja' significa sem risco psicossocial, sem bandeira, sem
        # pergunta clinica e sem pedido de pessoa. `_consulta_recusada` confere de novo (2 camadas).
        if intent == INTENT_CONSULTA_PLANO:
            return self._rota_de_consulta(update)

        # COBRANCA (onda e): passa ao Lucas SO' pela precondicao de 2 camadas. Chegar aqui ja'
        # significa sem risco psicossocial, sem bandeira da tabela, sem pergunta clinica e sem
        # pedido de pessoa; `_handoff_recusado` confere de novo, mais o sintoma e os lexicos.
        if intent == INTENT_COBRANCA:
            return _rota_de_cobranca(update, state, roteador_ligado=self._roteador_lucas_enabled)

        if intent == "greeting":
            return _rota_informativa(update)

        # Symptom: ALWAYS goes through the DMN — the DMN decides red flag, never the LLM.
        #
        # A TRIAGEM PODE JA' TER ACONTECIDO neste turno (gatilho 1, acima): quando havia
        # `sintoma_codigo`, a tabela ja' foi consultada e ja' disse que nao ha bandeira. Triar de
        # novo seria uma segunda chamada ao motor com as MESMAS entradas — mesmo veredito, duas
        # linhas de proveniencia e o dobro de latencia no caminho mais quente da agente. O ramo
        # continua existindo para o caso que o gatilho 1 nao cobre: `intent="symptom"` SEM codigo,
        # em que a tabela ainda tem de ser perguntada (o catch-all dela e' quem responde).
        if intent == "symptom":
            if not ja_triado and await self._triar_red_flag(extraction, update):
                return update
            # COLETA (passo 4): SO' aqui — a red flag ja' disse `false`. Antes desta linha nada
            # muda: emergencia nunca espera pergunta. A partir dela, "sem bandeira" deixa de
            # significar "pode responder" e passa a significar "pode responder SE os dados
            # bastavam" — e quem diz se bastavam e' a tabela de suficiencia, nao o modelo.
            if self._coleta_enabled:
                coleta = await self._avaliar_suficiencia(state, update)
                if coleta is not None:
                    update.update(coleta)
                    return update
            return _rota_informativa(update)

        # FORA DO CANAL (DL-0052): agendamento deixou de escalar. Chegar aqui significa que nao
        # houve sintoma, risco psicossocial, pergunta clinica nem pedido de humano neste turno.
        if intent in _INTENTS_FORA_DO_CANAL:
            return _rota_informativa(update)

        return _rota_informativa(update)

    async def _triar_red_flag(self, extraction: dict[str, Any], update: dict[str, Any]) -> bool:
        """Consulta a tabela de red flag da populacao e ESCREVE o veredito em `update`. Devolve
        `True` quando a tabela decidiu o roteamento do turno.

        EXTRAIDO DO RAMO `symptom` EM 22/09/2026 (CRITICO 3), sem mudar uma linha do que ele fazia:
        a triagem precisava acontecer ANTES dos gatilhos de INTENCAO, e duas copias da mesma
        sequencia (avaliar -> tratar tabela fora do ar -> marcar a avaliacao -> ler a bandeira)
        derivariam na primeira edicao que tocasse so' uma delas.

        DECIDE O ROTEAMENTO em dois casos, e os dois sao `escalate`:
          * a tabela nao respondeu (gatilho 4, `falha_tecnica`) — NUNCA lido como "sem bandeira"
            (fail-safe, ADR-0028 §3);
          * a tabela acusou bandeira (`red_flag` ou `conduta` `ESCALATE*`) — e ai a severidade sai
            da PRIORIDADE que ela emitiu, nunca de um literal.
        Fora esses dois, devolve `False` e quem chamou segue com os proprios gatilhos.
        """
        dmn_out = await self._evaluate_dmn(extraction)
        update.update(dmn_out)
        if dmn_out.get("error"):
            # Gatilho 4: DMN unavailable/no-result is a technical failure -> human, NEVER
            # treated as "no red flag" (fail-safe, ADR-0028 §3).
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "falha_tecnica"
            # HEL-04: AQUI ha' classificacao — o sintoma foi extraido e validado antes de a DMN
            # cair —, entao a severidade DERIVA dela em vez de ser um literal.
            update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
            return True
        # F5: a avaliacao ACONTECEU — so' AQUI, depois de a tabela responder, "houve avaliacao" e'
        # fato e nao intencao. A conversa passa a lembrar QUAL dado ela usou, que e' a unica
        # informacao que permite refaze-la se a pessoa corrigir esse dado
        # (`_correcao_de_dado_avaliado`). NAO e' lembrar o sintoma da mensagem anterior — ver a nota
        # em `_MEMORIA_SINTOMA_AVALIADO` para a diferenca e por que ela importa.
        update["memoria_clinica"] = _marcar_avaliacao(
            update.get("memoria_clinica"),
            sintoma_codigo=extraction.get("sintoma_codigo"),
            intensidade=update.get("intensidade"),
        )
        decision = dmn_out.get("dmn_decision", {})
        conduta = str(decision.get("conduta", "CONTINUE"))
        if decision.get("red_flag") is True or conduta.startswith("ESCALATE"):
            prioridade = str(decision.get("prioridade", "P2"))
            is_mental = dmn_out.get("dmn_table") == _DMN_BY_POPULATION["mental_health"]
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "risco_psicossocial" if is_mental else "red_flag_clinico"
            update["escalation_severidade"] = _severidade_from_prioridade(prioridade)
            return True
        return False

    # -- COLETA (passo 4) ---------------------------------------------------------------------

    async def _avaliar_suficiencia(self, state: HelenaState, update: dict[str, Any]) -> dict[str, Any] | None:
        """Consulta `triage_sufficiency` e devolve a ATUALIZACAO de estado que decide o turno —
        ou `None` quando os dados bastam e o caminho informativo segue como antes.

        A tabela recebe FATOS sobre o que foi apurado (nunca o texto): a intencao, a populacao,
        se o sintoma casou com um codigo, se a intensidade foi dita, se o campo da populacao esta'
        presente e quantas perguntas ja' foram feitas. Ela devolve UM veredito. Este metodo nao
        interpreta clinica: converte o veredito em rota.

        TEM FIM antes de perguntar a tabela: com `coleta_rodadas >= COLETA_MAX_RODADAS` escala,
        qualquer que fosse o veredito — e' a regra 2 do desenho, e mora no codigo porque nao pode
        depender de a tabela lembrar-se dela.

        FAIL-CLOSED em tres formas: tabela indisponivel, sem linha casada, ou veredito fora do
        vocabulario -> `falha_tecnica` -> humano. Nunca "assume suficiente".
        """
        rodadas = _rodadas_de_coleta(state.get("coleta_rodadas", 0))
        if rodadas >= COLETA_MAX_RODADAS:
            logger.info("helena_coleta_esgotada", rodadas=rodadas, node="classify")
            return {
                "coleta_veredito": COLETA_VEREDITO_ESCALAR,
                "next_kind": "escalate",
                "escalation_motivo": MOTIVO_COLETA_ESGOTADA,
                "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
                "coleta_pendente": None,
            }

        population = str(update.get("population") or "none")
        campo_populacao = {
            "adult": update.get("idade_anos") is not None,
            "pediatric": update.get("idade_meses") is not None,
            "gestante": update.get("idade_gestacional_semanas") is not None,
            "mental_health": update.get("risco_imediato") is not None,
        }.get(population, False)
        entrada: dict[str, Any] = {
            "intent": str(update.get("intent") or ""),
            "population": population,
            "sintoma_reconhecido": update.get("sintoma_codigo") is not None,
            "intensidade_informada": str(update.get("intensidade") or "desconhecida") in _VALID_INTENSIDADES
            and str(update.get("intensidade")) != "desconhecida",
            "campo_populacao_disponivel": bool(campo_populacao),
            "rodadas": rodadas,
        }
        try:
            rows, version = await self._dmn.evaluate(SUFFICIENCY_DMN_KEY, entrada)
            row = first_row(rows, SUFFICIENCY_DMN_KEY, entrada)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {
                "coleta_veredito": None,
                "next_kind": "escalate",
                "escalation_motivo": "falha_tecnica",
                "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
                "error": dmn_unavailable_error(SUFFICIENCY_DMN_KEY, exc),
            }
        veredito = row.get("veredito")
        veredito = veredito.get("value") if isinstance(veredito, dict) else veredito
        veredito = str(veredito or "")
        ref = f"{SUFFICIENCY_DMN_KEY}#{version.id}"
        if veredito == COLETA_VEREDITO_SUFICIENTE:
            logger.info("helena_coleta_suficiente", ref=ref, node="classify")
            return None
        if veredito in COLETA_VEREDITOS_PERGUNTA:
            logger.info(
                "helena_coleta_pergunta", veredito=veredito, rodada=rodadas + 1, ref=ref, node="classify"
            )
            return {
                "coleta_veredito": veredito,
                "coleta_pergunta": veredito,
                "coleta_rodadas": rodadas + 1,
                "coleta_pendente": veredito,
                # O texto da mensagem ja' chegou pseudonimizado na borda; guarda-lo aqui e' o que
                # permite ao turno seguinte classificar "forte" junto com "dor de cabeca".
                "coleta_contexto": " | ".join(
                    t for t in (state.get("coleta_contexto") or "", state.get("message_body") or "") if t
                ),
                "next_kind": "collect",
            }
        if veredito == COLETA_VEREDITO_ESCALAR:
            return {
                "coleta_veredito": veredito,
                "next_kind": "escalate",
                "escalation_motivo": MOTIVO_COLETA_ESGOTADA,
                "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
                "coleta_pendente": None,
            }
        # Vocabulario desconhecido: a tabela nao e' confiavel para este caso -> humano.
        logger.warning(
            "helena_coleta_veredito_desconhecido",
            classification="fora_do_vocabulario",
            ref=ref,
            node="classify",
        )
        return {
            "coleta_veredito": None,
            "next_kind": "escalate",
            "escalation_motivo": "falha_tecnica",
            "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
            "error": f"{SUFFICIENCY_DMN_KEY}: veredito fora do vocabulario",
        }

    async def collect(self, state: HelenaState) -> dict[str, Any]:
        """Redige UMA pergunta ao beneficiario sobre o dado que falta (`coleta_pergunta`).

        Nao diagnostica, nao orienta, nao minimiza: pergunta. O prompt e' proprio
        (`coleta_prompt`, versao `COLETA_PROMPT_VERSION`) para nao mexer no `response_prompt`
        que os goldens da Helena pinam. Se o modelo falhar, a pergunta generica de fallback
        continua sendo uma PERGUNTA — o turno nunca vira resposta clinica por acidente.

        AS CERCAS DE SAIDA RODAM AQUI TAMBEM (21/09/2026, segunda rodada). Este no' montava prompt
        proprio, chamava `generate` direto e devolvia `response_text` sem passar por cerca nenhuma
        — e em `respond` a unica verificacao do turno e' a TEXTO x FATO, que nao consulta a cerca de
        canal. Resultado medido: na rota de coleta o canal inventado chegava ao beneficiario
        ("Antes de continuar: a dor esta' leve, moderada ou forte? Se preferir, veja no aplicativo
        do plano."). O docstring de `_respond_llm` prometia ser "o unico ponto por onde passa todo
        texto que chega ao beneficiario" e a enumeracao nao tinha `collect`; agora a promessa e'
        verdade, pelo metodo compartilhado `_cercar_saida`.

        ALCANCE HONESTO: a rota so' e' escolhida com `coleta_enabled=True` (default `False`,
        dependente de `triage_sufficiency` no motor). O no' JA existia e ja era chamavel, e a cerca
        e' de uma entrega posterior a ele — quando a feature ligar, o F7 voltava por esta porta.

        RECUSA AQUI QUASE NUNCA ESCALA, ao contrario de `inform`, e a assimetria e' deliberada: o
        turno de coleta tem uma saida honesta que `inform` nao tem — a PERGUNTA generica. Escalar
        cada texto barrado transformaria um defeito de redacao em fila humana, e a coleta existe
        justamente para nao escalar antes de saber. A pergunta de fallback e' segura POR
        CONSTRUCAO, nao por sorte: `test_helena_coleta.py` fixa que toda entrada de
        `_PERGUNTA_FALLBACK` passa nas duas cercas, o que e' o que impede um laco aqui.

        A EXCECAO E' A NEGATIVA CLINICA (21/09/2026, terceira rodada), e ela e' por GRUPO e nao por
        rota. "Perguntar de novo" e' uma saida honesta para um canal inventado ou uma promessa de
        capacidade: o modelo errou a redacao de uma pergunta. Nao e' saida honesta para o unico
        grupo que fala sobre o CORPO de alguem: um texto que afirma "nao ha sinais de alerta" no
        meio de um turno cuja unica tarefa era PERGUNTAR nao e' um defeito de redacao — e' o modelo
        saindo do roteiro para dar parecer clinico sobre quem ele nem acabou de ouvir, e a pergunta
        generica descartaria isso em silencio. O mesmo texto em `inform` escala; escalar aqui
        tambem e' o que torna a regra "negativa clinica nunca vira pergunta" verdadeira em toda
        rota, em vez de depender de por qual no' o turno passou.
        """
        pergunta = str(state.get("coleta_pergunta") or "")
        contexto = {
            "pergunta": pergunta,
            "rodada": state.get("coleta_rodadas"),
            "population": state.get("population"),
        }
        prompt = (
            f"{coleta_prompt()}\n\ncontexto={contexto}\n"
            f"{render_untrusted_block('message_body', state.get('message_body', ''))}"
        )
        try:
            text = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                task_kind="task_default",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            text = _PERGUNTA_FALLBACK.get(pergunta, _PERGUNTA_FALLBACK["default"])
        try:
            text = self._cercar_saida(text, "collect", node="collect")
        except RespostaRecusadaError:
            # A PERGUNTA E' RE-DERIVADA DO TEXTO, e NAO lida da excecao ligada. Duas razoes, e as
            # duas importam:
            #
            #   * `recusa.grupo` e' um atributo de excecao ligada, e a cerca LUC-06/NEW-01
            #     (`test_error_field_no_raw_exception.py`) recusa qualquer leitura assim — ela esta'
            #     certa mesmo com um valor que por acaso e' seguro, porque um ramo de hoje e' um
            #     campo de estado amanha. O mesmo argumento ja' esta' escrito em `inform`;
            #   * o grupo da excecao depende da ORDEM das duas cercas (`_cercar_saida` consulta a de
            #     canal primeiro), e um texto que cita um canal inventado E afirma ausencia de
            #     alerta clinico tem de escalar pelo segundo motivo, nao virar pergunta porque o
            #     primeiro venceu o log. `motivo_de_recusa` e' PURA, entao re-perguntar e' barato e
            #     responde a pergunta CERTA: "este texto afirma algo sobre o corpo de alguem?"
            #
            # O veredito da cerca nao e' refeito: ele ja' aconteceu, foi logado e contado la'.
            if _texto_tem_negativa_clinica(text):
                return await self._start_escalation(
                    state,
                    motivo="falha_tecnica",
                    # HEL-04: a severidade DERIVA da classificacao ja feita, nunca de um literal.
                    severidade=_severidade_de_intensidade(state.get("intensidade")),
                    response_kind="escalate",
                    error=ERRO_RESPOSTA_RECUSADA,
                )
            # A recusa ja foi logada e contada em `_cercar_saida`. A pergunta generica e' a saida
            # honesta desta rota para os outros grupos (ver docstring), e ela mesma NAO e'
            # recercada: seria um laco, e a cerca de teste que fixa as constantes e' o que torna
            # isso desnecessario.
            text = _PERGUNTA_FALLBACK.get(pergunta, _PERGUNTA_FALLBACK["default"])
        return {"response_text": text, "response_kind": "collect"}

    async def inform(self, state: HelenaState) -> dict[str, Any]:
        """Administrative response (no clinical guidance, no red flag).

        RECUSA DE SAIDA — A DECISAO E' POR GRUPO, NAO POR ROTA (22/09/2026, CRITICO 1). Entre
        13/09 e 21/09 QUALQUER rascunho barrado aqui virava escalonamento `falha_tecnica`. A
        bateria de 22/09 mediu o que isso custa: `C1` ("estou com dor de cabeca") e `B5` ("estou
        com um pouco de dor de garganta desde ontem") tinham estado clinico IDENTICO — extracao sem
        sintoma, DMN no catch-all, `red_flag=false` — e desfechos opostos. O C1 abriu chamado P3
        `atendimento-humano` rotulado *falha tecnica* porque o rascunho do modelo dizia "a equipe
        entre em contato"; o B5, com a mesma dor e outra frase, nao abriu nada. Quem decidia a
        abertura de fila humana era a REDACAO do modelo, nao criterio clinico nenhum.

        A LINHA DIVISORIA, e por que ela e' esta. Escalar por recusa so' faz sentido quando a
        recusa indica que a Helena NAO TEM resposta automatica legitima para dar:

          * `negativa_clinica` — afirmar que nao ha sinal de alerta e' exatamente o que ela nao
            pode fazer sozinha (aquela frase so' nasce de User Task). Se era isso que ela tinha a
            dizer, ela nao tinha nada a dizer, e quem tem e' um humano. CONTINUA ESCALANDO;
          * `promessa_de_humano`, `promessa_de_capacidade`, `canal_nao_confirmado` — sao defeitos
            de REDACAO num turno cuja tarefa era orientar. A resposta honesta nao e' abrir uma fila
            que ninguem pediu: e' nao mentir. TROCA DE TEXTO, e o turno segue `inform`.

        E' a mesma assimetria por grupo que `collect` ja' aplica desde 21/09, e pela mesma razao —
        ver o `except` dele, onde o porque esta' escrito por extenso.

        A PERGUNTA E' FEITA AO TEXTO, e nao ao grupo da excecao, pelos dois motivos que aquele
        `except` enumera: `RespostaRecusadaError.grupo` e' atributo de excecao ligada (a cerca
        LUC-06/NEW-01 recusa a leitura, e ela esta' certa mesmo com um valor que por acaso e'
        seguro) e o grupo depende da ORDEM das duas cercas, entao um texto que cita canal inventado
        E afirma ausencia de alerta tem de escalar pelo segundo motivo, nao virar troca de texto
        porque o primeiro venceu o log. `motivo_de_recusa` e' PURA: re-perguntar e' barato.

        A TROCA E' POR CONSTANTE, nunca por um segundo rascunho — o mesmo prompt com a mesma
        mensagem tende ao mesmo texto, e um laco de tentativas transforma uma cerca num atraso
        (a mesma escolha de CC-01 e da cerca TEXTO x FATO).
        """
        identidade = _resposta_fixa_de_identidade(state, nome_operadora=self._nome_operadora)
        if identidade is not None:
            # F6: a pergunta de identidade recebe a frase do dono, LITERAL, antes de qualquer
            # decisao sobre "fora do canal" — ver `_resposta_fixa_de_identidade`.
            return {"response_text": identidade, "response_kind": "inform"}
        if state.get("intent") in _INTENTS_FORA_DO_CANAL:
            # Texto FIXO, sem modelo: nao varia, nao se oferece para orientar o assunto e nao
            # convida a continuar. A precondicao da DMN ja' foi aplicada por `_route`.
            return {"response_text": RESPOSTA_FORA_DO_CANAL, "response_kind": "inform"}
        if state.get("intent") == "symptom" or state.get("sintoma_codigo"):
            # SINTOMA SEM BANDEIRA: texto FIXO, sem modelo — ver `RESPOSTA_SINTOMA_SEM_ALERTA`. A
            # precondicao de `_route` (`_inform_recusado`) garante que a tabela JA' disse `red_flag
            # false`; quem decide humano e' a tabela ou o pedido da pessoa, nunca a redacao. A
            # frase de confirmacao de dado (F5) e' pronta e vem de `classify`: entra na frente.
            confirmacao = state.get("memoria_a_confirmar")
            texto_fixo = (
                f"{confirmacao} {RESPOSTA_SINTOMA_SEM_ALERTA}" if confirmacao else RESPOSTA_SINTOMA_SEM_ALERTA
            )
            return {"response_text": texto_fixo, "response_kind": "inform"}
        if state.get("intent") in ("greeting", "information") and _e_despedida(
            str(state.get("message_body") or "")
        ):
            # Despedida: agradecimento ou adeus e nada mais — ver `_e_despedida`.
            return {"response_text": RESPOSTA_DESPEDIDA, "response_kind": "inform"}
        if state.get("intent") == "greeting":
            # Saudacao SEM pedido (`classify` so' devolve `greeting` quando nao ha pedido nenhum).
            # O cartao so' na primeira vez; `apresentacao_ja_feita` e' mantido pelo grafo.
            ja_se_apresentou = state.get("apresentacao_ja_feita") is True
            abertura = RESPOSTA_SAUDACAO_CURTA if ja_se_apresentou else RESPOSTA_SAUDACAO_ABERTURA
            return {"response_text": abertura, "response_kind": "inform"}
        text = await self._redigir_resposta(state, "inform")
        try:
            text = self._cercar_saida(text, "inform", node="inform")
        except RespostaRecusadaError:
            if _texto_tem_negativa_clinica(text):
                # O `error` leva o TOKEN DE CLASSE e nada mais: ele sobrevive para o sufixo
                # `[falha tecnica: ...]` do handoff no turno seguinte. QUAL padrao barrou fica onde
                # detalhe deve ficar — no log e no contador, os dois emitidos em `_cercar_saida`.
                return await self._start_escalation(
                    state,
                    motivo="falha_tecnica",
                    # HEL-04: a severidade DERIVA da classificacao ja feita, nunca de um literal.
                    severidade=_severidade_de_intensidade(state.get("intensidade")),
                    response_kind="escalate",
                    error=ERRO_RESPOSTA_RECUSADA,
                )
            # A recusa ja foi logada e contada em `_cercar_saida`. O `error` aqui e' o RASTRO no
            # proprio turno (item 3 do diretor: toda recusa deixa rastro) e NAO um roteamento —
            # este turno nao escala, e nao ha falha tecnica nenhuma a declarar ao motor.
            return {
                "response_text": RESPOSTA_INFORM_RECUSADA,
                "response_kind": "inform",
                "error": ERRO_RESPOSTA_RECUSADA,
            }
        return {"response_text": text, "response_kind": "inform"}

    def _consulta_recusada(self, estado: Mapping[str, Any]) -> str | None:
        """Precondicao DETERMINISTICA da rota `consultar_plano` (as duas camadas, como `inform`)."""
        if estado.get("error"):
            return "erro_do_turno"
        if self._consultas is None:
            return "consultas_desligadas"
        if estado.get("intent") != INTENT_CONSULTA_PLANO:
            return "intent_nao_e_consulta"
        if estado.get("psychosocial_risk") is not False:
            return "risco_psicossocial"
        if estado.get("sintoma_codigo"):
            return "sintoma_reportado"
        if not _em_dominio(estado.get("consulta_subtipo"), CONSULTA_SUBTIPOS):
            return "subtipo_fora_do_dominio"
        return None

    def _rota_de_consulta(self, update: dict[str, Any]) -> dict[str, Any]:
        """Camada 1: `consultar_plano` so' com a precondicao; senao `falha_tecnica` (estado injustificado)."""
        recusa = self._consulta_recusada(update)
        if recusa is None:
            update["next_kind"] = NEXT_KIND_CONSULTA_PLANO
            return update
        logger.warning("helena_consulta_recusada", motivo=recusa, node="classify")
        update["next_kind"] = "escalate"
        update["escalation_motivo"] = "falha_tecnica"
        update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
        update["error"] = f"{ERRO_CONSULTA_RECUSADA}: {recusa}"
        return update

    async def consultar_plano(self, state: HelenaState) -> dict[str, Any]:
        """CONSULTA DO PLANO (DL de 07/10/2026): fatos do cadastro do PROPRIO beneficiario.

        SO' com identidade resolvida (DL-0077: candidato unico, `portable_subject_ref` no estado). Sem
        ela, ou com qualquer falha da AMH, o texto e' FIXO (`RESPOSTA_CONSULTA_SEM_IDENTIDADE`): nao
        abre processo e oferece a pessoa — quem pedir cai no gatilho 3 no turno seguinte. Com fatos, o
        modelo redige SO' a partir do bloco de fatos; a cerca propria
        (`motivo_de_recusa_da_consulta`) troca qualquer rascunho recusado pela resposta
        DETERMINISTICA, montada so' com os fatos — nunca por um segundo rascunho.

        LOG: so' tokens (subtipo, desfecho). Nunca a referencia, os fatos nem o texto.
        """
        subtipo = str(state.get("consulta_subtipo") or "")
        identidade = state.get("identidade_beneficiario")
        ref = identidade.get("portable_subject_ref") if isinstance(identidade, Mapping) else None
        fixo = {"response_text": RESPOSTA_CONSULTA_SEM_IDENTIDADE, "response_kind": "inform"}
        if (
            self._consultas is None
            or subtipo not in CONSULTA_SUBTIPOS
            or state.get("identidade_desfecho") != IDENTIDADE_RECONHECIDA
            or not isinstance(ref, str)
            or not ref
        ):
            logger.info(
                "helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="sem_identidade"
            )
            return fixo
        try:
            async with asyncio.timeout(CONSULTA_PRAZO_S):
                view = await self._consultas.consultar(ref, subtipo)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            # Prazo (`TimeoutError` <: `OSError`) e falha de rede/servico viram o texto fixo; bug sobe.
            view = None
        fatos = fatos_da_consulta(subtipo, view)
        if fatos is None:
            logger.info(
                "helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="indisponivel"
            )
            return fixo
        texto: str | None = None
        if subtipo in SUBTIPOS_REDIGIDOS:
            texto = await self._redigir_consulta(state, subtipo, fatos)
            recusa = motivo_de_recusa_da_consulta(texto, fatos) if isinstance(texto, str) else "sem_rascunho"
        else:
            # Autorizacao e carencia (revisao do #690): SEMPRE a resposta deterministica, sem modelo.
            recusa = "subtipo_deterministico"
        if recusa is not None or not isinstance(texto, str):
            texto = resposta_deterministica(subtipo, fatos)
        logger.info(
            "helena_consulta_plano",
            node="consultar_plano",
            subtipo=subtipo,
            resultado="redigido" if recusa is None else "deterministico",
            recusa=recusa,
            prompt_version=CONSULTA_PLANO_PROMPT_VERSION,
        )
        return {
            "response_text": texto.strip(),
            "response_kind": "inform",
            "consulta_literais": literais_dos_fatos(fatos),
            # Onda 0 (historico): a resposta carrega dados do plano -> o historico grava so' um marcador.
            "resposta_com_dados_do_plano": True,
        }

    async def _redigir_consulta(
        self, state: HelenaState, subtipo: str, fatos: Mapping[str, Any]
    ) -> str | None:
        """O rascunho do modelo sobre os FATOS, ou `None` se o modelo falhou. SEM cerca (quem cerca e'
        `consultar_plano`). Os fatos e a mensagem vao em blocos demarcados (HEL-06)."""
        prompt = consulta_prompt(
            subtipo=subtipo,
            fatos=fatos,
            bloco_mensagem=render_untrusted_block("message_body", state.get("message_body", "")),
        )
        try:
            return await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: fraseia fatos ja' decididos -> task_default.
                task_kind="task_default",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return None

    async def handoff_cobranca(self, state: HelenaState) -> dict[str, Any]:
        """NUMERO UNICO (onda e; plano §2.4): escreve a saida tipada `handoff` e a frase de
        passagem. SEM LLM, SEM processo — o Lucas e' quem atende, no despachante, depois do turno.

        A frase (`FRASE_PASSAGEM_COBRANCA`) so' sai na PRIMEIRA passagem: com o Lucas ja' ativo
        (`agente_ativo_conversa == "lucas"`) o texto fica vazio e `respond` nao envia nada. Este
        e' o UNICO lugar do repositorio que constroi um `HandoffCobranca`.
        """
        handoff: HandoffCobranca = {
            "para": "lucas",
            # Ja' validado em `_validate_extraction` e de novo em `_handoff_recusado` (2 camadas).
            "cobranca_subtipo": cast(CobrancaSubtipo, state.get("cobranca_subtipo")),
            "competencia": state.get("cobranca_competencia"),
            "message_ref": str(state.get("message_ref") or ""),
        }
        lucas_ativo = state.get("agente_ativo_conversa") == "lucas"
        logger.info(
            "helena_handoff_cobranca",
            node="handoff_cobranca",
            cobranca_subtipo=handoff["cobranca_subtipo"],
            competencia_presente=handoff["competencia"] is not None,
            frase_de_passagem=not lucas_ativo,
        )
        return {
            "handoff": handoff,
            "response_kind": RESPONSE_KIND_HANDOFF,
            "response_text": "" if lucas_ativo else FRASE_PASSAGEM_COBRANCA,
        }

    async def schedule(self, state: HelenaState) -> dict[str, Any]:
        """GAP 9.2 fix: direct scheduling is out of scope this phase, but the beneficiary is
        actually HANDED OFF to a human, not just told one will follow up. Pre-fix this node only
        drafted the honest "not available on this channel" reply and ended the turn — the
        promise of a human follow-up (`prompts.py`'s `response_kind="schedule"` instructions)
        was never backed by a real SP-OP-ESCALATION-001 start, so a beneficiary asking to
        (re)schedule got a dead end unless they asked again in words `classify` recognizes as
        `human_request`. This now routes through the SAME `_start_escalation` helper `escalate`
        uses (`motivo_categoria="solicitacao_humano"` — the contract vocabulary's closest fit for
        "needs a human to act", `spec/agents/helena/agent.yaml`'s `escalation.triggers`), just
        with the scheduling-specific reply text (`response_kind="schedule"`) instead of the
        generic escalate one.

        HELENA-SEVERIDADE-DEFAULT: `severidade="leve"` here is an EXPLICIT business value tied
        1:1 to `motivo="solicitacao_humano"` — the same pairing `classify`'s own gatilho 3
        (`intent == "human_request"`) assigns explicitly below — not a fallback for an unknown
        clinical state. A scheduling request carries no clinical signal at all (`escalation_routing.
        dmn` rule `r5` does not even read `severidade` for `solicitacao_humano`), so `leve` is a
        valor de negocio explicito (nao revisado por SME), never a guess standing in for a missing one.
        """
        return await self._start_escalation(
            state,
            motivo="solicitacao_humano",
            severidade="leve",
            response_kind="schedule",
        )

    async def escalate(self, state: HelenaState) -> dict[str, Any]:
        """Start SP-OP-ESCALATION-001 idempotently and draft the handoff response.

        A tool failure here never blocks the turn — the response still tells the beneficiary a
        human will follow up (`error` records the failure for observability; the runtime's own
        fail-closed posture treats an unstarted escalation as an incident, never a silent drop).

        HELENA-SEVERIDADE-DEFAULT: `severidade` is read WITHOUT a fallback. Every real gatilho in
        `classify` (the only place that decides `next_kind="escalate"` off a genuine clinical/
        administrative signal) assigns `escalation_severidade` explicitly; the one path that
        reaches here without it is `receive`'s own missing-runtime-context escalate. That case's
        severidade is genuinely UNKNOWN, and an unknown one is never announced as `leve` (mirrors
        GAP-ESC-SEVERITY-GROUP's own principle, one layer down). `None` is forwarded verbatim into
        `_start_escalation`.

        §Delta-3 (regressao P-12) — CORRECAO of what this docstring used to claim: that a `None`
        `severidade` was REFUSED fail-closed on both notify channels and that "NO notification is
        published on either channel", the case still reaching the HITL through
        `BE_NotifFallbackFailed`. It did not reach it — the live engine proved the escalation
        stopped short of `UT_TratarEscalonamento`. Both paths that arrive here without a severidade
        (missing runtime context, and the classifier failure) carry
        `motivo_categoria="falha_tecnica"`, which is exactly the motivo the contract declares
        `null` for; `escalation.py::_exigir_severidade` now implements that declared exception, so
        the notification IS published (to the group `escalation_routing`'s rule `r6` chose — that
        rule matches `severidade` at the `-` wildcard, so it never read it) and the instance
        continues along `Flow_Notificar_UT` -> `UT_TratarEscalonamento`. What is removed is the
        fabricated value, not the page.
        """
        # Ausencia nao e classificacao "outro" (HELENA-ESCALATION-MOTIVO-OUTRO-FALLBACK).
        motivo: MotivoCategoria | None = state.get("escalation_motivo") or (
            "falha_tecnica" if state.get("error") else None
        )
        severidade: Severidade | None = state.get("escalation_severidade")
        return await self._start_escalation(
            state, motivo=motivo, severidade=severidade, response_kind="escalate"
        )

    async def _caso_aberto_e_clinico(self, business_key: str) -> bool:
        """O caso que ocupa a chave da conversa e' um alerta clinico? Na duvida, NAO: abrir um caso
        clinico a mais custa um atendimento; deixar de abrir um custa o alerta (a idempotencia da
        chave `-clin` impede que o "a mais" se repita)."""
        try:
            status = await self._cibseven.get_process_status(business_key)
        except PROGRAMMING_ERRORS:
            raise
        except CibSevenError:
            return False
        except EXTERNAL_DEPENDENCY_FAILURES:
            return False
        return str((status.variables or {}).get("motivo_categoria") or "") in MOTIVOS_DE_ALERTA_CLINICO

    async def _start_escalation(
        self,
        state: HelenaState,
        *,
        motivo: MotivoCategoria | None,
        severidade: Severidade | None,
        response_kind: ResponseKind,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Shared SP-OP-ESCALATION-001 start, factored out of `escalate` (GAP 9.2) so `schedule`
        can hand off to a human through the EXACT SAME audited/idempotent path instead of a
        second, parallel (and easy-to-drift) implementation. `response_kind` is the only thing
        that varies downstream: it selects which honest reply `prompts.py.response_prompt()`
        drafts (`"escalate"`'s generic handoff text vs `"schedule"`'s scheduling-specific one) —
        the escalation itself (business key, audit-before-effect, idempotent start, provenance)
        is identical regardless of caller.

        HELENA-SEVERIDADE-DEFAULT: `severidade` is `Severidade | None` — `None` on `escalate`'s
        missing-runtime-context path and on its classifier-failure path (never fabricated to
        `leve`); `schedule` and every real clinical `escalate` gatilho always pass a real domain
        value. `None` rides verbatim into the `severidade` process variable. §Delta-3: the worker
        (`escalation.py::_exigir_severidade`) ACCEPTS that `None` under the contract's declared
        `motivo_categoria=falha_tecnica` exception — it is not refused, and it is never coerced to
        a domain value anywhere along the way; every OTHER motivo still fails closed there.
        """
        business_key = _business_key(state)

        # HEL-05 — DEFENSE IN DEPTH, on top of the chokepoint's own scrub. `_resumo_contexto` is
        # a free-text LLM draft over the beneficiary's own message, and Helena is the DIRECT
        # producer of the contractual `resumo_contexto` (SP-OP-ESCALATION-001 §Variaveis:
        # "pseudonimizado"), so the identifier net is applied HERE, at the producer, and again at
        # `start_process_idempotent` (CC-06). Scrubbing twice is idempotent: `redact_free_text`
        # replaces identifier substrings with class tokens, and the class tokens match no pattern.
        resumo = redact_free_text(await self._resumo_contexto(state, motivo))
        motivo_tecnico = error or state.get("error")
        if motivo == "falha_tecnica" and motivo_tecnico:
            # R1 cycle-1 fix: carry the technical-failure reason into the human handoff so the
            # attendant sees WHY the automated turn failed. The reason is bounded and contains
            # no raw LLM output / no beneficiary text (see `_classify_llm`) — everything else in
            # this variable set is already pseudonymized (ADR-0006).
            #
            # HEL-05: `str(...)[:300]` was a LENGTH bound, never a CONTENT one. `error` is not
            # always the bounded classifier token this comment describes — `escalate`'s own
            # `CibSevenError` handler writes `f"start_process indisponivel: {exc}"` into it, and a
            # transport exception message is arbitrary text from another system. `redact_error_message`
            # (which delegates to the same `redact_free_text` net) bounds BOTH.
            resumo = f"{resumo} [falha tecnica: {redact_error_message(motivo_tecnico)}]"
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "source_agent_id": "helena",
            "source_agent_version": self._agent_version,
            "conversation_id": state.get("conversation_id", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "canal": state.get("canal", "whatsapp"),
            "motivo_categoria": motivo,
            "severidade": severidade,
            "resumo_contexto": resumo,
        }
        ref = state.get("dmn_decision_ref")
        if ref:
            variables["dmn_decision_ref"] = ref

        if response_kind == "escalate":
            # TEXTO FIXO, sem modelo — ver `RESPOSTA_ESCALONAMENTO_URGENTE`. O modelo continua
            # redigindo so' o `schedule` (hoje sem rota de entrada, DL-0052).
            response_text = _texto_de_escalonamento(motivo, severidade)
        else:
            try:
                response_text = await self._respond_llm(state, response_kind)
            except RespostaRecusadaError:
                # A recusa ja foi registrada e contada em `_respond_llm`. AQUI um humano FOI mesmo
                # acionado (este metodo esta' abrindo o processo), entao a constante honesta promete
                # o que e' verdade e nao contem nenhum dos padroes proibidos. Nao ha nova tentativa
                # pelo mesmo motivo que em `inform`.
                response_text = RESPOSTA_HANDOFF_RECUSADA
        provenance = AgentDecisionProvenance(
            agent_id="helena",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            # Bounded routing tokens ONLY — never `resumo_contexto` (PHI, ADR-0006).
            decision_basis={
                "escalation_motivo": motivo,
                "escalation_severidade": severidade,
                "dmn_decision_ref": state.get("dmn_decision_ref") or "",
            },
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=PROCESS_KEY,
                business_key=business_key,
                variables=variables,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
            if (
                _start_desfecho_de(instance.start_outcome) == START_DESFECHO_JA_ATIVO
                and motivo in MOTIVOS_DE_ALERTA_CLINICO
                and severidade == "grave"
                and not business_key.endswith(SUFIXO_CASO_CLINICO)
                and not await self._caso_aberto_e_clinico(business_key)
            ):
                # DL-0072 (LU090): alerta clinico GRAVE nunca e' suprimido por um caso de outra
                # classe. A chave da conversa esta' ocupada por cobranca (ou outro assunto): abre-se um
                # caso clinico PROPRIO, P1, e o outro segue como esta. Uma falha aqui cai no mesmo
                # `except CibSevenError` abaixo: a pessoa recebe a resposta honesta de falha, nunca o
                # "seu atendimento ja esta aberto" que escondia a emergencia.
                business_key = chave_do_escalonamento(
                    state.get("tenant_id", ""), state.get("conversation_id", ""), clinico=True
                )
                instance = await start_process_idempotent(
                    self._cibseven,
                    process_key=PROCESS_KEY,
                    business_key=business_key,
                    variables={
                        **variables,
                        "resumo_contexto": f"{variables['resumo_contexto']} {NOTA_CASO_CLINICO_PARALELO}",
                    },
                    audit_sink=self._audit_sink,
                    provenance=provenance,
                )
        except CibSevenError as exc:
            return {
                "escalation_started": False,
                # CC-01: o marcador que `respond` le para NAO prometer um humano que ninguem
                # acionou. `escalation_started is False` sozinho nao serve: e tambem o neutro de
                # um turno informativo, que nunca tentou escalar coisa nenhuma.
                "start_failed": True,
                "start_desfecho": START_DESFECHO_FALHOU,
                "escalation_motivo": motivo,
                "escalation_severidade": severidade,
                "escalation_business_key": business_key,
                # HEL-05 (feeder): a transport exception message is arbitrary text from another
                # system, and this `error` survives into the NEXT turn's `[falha tecnica: ...]`
                # suffix under the live checkpointed dispatch (T4b).
                "error": start_unavailable_error(exc),
                "response_text": response_text,
                "response_kind": response_kind,
            }

        # CERCA TEXTO x FATO (F1): o veredito que o chokepoint ja' ASSERTAVA do proprio fluxo de
        # controle (`StartOutcome`, F3 MAJOR-2) e que este grafo ignorava. `already_existed` sozinho
        # nao serve — e' um booleano REPORTADO PELO TRANSPORTE, e nao distingue "instancia viva" de
        # "instancia que ja' rodou e terminou"; o proprio docstring de `StartOutcome` registra que
        # e' por isso que o campo tipado existe.
        start_desfecho = _start_desfecho_de(instance.start_outcome)
        if start_desfecho != START_DESFECHO_NOVO:
            # ITEM 3 DO DIRETOR ("toda recusa/nao-start deixa rastro"). Antes deste log um
            # nao-start SEM erro era completamente silencioso: nenhuma excecao, nenhum contador,
            # nenhum campo — foi assim que 27 escalonamentos reusados atravessaram uma bateria
            # inteira sem aparecer. `business_key` e' a chave idempotente que o proprio engine ja
            # ve; nada aqui e' texto de beneficiario.
            logger.warning(
                "helena_escalonamento_nao_aberto",
                node="_start_escalation",
                start_desfecho=start_desfecho,
                process_key=PROCESS_KEY,
                business_key=business_key,
                instance_id=instance.instance_id,
                engine_state=instance.state,
                motivo_categoria=motivo,
            )
        saida_ok: dict[str, Any] = {
            "escalation_started": True,
            "start_desfecho": start_desfecho,
            "escalation_motivo": motivo,
            "escalation_severidade": severidade,
            "escalation_business_key": business_key,
            "escalation_process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
                # O veredito do chokepoint, verbatim, ao lado do booleano que ele corrige — quem
                # le' o handoff no painel precisa saber se a instancia citada nasceu AGORA.
                "start_outcome": str(getattr(instance.start_outcome, "value", instance.start_outcome)),
            },
            "response_text": response_text,
            "response_kind": response_kind,
        }
        if error:
            # A recusa de saida (o unico chamador que informa `error` hoje) precisa sobreviver no
            # estado do turno: sem ela o desfecho seria indistinguivel de um escalonamento clinico
            # comum, e a linha de log seria a unica testemunha de que a Helena foi barrada.
            saida_ok["error"] = error
        return saida_ok

    async def respond(self, state: HelenaState) -> dict[str, Any]:
        """Send the drafted response over WhatsApp. A policy refusal or transport failure is
        recorded in `error` (observable) — it never silently disappears (v1 landmine #2 this
        design avoids by design: no blanket `except Exception: pass`).

        CC-01 — A MENSAGEM TEM DE CORRESPONDER AO QUE ACONTECEU. `_start_escalation` redige o
        texto de handoff (`_respond_llm`) ANTES de tentar o start, e ate 2026-09-04 este no
        enviava esse texto tambem quando o start havia falhado: o beneficiario lia "um
        profissional vai continuar seu atendimento" enquanto ZERO instancias de
        SP-OP-ESCALATION-001 existiam — uma promessa de humano que ninguem acionou, sem prazo
        (o SLA vive na instancia que nao nasceu) e sem alerta. Agora o handoff so sai quando a
        escalacao existe; caso contrario o texto e SUBSTITUIDO pela mensagem honesta de falha
        tecnica e o turno declara `desfecho=erro_inicio_processo`.

        CERCA TEXTO x FATO (21/09/2026, F1+F2) — CC-01 cobriu a FALHA de start, e a bateria do
        diretor mostrou que faltavam os outros dois lados. `_texto_bate_com_o_fato` (chamado no
        ramo de sucesso, logo abaixo) fecha os tres: promessa de humano so' sai quando o start
        ACONTECEU (`start_desfecho` in `_START_DESFECHOS_COM_HUMANO`), o texto e' OBRIGADO a
        mencionar o encaminhamento quando aconteceu, e um `already_existed` diz a verdade — o
        atendimento ja' estava aberto e nenhum outro foi iniciado.

        CC-09: emite UM `maezo_agent_desfecho_total` para este turno. O `desfecho` do label e'
        DERIVADO aqui, via override, de `escalation_started`/`response_kind`/`escalation_motivo`
        — os sinais reais desta agente — nunca lido de volta de `state.get("desfecho")` (o valor
        so existiria se um turno ANTERIOR ja o tivesse escrito, e o resultado deste turno e' o
        que importa). NEW-10: o MESMO valor tambem e' gravado em `saida["desfecho"]` logo abaixo
        — ate 2026-09-05 esse ramo de sucesso so passava o valor para a telemetria, nunca de
        volta ao proprio estado, entao `HelenaState.desfecho` ficava "" apos qualquer turno
        bem-sucedido (so o ramo `start_failed=True`, via `notify_start_failure`, o escrevia). O
        ramo `start_failed=True` NAO emite aqui: `_start_failure_outcome` ja delega ao helper
        compartilhado (`runtime.start_outcome.notify_start_failure`), que emite por conta
        propria E ja grava `desfecho=erro_inicio_processo` no dict que retorna — emitir aqui
        tambem duplicaria o turno.
        """
        # NUMERO UNICO (onda e): o Lucas ja' estava com a conversa, entao a Helena NAO envia nada
        # — quem responde e' o Lucas, no despachante. Desfecho proprio, e nao a guarda HEL-07 de
        # resposta vazia: nao e' um rascunho que sumiu, e' um silencio decidido.
        if (
            state.get("response_kind") == RESPONSE_KIND_HANDOFF
            and state.get("start_failed") is not True
            and not str(state.get("response_text") or "").strip()
        ):
            emit_turn_desfecho(
                state,
                agent_id="helena",
                desfecho=DESFECHO_PASSAGEM_SEM_FRASE,
                route=RESPONSE_KIND_HANDOFF,
                motivo_categoria=None,
                enviada=False,
            )
            return {"desfecho": DESFECHO_PASSAGEM_SEM_FRASE, **self._historico_do_turno(state, None)}
        # UM unico `send` e UM unico handler de falha de envio nos dois ramos — o que muda entre
        # eles e O QUE se diz e O QUE o turno declara, nunca o mecanismo de envio.
        if state.get("start_failed") is True:
            saida = self._start_failure_outcome(state)
            text = RESPOSTA_FALHA_TECNICA_START
        else:
            saida = {}
            text, cerca = self._texto_bate_com_o_fato(state, state.get("response_text") or "")
            saida.update(cerca)
        if not text.strip():
            # HEL-07: NADA e' enviado. Uma mensagem em branco no WhatsApp nao informa e ainda
            # parece um sistema quebrado; e a constante de handoff (`RESPOSTA_HANDOFF_RECUSADA`,
            # "um profissional vai dar continuidade") NAO serve de substituto num turno `inform`,
            # onde ninguem foi acionado — seria a promessa sem lastro que CC-01 removeu do ramo de
            # falha de start e que 21/09/2026 removeu tambem do fallback de redacao. Entao a
            # postura e' fail-closed e OBSERVAVEL: sem envio, `error` com token de classe,
            # `desfecho` proprio e um contador com `enviada=False`.
            #
            # O ramo `start_failed=True` nao alcanca este ponto — seu texto e a constante
            # `RESPOSTA_FALHA_TECNICA_START` —, e a guarda esta assim mesmo DEPOIS dos dois ramos
            # de proposito: e' estrutural, imediatamente antes do unico `send` do no'. Se um dia
            # aquela constante ficasse vazia, este e' o lugar que pegaria.
            saida = {**saida, "error": ERRO_RESPOSTA_VAZIA, "desfecho": DESFECHO_RESPOSTA_VAZIA}
            if state.get("start_failed") is not True:
                emit_turn_desfecho(
                    state,
                    agent_id="helena",
                    desfecho=DESFECHO_RESPOSTA_VAZIA,
                    route=state.get("response_kind"),
                    motivo_categoria=state.get("escalation_motivo"),
                    enviada=False,
                )
            return {**saida, **self._historico_do_turno(state, None)}
        # DL-0078: o aviso de identidade de PRIMEIRO CONTATO, como mensagem SEPARADA e ANTES da resposta
        # do turno — nunca no lugar dela. Ver `_aviso_de_identidade` (quando sai, quando e' adiado).
        aviso_enviado, aviso_na_resposta = await self._aviso_de_identidade(state, text)
        enviada = False
        try:
            await self._whatsapp.send(_to_hash_from_state(state), text)
            enviada = True
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            # HEL-05 (feeder): same chain as the two above — `error` reaches `resumo_contexto`.
            # O desfecho de falha de start (quando ha um) NAO e apagado por uma falha de envio:
            # o caso continua marcado como start falho, que e o que a operacao precisa ver.
            saida = {**saida, "error": f"whatsapp send failed: {redact_error_message(exc)}"}
        # DL-0080: a mensagem da pessoa entra sempre; a da Helena SO' se SAIU, e e' o texto ENVIADO
        # (`text`, ja' depois das cercas) — nunca o rascunho barrado. O aviso de identidade (mensagem
        # separada) nao entra: e' texto fixo de identidade, nao conversa.
        saida = {**saida, **self._historico_do_turno(state, text if enviada else None)}
        if aviso_enviado or (aviso_na_resposta and enviada):
            # DL-0078: so' depois de a pessoa ter RECEBIDO o texto de identidade (separado ou como a
            # propria resposta do turno). Vai ao checkpoint: o aviso nao se repete nesta conversa.
            saida = {**saida, "aviso_identidade_dado": True}
        if state.get("start_failed") is not True:
            # NEW-10: antes, so o ramo de falha de start escrevia `desfecho` em `state`
            # (via `notify_start_failure`, chamado por `_start_failure_outcome` acima) — este
            # ramo de sucesso so passava o valor por `desfecho=` ao helper de telemetria, nunca
            # de volta ao proprio `saida`, entao `HelenaState.desfecho` ficava "" apos um turno
            # bem-sucedido (self-disclosed como campo morto no comentario que citava esta linha).
            # Agora o mesmo valor rotulado na telemetria tambem e' gravado no estado — o campo
            # deixa de ser so-as-vezes-verdadeiro.
            if str(state.get("start_desfecho") or "") == START_DESFECHO_JA_ATIVO:
                # ITEM 3: rastro PROPRIO para o nao-start silencioso. Antes desta linha o turno
                # contava como `escalado_humano` — trabalho criado numa fila que ele nao criou.
                desfecho = DESFECHO_ESCALONAMENTO_JA_ABERTO
            elif state.get("escalation_started") is True:
                desfecho = "escalado_humano"
            elif (saida.get("response_kind") or state.get("response_kind")) == "collect":
                # COLETA: nem resolvido nem escalado — a conversa continua. Rotular como
                # `resolvido_automatico` inflaria a taxa de resolucao com perguntas.
                desfecho = DESFECHO_PERGUNTA_COLETA
            elif state.get("response_kind") == RESPONSE_KIND_HANDOFF:
                # Onda (e): a frase de passagem saiu; a conversa e' do Lucas a partir daqui.
                desfecho = DESFECHO_PASSAGEM_COBRANCA
            else:
                desfecho = "resolvido_automatico"
            emit_turn_desfecho(
                state,
                agent_id="helena",
                desfecho=desfecho,
                route=saida.get("response_kind") or state.get("response_kind"),
                motivo_categoria=saida.get("escalation_motivo") or state.get("escalation_motivo"),
                enviada=enviada,
            )
            saida = {**saida, "desfecho": desfecho}
            if enviada and _apresentou_se(text):
                # F6, 21/09/2026 (segunda rodada): o sinal acende SO' quando o texto ENVIADO trouxe
                # a apresentacao. Ele acendia em qualquer envio, inclusive nos turnos em que a cerca
                # TEXTO x FATO trocou o rascunho por uma constante que nao tem cartao nenhum — e o
                # efeito de liga-lo e' PROIBIR a apresentacao pelo resto da conversa. A pessoa lia
                # uma mensagem, nao o cartao, e a Helena nunca mais se apresentava: o oposto do F6,
                # que existe para ela nao REPETIR o cartao. Ver `MARCAS_DE_APRESENTACAO`.
                #
                # O ramo `start_failed=True` nao alcanca esta linha (ele retorna antes), e esta'
                # certo assim: o texto daquele ramo e' `RESPOSTA_FALHA_TECNICA_START`, que nao tem
                # cartao — o sinal ficaria apagado de qualquer forma.
                saida["apresentacao_ja_feita"] = True
            if enviada and _confirmacao_chegou(text, state.get("memoria_a_confirmar")):
                # QUARTA RODADA, 21/09/2026 — IRMA EXATA DA LINHA ACIMA, e pelo mesmo defeito.
                # `memoria_clinica.confirmada` era gravada em `classify`, quando a frase e'
                # GERADA; a cerca TEXTO x FATO troca o rascunho por constante depois disso, e num
                # `ja_ativo` a troca e' integral. A pessoa nunca lia a pergunta, `confirmada`
                # ficava `True` e a confirmacao nunca mais acontecia (`confirmar_agora` exige
                # `not ja_confirmada`) — o dado lembrado seguia decidindo a tabela de red flag sem
                # ninguem poder corrigi-lo. Ver `_confirmacao_chegou`.
                #
                # A GRAVACAO E' SOBRE A MEMORIA DESTE TURNO (`state["memoria_clinica"]`, ja'
                # escrita por `classify` neste mesmo turno), nunca sobre a do turno anterior: e' a
                # que vai ao checkpointer. E so' acrescenta o flag — o resto da memoria (quem e' o
                # paciente, a marca da avaliacao, o carimbo) e' preservado por copia.
                memoria_do_turno = state.get("memoria_clinica")
                if isinstance(memoria_do_turno, dict):
                    saida["memoria_clinica"] = {**memoria_do_turno, _MEMORIA_CONFIRMADA: True}
        return saida

    def _historico_do_turno(self, state: HelenaState, resposta_enviada: str | None) -> dict[str, Any]:
        """A atualizacao de `historico_conversa` deste turno (DL-0080), ou `{}` com a flag desligada.

        Chamada por TODO retorno de `respond` — inclusive a passagem silenciosa ao Lucas e a resposta
        vazia, em que nada sai mas a pessoa falou. A fala do beneficiario passa por `redact_free_text`
        (CPF, telefone, e-mail digitados nao ficam guardados). A da Helena e' o texto ENVIADO, ou o
        marcador `MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO` quando o turno respondeu com dados do plano
        (gancho da onda 1: a chave `CHAVE_RESPOSTA_COM_DADOS_DO_PLANO` verdadeira no estado).
        """
        if not self._historico_enabled:
            return {}
        mensagem = state.get("message_body")
        if isinstance(mensagem, str) and mensagem.strip():
            mensagem = redact_free_text(mensagem, max_chars=len(mensagem) + 64)
        resposta = resposta_enviada
        if resposta and cast(Mapping[str, Any], state).get(CHAVE_RESPOSTA_COM_DADOS_DO_PLANO) is True:
            resposta = MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO
        return {
            "historico_conversa": acrescentar_turno(
                state.get("historico_conversa"),
                mensagem=mensagem,
                resposta=resposta,
                agora=datetime.now(UTC),
            )
        }

    async def _aviso_de_identidade(self, state: HelenaState, texto_do_turno: str) -> tuple[bool, bool]:
        """AVISO DE IDENTIDADE DE PRIMEIRO CONTATO (DL-0078). Devolve `(enviado, ja_na_resposta)`.

        QUANDO SAI. Desfecho determinado (`reconhecido`/`nao_encontrado`; indeterminado ou flag desligada =
        silencio), turno do BENEFICIARIO, aviso ainda nao dado nesta conversa (`aviso_identidade_dado`).
        "Primeiro contato" e' o primeiro turno da conversa (thread do checkpoint) em que essas condicoes
        valem e o turno e' comum; uma conversa que ja' existia quando a flag foi ligada recebe o aviso no
        proximo turno comum.

        ADIADO, NUNCA NA FRENTE DE UM ESCALONAMENTO. Turno que escala (red flag, risco psicossocial,
        pedido de humano, falha tecnica, falha de start) ou agenda NAO recebe o aviso: a resposta de
        seguranca sai sozinha e primeiro, e o aviso fica para o proximo turno comum. A triagem, as red
        flags, SP-OP-ESCALATION-001, o roteador do Lucas e o DL-0052 nao mudam em nada — este metodo so'
        acrescenta uma mensagem antes da resposta que o turno ja' ia mandar.

        JA' NA RESPOSTA. Se o texto do turno ja' contem o texto de identidade (a pessoa perguntou "sabe
        quem sou eu?"), nada sai a mais; `respond` acende o marcador se a resposta for entregue.

        PELO MESMO CAMINHO GOVERNADO. O mesmo `self._whatsapp` (o remetente com o gate de efeito do
        despachante) e as MESMAS cercas de saida da resposta (`motivo_de_canal_nao_confirmado` +
        `motivo_de_recusa` com `start_aconteceu=False`); recusado, NAO sai. Uma falha de envio do aviso
        nao derruba nem atrasa a resposta: vira log e o aviso volta no proximo turno. Telemetria so' com
        tokens fechados — nunca referencia, hash ou telefone.
        """
        desfecho = state.get("identidade_desfecho")
        aviso = texto_aviso_de_identidade(desfecho, nome_operadora=self._nome_operadora)
        if (
            aviso is None
            or state.get("aviso_identidade_dado") is True
            or state.get("origem_do_turno", ORIGEM_BENEFICIARIO) != ORIGEM_BENEFICIARIO
        ):
            return False, False
        pergunta = texto_pergunta_de_identidade(desfecho, nome_operadora=self._nome_operadora)
        if aviso in texto_do_turno or (pergunta is not None and pergunta in texto_do_turno):
            logger.info(
                "helena_aviso_identidade", node="respond", aviso_identidade=desfecho, motivo="na_resposta"
            )
            return False, True
        if (
            state.get("next_kind") in ("escalate", "schedule")
            or state.get("escalation_motivo") is not None
            or state.get("escalation_started") is True
            or state.get("start_failed") is True
        ):
            logger.info("helena_aviso_identidade", node="respond", aviso_identidade=desfecho, motivo="adiado")
            return False, False
        recusa = motivo_de_canal_nao_confirmado(aviso) or motivo_de_recusa(
            aviso, "inform", start_aconteceu=False
        )
        if recusa is not None:
            logger.error(
                "helena_aviso_identidade",
                node="respond",
                aviso_identidade=desfecho,
                motivo="recusado_pela_cerca",
                grupo=recusa[0],
            )
            return False, False
        try:
            await self._whatsapp.send(_to_hash_from_state(state), aviso)
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            # ENVIO BEST-EFFORT, largo pelo mesmo motivo do envio de `respond` (a superficie declarada do
            # cliente inclui `ValueError` cru): o aviso e' cortesia e nunca pode custar a resposta.
            logger.warning(
                "helena_aviso_identidade",
                node="respond",
                aviso_identidade=desfecho,
                motivo="falha_de_envio",
                error_type=type(exc).__name__,
            )
            return False, False
        logger.info("helena_aviso_identidade", node="respond", aviso_identidade=desfecho, motivo="enviado")
        return True, False

    def _texto_bate_com_o_fato(self, state: HelenaState, texto: str) -> tuple[str, dict[str, Any]]:
        """CERCA TEXTO x FATO (21/09/2026, F1+F2): o texto que sai tem de corresponder ao que
        ACONTECEU. Devolve `(texto_a_enviar, atualizacao_de_estado)`.

        POR QUE AQUI, E NAO EM `_respond_llm`. A cerca de 13/09 mora em `_respond_llm`, que redige
        o rascunho — e o rascunho e' escrito ANTES de o start ser tentado (`_start_escalation`
        chama `_respond_llm` e so' depois `start_process_idempotent`). Naquele ponto o fato ainda
        nao existe: e' por isso que `motivo_de_recusa` recebe la' `start_aconteceu=None` e decide
        pela ROTA. `respond` e' o UNICO lugar do grafo em que o fato ja' esta' decidido e o texto
        ainda nao saiu — e' o ponto onde a verificacao e' possivel, e o unico.

        TRES SUBSTITUICOES, e todas por CONSTANTE — nunca uma segunda chamada ao modelo. E' a mesma
        escolha de CC-01 e da recusa de saida, pela mesma razao: o mesmo prompt com a mesma
        mensagem tende ao mesmo texto, e um laco de tentativas transforma uma cerca num atraso.

          1. `ja_ativo` — a escalacao desta conversa JA estava aberta (o C1 da bateria). So' a
             AFIRMACAO DE ASSUNCAO e' falsa aqui, e e' ela que a cerca troca
             (`motivo_de_recusa(..., start_aconteceu=False)`, a mesma definicao do item 3); um
             rascunho que nao afirma nada sobre humano recebe a constante como PREFIXO e segue
             atras dela, porque a `memoria_a_confirmar` e a orientacao do turno nao tem nada de
             errado — descartar as duas trocava um defeito por outro.
          2. START ACONTECEU e o texto NAO MENCIONA o encaminhamento (o E4). O texto e' trocado
             pela constante de handoff, que menciona.
          3. START NAO ACONTECEU e o texto ANUNCIA um humano. Trocado pela constante que diz o que
             nao aconteceu. O gatilho e' UMA definicao para os dois sentidos — a mesma
             `menciona_encaminhamento` do item 2, agora consultada por `motivo_de_recusa` com
             `start_aconteceu=False` —, porque as duas listas antigas (a positiva e a de literais
             proibidos) foram escritas para textos diferentes e a diferenca entre elas era um
             conjunto de rascunhos plausiveis que saiam intactos. Inalcancavel pela rota
             `escalate`/`schedule` (as duas sempre passam por `_start_escalation`, que grava o
             desfecho) — e' o backstop ESTRUTURAL para as rotas que nao abrem processo. O fallback
             canned de `_respond_llm`, que era o caminho REAL deste item, deixou de prometer
             humano na origem (`RESPOSTA_FALHA_DE_REDACAO`): o backstop fica, a promessa sai.

        O QUE ELA NAO FAZ: nao muda `response_kind` nem `escalation_*`. A rota e o efeito
        aconteceram; o que esta' errado e' a NARRACAO deles, e e' so' a narracao que se corrige.

        RESIDUAL DIVULGADO: uma falha de ENVIO logo depois sobrescreve o `error` que esta cerca
        acaba de gravar (`respond` prefere o `whatsapp send failed`, que e' o mais acionavel). O
        rastro da troca nao se perde — ele esta' na linha de log e no contador, os dois emitidos
        por `_registrar_troca` ANTES de qualquer tentativa de envio.
        """
        if not texto.strip():
            # Turno sem rascunho nenhum: quem trata e' a guarda HEL-07 de `respond`, com desfecho
            # e contador proprios. Substituir aqui esconderia um "nada foi enviado" atras de uma
            # frase plausivel.
            return texto, {}
        response_kind = str(state.get("response_kind") or "")
        start_desfecho = str(state.get("start_desfecho") or START_DESFECHO_NAO_TENTADO)
        acionado = _humano_acionado(state)
        # CONSULTA DO PLANO: as cercas leem o texto com os literais DO CADASTRO apagados (um status
        # "em analise" que consta no cadastro e' fato). `None` em todo turno sem consulta = o texto
        # de sempre, sem mudanca nenhuma. O que SAI continua sendo `texto`, inteiro.
        literais = state.get("consulta_literais")
        if literais:
            texto_cercado = texto_para_cerca(texto, literais)
            enviado, cerca = self._texto_bate_com_o_fato_cercado(
                state, texto_cercado, response_kind, start_desfecho, acionado
            )
            # O texto cercado so' pode sair como ele mesmo (ou como sufixo do prefixo honesto do
            # `ja_ativo`): nos dois casos volta o ORIGINAL. Uma constante de troca sai como esta'.
            if enviado == texto_cercado:
                return texto, cerca
            if texto_cercado.strip() and texto_cercado.strip() in enviado:
                enviado = enviado.replace(texto_cercado.strip(), texto.strip())
            return enviado, cerca
        return self._texto_bate_com_o_fato_cercado(state, texto, response_kind, start_desfecho, acionado)

    def _texto_bate_com_o_fato_cercado(
        self,
        state: HelenaState,
        texto: str,
        response_kind: str,
        start_desfecho: str,
        acionado: bool,
    ) -> tuple[str, dict[str, Any]]:
        """O corpo de `_texto_bate_com_o_fato`, inalterado (a extracao so' separou o texto cercado)."""

        if start_desfecho == START_DESFECHO_JA_ATIVO:
            # 21/09/2026, SEGUNDA RODADA: a troca deixou de ser INCONDICIONAL. O que e' falso num
            # `ja_ativo` e' o anuncio de um encaminhamento NOVO — nao o resto do rascunho. Descartar
            # tudo custava duas coisas medidas: a `memoria_a_confirmar` (a pergunta que o
            # `response_prompt` obriga a fazer antes de usar um dado LEMBRADO, e que existe PARA a
            # pessoa poder corrigir a populacao que decidiu a tabela) e a orientacao do turno. Num
            # turno em que a escalacao ja' estava aberta, perder a confirmacao do dado clinico e'
            # trocar um defeito por outro.
            #
            # Entao: se o rascunho faz QUALQUER AFIRMACAO DE ASSUNCAO, ele e' trocado por inteiro —
            # aquela frase depende de um fato que o modelo nao tinha quando redigiu. Se nao faz, o
            # texto honesto entra como PREFIXO e o rascunho segue atras. O rastro e' emitido nos
            # dois caminhos, com o mesmo grupo: o que a operacao conta e' "houve nao-start", e isso
            # aconteceu igual nos dois.
            #
            # "QUALQUER AFIRMACAO DE ASSUNCAO" E' `motivo_de_recusa(..., start_aconteceu=False)`, E
            # NAO SO' `menciona_encaminhamento` (21/09/2026, TERCEIRA RODADA). O gatilho anterior
            # era a lista POSITIVA de mencao, e o sufixo sobrevivente contradizia o proprio prefixo:
            #
            #     "(...) Por isso nao abri outro atendimento. (...) Nao se preocupe, um atendente
            #      vai assumir o seu caso agora."
            #
            # A causa raiz daquele texto era o filtro de negacao da cerca de mencao (removido em
            # `prompts.py::padrao_de_encaminhamento`), e consertar a causa raiz basta para ESTE
            # texto. O que muda aqui e' a CLASSE: a decisao passa a usar a MESMA definicao do item
            # 3 logo abaixo — a de "este texto afirma que um humano assumiu" —, que e' mais larga
            # que a lista positiva (soma os literais de `PROMESSA_DE_HUMANO_PROIBIDA`). O custo e'
            # declarado e pequeno: um rascunho que diga "aguarde nosso contato" num `ja_ativo`
            # perde a `memoria_a_confirmar`, e a constante ja' diz tudo o que e' verdade sobre a
            # acao humana neste turno. O prefixo existe para preservar a confirmacao clinica e a
            # orientacao, nao uma segunda afirmacao sobre o handoff.
            #
            # POR QUE NAO RECERCAR O TEXTO MONTADO: `RESPOSTA_HANDOFF_JA_ABERTO` casa a propria
            # cerca de mencao de proposito ("seu atendimento ... ja esta aberto" e' a frase honesta
            # que o item 2 envia). Cercar `com_prefixo` daria True SEMPRE e mataria o caminho do
            # prefixo inteiro — a cerca tem de ler o RASCUNHO, nao a montagem.
            recusa = motivo_de_recusa(texto, response_kind, start_aconteceu=False)
            enviar = (
                RESPOSTA_HANDOFF_JA_ABERTO
                if recusa is not None
                else f"{RESPOSTA_HANDOFF_JA_ABERTO} {texto.strip()}"
            )
            return enviar, self._registrar_troca(
                state,
                grupo=RECUSA_ESCALONAMENTO_JA_ABERTO,
                response_kind=response_kind,
                start_desfecho=start_desfecho,
                texto=enviar,
                padrao=recusa[1] if recusa is not None else None,
            )

        if acionado and not menciona_encaminhamento(texto):
            # F2: um humano recebeu o caso e o beneficiario nao foi avisado. A constante de handoff
            # da recusa de saida serve exatamente aqui — ela JA e' a frase honesta para "um humano
            # foi acionado neste ponto", e passa na propria cerca (teste fixa as duas coisas).
            return RESPOSTA_HANDOFF_RECUSADA, self._registrar_troca(
                state,
                grupo=RECUSA_HANDOFF_SEM_MENCAO,
                response_kind=response_kind,
                start_desfecho=start_desfecho,
                texto=RESPOSTA_HANDOFF_RECUSADA,
                error=ERRO_HANDOFF_SEM_MENCAO,
            )

        if not acionado:
            recusa = motivo_de_recusa(texto, response_kind, start_aconteceu=False)
            if recusa is not None:
                return RESPOSTA_SEM_ENCAMINHAMENTO, self._registrar_troca(
                    state,
                    grupo=recusa[0],
                    response_kind=response_kind,
                    start_desfecho=start_desfecho,
                    texto=RESPOSTA_SEM_ENCAMINHAMENTO,
                    error=ERRO_PROMESSA_SEM_START,
                    padrao=recusa[1],
                )
        return texto, {}

    @staticmethod
    def _registrar_troca(
        state: HelenaState,
        *,
        grupo: str,
        response_kind: str,
        start_desfecho: str,
        texto: str,
        error: str | None = None,
        padrao: str | None = None,
    ) -> dict[str, Any]:
        """O RASTRO de uma troca de texto pela cerca TEXTO x FATO: log + contador + estado.

        O PADRAO no log (onde alguem depura) e o GRUPO no contador (onde alguem conta) — a mesma
        disciplina de `_respond_llm`, pela mesma razao de cardinalidade. O TEXTO nao vai para
        nenhum dos dois: e' saida de modelo sobre a mensagem do beneficiario.

        `error` e' OPCIONAL de proposito. `ja_ativo` nao e' falha de ninguem — a idempotencia
        funcionou —, e escrever um `error` ali sujaria o sufixo `[falha tecnica: ...]` do handoff
        do turno seguinte com um fato que nao e' falha. O rastro daquele caso e' o `desfecho`
        proprio, o log e o contador.

        E UM `error` QUE O TURNO JA' TINHA NAO E' SOBRESCRITO (21/09/2026, segunda rodada). O caso
        que expos isto: uma falha do provedor de inferencia faz `classify` escrever
        `error="classify LLM call failed: ..."`, que e' a CAUSA do escalonamento e e' o que o
        atendente le' no `[falha tecnica: ...]` do handoff; a mesma falha faz o rascunho cair na
        constante de falha de redacao, que nao menciona o encaminhamento, e a troca do item 2
        gravava `handoff sem mencao` em cima. A narracao do texto nao pode apagar o motivo tecnico
        do turno — e o rastro da troca nao se perde, porque ele esta' no log e no contador, os
        dois emitidos aqui (exatamente o argumento que o `ja_ativo` acima ja' fazia).

        O NIVEL SEGUE A MESMA DISTINCAO (21/09/2026, segunda rodada). `escalonamento_ja_aberto` sai
        em `warning`: a idempotencia funcionou, ninguem precisa ser acordado, e um `error` ali
        treina a operacao a ignorar o canal. Os dois de TEXTO x FATO (`promessa_sem_start` e
        `handoff_sem_mencao`) saem em `error`: os dois significam que a Helena ia dizer ao
        beneficiario algo que nao corresponde ao que aconteceu. Emitir tudo no mesmo nivel apagava
        justamente a diferenca que o `error` opcional acima ja' registrava no estado.
        """
        emitir = logger.warning if grupo == RECUSA_ESCALONAMENTO_JA_ABERTO else logger.error
        emitir(
            "helena_texto_nao_bate_com_o_fato",
            node="respond",
            grupo=grupo,
            padrao=padrao,
            response_kind=response_kind,
            start_desfecho=start_desfecho,
            escalation_business_key=str(state.get("escalation_business_key") or ""),
            recusa_version=RECUSA_DE_SAIDA_VERSION,
            prompt_version=RESPONSE_PROMPT_VERSION,
        )
        record_resposta_recusada(agent_id="helena", motivo=grupo, response_kind=response_kind)
        saida: dict[str, Any] = {"response_text": texto}
        if error and not state.get("error"):
            saida["error"] = error
        return saida

    def _start_failure_outcome(self, state: HelenaState) -> dict[str, Any]:
        """CC-01: o desfecho + o alerta do turno em que a escalacao NAO pode ser aberta.

        O texto que acompanha (`RESPOSTA_FALHA_TECNICA_START`) e uma CONSTANTE, nao um draft de
        LLM: um modelo, pedido para "explicar uma falha tecnica", volta a prometer um atendente
        com facilidade — e a promessa e exatamente o que nao pode existir aqui. Ele substitui o
        handoff que `_start_escalation` ja havia redigido ANTES de tentar o start.

        O desfecho e o alerta vem do helper compartilhado (uma definicao para os 9 agentes).

        ITEM 2 DA REVISAO DE 21/09/2026 — "ele parece cobrir so' uma forma de falha". Cobre, e a
        auditoria abaixo e' a razao de estar CERTO assim. Este ramo e' alcancado por UM caminho:
        `start_failed=True`, escrito por EXATAMENTE um `except CibSevenError` (`_start_escalation`).
        O que mais `start_process_idempotent` pode levantar, e por que nao entra aqui:

          * `AuditPersistenceError`, `StartVariableRedactionError`, `StartClaimWithoutInstanceError`
            e `StartDedupGateUnavailableError` NAO sao `CibSevenError`, e cada uma declara no
            proprio docstring que isso e' DELIBERADO: um scrub de PHI que nao roda, um audit que
            nao persiste ou um portao de dedup nao avaliavel nao sao indisponibilidade de
            fornecedor — sao defeito do proprio controle. Elas PROPAGAM e derrubam o turno alto,
            que e' a postura correta, e nunca degradam para "uma falha tecnica a mais".
          * `CibSevenStartAuthorizationError` E' subclasse de `CibSevenError` (D7-A) e portanto
            CAI aqui, como toda indisponibilidade/recusa do engine no momento do start.
          * A RECUSA DE CONTRATO DO WORKER (`tools/workers/escalation.py::_exigir_severidade`, a
            hipotese do relatorio) nao pode chegar a este `except` por construcao, e nao por
            sorte: o worker e' uma EXTERNAL TASK que o engine entrega DEPOIS de a instancia
            existir, num processo separado. Se ele recusar, a instancia ja' nasceu — o start
            SUCEDEU. A prova esta' em `test_helena_cerca_texto_x_fato.py`
            (`test_falha_nao_cibseven_no_start_propaga_em_vez_de_virar_falha_tecnica` e o teste
            irmao que fixa o caminho da subclasse).

        E o C1 da bateria NAO era esta classe de falha: nada falhou la'. O start foi idempotente
        sobre uma instancia viva — silencio sem excecao —, que e' exatamente o que
        `start_desfecho`/`_humano_acionado` passaram a tornar visivel.

        F1 (VERIFY-CC09): `HelenaState` nao tem chave `route` (usa `response_kind`), entao o
        `state.get("route")` generico do helper devolveria `None` — passamos
        `route=RESPONSE_KIND_FALHA_TECNICA_START` explicitamente, o literal que
        `_ROUTE_VOCAB["helena"]` ja declara para este caso.
        """
        return emit_start_failure_notice(
            dict(state),
            agent_id="helena",
            process_key=PROCESS_KEY,
            route=RESPONSE_KIND_FALHA_TECNICA_START,
            extra={
                "response_text": RESPOSTA_FALHA_TECNICA_START,
                "response_kind": RESPONSE_KIND_FALHA_TECNICA_START,
                "escalation_started": False,
            },
        )

    async def resume(self, state: HelenaState) -> dict[str, Any]:
        """GAP-XHITL-4 — a porta de RETOMADA: instrucoes do humano -> cerca -> envio -> desfecho.

        Alcancada SO' por `_entrada` com `origem_do_turno == retomada`, que so'
        `new_helena_resume_state` grava. Faz, nesta ordem:

          1. SANEAMENTO DE ENTRADA, como `receive`: todo campo de saida volta ao neutro, para que
             nada do turno anterior (salvo no checkpoint) seja lido como se fosse deste. Ficam SO'
             as memorias de conversa que continuam verdadeiras depois de um humano ter atendido:
             `apresentacao_ja_feita` (a pessoa ja' leu o cartao) e `memoria_clinica` (quem e' o
             paciente). A memoria de COLETA e' zerada de proposito: uma pergunta deixada em aberto
             antes do escalonamento nao vale mais — o caso passou por um humano, e o turno
             seguinte do beneficiario nao pode ser lido como resposta a ela.
          2. COMPOSICAO pelo template (placeholder de produto) e CERCA DE SAIDA —
             `motivo_de_recusa_da_retomada`, as mesmas cercas das outras rotas com
             `start_aconteceu=False`. Texto barrado NAO sai: vai o placeholder de recusa, que nao
             repete nada da nota; o padrao vai para o log e o grupo para o contador, como em
             `_cercar_saida`.
          3. ENVIO por `mcp-whatsapp.send_message` (o `WhatsAppSender` injetado, cercado por
             `gate_whatsapp` na raiz). Falha de envio PROPAGA (`RetomadaEnvioFalhouError`) — ver
             o docstring da excecao.
          4. DESFECHO + TELEMETRIA: um `maezo_agent_desfecho_total` com rota `retomada` e
             desfecho proprio, gravado tambem no estado (como `respond`).

        Nao passa por `classify`, DMN, LLM nem start de processo: nao ha mensagem do beneficiario
        para classificar, e o texto e' do humano, nao do modelo.
        """
        reset: dict[str, Any] = dict(_HELENA_NEUTRAL_OUTPUTS)
        # A retomada nao e' mensagem da pessoa: o carimbo da ultima mensagem DELA e' preservado como esta',
        # sem renovar (renovar adiaria a reapresentacao por causa de uma mensagem do atendente).
        reset["ultima_mensagem_em"] = state.get("ultima_mensagem_em")
        if state.get("apresentacao_ja_feita") is True and _apresentacao_continua_valida(
            state.get("ultima_mensagem_em"), agora=datetime.now(UTC)
        ):
            reset["apresentacao_ja_feita"] = True
        # DL-0080: a conversa continua sendo a mesma depois do humano; o texto do atendente nao entra.
        if self._historico_enabled:
            reset["historico_conversa"] = historico_valido(
                state.get("historico_conversa"),
                ultima_mensagem_em=state.get("ultima_mensagem_em"),
                agora=datetime.now(UTC),
            )
        if self._memoria_clinica_enabled and "memoria_clinica" in state:
            reset["memoria_clinica"] = _memoria_clinica_valida(
                state["memoria_clinica"], agora=datetime.now(UTC)
            )
        reset["response_kind"] = RESPONSE_KIND_RETOMADA

        instrucoes = state.get("retomada_instrucoes")
        if (
            not state.get("tenant_id")
            or not state.get("conversation_id")
            or not isinstance(instrucoes, str)
            or not instrucoes.strip()
        ):
            # Defensivo: o consumidor ja' recusa nota vazia antes de invocar o grafo. Aqui nada
            # e' enviado — nao ha' o que retomar, e um texto generico fingiria uma instrucao.
            emit_turn_desfecho(
                state,
                agent_id="helena",
                desfecho=DESFECHO_RETOMADA_SEM_INSTRUCOES,
                route=RESPONSE_KIND_RETOMADA,
                motivo_categoria=None,
                enviada=False,
            )
            return {
                **reset,
                "error": ERRO_RETOMADA_SEM_INSTRUCOES,
                "desfecho": DESFECHO_RETOMADA_SEM_INSTRUCOES,
            }

        erro: str | None = None
        if len(instrucoes) > RETOMADA_MAX_INSTRUCOES:
            erro = ERRO_RETOMADA_INSTRUCOES_LONGAS
            texto = RETOMADA_RECUSADA_PLACEHOLDER
            logger.error(
                "helena_retomada_instrucoes_longas", tamanho=len(instrucoes), limite=RETOMADA_MAX_INSTRUCOES
            )
        else:
            texto = compor_mensagem_de_retomada(instrucoes)
            recusa = motivo_de_recusa_da_retomada(texto)
            if recusa is not None:
                grupo, padrao = recusa
                # Mesmo par de `_cercar_saida`: o PADRAO no log, o GRUPO no contador, o TEXTO em
                # nenhum dos dois (e' nota humana sobre um caso de saude).
                logger.error(
                    "helena_resposta_recusada",
                    node="resume",
                    grupo=grupo,
                    padrao=padrao,
                    response_kind=RESPONSE_KIND_RETOMADA,
                    recusa_version=RECUSA_DE_SAIDA_VERSION,
                )
                record_resposta_recusada(
                    agent_id="helena", motivo=grupo, response_kind=RESPONSE_KIND_RETOMADA
                )
                erro = ERRO_RESPOSTA_RECUSADA
                texto = RETOMADA_RECUSADA_PLACEHOLDER
        desfecho = DESFECHO_RETOMADA_ENVIADA if erro is None else DESFECHO_RETOMADA_RECUSADA

        try:
            await self._whatsapp.send(_to_hash_from_state(state), texto)
        except PROGRAMMING_ERRORS:
            raise
        # ESTREITO (nao `except Exception`): so' falha de DEPENDENCIA vira `RetomadaEnvioFalhouError`.
        # Qualquer outra excecao propaga como esta' — o consumidor tambem nao confirma o offset.
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            emit_turn_desfecho(
                state,
                agent_id="helena",
                desfecho=DESFECHO_RETOMADA_FALHA_ENVIO,
                route=RESPONSE_KIND_RETOMADA,
                motivo_categoria=None,
                enviada=False,
            )
            raise RetomadaEnvioFalhouError(f"retomada: envio falhou: {redact_error_message(exc)}") from exc
        emit_turn_desfecho(
            state,
            agent_id="helena",
            desfecho=desfecho,
            route=RESPONSE_KIND_RETOMADA,
            motivo_categoria=None,
            enviada=True,
        )
        saida: dict[str, Any] = {**reset, "response_text": texto, "desfecho": desfecho}
        if erro is not None:
            saida["error"] = erro
        return saida

    # -- Conditional routing ----------------------------------------------------------------

    @staticmethod
    def _entrada(state: HelenaState) -> str:
        """GAP-XHITL-4: qual porta abre este turno. `resume` SO' com a origem da retomada.

        A origem e' reescrita por TODO construtor de entrada (`new_helena_state`,
        `gate_inbound_state` — sempre `beneficiario`), entao um valor velho do checkpoint nao
        decide nada. Qualquer outro valor, inclusive ausente, e' um turno do beneficiario.
        """
        e_retomada = state.get("origem_do_turno") == ORIGEM_RETOMADA
        return "resume" if e_retomada else "receive"

    @staticmethod
    def _route(state: HelenaState) -> str:
        """HEL-03, camada 2 (backstop ESTRUTURAL): a aresta condicional nao entrega o turno ao no'
        `inform` sem a precondicao deterministica, mesmo que `next_kind` diga `inform`.

        Camada 1 (`_rota_informativa`, dentro de `classify`) e' quem produz o rotulo honesto —
        motivo, severidade e `error`. Esta aqui existe para o dia em que um `classify` futuro
        regredir: o turno cai em `escalate`, que deriva `motivo_categoria` do proprio estado
        (`falha_tecnica` quando ha `error`, `outro` caso contrario) e encaminha ao humano. Um
        backstop nao precisa nomear bem; precisa nao deixar passar."""
        kind = state.get("next_kind", "inform")
        if kind == "escalate":
            return "escalate"
        if kind == "schedule":
            return "schedule"
        if kind == "collect":
            # Backstop estrutural da coleta: so' pergunta quem tem um veredito de PERGUNTA da
            # tabela E ainda tem rodada. `next_kind="collect"` sem isso e' estado injustificado
            # -> humano, mesmo raciocinio do `inform`.
            rodadas = state.get("coleta_rodadas")
            if (
                state.get("coleta_veredito") in COLETA_VEREDITOS_PERGUNTA
                and type(rodadas) is int
                and 1 <= rodadas <= COLETA_MAX_RODADAS
                and not state.get("error")
            ):
                return "collect"
            logger.warning("helena_collect_recusado", node="_route")
            return "escalate"
        recusa = _inform_recusado(state)
        if recusa is not None:
            logger.warning("helena_inform_recusado", motivo=recusa, node="_route")
            return "escalate"
        return "inform"

    def _route_com_roteador(self, state: HelenaState) -> str:
        """Onda (e), camada 2 (backstop ESTRUTURAL de `_rota_de_cobranca`) — a aresta do grafo SO'
        com o roteador ligado; desligado, a aresta e' `_route`, intocada.

        A aresta nao entrega o turno ao Lucas sem a precondicao: recusado aqui, cai em `escalate`
        (humano), nunca em `inform`. Todo outro `next_kind` segue a regra de `_route`.
        """
        if state.get("next_kind") != RESPONSE_KIND_HANDOFF:
            return HelenaGraph._route(state)
        recusa = _handoff_recusado(state, roteador_ligado=self._roteador_lucas_enabled)
        if recusa is not None:
            logger.warning("helena_handoff_recusado", motivo=recusa, node="_route")
            return "escalate"
        # Literal, como os destinos de `_route`: e' o NOME de uma aresta, nao texto ao beneficiario.
        return "handoff"

    def _route_com_consultas(self, state: HelenaState) -> str:
        """CONSULTA DO PLANO, camada 2 (backstop ESTRUTURAL de `_rota_de_consulta`): a aresta so'
        com as consultas ligadas. Recusado aqui, cai em `escalate` (humano). Todo outro `next_kind`
        segue a aresta de antes (com ou sem o roteador do Lucas)."""
        if state.get("next_kind") != NEXT_KIND_CONSULTA_PLANO:
            if self._roteador_lucas_enabled:
                return self._route_com_roteador(state)
            return HelenaGraph._route(state)
        recusa = self._consulta_recusada(state)
        if recusa is not None:
            logger.warning("helena_consulta_recusada", motivo=recusa, node="_route")
            return "escalate"
        # Literal, como os destinos de `_route`: e' o NOME de uma aresta, nao texto ao beneficiario.
        return "consulta_plano"

    # -- DMN (ADR-0012/ADR-0028): the DMN decides red flag, never the LLM -------------------

    async def _evaluate_dmn(
        self,
        extraction: dict[str, Any],
        *,
        force_population: str | None = None,
    ) -> dict[str, Any]:
        population = force_population or extraction.get("population") or "adult"
        table = _DMN_BY_POPULATION.get(population, "triage_redflag_adult")

        dmn_input: dict[str, Any] = {
            "sintoma_codigo": extraction.get("sintoma_codigo"),
            "intensidade": extraction.get("intensidade", "desconhecida"),
        }
        if table == "triage_redflag_adult":
            dmn_input["idade_anos"] = extraction.get("idade_anos")
        elif table == "triage_redflag_pediatric":
            dmn_input["idade_meses"] = extraction.get("idade_meses")
        elif table == "triage_redflag_gestante":
            dmn_input["idade_gestacional_semanas"] = extraction.get("idade_gestacional_semanas")
        elif table == "triage_redflag_mental_health":
            risco = extraction.get("risco_imediato")
            # Conservative posture: immediate risk UNLESS the LLM says explicitly false. This
            # matches the DMN's fail-safe stance and the classify prompt's instruction, and fixes
            # a wrong coercion: the prior `bool(risco)` read the string `"false"` as `True`
            # (harmless here — it fails safe) but also read `""`/`0`/`[]` as `False` (fail-OPEN).
            # `_is_explicitly_false` yields True only for native `False`/`"false"`; everything
            # else (None, "true", unparseable) stays immediate-risk.
            dmn_input["risco_imediato"] = not _is_explicitly_false(risco)

        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            # HEL-05 (feeder): the DMN-down path routes to escalate `falha_tecnica` in THIS turn,
            # so this message reaches `resumo_contexto` directly (`table` is a class token).
            return {
                "dmn_table": table,
                "dmn_decision": {},
                "error": dmn_unavailable_error(table, exc),
            }

        # Provenance reference: `{table}#{decision-definition id}` — the engine's evaluate
        # response does not return which RULE fired (no `ruleId` in the Camunda 7 REST evaluate
        # contract), so this cites the authoritative deployed decision-definition id/version
        # (ADR-0028 §2) rather than fabricating a rule id the engine never gave us.
        ref = f"{table}#{version.id}"
        return {"dmn_table": table, "dmn_decision": row, "dmn_decision_ref": ref}

    # -- LLM helpers (all PHI-tagged — ADR-0006/ADR-0017/T1.7) ------------------------------

    async def _classify_llm(self, state: HelenaState) -> tuple[dict[str, Any] | None, str | None]:
        """Run the classify LLM call. Returns `(extraction, None)` on success or
        `(None, failure_reason)` on ANY failure — exception, unparseable JSON, or
        schema-invalid JSON per `_validate_extraction`.

        FAIL-CLOSED (R1 cycle-1 blocking fix): a failure here must NEVER be read as a benign
        `intent="information"` turn — the pre-fix behavior defaulted to exactly that, silently
        routing an unclassifiable (possibly clinical) message to `inform` (fail-OPEN,
        live-reproduced by the verifier with "não consigo respirar" + malformed JSON). The
        caller (`classify`) routes any failure to escalation `falha_tecnica` — the same
        SP-OP-ESCALATION-001 technical-failure trigger the DMN-down path already uses, restoring
        symmetry with every other failure path in this graph.

        The failure reason is short and bounded and names the failure CLASS only (for schema
        violations: `_validate_extraction`'s class token, e.g. `non_allowlisted_sintoma_codigo`)
        — never a field value, never the raw LLM output, never the beneficiary's message text
        (R1 cycle-2 fix: the cycle-1 version echoed the offending field value, which flowed
        into engine process variables via `resumo_contexto` — a live-proven PHI leak vector
        when the LLM copies a beneficiary-typed identifier into a field).
        """
        # HEL-06: a mensagem crua do WhatsApp e' conteudo de TERCEIRO. Ela viaja demarcada
        # (`render_untrusted_block`), no FIM do prompt — o bloco e' a parte variavel por
        # requisicao, e por-lo depois do texto estatico preserva o prefixo cacheavel que
        # `prompt_format` documenta.
        # COLETA: quando ha' pergunta em aberto, a(s) mensagem(ns) anterior(es) viajam JUNTO com a
        # atual, no MESMO bloco nao confiavel — "forte" so' significa algo ao lado de "dor de
        # cabeca". Sem pergunta em aberto o prompt e' byte-a-byte o de antes.
        corpo = state.get("message_body", "")
        if self._coleta_enabled and state.get("coleta_pendente") and state.get("coleta_contexto"):
            anteriores = state.get("coleta_contexto")
            corpo = f"[mensagens anteriores desta conversa] {anteriores}\n[mensagem atual] {corpo}"
        # NUMERO UNICO (onda e): `classify-v6` SO' com o roteador ligado; desligado, o texto e' o
        # `classify-v5` byte a byte (sha256 fixado em teste).
        instrucoes = classify_prompt(roteador_lucas=self._roteador_lucas_enabled)
        if self._consultas is not None:
            # CONSULTA DO PLANO: o adendo so' existe com as consultas ligadas; desligado, o texto e' o
            # de antes byte a byte.
            instrucoes = f"{instrucoes}{ADENDO_CLASSIFY_CONSULTAS}"
        # HISTORICO CURTO (DL-0080): com a flag ligada e historico a mostrar, o adendo vai depois das
        # instrucoes e o historico viaja num bloco NAO CONFIAVEL proprio, antes da mensagem atual. Ele
        # so' alimenta a EXTRACAO (anafora, quadro acumulado); a decisao continua na DMN. Sem
        # historico o prompt e' o de antes byte a byte.
        historico = state.get("historico_conversa") if self._historico_enabled else None
        if historico:
            prompt = (
                f"{instrucoes}\n\n{classify_historico_adendo()}\n\n"
                f"{render_untrusted_block('historico_conversa', historico_em_texto(historico))}\n"
                f"{render_untrusted_block('message_body', corpo)}"
            )
        else:
            prompt = f"{instrucoes}\n\n{render_untrusted_block('message_body', corpo)}"
        falha: str | None = None
        for tentativa in range(1, CLASSIFY_TENTATIVAS + 1):
            try:
                raw = await self._llm.generate(
                    prompt,
                    phi=True,
                    agent_id="helena",
                    tenant_id=state.get("tenant_id", ""),
                    # ADR-0009 §2 / CC-12: extracao estruturada (JSON) -> task_default.
                    task_kind="task_default",
                )
            except PROGRAMMING_ERRORS:
                raise
            except EXTERNAL_DEPENDENCY_FAILURES as exc:  # classified into a failure reason, never swallowed.
                # HEL-05 (feeder): `str(exc)[:200]` was a LENGTH bound, never a CONTENT one, and this
                # string becomes `state["error"]` (`classify`) which `_start_escalation` appends to
                # `resumo_contexto` as `[falha tecnica: ...]` — i.e. straight into engine process
                # variables, IN THE SAME TURN. The exception comes from the inference provider, whose
                # message may echo the request (which carries the beneficiary's own message body and
                # any identifier typed into it). `redact_error_message` bounds BOTH, and keeps the
                # `{ClassName}: ` prefix this f-string used to build by hand.
                return None, f"classify LLM call failed: {redact_error_message(exc)}"
            data, falha = self._avaliar_extracao(raw)
            if data is not None:
                return data, None
            if tentativa < CLASSIFY_TENTATIVAS:
                # So' o NOME da classe da falha vai ao log: nunca o valor do campo nem o texto do modelo.
                logger.warning(
                    "helena_classify_repetido",
                    tenant_id=state.get("tenant_id", ""),
                    tentativa=tentativa,
                    falha=falha,
                )
        return None, falha

    def _avaliar_extracao(self, raw: str) -> tuple[dict[str, Any] | None, str | None]:
        """A resposta do classificador: `(extracao, None)` ou `(None, motivo_da_falha)`."""
        data = _parse_json_object(raw)
        if data is None:
            return None, "classify LLM returned unparseable JSON"
        schema_failure = _validate_extraction(
            data,
            cobranca_habilitada=self._roteador_lucas_enabled,
            consultas_habilitadas=self._consultas is not None,
        )
        if schema_failure is not None:
            return None, f"classify LLM returned schema-invalid JSON: {schema_failure}"
        return data, None

    async def _respond_llm(self, state: HelenaState, response_kind: ResponseKind) -> str:
        """O RASCUNHO JA CERCADO — o caminho de todo no' que nao precisa ver o texto barrado.

        SEPARADO EM DOIS EM 22/09/2026 (CRITICO 1), e a razao e' a mesma que `collect` ja' tinha:
        quem precisa DECIDIR o que fazer com uma recusa precisa do texto recusado para perguntar a
        ele qual foi o grupo — e o texto nao pode vir pela excecao (`RespostaRecusadaError.grupo` e'
        atributo de excecao ligada, que a cerca LUC-06/NEW-01 recusa em campo de estado). `inform`
        passou a chamar `_redigir_resposta` + `_cercar_saida`, exatamente como `collect`.
        """
        return self._cercar_saida(await self._redigir_resposta(state, response_kind), response_kind)

    async def _redigir_resposta(self, state: HelenaState, response_kind: ResponseKind) -> str:
        """O rascunho do modelo, SEM cerca. O unico chamador que o usa cru e' quem vai julgar a
        recusa por grupo (`inform`); todos os outros entram por `_respond_llm`."""
        # O `motivo` DA TABELA NAO VAI MAIS AO MODELO (01/10/2026). Ele e' texto de engenharia
        # escrito para o atendente ("Sem criterio de red flag adulto", "Dor toracica: possivel
        # sindrome coronariana aguda", "suspeita de AVC"), e o modelo o repetia ao beneficiario:
        # "nao ha sinais de alerta" (que a cerca de negativa clinica barra e que, em `inform`,
        # virava P3 `falha_tecnica` por sorteio) e "possivel sindrome coronariana aguda" (diagnostico).
        # O motivo continua na auditoria e no resumo do atendente (`_resumo_contexto`); o que o
        # modelo precisa para redigir esta' na mensagem da pessoa e em `response_kind`.
        context: dict[str, Any] = {
            "response_kind": response_kind,
            "escalation_severidade": state.get("escalation_severidade"),
        }
        # F6: o prompt proibe repetir o cartao quando isto vem no contexto (ver `response_prompt`).
        # SO' QUANDO TRUE (21/09/2026, segunda rodada), como as chaves irmas `population` e
        # `memoria_a_confirmar`: o prompt inteiro e' escrito no idioma "QUANDO O CONTEXTO TROUXER
        # X", e mandar `apresentacao_ja_feita: False` pede ao modelo que interprete uma negacao
        # explicita num texto que so' fala de presenca. Um `False` presente e' ruido no unico lugar
        # em que a ausencia ja' era a informacao.
        if state.get("apresentacao_ja_feita") is True:
            context["apresentacao_ja_feita"] = True
        # MEMORIA CLINICA (Frente 2.1, decisao 5 do documento): a populacao vale TAMBEM para o
        # texto, nao so' para a tabela. O defeito medido em 13/09 nao foi apenas triar um bebe pela
        # tabela de adulto — foi a Helena listar sinais de alerta de ADULTO para a mae de um bebe
        # de 11 meses, e depois perguntar "descreva melhor como VOCE esta se sentindo" a quem nao
        # era o paciente. O erro de populacao muda a ORIENTACAO que a pessoa recebe.
        populacao = state.get("population")
        if populacao and populacao != "none":
            context["population"] = populacao
        # A frase de confirmacao so' entra quando `classify` decidiu que ha' o que confirmar. Ela
        # vai PRONTA, e nao como um sinalizador para o modelo redigir: o que se confirma e' um dado
        # clinico, e deixar o modelo formular significaria deixa-lo escolher qual dado.
        a_confirmar = state.get("memoria_a_confirmar")
        if a_confirmar:
            context["memoria_a_confirmar"] = a_confirmar
        # HEL-06: mesma fronteira do `_classify_llm`. Aqui o poder da injecao seria sobre o
        # TEXTO enviado ao beneficiario (a rota ja esta decidida), o que nao a torna menos
        # necessaria: a resposta e' a unica coisa que a pessoa do outro lado le'.
        prompt = (
            f"{response_prompt()}\n\ncontexto={context}\n"
            f"{render_untrusted_block('message_body', state.get('message_body', ''))}"
        )
        # HISTORICO CURTO (DL-0080): SO' na redacao do `inform` — responder em continuidade. As rotas de
        # escalonamento e os textos fixos (DL-0061/0062) nao mudam. Sem historico, o prompt de antes.
        historico = state.get("historico_conversa") if self._historico_enabled else None
        if historico and response_kind == "inform":
            prompt = (
                f"{response_prompt()}\n\n{response_historico_adendo()}\n\ncontexto={context}\n"
                f"{render_untrusted_block('historico_conversa', historico_em_texto(historico))}\n"
                f"{render_untrusted_block('message_body', state.get('message_body', ''))}"
            )
        try:
            texto = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: fraseia fatos ja decididos (DMN motivo/severidade) -> task_default.
                task_kind="task_default",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            # fail-safe: nunca deixar o beneficiario sem nada — e nunca com uma PROMESSA. Ate
            # 21/09/2026 este ramo tinha um `return` proprio, ANTES do bloco de cerca, com o texto
            # "um profissional humano vai continuar o atendimento em breve": a unica frase deste
            # modulo que prometia um humano sem passar por cerca nenhuma. Agora o fallback e' a
            # constante honesta e ele SEGUE PELO MESMO CAMINHO do rascunho do modelo — se um dia
            # ela violar uma das cercas, e' recusada, logada e contada como qualquer outro texto,
            # em vez de sair pela porta de tras.
            texto = RESPOSTA_FALHA_DE_REDACAO

        # RECUSA DE SAIDA (13/09/2026). O RASCUNHO TERMINA AQUI, E A CERCA VEM LOGO DEPOIS:
        # `_respond_llm` (o caminho de `_start_escalation` — escalate e schedule) e `inform` e
        # `collect`, que chamam `_cercar_saida` a mao para poder julgar a recusa por GRUPO. Todo
        # texto que chega ao beneficiario passa por `_cercar_saida`; o que mudou em 22/09/2026 foi
        # QUEM chama, nunca SE chama.
        #
        # POR QUE A CERCA EXISTE MESMO COM A PROIBICAO NO PROMPT. O `response-v3` proibiu a
        # negativa clinica em maiusculas e com o raciocinio inteiro, e o modelo passou por cima
        # DUAS VEZES na mesma conversa (medido 13/09/2026). Toda esta agente e' feita de travas —
        # a DMN decide em vez do modelo, a negativa so' nasce de User Task, o provedor recusa
        # construir sem atestacao — e o texto, que e' a unica coisa que a pessoa do outro lado le',
        # nao tinha nenhuma. Pedir e' instrucao; isto e' cerca.
        # F7 (21/09/2026): a cerca de CANAL vem antes e vale em toda rota — um canal que nao existe
        # nao e' verdade em rota nenhuma. As duas devolvem a mesma forma `(grupo, padrao)`, entao o
        # log, o contador e a excecao servem as duas sem ramo novo.
        return texto

    @staticmethod
    def _cercar_saida(texto: str, response_kind: ResponseKind, *, node: str = "_respond_llm") -> str:
        """As duas cercas de saida sobre UM texto, ou `RespostaRecusadaError`.

        EXTRAIDA DE `_respond_llm` EM 21/09/2026 (segunda rodada) por uma razao medida: aquele
        docstring afirmava ser "o unico ponto por onde passa todo texto que chega ao
        beneficiario", e a enumeracao nao tinha `collect`. O no' `collect` monta prompt proprio,
        chama `generate` direto e devolvia `response_text` sem passar por cerca nenhuma — e em
        `respond` a unica verificacao do turno e' a TEXTO x FATO, que nao consulta a cerca de
        canal. Resultado: na rota de coleta o canal inventado chegava ao beneficiario. Com a cerca
        num metodo, a afirmacao do docstring passa a ser verdade para as duas rotas.

        `node` e' explicito porque o log passou a ter DOIS chamadores: um campo que diz
        `_respond_llm` num texto barrado dentro do no' `collect` manda quem depura para o lugar
        errado, e o custo de um log que mente e' exatamente o tempo de quem esta de plantao.
        """
        recusa = motivo_de_canal_nao_confirmado(texto) or motivo_de_recusa(texto, response_kind)
        if recusa is None:
            return texto
        grupo, padrao = recusa
        # O PADRAO no log (onde alguem depura), o GRUPO no contador (onde alguem conta). O
        # TEXTO nao vai para nenhum dos dois: e' saida de modelo sobre a mensagem do
        # beneficiario, e um log nao e' lugar de ampliar o alcance dela.
        logger.error(
            "helena_resposta_recusada",
            node=node,
            grupo=grupo,
            padrao=padrao,
            response_kind=response_kind,
            recusa_version=RECUSA_DE_SAIDA_VERSION,
            prompt_version=RESPONSE_PROMPT_VERSION,
        )
        record_resposta_recusada(agent_id="helena", motivo=grupo, response_kind=response_kind)
        raise RespostaRecusadaError(grupo, padrao, response_kind)

    async def _resumo_contexto(self, state: HelenaState, motivo: MotivoCategoria | None) -> str:
        # Token somente de exibicao; motivo_categoria continua None na ausencia.
        motivo_label = motivo or "nao_classificado"
        # HEL-06: este resumo e' lido por um ATENDENTE HUMANO e aterra em variaveis de processo
        # (Zona Geral) depois do scrub de identificadores de `_start_escalation`. Uma injecao que
        # sequestrasse este prompt escreveria no handoff o que quisesse sobre o proprio caso — por
        # isso a mensagem vem demarcada aqui tambem. O scrub de PHI continua sendo do EGRESSO
        # (`redact_free_text`, em `_start_escalation`), nao deste bloco: a chamada e' `phi=True`,
        # ou seja, o texto nao sai de zona aqui.
        prompt = (
            "Resuma em 1-2 frases, em portugues, o contexto desta conversa para um atendente "
            "humano assumir. NAO inclua dado identificavel. NAO de conduta clinica. Apenas o "
            f"essencial do caso e o motivo do encaminhamento.\nmotivo={motivo_label}\n"
            f"{render_untrusted_block('message_body', state.get('message_body', ''))}"
        )
        try:
            text = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=state.get("tenant_id", ""),
                # ADR-0009 §2 / CC-12: o humano le isto antes de assumir o caso -> reasoning
                # (mesma logica do dossie de escalacao do lucas).
                task_kind="reasoning",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:  # fail-safe: never block the escalation on a summary.
            text = ""
        return text or f"Encaminhamento automatico ({motivo_label})."

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[HelenaState]:
        g: StateGraph[HelenaState] = StateGraph(HelenaState)
        g.add_node("receive", self.receive)
        g.add_node("classify", self.classify)
        g.add_node("inform", self.inform)
        g.add_node("schedule", self.schedule)
        g.add_node("escalate", self.escalate)
        g.add_node("collect", self.collect)
        g.add_node("respond", self.respond)
        g.add_node("resume", self.resume)
        destinos: dict[Hashable, str] = {
            "inform": "inform",
            "schedule": "schedule",
            "escalate": "escalate",
            "collect": "collect",
        }
        if self._roteador_lucas_enabled:
            # Onda (e): o no' de passagem so' EXISTE com o roteador ligado — desligado, o grafo
            # compilado e' o de antes, no a no'.
            g.add_node("handoff_cobranca", self.handoff_cobranca)
            g.add_edge("handoff_cobranca", "respond")
            destinos[RESPONSE_KIND_HANDOFF] = "handoff_cobranca"
        if self._consultas is not None:
            # CONSULTA DO PLANO: o no' so' EXISTE com as consultas ligadas (mesmo desenho do Lucas).
            g.add_node("consultar_plano", self.consultar_plano)
            g.add_edge("consultar_plano", "respond")
            destinos[NEXT_KIND_CONSULTA_PLANO] = "consultar_plano"

        g.add_conditional_edges(START, self._entrada, {"receive": "receive", "resume": "resume"})
        g.add_edge("receive", "classify")
        aresta = self._route_com_roteador if self._roteador_lucas_enabled else self._route
        if self._consultas is not None:
            aresta = self._route_com_consultas
        g.add_conditional_edges("classify", aresta, destinos)
        g.add_edge("inform", "respond")
        g.add_edge("collect", "respond")
        g.add_edge("schedule", "respond")
        g.add_edge("escalate", "respond")
        g.add_edge("respond", END)
        g.add_edge("resume", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[HelenaState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain:
      - `inference`: an `InferenceProvider` (ADR-0009).
      - `dmn`: a `DmnTransport` (ADR-0028/T1.5).
      - `cibseven`: a `CibSevenTransport` (ADR-0001/T1.11).
      - `whatsapp`: a `WhatsAppSender`.
    Optional:
      - `agent_version`: audit provenance string (ADR-0007), defaults to `"helena@v0"`.

    Fail-closed: missing a required dependency raises `ValueError` at build time — Helena never
    silently constructs a graph that would crash mid-conversation on its first tool call.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    dmn = cfg.get("dmn")
    cibseven = cfg.get("cibseven")
    whatsapp = cfg.get("whatsapp")
    audit_sink = cfg.get("audit_sink")
    missing = [
        name
        for name, value in (
            ("inference", inference),
            ("dmn", dmn),
            ("cibseven", cibseven),
            ("whatsapp", whatsapp),
            ("audit_sink", audit_sink),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"Helena build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/whatsapp/audit_sink must all be "
            "injected; audit_sink is the T-C2 fence — no escalation start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "helena@v0"))
    # COLETA: `True` SOMENTE quando a composicao (dispatcher) o disser explicitamente. Nao ha'
    # leitura de env aqui de proposito — ligar coleta e' decisao de quem monta o runtime, com a
    # tabela ratificada no motor, nao uma variavel que aparece num container.
    coleta_enabled = cfg.get("coleta_enabled", False) is True
    # MEMORIA CLINICA: ligada salvo desligamento EXPLICITO (`is not False`), ao contrario da
    # coleta. Uma composicao que nao diz nada sobre memoria recebe a Helena que lembra quem e' o
    # paciente — ver a justificativa do default no construtor.
    memoria_clinica_enabled = cfg.get("memoria_clinica_enabled", True) is not False
    # NUMERO UNICO (onda e): como a coleta, `True` SO' quando a composicao disser explicitamente.
    roteador_lucas_enabled = cfg.get("roteador_lucas_enabled", False) is True
    # DL-0078: o nome da operadora nos textos de identidade; ausente = `NOME_OPERADORA_PADRAO`.
    nome_operadora = cfg.get("identidade_nome_operadora")
    # DL-0080: como a coleta, `True` SO' quando a composicao disser explicitamente.
    historico_enabled = cfg.get("historico_enabled", False) is True
    # CONSULTA DO PLANO: a fonte de fatos SO' quando a composicao a passar; ausente = desligada.
    consultas = cfg.get("consultas_plano")
    return HelenaGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        whatsapp=cast(WhatsAppSender, whatsapp),
        agent_version=agent_version,
        coleta_enabled=coleta_enabled,
        memoria_clinica_enabled=memoria_clinica_enabled,
        roteador_lucas_enabled=roteador_lucas_enabled,
        nome_operadora=nome_operadora if isinstance(nome_operadora, str) else NOME_OPERADORA_PADRAO,
        historico_enabled=historico_enabled,
        consultas_plano=consultas if isinstance(consultas, FonteDeFatosDoPlano) else None,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "classify": CLASSIFY_PROMPT_VERSION,
    # Onda (e) do numero unico: o classify usado SO' com `MAEZO_ROTEADOR_LUCAS` ligado.
    "classify_roteador": CLASSIFY_PROMPT_VERSION_ROTEADOR,
    "response": RESPONSE_PROMPT_VERSION,
    "coleta": COLETA_PROMPT_VERSION,
    # A lista de recusa nao e' um prompt, mas e' um artefato VERSIONADO que decide o que chega ao
    # beneficiario — exatamente o que este mapa existe para tornar auditavel. Deixa-la de fora
    # significaria um texto barrado sem registro de QUAL cerca o barrou.
    "recusa_de_saida": RECUSA_DE_SAIDA_VERSION,
    # DL-0080: os adendos do historico curto, usados SO' com `MAEZO_HELENA_HISTORICO` ligada.
    "classify_historico": CLASSIFY_HISTORICO_VERSION,
    "response_historico": RESPONSE_HISTORICO_VERSION,
    # CONSULTA DO PLANO (DL de 07/10/2026): o adendo do classify e a redacao com fatos, SO' com
    # `MAEZO_HELENA_CONSULTAS_AMH` ligada.
    "classify_consultas": CLASSIFY_CONSULTAS_ADENDO_VERSION,
    "consulta_plano": CONSULTA_PLANO_PROMPT_VERSION,
}
