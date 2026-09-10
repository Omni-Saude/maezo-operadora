# Contrato — SP-OP-ESCALATION-001 (Escalonamento Humano Universal)

**Status:** FINAL (v1.0.0) · **Fase:** 0 · **BPMN:** `spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`
**Gatilho regulatorio:** seguranca assistencial — Lei 9.656/1998 art. 35-C (urgencia = imediato); ADR-0005/0008 (decisao clinica por agente: L0 hard).

## Business key (idempotencia)

### Portal e relação com AUTH — interface E01 (2026-09-10)

Complemento de construção do ADR-0049, sem alterar a forma/SLAs deste contrato ou seu
BPMN executável. O portal é canal já permitido em `canal`; mantém a MESMA instância
universal e os produtores de agente existentes, sem BPMN novo por público. Login,
navegação, inbox e leitura são comportamento de aplicação, não novos processos.

`UT_TratarEscalonamento` e `UT_SupervisorAssume` reutilizam catálogo/binding, posse,
comando atômico e recibo do gateway humano; grupo dinâmico vem de `escalation_routing`
intersectado com membership atual, nunca do browser. Beneficiário/prestador não podem
completar essas tarefas internas. Histórico/comunicações externas exigem vínculo atual
e projeção autorizada; `notas_resolucao` permanece na Zona PHI, fora do evento geral.

`notify_team`/`notify_supervisor` e os eventos de domínio exigem consumidor de inbox e
evidência de entrega para que o portal afirme notificação. Reusar o fallback modelado de
`ERR_ESC_NOTIFY_FAILED` (ADR-0030); Kafka aceitou não significa humano recebeu.
Claim/ACK de interface não cancela timer nem resolve tarefa por inferência. O engine
continua dono dos timers e o caso exibe o estado real após takeover/timeout.

**AUTH não chama ESCALATION automaticamente.** A coordenação da auditoria e junta já
existem dentro de AUTH. Iniciar ESCALATION requer gatilho deste contrato, conversa de
origem, produtor autorizado e correlação de retorno; não inventar `source_agent_id`
para uma pessoa do portal. Um futuro start exclusivamente humano exige extensão tipada
e revisão dos campos obrigatórios, não contornar o contrato de proveniência do agente.
`devolvido_agente` continua o único desfecho de retomada; não converte resolução de
escalonamento em aprovação de AUTH. Reusar as APIs de caso/comunicações/recibos e os
critérios de autorização/replay descritos no complemento portal de SP-OP-AUTH-001.

### Chave legada interna

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
| `severidade` | string | sim, EXCETO quando `motivo_categoria = falha_tecnica` (secao logo abaixo: `null` declarado) | `grave` \| `moderada` \| `leve` (red flag P1 => `grave`, P2 => `moderada`) |
| `resumo_contexto` | string | sim | Handoff escrito pelo agente, pseudonimizado |
| `dmn_decision_ref` | string | nao | Tabela/regra DMN que disparou (ex.: `triage_redflag_adult#r1`) |

### `severidade` quando `motivo_categoria = falha_tecnica` (HEL-04, 2026-09-05)

A parentese acima (`red flag P1 => grave, P2 => moderada`) descreve o gatilho de red flag, o
unico em que existe veredito de DMN. No gatilho **`falha_tecnica`** nao ha veredito nenhum — e' o
proprio motor de decisao que faltou — e ate 2026-09-05 o agente emitia `leve` literal nos dois
ramos, inclusive sobre um sintoma ja classificado como `grave`. A regra agora e:

| Situacao | `severidade` | Por que |
|---|---|---|
| DMN indisponivel COM sintoma classificado | derivada da `intensidade` ja validada: `leve` so' quando a `intensidade` e' explicitamente `leve`; qualquer outro valor (`grave`, `moderada`, `desconhecida`, ausente) => `moderada` | ha' sinal para calibrar, e uma intensidade nao apurada nao e' a mais branda. **Teto em `moderada`**: `grave` fica reservado ao red flag P1, que so' a DMN emite |
| Falha do classificador (excecao, JSON invalido, schema invalido, roteamento PHI recusado) | ausente (`null`) | nao houve extracao alguma de onde derivar. Uma severidade desconhecida nunca e' anunciada como `leve` — mesma decisao ja vigente para o escalonamento sem contexto de runtime (HELENA-SEVERIDADE-DEFAULT). **CORRECAO (§Delta-3, regressao P-12, 2026-09-06):** a linha obrigatoria anterior contradizia o `null` declarado nesta secao. O reparo Fleet `6af016b1` aceita ausencia/vazio somente em `falha_tecnica` (`escalation.py::_exigir_severidade`). Os testes historicos `test_malformed_classifier_json_escalates_falha_tecnica` e `test_classifier_llm_exception_escalates_falha_tecnica` registraram `no user task ever appeared`; esse relato nao prova falha dos dois canais de producao: a fixture daquele corte omitia a allowlist BPMN e usava `kafka=None`. Os topicos sao distintos (`notify_team` e `notify_supervisor`), embora compartilhem a checagem. Com produtor disponivel, a notificacao pode ser publicada; sem produtor o status e `teams_notification_skipped_no_producer`, que significa progresso sem entrega. A escalacao deve chegar a `UT_TratarEscalonamento`; a revisao integrada exige tarefa exata, `null` presente, roteamento r6, grupo humano e status de publicacao verificados no motor. Legitimidade da excecao: a DMN `escalation_routing` (hitPolicy FIRST) roteia esse motivo pela regra **`r6`**, cuja coluna `severidade` e' o coringa `-` (qualquer valor, `null` inclusive) -> `P3` / `atendimento-humano` / `PT4H` / `PT24H`; a severidade NUNCA foi entrada de roteamento neste motivo, entao aceitar `null` nao enfraquece decisao nenhuma. Limites da excecao, os dois fail-closed: um valor PRESENTE fora de `{grave, moderada, leve}` continua recusando (aqui como em qualquer motivo — ausencia e' a verdade declarada, corrupcao nao e'), e nenhum outro `motivo_categoria` (nem um ausente) aceita ausencia. O que continua nao existindo e' o rotulo fabricado `leve` |

`event_payload_vars` de `ST_PublishRequested` (BPMN `:66`) e dos dois publicadores de breach
(`:188`, `:243`) inclui `severidade`. O publicador generico
(`workers/events.py::make_publish_event_handler`) copia a variavel exatamente como o motor a
entrega e so' quando ela chega (`if var_name in task.variables`): com `null` o payload carrega
`"severidade": null`, e se o motor a omitir a chave simplesmente nao aparece — em nenhum dos dois
casos um leitor recebe um `leve` fabricado, e nao ha' default em lugar nenhum do caminho. Nenhum
consumidor le esse campo: `grep -rn severidade src/maezo/tools/workers/events.py
src/maezo/platform` devolve apenas prosa de docstring/comentario e a constante de PRODUCAO
`notification_bridge.SLA_ALERT_SEVERIDADE` (o alerta de risco de SLA, que escreve `moderada` com
`motivo_categoria=outro` e nada le de volta).

### Fronteira de conteudo nao confiavel no caminho do agente (HEL-06/HEL-03, 2026-09-05)

O texto que o beneficiario digita e' conteudo de terceiro e chega aos prompts do agente dentro de
um bloco `<<<NAO_CONFIAVEL message_body ... NAO_CONFIAVEL message_body>>>`
(`maezo.runtime.prompt_format.render_untrusted_block`), com preambulo fixo, delimitador
nao-falsificavel e tamanho maximo. Isso **reduz** a chance de uma injecao funcionar; o que
**tira dela o poder de roteamento** e' a precondicao deterministica da rota informativa
(`agents/helena/graph.py::_inform_recusado`): um `sintoma_codigo` reportado — venha de que
`intent` vier — nunca termina em resposta automatica sem veredito de DMN (`red_flag` explicito
`false` e `conduta` fora de `ESCALATE*`). Nenhuma tabela DMN mudou: as quatro
`triage_redflag_*` ja emitem `red_flag` em toda regra, catch-all inclusive.

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
| `plantao-clinico` | Enfermeiro/medico de plantao — **DRAFT/verify** (nome candidato, R-034; ver `docs/sme-dispatch/po/ORG-TAXONOMY-TABLE.md`) | `UT_TratarEscalonamento` (P1) |
| `enfermagem-triagem` | Enfermagem de triagem — **DRAFT/verify** (nome candidato, R-034; ver `docs/sme-dispatch/po/ORG-TAXONOMY-TABLE.md`) | `UT_TratarEscalonamento` (P2) |
| `atendimento-humano` | Atendimento ao beneficiario — **DRAFT/verify** (nome candidato, R-034; ver `docs/sme-dispatch/po/ORG-TAXONOMY-TABLE.md`) | `UT_TratarEscalonamento` (P3 via `r5`/`r6`; TAMBEM P2 via o catch-all fail-safe `r7` da DMN — motivo desconhecido nunca vira P3, ver `escalation_routing.dmn` regra `r7`) |
| `supervisao-atendimento` | Supervisor — **DRAFT/verify** (nome candidato, R-034; ver `docs/sme-dispatch/po/ORG-TAXONOMY-TABLE.md`) | `UT_SupervisorAssume` (SLA resolucao estourado) + alertas de ack |

> **PERSP-ESCALATION-VOCAB-b (parcial — rename bloqueado por R-034):** as 4 linhas acima ganham a
> marca `DRAFT/verify` diretamente na tabela (antes so o paragrafo PROPOSTO abaixo explicava a
> ressalva em prosa). O RENAME de fato dos `candidateGroups` (`plantao-clinico`,
> `enfermagem-triagem`, `atendimento-humano`, `supervisao-atendimento` — 6 locais no total, incl.
> `escalation_routing.dmn`, `escalation.py::_SEVERITY_TO_GROUP`, `test-specs/SP-OP-ESCALATION-001.md`)
> continua BLOQUEADO ate a sessao de nomeacao de R-034 produzir os nomes reais do dono
> organizacional. Nenhum rename foi aplicado por esta linha.

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora** (ver
> `docs/review-queue.md`; R-034 / gap `PERSP-ESCALATION-VOCAB-a`). Os nomes `plantao-clinico` e
> `enfermagem-triagem` sao candidatos DRAFT herdados de vocabulario de prestador e podem nao
> corresponder aos grupos reais do IdP/console de User Tasks da operadora; `atendimento-humano` e
> `supervisao-atendimento` tambem aguardam a mesma confirmacao. A tabela consolidada de
> `grupo declarado -> arquivo:linha -> processo -> SLA/ato` para esta sessao de nomeacao esta em
> `docs/sme-dispatch/po/ORG-TAXONOMY-TABLE.md`. Nenhum rename e aplicado sem os nomes reais do
> dono organizacional da operadora.

## SLAs

| Timer | Valor | Tipo | Fonte regulatoria |
|---|---|---|---|
| Ciencia (ack) P1 | PT5M | nao-interruptivo -> alerta supervisor + evento breach(fase=ack) | Politica assistencial ancorada em Lei 9.656/98 art. 35-C — **DRAFT/verify (gestao assistencial)** |
| Ciencia (ack) P2 / P3 | PT30M / PT4H | idem | idem |
| Resolucao P1 | PT30M | interruptivo -> supervisor assume + evento breach(fase=resolucao) | idem |
| Resolucao P2 / P3 | PT4H / PT24H | idem | idem |

## Desfecho de agente: falha de start (CC-01)

| Desfecho | Onde vive | Quem escreve | Significado |
|---|---|---|---|
| `erro_inicio_processo` | **estado do agente lucas e helena** — NAO e variavel de processo | no `notify_start_failure` do grafo, via o helper unico `maezo.runtime.start_outcome.notify_start_failure` | o agente TENTOU iniciar SP-OP-ESCALATION-001 pelo chokepoint `start_process_idempotent` e o engine recusou (`CibSevenError`). NENHUMA instancia nasceu |

ONDE ESTE VALOR **NAO** ESTA, e por que. Ele nunca chega ao engine: nao consta de
`## Variaveis de entrada` nem de `## Variaveis de saida`, nao tem `bpmnError` associado, nao
aparece em nenhum `camunda:` do BPMN e **nao exige mudanca nenhuma no BPMN deste processo**. Nao
poderia ser diferente — o processo NAO nasceu, entao nao existe instancia onde gravar uma
variavel nem escopo onde lancar um erro. Ele e declarado AQUI, no contrato, porque e um desfecho
CONTRATUAL do agente que serve este processo e porque quem consome o estado do agente (o handler
A2A, um golden de eval, uma regra de alerta) precisa do literal exato e estavel.

POR QUE ELE EXISTE (auditoria de frota 2026-09-04, achado CC-01). Ate essa data a falha de start
era engolida: o `except CibSevenError` do no de start devolvia apenas `process_started=false` e a
aresta seguinte era INCONDICIONAL para um terminal no-op, de modo que o desfecho de SUCESSO ja
gravado a montante (`escalado_humano`) sobrevivia — o estado do agente AFIRMAVA um fato que nao
aconteceu. E o caso se perdia em silencio, porque os prazos desta especificacao vivem em timers
da instancia BPMN que nunca nasceu: sem SLA, sem alerta, sem retry.

EFEITOS ASSOCIADOS ao desfecho, todos no lado do agente:

* `process_started = false` (e, quando o agente distingue no-ops legitimos, o marcador
  `start_failed = true`, que e o que a aresta condicional le);
* `record_agent_error()` -> `maezo_agent_errors_total`, o contador que a regra
  `MaezoAgentCrashLoop` observa;
* evento estruturado `agent_process_start_failed` com a business key idempotente deste contrato —
  este evento e o SUBSTITUTO operacional do prazo enquanto a instancia nao existe;
* RETRY seguro por construcao: `start_process_idempotent` e idempotente por business key, entao
  uma reentrega reencontra a instancia viva (`ALREADY_ACTIVE`) em vez de abrir uma segunda.

ORDEM DO ACK AO BENEFICIARIO (LUC-05). Este contrato e o unico dos dez cujo agente FALA com o
beneficiario no mesmo turno em que abre o processo, e por isso ele carrega uma regra de ORDEM
alem do desfecho:

* **Lucas** montava o dossie e ENVIAVA o ACK ("um atendente humano vai continuar") dentro de
  `escalate_human`, ANTES do start. O envio migrou para o no `send_escalation_ack`, alcancavel
  somente pelo ramo de SUCESSO da aresta condicional que sai de `start_process`. Enquanto o ACK
  nao sai, o estado carrega `ack_pending=true`; numa falha de start ele carrega tambem
  `retryable_error=true`.
* **Helena** redige a resposta de handoff ANTES do start (a chamada de LLM ocorre dentro de
  `_start_escalation`) e a enviava no no `respond` mesmo apos um start falho. Agora `respond` so
  envia o handoff quando `escalation_started` e verdadeiro; caso contrario substitui o texto pela
  mensagem honesta de falha tecnica (`response_kind=falha_tecnica_start`), que NAO promete
  atendente nem prazo.

Em ambos os casos a regra e a mesma e nao e negociavel: **nao se promete ao beneficiario um
humano que nao foi acionado.**


## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_ESC_NOTIFY_FAILED` | boundary error em `ST_NotificarTime` | fallback: notifica supervisor por canal alternativo e segue para a User Task |

## Notas de design

- Nenhum fim do processo ocorre sem evento de dominio publicado antes (auditoria dupla: engine + Kafka, ADR-0007).
- O processo NAO contem logica clinica: quem decide red flag e a DMN `triage_redflag_*` consumida pela Helena ANTES do start; quem resolve o caso e humano.
- `devolvido_agente` e o unico caminho que devolve controle ao agente, sempre com instrucoes humanas em `notas_resolucao`.
- `agents.events.process_completed` (Zona Geral) carrega apenas identificadores estruturais — `tenant_id`, `agent_id`, `conversation_id`, `resultado` + `_business_key` — NUNCA `notas_resolucao` (texto livre humano, Zona PHI). O consumidor de retomada (GAP-XHITL-4) resolve as instrucoes humanas pelo business key na Zona PHI (GAP-ESC-1; scrub sistemico via `phi_vars.py` e GAP-XPHI-1).
