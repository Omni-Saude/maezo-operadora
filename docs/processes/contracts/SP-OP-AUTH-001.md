# Contrato — SP-OP-AUTH-001 (Autorizacao Previa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 1 (modelado uma fase a frente) · **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
**Gatilho regulatorio:** RN 259/2011 (garantia de atendimento — consolidacoes posteriores ANS: **DRAFT/verify**), RN 395/2016 (resposta/negativa por escrito: **DRAFT/verify**), RN 424/2017 (junta medica: **DRAFT/verify**), Lei 9.656/1998 art. 35-C.

## Invariante L0 hard (nao negociavel)

Negativa de cobertura SO nasce nas User Tasks humanas (`UT_AnaliseMedicoAuditor`,
`UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta`). Nenhuma DMN deste processo possui
saida de negativa; inelegibilidade/carencia aparentes roteiam para analise humana.
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
| `valor_estimado_brl` | number | sim | Valor estimado |
| `cid10` | string | nao | CID-10 informado |
| `documentos_refs` | json | sim | Referencias de anexos TISS (pode ser vazio) |
| `requer_autorizacao` | boolean | sim | Pre-resolvido por worker (catalogo do tenant) |
| `documentacao_completa` | boolean | sim | Pre-resolvido por worker |
| `beneficiario_ativo` | boolean | sim | Pre-resolvido por worker (cadastro) |
| `carencia_cumprida` | boolean | sim | Pre-resolvido por worker (contagem de carencia) |
| `dut_atendida` | boolean | sim* | Pre-resolvido por worker DUT/ROL (*antes de `BRT_AutoApproval`) |
| `dentro_teto_l2` | boolean | sim* | Pre-resolvido: `valor_estimado_brl <= teto do tenant` (tenants-amh.yaml) |
| `rede_credenciada` | boolean | sim* | Pre-resolvido por worker |

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_auditor` | string | `APROVAR` \| `NEGAR` \| `SOLICITAR_INFO` \| `JUNTA_MEDICA` (User Tasks humanas) |
| `justificativa_clinica` | string | Obrigatoria se NEGAR |
| `cid10_referencia` | string | Obrigatoria se NEGAR |
| `fundamentacao_dut` | string | Obrigatoria se NEGAR |
| `decisao_pendencia` | string | `cancelar_guia` \| `conceder_prazo_extra` \| `seguir_analise` (pendencia expirada — humano) |
| `numero_autorizacao` | string | Emitida por `operadora.auth.issue_authorization` |

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

## Pendencias para promocao a FINAL

DI (diagrama); confirmacao de todos os prazos RN com regulatorio; detalhamento do fluxo
de junta (prazos/desempate RN 424); anexos TISS obrigatorios por categoria; politica de
suspensao de prazo em pendencia.
