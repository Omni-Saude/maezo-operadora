# Contrato — SP-OP-NIP-001 (Resposta a NIP — ANS)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 (Wave B — canal regulatorio ANS) · **BPMN:** `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn` (autoria em wave posterior)
**Gatilho regulatorio:** RN 388/2016 (NIP — Notificacao de Intermediacao Preliminar; prazos de resposta assistencial/nao-assistencial: **DRAFT/verify**), IN DIDES correlata (procedimento NIP: **DRAFT/verify**), Lei 9.656/1998 (cobertura). Citacoes de RN nao confirmadas — ver §9 OQ/R2 do phase2-plan e review-queue.

## Invariante L0 hard (nao negociavel)

Negativa-like na branch **"mantem a negativa"**: manter a negativa original apos uma NIP
embute `authorization_denial`-class (L0 hard). A decisao de **manter a negativa SO nasce
numa User Task humana** (`UT_RevisaoJuridicaNip`, com `UT_ElaborarRespostaNip` autorando o
texto). Especificamente:

1. **Nenhum enum de rota tem variante "manter negativa" em branch automatizado.** `classificacao`/`grupo_revisor` apenas roteiam para o grupo humano; nunca decidem o merito.
2. **Nenhuma DMN deste processo possui coluna de saida de negativa.** `nip_classification`/`nip_routing` classificam e roteiam (assistencial/nao-assistencial, prazo, grupo); nao produzem `decisao_nip`.
3. **Ambiguo/inelegivel/expirado → fail-safe humano.** Toda linha catch-all das DMNs roteia para `juridico-regulatorio` com o SLA mais curto (conservador). Tema NIP nao reconhecido NUNCA vira "nao-assistencial respondida automaticamente".
4. **Worker-guard `ERR_NIP_NEGATIVA_NOT_HUMAN`** no efeito adverso: `operadora.nip.submit_response` recusa transmitir/arquivar resposta que mantem negativa sem `decisao_nip==MANTER_NEGATIVA` setado por humano; carrega `revisor_id` (ADR-0007). **Nenhum caminho DMN ou de agente produz o texto da resposta como final** — o agente (Gustavo) so instrui/monta dossie; humano autora e aprova.
5. **Teste de invariante** consultando `history/activity-instance` do engine: o end-event adverso `End_NipNegativaMantida` NUNCA aparece no historico sem uma User Task humana concluida que setou `decisao_nip==MANTER_NEGATIVA`.

Resposta favoravel (concede o pleito do beneficiario) e permitida e desejavel, mas o texto
legal e **sempre** revisado por humano (regulatorio/juridico autora+aprova). Resposta
nao-assistencial idem: o agente monta, humano confirma o envio.

### Terminais humano-gated (nomeados)

| End-event | Significado | Alcancavel SOMENTE via |
|---|---|---|
| `End_NipNegativaMantida` | mantem a negativa original ao beneficiario/ANS (adverso) | `UT_RevisaoJuridicaNip` (humano) com `decisao_nip==MANTER_NEGATIVA` + campos obrigatorios |

Terminais NEUTROS (nao adversos, mas o texto e revisado por humano antes do filing):
`End_NipResolvidaFavoravel` (concede), `End_NipNaoAssistencialRespondida` (resposta
clerical revisada). Nenhum desses e produzido por automacao sem passagem por User Task de
elaboracao/revisao.

### Worker-guard (nomeado)

`ERR_NIP_NEGATIVA_NOT_HUMAN` em `operadora.nip.submit_response`: o worker recusa enviar uma
resposta cujo `decisao_nip == MANTER_NEGATIVA` se a variavel nao foi setada por uma User
Task humana (ausencia de `revisor_id`/grupo humano na cadeia). Espelha
`ERR_GLOSA_ACCEPT_NOT_HUMAN`/`ERR_DESISTENCIA_NOT_HUMAN`/`ERR_AUTH_*` dos demais
negativa-like.

## Business key (idempotencia)

```
NIP-{tenant_id}-{numero_nip_ans}
```

Uma instancia por protocolo NIP (alternativa: `NIP-{tenant_id}-{protocolo_ans}` quando o
identificador disponivel for o protocolo ANS, nao o numero da NIP — **DRAFT/verify** com
regulatorio qual e o identificador estavel). `mcp-cibseven.start_process` consulta a
business key antes de iniciar; instancia ativa existente => retorna a existente (start
idempotente; reentrega da mesma NIP nao duplica).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_nip_ans` | string | sim | Numero da NIP (chave de negocio) |
| `protocolo_ans` | string | nao | Protocolo ANS associado (correlacao com ANS-SUBMIT) |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo do beneficiario (ADR-0006 — NUNCA CPF/nome) |
| `classificacao_nip` | string | sim | `assistencial` \| `nao_assistencial` (pre-resolvido por worker a partir do tema/origem) |
| `tema_nip` | string | sim | Tema/assunto da NIP (ex.: `negativa_cobertura`, `prazo_atendimento`, `reembolso`, `rede`, `cobranca`) |
| `referencia_negativa_original` | string | nao | Referencia ao ato original contestado (guia AUTH, glosa, reembolso) — presente quando a NIP contesta uma negativa |
| `data_recebimento_nip_iso` | string | sim | Data de recebimento (YYYY-MM-DD) — base de contagem de prazo |
| `documentos_refs` | json | sim | Referencias de anexos da NIP (pode ser vazio) |
| `contesta_negativa` | boolean | sim | Pre-resolvido por worker: a NIP contesta uma negativa previa (define o caminho que pode levar a `MANTER_NEGATIVA`) |
| `documentacao_suficiente` | boolean | sim | Pre-resolvido por worker: ha base documental para responder |
| `origem_a2a` | boolean | nao | `true` quando a instancia nasce de delegacao A2A `nip.instruct` (Helena/intake → Gustavo) |

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_nip` | string | `MANTER_NEGATIVA` \| `CONCEDER` \| `RESPONDER_NAO_ASSISTENCIAL` \| `SOLICITAR_INFO` (User Tasks humanas — unica origem de `decisao_nip`) |
| `fundamentacao_regulatoria` | string | **Obrigatoria se `MANTER_NEGATIVA`** (espelha `justificativa_clinica`/`fundamentacao_dut` do AUTH) |
| `referencia_negativa_original` | string | **Obrigatoria se `MANTER_NEGATIVA`** (rastreia o ato mantido) |
| `texto_resposta_nip` | string | Texto final da resposta a ANS — autorado/revisado por humano (NUNCA gerado como final por DMN/agente) |
| `revisor_id` | string | Identificador do humano que aprovou a resposta (cadeia de auditoria ADR-0007) |

**GAP-NIP-4 (resolvido — correcao de contrato):** este processo **NAO produz** `protocolo_filing`
como variavel de processo propria. A instancia SP-OP-NIP-001 termina (`End_NipNegativaMantida` /
`End_NipResolvidaFavoravel` / `End_NipNaoAssistencialRespondida`) imediatamente apos o handoff
(`operadora.nip.handoff_ans_submit`) — ANTES de a ANS emitir qualquer protocolo. Nao ha worker/
BPMN element deste processo capaz de setar `protocolo_filing` numa instancia ja concluida (era um
output declarado mas estruturalmente inalcancavel). O protocolo de filing real e
`protocolo_ans` — saida de **SP-OP-ANS-SUBMIT-001** (ver
`docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md` §"Variaveis de saida"), correlacionado de
volta a esta NIP via `nip_protocolo_origem` (input obrigatorio de ANS-SUBMIT quando
`origem_envio==nip_filing`). GAP-FAB-NOTIF fix (citacao corrigida — os nomes abaixo NAO existem
no repo: `make_handoff_ans_submit_handler`, `notifications_bridge/consumer.py::
_nip_protocolo_origem`, e o arquivo de teste `test_cross_process_handoff_seam.py` citado como
prova nunca existiu — `find`/`grep` confirmam todos os tres ausentes). A correlacao E IMPLEMENTADA
sob os nomes REAIS: o worker `handoff_ans_submit_entry` (dict-boundary entry sobre a funcao pura
`handoff_ans_submit`, `src/maezo/tools/workers/nip.py:254,492`, registrado no topico
`operadora.nip.handoff_ans_submit`) publica `protocolo_ans`/`numero_nip_ans`/`tenant_id` no
payload de handoff; o consumidor de runtime — que vive em
`src/maezo/platform/notification_bridge.py` (NAO num pacote `notifications_bridge/consumer.py`,
que nao existe em lugar nenhum do repo), na regra `_ans_submit_variables_from_nip_handoff`
(`:266-304`) — mapeia esse payload para `nip_protocolo_origem` (`:298`) ao iniciar
SP-OP-ANS-SUBMIT-001. Prova REAL disponivel (unitaria, sem engine):
`tests/unit/platform/test_notification_bridge.py::test_nip_handoff_triggers_ans_submit` (`:659-681`)
exercita `_ans_submit_variables_from_nip_handoff` isoladamente e afirma
`result.variables["nip_protocolo_origem"] == "PROTO-TESTE-0001"`. Isso prova a FUNCAO DE
MAPEAMENTO — nao a cadeia completa contra um engine real (`UT_RevisarEnvioJuridico` ->
`ST_SubmeterEnvio` emitindo `protocolo_ans`); nenhum teste desse tipo foi encontrado no repo
(UNREPRODUCED, nao fechado). Para obter "o protocolo de filing de uma NIP", consulte
`protocolo_ans` em SP-OP-ANS-SUBMIT-001 filtrando por `nip_protocolo_origem`/business key
`ANSSUB-{tenant}-nipfiling-{submit_id}` — nunca uma variavel `protocolo_filing` em SP-OP-NIP-001.

## Topicos

Convencao `{dominio}.{contexto}.{acao}`. Dominio de eventos = `agents.events.nip.*`;
external-task = `operadora.nip.*` / `regulatorio.nip.*`; mensagens BPMN = `msg.nip.*`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.nip.received` | produz | apos start (NIP recebida/instancia aberta) |
| Kafka | `agents.events.nip.classified` | produz | apos `nip_classification` (payload.classificacao, prazo_dias) |
| Kafka | `agents.events.nip.deadline_risk` | produz | timer nao-interruptivo de prazo (deadline-risk; alimenta countdown de observabilidade WD.3) |
| Kafka | `agents.events.nip.breached` | produz | prazo ANS estourado (incrementa metrica de compliance — exposicao a sancao) |
| Kafka | `agents.events.nip.completed` | produz | fim (payload.desfecho = `resolvida_favoravel` \| `negativa_mantida` \| `nao_assistencial_respondida`) |
| External task | `operadora.events.publish` | consome | publicador generico de eventos de dominio (todos os SP-OP) |
| External task | `operadora.nip.instruct_dossier` | consome | convoca Gustavo: monta dossie de instrucao da resposta (sem decidir merito). **Fail-safe GAP-NIP-1:** garante `data_recebimento_nip_iso` canonico (YYYY-MM-DD) como output var em todo start path — ausente/vazio/inparseavel → hoje (UTC) + warning estruturado `nip_prazo_anchor_failsafe`. A ancora regulatoria DEVE vir do ingress; o default e rede de seguranca contra timer insta-firing/incidente FEEL (fail-safe frequente = bug de integracao). Datas VALIDAS no passado passam intactas — NIP realmente estourada DEVE estourar |
| External task | `operadora.nip.submit_response` | consome | transmite/arquiva a resposta NIP — **guard `ERR_NIP_NEGATIVA_NOT_HUMAN`**; carrega `revisor_id` |
| External task | `operadora.nip.notify_deadline_risk` | consome | alerta `regulatorio-ans`/`nucleo-ans` de risco de prazo (timer nao-interruptivo) |
| External task | `operadora.nip.handoff_ans_submit` | consome | encaminha o filing formal para SP-OP-ANS-SUBMIT-001; payload Kafka ecoa `data_recebimento_nip_iso` (res-ans-competencia-sentinel — `notifications_bridge` a usa para computar a `competencia` REAL do filing, DRAFT/verify regulatorio-ANS) |
| Message BPMN | `msg.nip.instruct` | recebe | start por delegacao A2A `nip.instruct` (correlaciona `NIP-{tenant}-{numero_nip_ans}`) |
| Message BPMN | `msg.nip.info_recebida` | recebe | informacao adicional chegou (destrava `SOLICITAR_INFO`) |

Nota: o filing formal a ANS e responsabilidade de **SP-OP-ANS-SUBMIT-001** (HITL pre-filing,
nao-repudio ADR-0007). NIP-001 produz a decisao+texto revisados por humano e faz handoff;
nao transmite diretamente a ANS.

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): typeRef ∈ {string, boolean, integer, long, double, date};
prazos/SLA em **string ISO 8601**; dias uteis convertidos conservadoramente para ISO **no
worker**, nunca na DMN. Toda tabela com `hitPolicy` e linha catch-all → caminho humano
conservador. **Nenhuma DMN abaixo tem saida de negativa/`decisao_nip`.**

### `nip_classification` (hitPolicy FIRST — DRAFT)

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `classificacao_nip` | string | `assistencial` \| `nao_assistencial` |
| in | `tema_nip` | string | ver variaveis de entrada |
| in | `contesta_negativa` | boolean | a NIP contesta uma negativa previa |
| out | `classificacao` | string | `ASSISTENCIAL_CONTESTA_NEGATIVA` \| `ASSISTENCIAL_OUTRO` \| `NAO_ASSISTENCIAL` |
| out | `prazo_dias` | integer | prazo de resposta em **dias uteis** (worker converte → ISO) — **DRAFT/verify RN 388** |
| out | `grupo_revisor` | string | `regulatorio-ans` \| `nucleo-ans` \| `juridico-regulatorio` |

Catch-all (tema nao reconhecido OU `contesta_negativa=true` ambiguo) → `classificacao =
ASSISTENCIAL_CONTESTA_NEGATIVA`, `grupo_revisor = juridico-regulatorio`, menor `prazo_dias`
(conservador). Sem saida "deny".

### `nip_routing` (hitPolicy FIRST — DRAFT)

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `classificacao` | string | saida de `nip_classification` |
| in | `documentacao_suficiente` | boolean | base documental disponivel |
| out | `grupo_humano` | string | `regulatorio-ans` \| `nucleo-ans` \| `juridico-regulatorio` |
| out | `roteamento` | string | `ELABORAR_RESPOSTA` \| `PENDENTE_INFO` \| `REVISAO_JURIDICA` (catch-all) |

Catch-all → `roteamento = REVISAO_JURIDICA`, `grupo_humano = juridico-regulatorio`. **Sem
saida que decida o merito** (manter/conceder); apenas encaminha ao humano. `ELABORAR_RESPOSTA`
sempre desemboca numa User Task — nunca em `submit_response`.

**GAP-NIP-2 (resolvido; root-cause do 500/ENGINE-16004 corrigido):** `grupo_humano` e CONSUMIDO
em `UT_ElaborarRespostaNip` via `camunda:candidateGroups="${grupo_humano}"` — uma variavel FLAT
promovida por `camunda:outputParameter` na propria `BRT_Roteamento` a partir de
`roteamento_nip.grupo_humano` (`mapDecisionResult=singleResult`). O dot-path direto
`${roteamento_nip.grupo_humano}` (idioma original, espelhando `escalation_routing`/
SP-OP-ESCALATION-001 `${roteamento.grupo_atendimento}`, ADR-0012) e seguro em ESCALATION porque um
wait-state (external task `ST_NotificarTime`) intercala entre a businessRuleTask e a User Task,
persistindo a variavel antes do dot-path ser resolvido; em NIP nao ha wait-state entre
`BRT_Roteamento` e `UT_ElaborarRespostaNip` (`GW_Roteamento` e um gateway inline), entao resolver o
dot-path na mesma transacao sincrona que completa `ST_PublishClassified` lanca ENGINE-16004
"Cannot resolve identifier" no CIB Seven 2.1.0 (confirmado em CI run 28637356996 — 500 em
`.../external-task/{id}/complete`, 2 testes happy-path falhando com "UT_ElaborarRespostaNip nao
apareceu"). A promocao flat (mesmo idioma do fix ja aplicado a
`classificacao`/`prazo_dias`/`grupo_revisor` em `BRT_Classificacao`) resolve o bug — seguro porque
essa User Task tem um unico incoming flow, so alcancado quando `roteamento=='ELABORAR_RESPOSTA'`
(grupo_humano sempre `nucleo-ans` ou `regulatorio-ans` nessa condicao, nunca
`juridico-regulatorio`; fail-safe conservador `juridico-regulatorio` se a saida da DMN vier vazia).
`UT_RevisaoJuridicaNip` permanece com candidateGroups ESTATICO (`juridico-regulatorio,medico-auditor`):
tem tres incoming flows e nos dois que nao vem direto do roteamento (pos-elaboracao via `GW_Minuta`;
retomada via `ICE_PrazoInfo`) `grupo_humano` ainda carregaria o valor da rodada `ELABORAR_RESPOSTA`
— wiring dinamico ali violaria o invariante L0 de revisao sempre-juridica.
O output `sla_alerta_iso` (calculado por esta DMN, nunca lido por nenhum consumidor — nem BPMN
nem o dossie do agente Gustavo) foi REMOVIDO: superseded por `nip_sla.sla_alerta_absoluto_iso`
(unico valor que alimenta os timers `timeDate` reais, GAP-NIP-1).

### `nip_sla` (hitPolicy FIRST — DRAFT, todos os prazos DRAFT/verify)

| Direcao | Campo | typeRef | Dominio |
|---|---|---|---|
| in | `classificacao` | string | saida de `nip_classification` |
| in | `data_recebimento_nip_iso` | string | ancora regulatoria do prazo (GAP-NIP-1 — nao participa do matching, so do calculo do deadline absoluto) |
| out | `prazo_resposta_iso` | string (ISO duration) | prazo total de resposta, duracao relativa (legado/observabilidade — worker resolve dias uteis→ISO conservador) |
| out | `prazo_resposta_absoluto_iso` | string (ISO datetime) | **deadline absoluto** = `data_recebimento_nip_iso` + `prazo_resposta_iso` (FEEL `date and time(...) + duration(...)`); consumido pelos boundary timers `timeDate` (BT_PrazoNipEstourado/BT_PrazoRevisaoEstourado) |
| out | `sla_alerta_iso` | string (ISO duration) | alerta nao-interruptivo (~50-70% do prazo), duracao relativa (legado/observabilidade) |
| out | `sla_alerta_absoluto_iso` | string (ISO datetime) | **alerta absoluto** = `data_recebimento_nip_iso` + `sla_alerta_iso`; consumido pelos timers `timeDate` (BT_AlertaPrazoNip/BT_AlertaPrazoRevisao/ICE_PrazoInfo) |
| out | `fonte_regulatoria` | string | citacao da RN/IN (DRAFT/verify) |

## Papeis humanos (candidate groups)

> Nomes de grupo **PROPOSTOS** — `nucleo-ans`, `regulatorio-ans`, `medico-auditor` (quando
> assistencial), `juridico-regulatorio`. **Confirmar contra a taxonomia organizacional da
> operadora antes da promocao a FINAL** (ver §9 / open_questions).

| Grupo | Tarefa | Notas |
|---|---|---|
| `regulatorio-ans` / `nucleo-ans` | `UT_ElaborarRespostaNip` (autora a minuta da resposta) | origem do texto; humano sempre autora; candidateGroups DMN-decidido (`${grupo_humano}`, flat var promovida em `BRT_Roteamento` — GAP-NIP-2) |
| `juridico-regulatorio` | `UT_RevisaoJuridicaNip` (manter-negativa / casos complexos — **unica origem de `decisao_nip==MANTER_NEGATIVA`**) | gate adverso; campos obrigatorios; candidateGroups ESTATICO por design (multiplos incoming flows — GAP-NIP-2) |
| `medico-auditor` | parecer assistencial quando o merito da NIP e clinico (consulta dentro de `UT_RevisaoJuridicaNip` ou User Task dedicada) | so quando assistencial; auditor decide o merito clinico, nunca automacao |
| `coordenacao-regulatorio` (proposto) | `UT_CoordenacaoNip` (SLA estourado — coordenacao/juridico assume) | decisao continua humana |

Campos obrigatorios ao **manter a negativa** (`decisao_nip==MANTER_NEGATIVA`):
`fundamentacao_regulatoria` + `referencia_negativa_original`. Sem ambos, a User Task NAO
completa (validacao de formulario/listener) — manter negativa sem fundamentacao e impossivel.

## SLAs

Prazos NIP sao os **timers mais schedule-criticos da Phase 2** (descumprimento → sancao ANS).
Todos os prazos legais sao em **dias uteis**; ISO 8601 conta dias corridos. **Converter dias
uteis → ISO conservadoramente no worker** (nunca na DMN), respeitando feriados/calendario util.
Todos os valores abaixo sao **DRAFT/verify** com regulatorio/juridico antes de qualquer timer
em producao.

| Timer | Valor (ref) | Tipo | Fonte |
|---|---|---|---|
| Prazo NIP assistencial | ~P5D (5 dias **uteis**, ref) | boundary interruptivo (`timeDate` = `${sla.prazo_resposta_absoluto_iso}`) → `UT_CoordenacaoNip` | RN 388/2016 — **DRAFT/verify** |
| Prazo NIP nao-assistencial | ~P10D (10 dias **uteis**, ref) | boundary interruptivo (`timeDate` = `${sla.prazo_resposta_absoluto_iso}`) → `UT_CoordenacaoNip` | RN 388/2016 — **DRAFT/verify** |
| Alerta de risco de prazo | ~50-70% do prazo (`timeDate` = `${sla.sla_alerta_absoluto_iso}`) | nao-interruptivo → `operadora.nip.notify_deadline_risk` + `nip.deadline_risk` | politica interna |
| Espera de info adicional | event gateway (`msg.nip.info_recebida` vs `timeDate` = `${sla.sla_alerta_absoluto_iso}`) | timer → **sempre** `UT_RevisaoJuridicaNip` (incondicional; nunca elaboracao/coordenacao) | politica interna — **DRAFT/verify** |

Nota (GAP-NIP-1 — resolvido): o prazo conta de `data_recebimento_nip_iso` — **literalmente**: os
cinco boundary/intermediate timers (`BT_AlertaPrazoNip`, `BT_PrazoNipEstourado`,
`BT_AlertaPrazoRevisao`, `BT_PrazoRevisaoEstourado`, `ICE_PrazoInfo`) usam `timeDate` (deadline
ABSOLUTO, ISO 8601 datetime) computado por `BRT_NipSla` (DMN `nip_sla`) como
`data_recebimento_nip_iso` + duracao regulatoria — nunca `timeDuration` relativo ao instante em
que a User Task foi criada/anexada. Isto evita que o prazo "reinicie" a cada vez que o fluxo
alcanca uma nova User Task (apos o dossie de instrucao de Gustavo, ou apos uma rodada de
SOLICITAR_INFO). O timer interruptivo de prazo **substitui qualquer auto-resposta por timeout**
(INVERTIDO em relacao ao anti-padrao do reference: nunca auto-responde/auto-mantem por estouro de
prazo — escala a coordenacao/juridico humano).

Nota (GAP-NIP-5 — **rescoped**, ver #137): a redacao anterior deste contrato dizia que o timer de
"espera de info adicional" (`ICE_PrazoInfo`) volta a `elaboracao/coordenacao`, com o humano
decidindo o destino. O BPMN sempre roteou incondicionalmente `ICE_PrazoInfo` →
`UT_RevisaoJuridicaNip` (`Flow_PrazoInfo_Revisao`, sem `conditionExpression`) — divergencia
identificada como GAP-NIP-5. A investigacao de #137 (engine-instrumented, nao teorizada)
estabeleceu que **a rota incondicional do BPMN esta correta e o texto do contrato e que estava
desatualizado** — direcao de fix invertida em relacao ao plano original. Motivo (documentado em
`BRT_Roteamento` no BPMN): `UT_RevisaoJuridicaNip` tem TRES incoming flows; nos dois que NAO
partem diretamente de `GW_Roteamento` (pos-elaboracao via `GW_Minuta`, e a retomada via
`ICE_PrazoInfo`) as variaveis flat `roteamento`/`grupo_humano` ainda carregam o valor promovido na
rodada `ELABORAR_RESPOSTA` (`nucleo-ans`/`regulatorio-ans`) — usa-las para roteamento dinamico
naqueles dois pontos reatribuiria `UT_RevisaoJuridicaNip` a um grupo nao-juridico, violando o
invariante L0 "revisao sempre juridica" deste processo (§"Invariante L0 hard" acima:
`decisao_nip==MANTER_NEGATIVA` so pode nascer em `UT_RevisaoJuridicaNip`, com candidateGroups
ESTATICO `juridico-regulatorio,medico-auditor`). Por isso `UT_RevisaoJuridicaNip` mantem
candidateGroups estatico e as duas entradas nao-`GW_Roteamento` sao incondicionais — nunca voltam a
`elaboracao` nem a uma User Task de coordenacao separada. O BPMN nao foi alterado por este fix;
apenas a prosa do contrato foi corrigida para refletir o comportamento real e a justificativa L0.

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_NIP_NEGATIVA_NOT_HUMAN` | `operadora.nip.submit_response` | worker recusa transmitir/arquivar resposta com `decisao_nip==MANTER_NEGATIVA` sem decisao humana (sem `revisor_id`/grupo humano na cadeia); lanca BPMN error. **Guard L0 hard do negativa-like.** |
| `ERR_NIP_PROTOCOLO_INVALIDO` | `operadora.nip.handoff_ans_submit` (GAP-NIP-6, implementado) | `protocolo_ans` e **opcional** (`obrigatoria=nao` — correlacao com ANS-SUBMIT); AUSENTE (`None`) e legitimo e nunca lanca. Worker recusa (BPMN error) se `protocolo_ans` chegar **presente porem vazio/em branco** (ex.: `"   "`) — valor truthy em Python que quebraria a leitura direta `nip_protocolo_origem = protocolo_ans if isinstance(protocolo_ans, str) else ""` em `notification_bridge._ans_submit_variables_from_nip_handoff` (`src/maezo/platform/notification_bridge.py:298` — GAP-FAB-NOTIF fix: NAO existe `notifications_bridge/consumer.py`, e NAO ha fallback para `numero_nip_ans` nessa linha, ao contrario do que a versao anterior deste contrato afirmava), corrompendo a correlacao com SP-OP-ANS-SUBMIT-001. Capturado por tres boundary events (um por serviceTask de handoff — `ST_HandoffAnsManter`/`ST_HandoffAnsConceder`/`ST_HandoffAnsNaoAssistencial`, mesmo handler) roteados a um terminal neutro compartilhado `End_NipProtocoloInvalido` (fail-safe TECNICO, nao decisao de merito — `decisao_nip` ja foi fixada pela User Task humana antes deste handoff). |

## Pendencias para promocao a FINAL

DI (diagrama); **confirmacao dos prazos exatos RN 388 assistencial (~5 uteis) /
nao-assistencial (~10 uteis) e da conversao dias-uteis→ISO** com regulatorio/juridico (R1/R2
do phase2-plan §9 — exposicao a sancao); confirmacao do identificador estavel da business key
(`numero_nip_ans` vs `protocolo_ans`); confirmacao dos **nomes de candidate groups** contra a
taxonomia da operadora (`nucleo-ans`/`regulatorio-ans`/`juridico-regulatorio`/`medico-auditor`/
`coordenacao-regulatorio`); detalhamento do handoff `NIP → ANS-SUBMIT-001` (correlacao de
protocolo); politica de suspensao/recontagem de prazo durante `SOLICITAR_INFO`; anexos
obrigatorios por classificacao de NIP.
