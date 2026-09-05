# Contrato — SP-OP-ESCALATION-001 (Escalonamento Humano Universal)

**Status:** FINAL (v1.0.0) · **Fase:** 0 · **BPMN:** `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
**Gatilho regulatorio:** seguranca assistencial — Lei 9.656/1998 art. 35-C (urgencia = imediato); ADR-0005/0008 (decisao clinica por agente: L0 hard).

## Business key (idempotencia)

```
ESC-{tenant_id}-{conversation_id}
```

Maximo UMA instancia ativa por conversa. `mcp-cibseven.start_process` DEVE consultar a
business key antes de iniciar; instancia ativa existente => retorna a existente (start
idempotente, sem duplicar escalonamento).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `source_agent_id` | string | sim | Agente que escala (ex.: `helena`) |
| `source_agent_version` | string | sim | Versao do agente (auditoria ADR-0007) |
| `conversation_id` | string | sim | Conversa/caso de origem (correlaciona retomada) |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo (Zona Geral, ADR-0006 — NUNCA CPF/nome) |
| `canal` | string | sim | `whatsapp` \| `portal` \| `telefone` |
| `motivo_categoria` | string | sim | `red_flag_clinico` \| `risco_psicossocial` \| `intencao_clinica` \| `solicitacao_humano` \| `falha_tecnica` \| `outro` |
| `severidade` | string | sim | `grave` \| `moderada` \| `leve` (red flag P1 => `grave`, P2 => `moderada`) |
| `resumo_contexto` | string | sim | Handoff escrito pelo agente, pseudonimizado |
| `dmn_decision_ref` | string | nao | Tabela/regra DMN que disparou (ex.: `triage_redflag_adult#r1`) |

## Variaveis de saida (preenchidas pela User Task)

| Variavel | Tipo | Descricao |
|---|---|---|
| `resultado` | string | `resolvido_humano` \| `devolvido_agente` \| `emergencia_acionada` |
| `notas_resolucao` | string | Notas do humano (pseudonimizadas) |

## Variaveis de proveniencia do agente (Lucas — ADR-0007/ADR-0015)

Este e o contrato universal de escalonamento (Helena tambem o inicia, sem variaveis aditivas —
apenas o §"Variaveis de entrada" acima). Lucas, ao iniciar a MESMA SP-OP-ESCALATION-001, semeia
anotacoes de auditoria adicionais via `LucasGraph._escalation_variables`
(`src/maezo/agents/lucas/graph.py`, mirror do estilo aditivo `dossie_rafael`/`rafael_route` de
SP-OP-AUTH-001) junto com as variaveis de entrada; NENHUMA delas e uma decisao — so proveniencia,
dossie instrutivo e roteamento humano (CC-13 — Agent Fleet Audit: antes deste registro,
`_escalation_variables` as emitia sem declaracao no contrato).

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `dossie_lucas` | json | nao | Dossie/narrativa de encaminhamento montado por Lucas — instrui o atendimento humano; carrega `decisao_cancelamento` sempre `None` (Lucas NUNCA decide) |
| `lucas_route` | string | nao | Roteamento do grafo do Lucas (`respond_member` \| `escalate_human`) — espelha, nao decide, o roteamento do processo |
| `motivo_encaminhamento` | string | nao | Motivo do encaminhamento de Lucas (`inadimplencia_detectada` \| `pedido_cancelamento` \| `contestacao_cobranca` \| `ambiguidade` \| `dmn_indisponivel` \| `falha_tecnica`) |
| `grupo_humano_sugerido` | string | nao | Grupo humano sugerido por Lucas (sugestao, catch-all `atendimento-humano`) — DIFERENTE do `dmn_decision_ref` singular ja declarado acima: aqui e a sugestao do agente, nao a saida da DMN `escalation_routing` |
| `dmn_decision_refs` | json | nao | Referencias auditaveis (tabela→regra, plural — dict `{tabela: ref}`) das DMN que Lucas consultou; complementa o `dmn_decision_ref` (singular) ja declarado em §Variaveis de entrada |

## Topicos

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.escalation.requested` | produz | apos start |
| Kafka | `agents.events.escalation.sla_breached` | produz | timer ack (payload.fase=`ack`) ou resolucao (payload.fase=`resolucao`) |
| Kafka | `agents.events.escalation.resolved` | produz | antes dos fins (payload.resultado) |
| Kafka | `agents.events.process_completed` | produz | somente se `resultado=devolvido_agente` (retoma conversa do agente). Payload (GAP-ESC-1): `tenant_id`, `agent_id` (alias de `source_agent_id`), `conversation_id`, `resultado` + `_business_key` (`ESC-{tenant_id}-{conversation_id}`, injetado pelo worker) — chaves de correlacao para o consumidor universal (GAP-XHITL-4). SEM `notas_resolucao` (texto livre, Zona PHI) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio |
| External task | `operadora.escalation.notify_team` | consome (worker) | alerta o grupo humano roteado |
| External task | `operadora.escalation.notify_supervisor` | consome (worker) | alerta supervisao (ack estourado ou fallback de canal) |

## DMN referenciada

### `escalation_routing` (hit policy FIRST — shape FINAL, SLAs DRAFT)

| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `motivo_categoria` | string | ver variaveis de entrada |
| in | `severidade` | string | `grave` \| `moderada` \| `leve` |
| out | `prioridade` | string | `P1` \| `P2` \| `P3` |
| out | `grupo_atendimento` | string | `plantao-clinico` \| `enfermagem-triagem` \| `atendimento-humano` |
| out | `sla_ack` | string (ISO 8601) | ex.: `PT5M` |
| out | `sla_resolucao` | string (ISO 8601) | ex.: `PT30M` |

Fail-safe: catch-all = P2/`atendimento-humano` (motivo desconhecido nunca vira P3).

## Papeis humanos (candidate groups)

| Grupo | Papel | Tarefa |
|---|---|---|
| `plantao-clinico` | Enfermeiro/medico de plantao | `UT_TratarEscalonamento` (P1) |
| `enfermagem-triagem` | Enfermagem de triagem | `UT_TratarEscalonamento` (P2) |
| `atendimento-humano` | Atendimento ao beneficiario | `UT_TratarEscalonamento` (P3) |
| `supervisao-atendimento` | Supervisor | `UT_SupervisorAssume` (SLA resolucao estourado) + alertas de ack |

## SLAs

| Timer | Valor | Tipo | Fonte regulatoria |
|---|---|---|---|
| Ciencia (ack) P1 | PT5M | nao-interruptivo -> alerta supervisor + evento breach(fase=ack) | Politica assistencial ancorada em Lei 9.656/98 art. 35-C — **DRAFT/verify (gestao assistencial)** |
| Ciencia (ack) P2 / P3 | PT30M / PT4H | idem | idem |
| Resolucao P1 | PT30M | interruptivo -> supervisor assume + evento breach(fase=resolucao) | idem |
| Resolucao P2 / P3 | PT4H / PT24H | idem | idem |

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_ESC_NOTIFY_FAILED` | boundary error em `ST_NotificarTime` | fallback: notifica supervisor por canal alternativo e segue para a User Task |

## Notas de design

- Nenhum fim do processo ocorre sem evento de dominio publicado antes (auditoria dupla: engine + Kafka, ADR-0007).
- O processo NAO contem logica clinica: quem decide red flag e a DMN `triage_redflag_*` consumida pela Helena ANTES do start; quem resolve o caso e humano.
- `devolvido_agente` e o unico caminho que devolve controle ao agente, sempre com instrucoes humanas em `notas_resolucao`.
- `agents.events.process_completed` (Zona Geral) carrega apenas identificadores estruturais — `tenant_id`, `agent_id`, `conversation_id`, `resultado` + `_business_key` — NUNCA `notas_resolucao` (texto livre humano, Zona PHI). O consumidor de retomada (GAP-XHITL-4) resolve as instrucoes humanas pelo business key na Zona PHI (GAP-ESC-1; scrub sistemico via `phi_vars.py` e GAP-XPHI-1).
