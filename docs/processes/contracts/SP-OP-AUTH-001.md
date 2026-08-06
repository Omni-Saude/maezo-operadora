# Contrato — SP-OP-AUTH-001 (Autorizacao Previa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 1 (modelado uma fase a frente) · **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
**Gatilho regulatorio:** RN 259/2011 (garantia de atendimento — consolidacoes posteriores ANS: **DRAFT/verify**), RN 395/2016 (resposta/negativa por escrito: **DRAFT/verify**), RN 424/2017 (junta medica: **DRAFT/verify**), Lei 9.656/1998 art. 35-C.

## Invariante L0 hard (nao negociavel)

Negativa de cobertura SO nasce nas User Tasks humanas (`UT_AnaliseMedicoAuditor`,
`UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta`); a decisao carrega `auditor_id` para a
cadeia de auditoria (ADR-0007) e e essa a proveniencia humana que o guard de
`operadora.auth.send_denial_notice` exige (`ERR_DENIAL_NOT_HUMAN`). Nenhuma DMN deste processo
possui saida de negativa; inelegibilidade/carencia aparentes roteiam para analise humana.
Aprovacao automatica (L2) existe apenas com DMN favoravel + teto do tenant (ADR-0008).

## Business key (idempotencia)

```
AUTH-{tenant_id}-{numero_guia_tiss}
```

Uma instancia por guia TISS; reenvio retorna a instancia ativa.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant |
| `numero_guia_tiss` | string | sim | Numero da guia TISS (chave de negocio) |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo (ADR-0006) |
| `prestador_id` | string | sim | Prestador solicitante |
| `codigo_procedimento_tuss` | string | sim | Procedimento TUSS |
| `categoria_procedimento` | string | sim | `consulta` \| `exame_simples` \| `exame_especial` \| `terapia` \| `internacao` \| `opme` \| `alta_complexidade` |
| `carater_atendimento` | string | sim | `urgencia` \| `eletivo` |
| `valor_estimado_brl` | number | sim | Valor estimado. Consumido por `AnalyzeRequestWorker` (computa `dentro_teto_l2` na perna humana) e — desde a mitigacao GAP-AUTH-4 — pelo guard de teto de `issue_authorization` no canal AUTOMATICO (ausente/nao-numerico/negativo => recusa emitir). Continua **semeado no start e nao verificado** (GAP-AUTH-4) |
| `cid10` | string | nao | CID-10 informado |
| `documentos_refs` | json | sim | Referencias de anexos TISS (pode ser vazio) |
| `requer_autorizacao` | boolean | sim | Pre-resolvido por worker (catalogo do tenant) |
| `documentacao_completa` | boolean | sim | Pre-resolvido por worker |
| `beneficiario_ativo` | boolean | sim | Pre-resolvido por worker (cadastro) |
| `carencia_cumprida` | boolean | sim | Pre-resolvido por worker (contagem de carencia) |
| `dut_atendida` | boolean | sim* | **GAP-AUTH-4: SEMEADO NO START, nao verificado.** Nenhum worker o computa antes de `BRT_AutoApproval` |
| `dentro_teto_l2` | boolean | sim* | **GAP-AUTH-4: SEMEADO NO START, nao verificado.** `CeilingResolver`/`within_l2_ceiling` NAO roda nesta rota — o teto (`tenants-amh.yaml`) e DECORATIVO na aprovacao automatica; decidir o D-07 NAO fecha isto |
| `rede_credenciada` | boolean | sim* | **GAP-AUTH-4: SEMEADO NO START, nao verificado.** Nenhum worker o computa antes de `BRT_AutoApproval` |

> **GAP-AUTH-4 (aberto, registrado 2026-08-05).** Na rota automatica os tres fatos acima chegam do
> payload de start e a DMN `auth_auto_approval` decide `AUTO_APROVAR` sobre eles sem verificacao.
> Contraste: SP-OP-REEMBOLSO-001 computa o teto ANTES da sua BRT (GAP-REEMBOLSO-5). Remedio =
> worker determinístico antes de `BRT_AutoApproval` (classe MZO-040 — portão Médico/ANS).
> **A promoção deste contrato de DRAFT para FINAL está vinculada ao fechamento deste gap.**
>
> **Mitigação parcial (2026-08-05) — o teto passou a ser LOAD-BEARING no ponto de emissão, o gap
> continua ABERTO.** `operadora.auth.issue_authorization` agora verifica
> `authorization_approval.max_value_brl` (via `CeilingResolver.within_l2_ceiling`, a MESMA chamada
> que `AnalyzeRequestWorker` faz em `ST_PrepararDossie`) **antes de emitir, exclusivamente no canal
> AUTOMÁTICO** (sanção do DMN sem decisão humana), e recusa emitir quando o teto não autoriza —
> `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`, fail-closed em tenant ausente / `valor_estimado_brl`
> ausente-inválido-negativo / resolver indisponível. **O canal humano NÃO é afetado:** um
> `decisao_auditor == 'APROVAR'` (e a rota da junta) emite independentemente do teto — exceder o
> teto automático é exatamente para o que a análise humana existe. Isto **não fecha** o
> GAP-AUTH-4: `BRT_AutoApproval` continua decidindo sobre `dut_atendida`/`dentro_teto_l2`/
> `rede_credenciada` semeados no start, e o próprio `valor_estimado_brl` comparado vem do mesmo
> payload não verificado. **Consequência observável HOJE** (`max_value_brl: 0`, estado D-07): a
> rota automática alcança `End_AprovadaAutomatica` e publica `desfecho=aprovada_automatica`
> **sem emitir `numero_autorizacao`** — o mesmo desfecho observável de antes, agora por um motivo
> principiado, logado e auditável (o teto não autoriza) em vez de um descasamento acidental de
> tipagem. Essa inconsistência residual — o processo anuncia uma aprovação que não emitiu — É o
> GAP-AUTH-4 e permanece aberta para o portão Médico/ANS. Decidido o D-07 com um teto real, a
> emissão automática passa a funcionar, limitada por esse teto.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_auditor` | string | `APROVAR` \| `NEGAR` \| `SOLICITAR_INFO` \| `JUNTA_MEDICA` (User Tasks humanas) |
| `justificativa_clinica` | string | Obrigatoria se NEGAR |
| `cid10_referencia` | string | Obrigatoria se NEGAR |
| `fundamentacao_dut` | string | Obrigatoria se NEGAR |
| `auditor_id` | string | Id do medico auditor humano que setou `decisao_auditor` (cadeia de auditoria ADR-0007). **Obrigatoria se NEGAR** — e a proveniencia humana que o guard de `operadora.auth.send_denial_notice` consome (`ERR_DENIAL_NOT_HUMAN`). Declarada como `camunda:formField` nas tres User Tasks humanas (`UT_AnaliseMedicoAuditor`, `UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta`); em `UT_RegistrarParecerJunta` e o medico relator do parecer. Espelha `analista_id`/`auditor_id` de SP-OP-RECURSO-001 e SP-OP-REEMBOLSO-001 |
| `decisao_pendencia` | string | `cancelar_guia` \| `conceder_prazo_extra` \| `seguir_analise` (pendencia expirada — humano) |
| `numero_autorizacao` | string | Emitida por `operadora.auth.issue_authorization`. **Ausente** quando o guard de teto recusa a emissao automatica (ver `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`) |
| `dentro_teto_l2` | boolean | Fato COMPUTADO escrito de volta pelos workers que o resolvem: `analyze_request` (sempre) e `issue_authorization` **so no canal automatico** (nunca na perna humana — la o teto nao e' consultado e escreve-lo seria fabricar uma verificacao) |
| `motivo_bloqueio_teto` | string | Token limitado (`TENANT_AUSENTE` \| `VALOR_AUSENTE_OU_INVALIDO` \| `TETO_NAO_AUTORIZA` \| `RESOLVER_INDISPONIVEL`), escrito SO na recusa de teto de `issue_authorization` — evidencia engine-visivel de QUAL vetor fail-closed disparou |

## Topicos

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.auth.received` | produz | apos start |
| Kafka | `agents.events.auth.pended` | produz | pendencia de documentacao aberta |
| Kafka | `agents.events.auth.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.auth.completed` | produz | fim (payload.desfecho = `aprovada_automatica` \| `aprovada_auditor` \| `negada_auditor` \| `nao_requer_autorizacao` \| `cancelada_pendencia`) |
| External task | `operadora.events.publish` | consome | publicador generico |
| External task | `operadora.auth.analyze_request` | consome | convoca Rafael: dossie de analise (ja registrado) |
| External task | `operadora.auth.request_documents` | consome | pendencia ao prestador |
| External task | `operadora.auth.issue_authorization` | consome | emite autorizacao (TISS) |
| External task | `operadora.auth.send_denial_notice` | consome | negativa formal por escrito (em nome do auditor) |
| External task | `operadora.auth.notify_sla_risk` | consome | alerta coordenacao (timer nao-interruptivo) |
| External task | `operadora.auth.convene_junta` | consome | convoca junta medica (RN 424 — DRAFT) |
| Message BPMN | `msg.auth.docs_received` | recebe | correlacao por business key, destrava pendencia |

## DMN referenciadas

### `auth_admissibility` (FIRST — DRAFT)
in: `requer_autorizacao: boolean`, `documentacao_completa: boolean`, `beneficiario_ativo: boolean`, `carencia_cumprida: boolean`
out: `resultado: string` (`NAO_REQUER` | `PENDENTE_DOCUMENTACAO` | `SEGUE_ANALISE`), `motivo: string`
Sem saida de negativa por design.

### `auth_auto_approval` (FIRST — DRAFT)
in: `dut_atendida: boolean`, `dentro_teto_l2: boolean`, `rede_credenciada: boolean`, `carater_atendimento: string`
out: `recomendacao: string` (`AUTO_APROVAR` | `ANALISE_HUMANA`), `motivo: string`
Catch-all = `ANALISE_HUMANA`.

### `auth_sla` (FIRST — DRAFT, todos os prazos DRAFT/verify)
in: `carater_atendimento: string`, `categoria_procedimento: string`
out: `sla_analise: string (ISO)`, `sla_alerta: string (ISO)`, `fonte_regulatoria: string`

## Papeis humanos

| Grupo | Tarefa |
|---|---|
| `medico-auditor` | `UT_AnaliseMedicoAuditor` (negativa SO aqui), `UT_DecidirPendenciaExpirada` |
| `coordenacao-auditoria-medica` | `UT_CoordenacaoAssume` (SLA estourado) |
| `junta-medica` | `UT_RegistrarParecerJunta` (RN 424 — DRAFT) |

## SLAs

| Timer | Valor | Tipo | Fonte |
|---|---|---|---|
| Analise (urgencia) | PT2H | interruptivo -> coordenacao assume | Lei 9.656 art. 35-C ("imediato") — **DRAFT/verify** |
| Analise (eletivo alta complexidade/OPME/internacao) | P10D | idem | RN 259 (21 dias uteis garantia) — **DRAFT/verify** |
| Analise (eletivo padrao) | P5D | idem | RN 395/2016 (5 dias uteis) — **DRAFT/verify** |
| Alerta de risco | 50–70% do SLA (DMN `sla_alerta`) | nao-interruptivo -> notify_sla_risk | politica interna |
| Pendencia de documentacao | P5D | event gateway -> `UT_DecidirPendenciaExpirada` (humano decide destino) | **DRAFT/verify** (suspensao de prazo durante pendencia: confirmar regra RN) |
| Negativa por escrito | embutido no worker `send_denial_notice` | — | RN 395 art. 10 (24h) — **DRAFT/verify** |

Nota: prazos legais sao em dias uteis; ISO 8601 usa dias corridos — valores conservadores. Resolver calendario util no worker.

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_AUTH_INVALID_GUIA` | declarado (`Error_AuthGuiaInvalida`) para uso dos workers | worker lanca BPMN error se guia inconsistente na origem; tratamento a detalhar na promocao a FINAL |
| `ERR_AUTH_DENIAL_INCOMPLETE` | guard do worker `operadora.auth.send_denial_notice` | recusa transmitir uma NEGAR sem `justificativa_clinica` + `cid10_referencia` + `fundamentacao_dut` (RN 395 art. 10). Lanca BPMN error (`Error_AuthDenialIncompleta`), capturado por `BE_NegativaIncompleta` -> `End_FundamentacaoIncompletaBloqueada` (terminal NEUTRO: nada foi enviado) |
| `ERR_DENIAL_NOT_HUMAN` | guard do worker `operadora.auth.send_denial_notice` | recusa transmitir uma NEGAR sem proveniencia humana: o sinal explicito `human_approved` **ou** um `auditor_id` nao-vazio setado pela User Task humana. Materializa a invariante L0: nenhuma negativa sem User Task humana na trilha (ADR-0007). NAO ha boundary modelado para este codigo — o worker retorna registro `blocked_by_guard`, nao lanca |
| `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED` | guard do worker `operadora.auth.issue_authorization`, **canal AUTOMATICO apenas** (`ST_EmitirAutorizacaoAuto`) | recusa EMITIR quando o teto de autonomia do tenant (`authorization_approval.max_value_brl`, resolvido por `CeilingResolver.within_l2_ceiling`) nao autoriza o `valor_estimado_brl`. Fail-closed em todos os vetores (tenant ausente/branco/nao-string; valor ausente/nao-numerico/bool/negativo/nao-finito; resolver indisponivel). Distinto de `ERR_DENIAL_NOT_HUMAN` de proposito: aquele significa "sem sancao modelada", este significa "sancionado pela DMN, mas o teto nao autoriza emissao AUTOMATICA". **O canal HUMANO nunca e' gateado por este codigo** (`decisao_auditor == 'APROVAR'` e a rota da junta emitem independentemente do teto). NAO ha boundary modelado em `ST_EmitirAutorizacaoAuto` (o unico boundary do arquivo e' `BE_NegativaIncompleta`), entao o worker RETORNA registro `blocked_by_guard` com `dentro_teto_l2=false` + `motivo_bloqueio_teto` como evidencia engine-visivel — nunca lanca (um `bpmnError` nao modelado encerra silenciosamente o escopo, ADR-0030) |

## Pendencias para promocao a FINAL

DI (diagrama); confirmacao de todos os prazos RN com regulatorio; detalhamento do fluxo
de junta (prazos/desempate RN 424); anexos TISS obrigatorios por categoria; politica de
suspensao de prazo em pendencia.
