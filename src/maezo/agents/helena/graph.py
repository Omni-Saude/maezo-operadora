"""Helena Moreira — Health Navigator Agent (Phase 0, AGJ-HELENA-TRIAGE, T1.11/defect B6).

Journey map (mirrors the v1 donor's `AGJ-HELENA-TRIAGE`, READ-ONLY reference
`Maezo-Healthcare-Plan src/maezo/agents/helena/graph.py`, adapted to v2's flatter seam set —
v2 has no `ToolRegistry`/PEP-gateway wiring for agent tool calls yet, so this graph's nodes call
the seams already on `main` DIRECTLY: `DmnTransport.evaluate` (ADR-0028/T1.5), `InferenceProvider.
generate(phi=True)` (ADR-0009/T1.7), and the new `CibSevenTransport` (ADR-0001, T1.11 —
`tools/mcp_cibseven/transport.py`)):

    receive -> classify -> {inform | schedule | escalate} -> respond

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
route is already decided) it degrades to a safe canned text. Neither path ever silently
downgrades to a general-zone provider.

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

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.platform.observability import record_resposta_recusada
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

from .prompts import (
    ALLOWED_SINTOMA_CODIGOS,
    CLASSIFY_PROMPT_VERSION,
    COLETA_PROMPT_VERSION,
    RECUSA_DE_SAIDA_VERSION,
    RECUSA_ESCALONAMENTO_JA_ABERTO,
    RECUSA_HANDOFF_SEM_MENCAO,
    RESPONSE_PROMPT_VERSION,
    SYSTEM_PROMPT_VERSION,
    classify_prompt,
    coleta_prompt,
    menciona_encaminhamento,
    motivo_de_recusa,
    response_prompt,
)

logger = structlog.get_logger(__name__)

# --- Domain enums (mirror the SP-OP-ESCALATION-001 contract + DMN schema) -------------------

Intent = Literal["symptom", "scheduling", "information", "human_request", "clinical_question", "greeting"]
Population = Literal["adult", "pediatric", "gestante", "mental_health", "none"]
#: `collect` (COLETA, 09/09/2026): o turno termina numa PERGUNTA ao beneficiario, nao numa
#: resposta — a mensagem descreveu um sintoma, a tabela de red flag NAO acusou bandeira, e a
#: tabela de suficiencia (`SUFFICIENCY_DMN_KEY`) disse que faltava um dado para decidir com
#: honestidade. Nunca substitui um escalonamento: `classify` so' chega a ele DEPOIS de a DMN de
#: red flag ter dito `false` — uma emergencia nao espera pergunta.
ResponseKind = Literal["inform", "schedule", "escalate", "collect"]
#: CC-01: o vocabulario do `response_kind` EMITIDO e um superconjunto do de ROTEAMENTO. Helena
#: pode responder um `falha_tecnica_start` (a resposta honesta quando a escalacao nao abriu), mas
#: nunca ROTEIA para ele — `next_kind` continua sendo `ResponseKind`, com os tres destinos que
#: `_route` sabe mapear. Alargar o tipo de roteamento aqui criaria um valor que nenhuma aresta
#: conhece; alargar so o de saida nao cria destino nenhum.
ResponseKindOut = Literal["inform", "schedule", "escalate", "collect", "falha_tecnica_start"]

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
    {"symptom", "scheduling", "information", "human_request", "clinical_question", "greeting"}
)
_VALID_POPULATIONS: frozenset[str] = frozenset({"adult", "pediatric", "gestante", "mental_health", "none"})
_VALID_INTENSIDADES: frozenset[str] = frozenset({"leve", "moderada", "grave", "desconhecida"})

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


class WhatsAppSender(Protocol):
    """Outbound WhatsApp send seam. Operates on a phone HASH, never a raw number — Helena's
    state is pseudonymized end to end (ADR-0006); resolving the hash back to a real number for
    actual delivery is the DISPATCH layer's job (it has the raw number in-hand for the inbound
    turn it is currently handling), never this graph's."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]: ...


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

    # Turn output.
    response_text: str
    response_kind: ResponseKindOut
    #: CC-01/NEW-10: desfecho do turno. Escrito exclusivamente por `respond`, nos DOIS ramos:
    #: `erro_inicio_processo` no ramo de falha de start (via `notify_start_failure`) e
    #: `escalado_humano`/`resolvido_automatico` no ramo de sucesso (mesmo valor rotulado na
    #: telemetria CC-09, gravado tambem aqui desde NEW-10 — antes ficava "" ate a proxima falha).
    desfecho: str
    error: str


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
    {"tenant_id", "conversation_id", "canal", "beneficiario_pseudo_id", "message_body"}
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
    "response_text": None,
    "response_kind": None,
    "desfecho": "",
    "error": None,
}

_HELENA_ALL_FIELDS = HELENA_INPUT_FIELDS | frozenset(_HELENA_NEUTRAL_OUTPUTS)

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
_HELENA_MEMORIA_DE_CONVERSA: frozenset[str] = frozenset(
    {"coleta_rodadas", "coleta_pendente", "coleta_contexto", "memoria_clinica"}
)

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
        if populacao not in _VALID_POPULATIONS:
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
            if intensidade_avaliada not in _VALID_INTENSIDADES:
                return None
            limpa[_MEMORIA_INTENSIDADE_AVALIADA] = intensidade_avaliada
    limpa[_MEMORIA_GRAVADA_EM] = gravado.isoformat()
    limpa[_MEMORIA_CONFIRMADA] = bruta.get(_MEMORIA_CONFIRMADA) is True
    return limpa


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
    for campo in _POPULACAO_DA_IDADE:
        lembrado = memoria.get(campo)
        if lembrado is None or fundida.get(campo) is not None:
            continue
        if populacao_final in _POPULACOES_COM_IDADE and _POPULACAO_DA_IDADE[campo] != populacao_final:
            continue
        fundida[campo] = lembrado
        veio_da_memoria[campo] = lembrado
    return fundida, veio_da_memoria


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
        intensidade if intensidade in _VALID_INTENSIDADES else "desconhecida"
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
        "intensidade": intensidade if intensidade in _VALID_INTENSIDADES else "desconhecida",
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
    """
    if lembrados.get("idade_meses") is not None:
        meses = int(lembrados["idade_meses"])
        quem = f"sua crianca de {meses // 12} anos" if meses > 24 else f"seu bebe de {meses} meses"
    elif lembrados.get("idade_anos") is not None:
        quem = f"a pessoa de {lembrados['idade_anos']} anos"
    elif lembrados.get("idade_gestacional_semanas") is not None:
        quem = f"a gestacao de {lembrados['idade_gestacional_semanas']} semanas"
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
) -> HelenaState:
    """Typed input-boundary constructor for a fresh Helena turn (T1.11).

    This is the production construction seam's ONLY sanctioned way to build a `HelenaState`: its
    explicit keyword-only signature makes it STRUCTURALLY impossible to pass an output-only key
    through it (a forged `next_kind`/`error`/`escalation_*`/`dmn_decision_ref`). Every accepted
    argument is an `HELENA_INPUT_FIELDS` member.
    """
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "canal": canal,
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "message_body": message_body,
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
    return cast(HelenaState, {k: raw[k] for k in HELENA_INPUT_FIELDS if k in raw})


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
_INTENTS_ADMISSIVEIS_INFORM: frozenset[str] = frozenset({"information", "symptom", "greeting"})


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


def _validate_extraction(data: dict[str, Any]) -> str | None:
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
    """
    if data.get("intent") not in _VALID_INTENTS:
        return "invalid_intent"
    if data.get("population") not in _VALID_POPULATIONS:
        return "invalid_population"
    if not isinstance(data.get("psychosocial_risk"), bool):
        return "missing_or_invalid_psychosocial_risk"
    codigo = data.get("sintoma_codigo")
    if codigo is not None and codigo not in ALLOWED_SINTOMA_CODIGOS:
        return "non_allowlisted_sintoma_codigo"
    intensidade = data.get("intensidade")
    if intensidade is not None and intensidade not in _VALID_INTENSIDADES:
        return "invalid_intensidade"
    for field in _AGE_FIELDS:
        ok, coerced = _coerce_age(data.get(field))
        if not ok:
            return "invalid_age"
        # Coerce in place (e.g. "5" -> 5) so `_evaluate_dmn` reads the same dict and hands the
        # DMN a correctly typed integer. Absent fields (coerced None) are left as-is.
        if coerced is not None:
            data[field] = coerced
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
    ) -> None:
        self._llm = inference
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
            for chave in _HELENA_MEMORIA_DE_CONVERSA - {"memoria_clinica"}:
                if chave in state:
                    reset[chave] = state[chave]  # type: ignore[literal-required]
            if "coleta_rodadas" in state:
                reset["coleta_rodadas"] = _rodadas_de_coleta(state["coleta_rodadas"])
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

        # MOSTRAR ANTES DE USAR (decisao 4 do documento), e as tres condicoes sao todas
        # necessarias:
        #   1. algum dado veio da MEMORIA, nao da mensagem — confirmar o que a pessoa acabou de
        #      dizer seria papagaio, nao transparencia;
        #   2. o turno e' CLINICO (`symptom` ou risco psicossocial) — e' onde o dado lembrado muda
        #      a tabela e a orientacao; num turno administrativo ele nao decide nada;
        #   3. a confirmacao ainda nao foi feita NESTA conversa — uma vez, nao a cada mensagem,
        #      senao vira ruido e a pessoa para de ler.
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
        update["memoria_clinica"] = _memoria_a_gravar(
            extraction,
            memoria,
            agora=datetime.now(UTC),
            confirmada=ja_confirmada or confirmar_agora,
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

        # Gatilho 5 (always evaluated, highest priority): psychosocial risk in ANY message.
        if psychosocial:
            dmn_out = await self._evaluate_dmn(extraction, force_population="mental_health")
            update.update(dmn_out)
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "risco_psicossocial"
            update["escalation_severidade"] = "grave"
            return update

        # Gatilho 2: clinical question (L0 hard — Helena never answers one herself).
        if intent == "clinical_question":
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "intencao_clinica"
            update["escalation_severidade"] = "moderada"
            return update

        # Gatilho 3: explicit request for a human.
        if intent == "human_request":
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
        if intent == "greeting":
            return _rota_informativa(update)

        # Symptom: ALWAYS goes through the DMN — the DMN decides red flag, never the LLM.
        if intent == "symptom":
            dmn_out = await self._evaluate_dmn(extraction)
            update.update(dmn_out)
            if dmn_out.get("error"):
                # Gatilho 4: DMN unavailable/no-result is a technical failure -> human, NEVER
                # treated as "no red flag" (fail-safe, ADR-0028 §3).
                update["next_kind"] = "escalate"
                update["escalation_motivo"] = "falha_tecnica"
                # HEL-04: AQUI ha' classificacao — o sintoma foi extraido e validado antes de a
                # DMN cair —, entao a severidade DERIVA dela em vez de ser um literal.
                update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
                return update
            # F5: a avaliacao ACONTECEU — so' AQUI, depois de a tabela responder, "houve avaliacao"
            # e' fato e nao intencao. A conversa passa a lembrar QUAL dado ela usou, que e' a unica
            # informacao que permite refaze-la se a pessoa corrigir esse dado
            # (`_correcao_de_dado_avaliado`). NAO e' lembrar o sintoma da mensagem anterior — ver a
            # nota em `_MEMORIA_SINTOMA_AVALIADO` para a diferenca e por que ela importa.
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

        if intent == "scheduling":
            update["next_kind"] = "schedule"
            return update

        return _rota_informativa(update)

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
        return {"response_text": text, "response_kind": "collect"}

    async def inform(self, state: HelenaState) -> dict[str, Any]:
        """Administrative response (no clinical guidance, no red flag).

        RECUSA DE SAIDA (13/09/2026): quando o rascunho e' barrado, este turno NAO vira uma
        resposta diferente — vira ESCALONAMENTO. E' a mesma escolha que `_rota_informativa` ja faz
        quando a precondicao da rota informativa nao se sustenta (HEL-03), so' que um passo
        adiante: se a unica coisa que a Helena tinha para dizer era algo que ela nao pode dizer,
        entao ela nao tem resposta automatica para dar, e quem tem e' um humano.

        Tentar redigir de novo seria a alternativa obvia e esta' deliberadamente FORA: o mesmo
        prompt com a mesma mensagem tende ao mesmo texto, e um laco de tentativas transformaria
        uma cerca num atraso. `_start_escalation` redige o texto do handoff pelo mesmo
        `_respond_llm`, agora na rota `escalate` — se ATE ESSE for recusado, ele cai na constante.
        """
        try:
            text = await self._respond_llm(state, "inform")
        except RespostaRecusadaError:
            # O `error` leva o TOKEN DE CLASSE e nada mais. Ler `recusa.grupo` aqui seria um
            # atributo de excecao ligada indo para campo de estado, que a cerca LUC-06/NEW-01
            # recusa — e ela esta' certa mesmo com um valor que por acaso e' seguro: este campo
            # sobrevive para o sufixo `[falha tecnica: ...]` do handoff no turno seguinte. QUAL
            # padrao barrou fica onde detalhe deve ficar: na linha de log e no contador, os dois
            # emitidos em `_respond_llm`.
            return await self._start_escalation(
                state,
                motivo="falha_tecnica",
                # HEL-04: a severidade DERIVA da classificacao ja feita, nunca de um literal.
                severidade=_severidade_de_intensidade(state.get("intensidade")),
                response_kind="escalate",
                error=ERRO_RESPOSTA_RECUSADA,
            )
        return {"response_text": text, "response_kind": "inform"}

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

        try:
            response_text = await self._respond_llm(state, response_kind)
        except RespostaRecusadaError:
            # A recusa ja foi registrada e contada em `_respond_llm`. AQUI um humano FOI mesmo
            # acionado (este metodo esta' abrindo o processo), entao a constante honesta promete o
            # que e' verdade e nao contem nenhum dos padroes proibidos. Nao ha nova tentativa pelo
            # mesmo motivo que em `inform`.
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
            # parece um sistema quebrado; e o canned de handoff ("um profissional vai continuar")
            # NAO serve de substituto num turno `inform`, onde ninguem foi acionado — seria a
            # promessa sem lastro que CC-01 acabou de remover do ramo de falha de start. Entao a
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
            return saida
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
        return saida

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

          1. `ja_ativo` — a escalacao desta conversa JA estava aberta (o C1 da bateria). O
             rascunho anuncia um encaminhamento novo que nao houve, entao ele e' trocado
             INCONDICIONALMENTE: aqui nao se trata de "o modelo errou a frase", e' que a frase
             certa depende de um fato que o modelo nao tinha quando redigiu.
          2. START ACONTECEU e o texto NAO MENCIONA o encaminhamento (o E4). O texto e' trocado
             pela constante de handoff, que menciona.
          3. START NAO ACONTECEU e o texto PROMETE um humano. Trocado pela constante que diz o que
             nao aconteceu. Inalcancavel pela rota `escalate`/`schedule` (as duas sempre passam por
             `_start_escalation`, que grava o desfecho) — e' o backstop ESTRUTURAL para as rotas
             que nao abrem processo, incluindo o fallback canned de `_respond_llm` num turno
             `inform`, que promete "um profissional humano vai continuar" sem passar pela cerca.

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

        if start_desfecho == START_DESFECHO_JA_ATIVO:
            return RESPOSTA_HANDOFF_JA_ABERTO, self._registrar_troca(
                state,
                grupo=RECUSA_ESCALONAMENTO_JA_ABERTO,
                response_kind=response_kind,
                start_desfecho=start_desfecho,
                texto=RESPOSTA_HANDOFF_JA_ABERTO,
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
        """
        logger.error(
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
        if error:
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

    # -- Conditional routing ----------------------------------------------------------------

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
        prompt = f"{classify_prompt()}\n\n{render_untrusted_block('message_body', corpo)}"
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
        data = _parse_json_object(raw)
        if data is None:
            return None, "classify LLM returned unparseable JSON"
        schema_failure = _validate_extraction(data)
        if schema_failure is not None:
            return None, f"classify LLM returned schema-invalid JSON: {schema_failure}"
        return data, None

    async def _respond_llm(self, state: HelenaState, response_kind: ResponseKind) -> str:
        context: dict[str, Any] = {
            "response_kind": response_kind,
            "dmn_motivo": (state.get("dmn_decision") or {}).get("motivo"),
            "escalation_severidade": state.get("escalation_severidade"),
        }
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
        except EXTERNAL_DEPENDENCY_FAILURES:  # fail-safe default: never leave the beneficiary with nothing.
            return "Recebemos sua mensagem. Um profissional humano vai continuar o atendimento em breve."

        # RECUSA DE SAIDA (13/09/2026). ESTE e' o unico ponto por onde passa todo texto que chega
        # ao beneficiario — `inform` (1x), `_start_escalation` (escalate e schedule) e o fallback
        # de `respond`. Cercar aqui cobre as tres rotas com uma verificacao so'.
        #
        # POR QUE A CERCA EXISTE MESMO COM A PROIBICAO NO PROMPT. O `response-v3` proibiu a
        # negativa clinica em maiusculas e com o raciocinio inteiro, e o modelo passou por cima
        # DUAS VEZES na mesma conversa (medido 13/09/2026). Toda esta agente e' feita de travas —
        # a DMN decide em vez do modelo, a negativa so' nasce de User Task, o provedor recusa
        # construir sem atestacao — e o texto, que e' a unica coisa que a pessoa do outro lado le',
        # nao tinha nenhuma. Pedir e' instrucao; isto e' cerca.
        recusa = motivo_de_recusa(texto, response_kind)
        if recusa is not None:
            grupo, padrao = recusa
            # O PADRAO no log (onde alguem depura), o GRUPO no contador (onde alguem conta). O
            # TEXTO nao vai para nenhum dos dois: e' saida de modelo sobre a mensagem do
            # beneficiario, e um log nao e' lugar de ampliar o alcance dela.
            logger.error(
                "helena_resposta_recusada",
                node="_respond_llm",
                grupo=grupo,
                padrao=padrao,
                response_kind=response_kind,
                recusa_version=RECUSA_DE_SAIDA_VERSION,
                prompt_version=RESPONSE_PROMPT_VERSION,
            )
            record_resposta_recusada(agent_id="helena", motivo=grupo, response_kind=response_kind)
            raise RespostaRecusadaError(grupo, padrao, response_kind)
        return texto

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

        g.add_edge(START, "receive")
        g.add_edge("receive", "classify")
        g.add_conditional_edges(
            "classify",
            self._route,
            {"inform": "inform", "schedule": "schedule", "escalate": "escalate", "collect": "collect"},
        )
        g.add_edge("inform", "respond")
        g.add_edge("collect", "respond")
        g.add_edge("schedule", "respond")
        g.add_edge("escalate", "respond")
        g.add_edge("respond", END)
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
    return HelenaGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        whatsapp=cast(WhatsAppSender, whatsapp),
        agent_version=agent_version,
        coleta_enabled=coleta_enabled,
        memoria_clinica_enabled=memoria_clinica_enabled,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "classify": CLASSIFY_PROMPT_VERSION,
    "response": RESPONSE_PROMPT_VERSION,
    "coleta": COLETA_PROMPT_VERSION,
    # A lista de recusa nao e' um prompt, mas e' um artefato VERSIONADO que decide o que chega ao
    # beneficiario — exatamente o que este mapa existe para tornar auditavel. Deixa-la de fora
    # significaria um texto barrado sem registro de QUAL cerca o barrou.
    "recusa_de_saida": RECUSA_DE_SAIDA_VERSION,
}
