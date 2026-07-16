# Contrato — SP-OP-REEMBOLSO-001 (Reembolso ao Beneficiario)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 · **Wave:** WC.2 · **BPMN (a autorar em wave posterior):** `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
**Gatilho regulatorio:** RN 259/2011 (garantia de atendimento / prazos — consolidacoes posteriores ANS: **DRAFT/verify**), Lei 9.656/1998 art. 12 (reembolso na livre escolha / fora da rede / urgencia-emergencia: **DRAFT/verify**), prazo de reembolso (~30 dias — **DRAFT/verify** RN vigente com regulatorio/juridico). **Negar/reduzir reembolso = `authorization_denial`-class, L0 hard (`_hard_frozen.yaml`).**

> Este contrato e o ponto de sincronizacao (§4-bis-F): o BPMN/DMN/agente de REEMBOLSO derivam dele. Toda mecanica no-denial de cinco partes esta materializada abaixo (invariante L0, rotas sem variante negar, DMN sem coluna de negativa, catch-all conservador para humano, worker-guard `ERR_*_NOT_HUMAN`, teste de invariante consultando o history do engine). **Clona o esqueleto provado SP-OP-AUTH-001.**

## Invariante L0 hard (nao negociavel)

A negativa (ou reducao) de reembolso ao beneficiario SO nasce nas User Tasks humanas
(`UT_AnaliseReembolso`, `UT_RevisaoAuditorMedico`, `UT_CoordenacaoReembolso`). **O DMN
CALCULA o valor de reembolso devido** (multiplo da tabela de referencia / teto contratual)
mas **nunca decide pagar ou negar** — quem decide e o humano. Nenhuma DMN deste processo
possui saida de negativa/reducao; cobertura ausente, documentacao incompleta, prazo
expirado ou inelegibilidade aparente roteiam para analise humana (linha catch-all
conservadora). Aprovacao automatica (L2, analoga a `auth_auto_approval`) existe apenas com
DMN de admissibilidade favoravel **E** valor dentro da tabela **E** dentro do teto do tenant
(ADR-0008) — e mesmo assim o caminho automatico produz **somente APROVACAO integral**,
jamais aprovacao parcial nem reducao (reducao = adverso = humano).

**Terminais humano-gated (so alcancaveis via User Task humana com decisao adversa):**
- `End_ReembolsoNegado` — negar reembolso. So via `UT_AnaliseReembolso` (ou
  `UT_RevisaoAuditorMedico` no merito clinico, ou `UT_CoordenacaoReembolso` no breach) com
  `decisao_reembolso == NEGAR`.
- `End_ReembolsoParcial` — aprovar valor reduzido (reembolso parcial e adverso: paga menos
  que o solicitado). So via User Task humana com `decisao_reembolso == APROVAR_PARCIAL`.

Terminais neutros (nao adversos, podem ser alcancados sem User Task): `End_ReembolsoAprovadoAutomatico`,
`End_ReembolsoAprovadoAnalista`, `End_ReembolsoProtocoloInvalido` (guard tecnico de validacao de
origem — `ERR_REEMBOLSO_INVALID_PROTOCOLO`; GAP-REEMBOLSO-2; NAO e decisao de pagar/negar).

**Worker-guard:** `operadora.reembolso.send_reembolso_denial` recusa
(`ERR_REEMBOLSO_DENIAL_NOT_HUMAN`) qualquer execucao sem `decisao_reembolso ∈ {NEGAR,
APROVAR_PARCIAL}` setado por um humano numa User Task; carrega `analista_id` (ou
`auditor_id`) para a cadeia de auditoria (ADR-0007).

## Business key (idempotencia)

```
REEMB-{tenant_id}-{protocolo_reembolso}
```

Alternativa por guia (quando o reembolso referencia uma guia TISS de livre escolha):
`REEMB-{tenant_id}-{numero_guia_tiss}`. Uma instancia por solicitacao de reembolso;
reenvio da mesma solicitacao retorna a instancia ativa (`mcp-cibseven.start_process`
consulta a business key antes de iniciar — start idempotente, sem duplicar adjudicacao).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `protocolo_reembolso` | string | sim | Protocolo da solicitacao de reembolso (chave de negocio) |
| `numero_guia_tiss` | string | nao | Guia TISS de livre escolha, quando aplicavel |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo do beneficiario (ADR-0006 — NUNCA CPF/nome) |
| `matricula_beneficiario` | string | sim | Matricula no plano (chave cadastral pseudonimizada) |
| `tipo_reembolso` | string | sim | `livre_escolha` \| `fora_rede` \| `urgencia_emergencia` \| `indisponibilidade_rede` |
| `codigo_procedimento_tuss` | string | sim | Procedimento TUSS objeto do reembolso |
| `categoria_procedimento` | string | sim | `consulta` \| `exame_simples` \| `exame_especial` \| `terapia` \| `internacao` \| `opme` \| `alta_complexidade` |
| `valor_solicitado_cents` | integer | sim | Valor solicitado pelo beneficiario, em centavos de BRL (inteiro — nunca `number`) |
| `data_atendimento` | date | sim | Data do atendimento/despesa |
| `data_solicitacao` | date | sim | Data da solicitacao de reembolso (base de contagem de prazo) |
| `cid10` | string | nao | CID-10 informado (relevante ao merito clinico) |
| `documentos_refs` | json | sim | Referencias dos comprovantes/nota fiscal/recibo TISS (pode ser vazio) |
| `cobertura_prevista` | boolean | sim | Pre-resolvido por worker (`operadora.reembolso.check_coverage`): procedimento coberto pelo contrato/segmentacao |
| `documentacao_completa` | boolean | sim | Pre-resolvido por worker: comprovantes minimos presentes |
| `dentro_prazo` | boolean | sim | Pre-resolvido por worker (`operadora.reembolso.check_prazo`): solicitacao dentro do prazo regulatorio/contratual |
| `beneficiario_ativo` | boolean | sim | Pre-resolvido por worker (cadastro): vinculo ativo na data do atendimento |
| `carencia_cumprida` | boolean | sim | Pre-resolvido por worker: carencia cumprida para a categoria |
| `dentro_tabela` | boolean | sim* | Pre-resolvido por worker (`operadora.reembolso.calculate_amount`): `valor_solicitado_cents <= valor_calculado_tabela_cents` (*antes de `BRT_AutoApproval`) |
| `dentro_teto_l2` | boolean | sim* | Pre-resolvido: `valor_calculado_tabela_cents <= teto de auto-aprovacao do tenant` (tenants-amh.yaml) |
| `requer_avaliacao_clinica` | boolean | sim* | Pre-resolvido: merito exige auditor medico (procedimento alta-complexidade/OPME, CID sensivel, divergencia de codificacao) |

\* usado a partir do `businessRuleTask` de auto-aprovacao; pode ser computado no fluxo.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_reembolso` | string | `APROVAR` \| `NEGAR` \| `APROVAR_PARCIAL` \| `SOLICITAR_INFO` (User Tasks humanas) |
| `valor_calculado_tabela_cents` | integer | Valor de reembolso devido calculado pela DMN (centavos BRL) — **calculo, nao decisao** |
| `valor_reembolso_aprovado_cents` | integer | Valor efetivamente aprovado (humano em APROVAR/APROVAR_PARCIAL; = `valor_calculado_tabela_cents` no caminho automatico) |
| `justificativa` | string | Obrigatoria se `NEGAR` ou `APROVAR_PARCIAL` |
| `fundamentacao_contratual` | string | Obrigatoria se `NEGAR` ou `APROVAR_PARCIAL` (clausula contratual / segmentacao) |
| `cid10_referencia` | string | Obrigatoria se NEGAR por merito clinico |
| `parecer_auditor` | string | Obrigatoria se a negativa/reducao decorre de `UT_RevisaoAuditorMedico` |
| `decisao_pendencia` | string | `cancelar_solicitacao` \| `conceder_prazo_extra` \| `seguir_analise` (pendencia expirada — humano decide) |
| `comprovante_pagamento_ref` | string | Referencia do pagamento emitido por `operadora.reembolso.issue_payment` (caminho CNAB/conciliacao) |

## Topicos

> Convencao `{dominio}.{contexto}.{acao}`. Kafka = fatos de dominio (`agents.events.reembolso.*`).
> External-task = `operadora.reembolso.*` (um topico por service task; todos a registrar em
> `config/topic_registry.yaml` — ver secao "Registro de topicos exigido" no fim).

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.reembolso.received` | produz | apos start (solicitacao de reembolso recebida) |
| Kafka | `agents.events.reembolso.pended` | produz | pendencia de documentacao aberta ao beneficiario |
| Kafka | `agents.events.reembolso.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.reembolso.completed` | produz | fim (payload.desfecho = `aprovado_automatico` \| `aprovado_analista` \| `negado_analista` \| `aprovado_parcial` \| `cancelado_pendencia`) |
| External task | `operadora.events.publish` | consome | publicador generico de eventos de dominio |
| External task | `operadora.reembolso.check_coverage` | consome | resolve `cobertura_prevista` (contrato/segmentacao) |
| External task | `operadora.reembolso.check_prazo` | consome | resolve `dentro_prazo` (calendario util → ISO conservador) |
| External task | `operadora.reembolso.calculate_amount` | consome | **CALCULA** `valor_calculado_tabela_cents`, `dentro_tabela`, `dentro_teto_l2` (aritmetica pura; sem decisao) |
| External task | `operadora.reembolso.analyze_request` | consome | convoca o agente analista (Marina-classe) para montar dossie de analise |
| External task | `operadora.reembolso.request_documents` | consome | pendencia de documentacao ao beneficiario (publica tambem `reembolso.pended`) |
| External task | `operadora.reembolso.issue_payment` | consome | emite o pagamento do reembolso aprovado (integra CNAB/conciliacao) |
| External task | `operadora.reembolso.send_reembolso_denial` | consome | **efeito adverso** — comunica negativa/reducao em nome do analista/auditor (worker-guard `ERR_REEMBOLSO_DENIAL_NOT_HUMAN`) |
| External task | `operadora.reembolso.notify_sla_risk` | consome | alerta coordenacao (timer nao-interruptivo) |
| Message BPMN | `msg.reembolso.docs_received` | recebe | correlacao por business key (`REEMB-{tenant}-{protocolo}`), destrava pendencia |

## DMN referenciadas

> Shape engine-deployavel (§4-bis-A): typeRef ∈ {string, boolean, integer, long, double, date}
> — **`"number"` e invalido**; dinheiro BRL → **inteiro-centavos (`integer`)** neste contrato
> (consistente com as variaveis `*_cents`); dias/SLA → **string ISO 8601**. Todo decisionTable
> tem `hitPolicy`; toda tabela tem linha catch-all → caminho humano conservador. **Nenhuma DMN
> tem coluna de saida de negativa/reducao.** `camunda:historyTimeToLive` namespaced (`P###D`)
> em cada `<decision>`.

### `reembolso_admissibility` (hitPolicy FIRST — DRAFT)
- **in:** `cobertura_prevista: boolean`, `documentacao_completa: boolean`, `dentro_prazo: boolean`, `beneficiario_ativo: boolean`, `carencia_cumprida: boolean`
- **out:** `roteamento: string` (`SEGUE_ANALISE` | `PENDENTE_DOCUMENTACAO` | `ANALISE_HUMANA`), `motivo: string`
- **Sem saida `NEGAR`/`REDUZIR` por design.** Cobertura ausente / fora de prazo / carencia / beneficiario inativo NAO produzem negativa: roteiam para `ANALISE_HUMANA` (so o humano nega). Catch-all (qualquer combinacao nao mapeada) → `ANALISE_HUMANA`.

### `reembolso_calculo` (hitPolicy UNIQUE/COLLECT — DRAFT) — **a DMN que CALCULA o valor**
- **in:** `tipo_reembolso: string`, `categoria_procedimento: string`, `codigo_procedimento_tuss: string`, `valor_solicitado_cents: integer`
- **out:** `valor_calculado_tabela_cents: integer` (centavos BRL — base: multiplo da tabela de referencia / limite contratual), `multiplo_tabela_aplicado: double`, `fonte_tabela: string`
- Esta DMN **calcula quanto seria devido** segundo tabela/teto; **nao decide pagar/negar** e **nao reduz** o pedido — apenas expressa o valor de referencia. A comparacao `valor_solicitado_cents` vs `valor_calculado_tabela_cents` alimenta `dentro_tabela`, mas a eventual reducao (`APROVAR_PARCIAL`) e decisao **humana**. Catch-all (procedimento sem tabela) → `valor_calculado_tabela_cents = 0` + `fonte_tabela = "SEM_TABELA"`, o que forca `dentro_tabela=false` → `ANALISE_HUMANA` (nunca auto-aprova fora de tabela).

### `reembolso_auto_approval` (hitPolicy FIRST — DRAFT)
- **in:** `dentro_tabela: boolean`, `dentro_teto_l2: boolean`, `requer_avaliacao_clinica: boolean`
- **out:** `recomendacao: string` (`AUTO_APROVAR` | `ANALISE_HUMANA`), `motivo: string`
- `AUTO_APROVAR` apenas com `dentro_tabela=true` E `dentro_teto_l2=true` E `requer_avaliacao_clinica=false`. **Sem saida de negativa/reducao.** Catch-all → `ANALISE_HUMANA`. O caminho `AUTO_APROVAR` produz **somente aprovacao integral** (`valor_reembolso_aprovado_cents = valor_calculado_tabela_cents` e, por construcao do gate `dentro_tabela`, `= valor_solicitado_cents`).

### `reembolso_sla` (hitPolicy UNIQUE — DRAFT, todos os prazos DRAFT/verify)
- **in:** `tipo_reembolso: string`, `categoria_procedimento: string`
- **out:** `sla_analise: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte_regulatoria: string`

## Papeis humanos (candidate groups)

> Nomes de grupo **PROPOSTOS** — confirmar contra a taxonomia organizacional do operador
> (ver Open Questions). Todo `userTask` carrega `camunda:candidateGroups`.

| Grupo (candidateGroups) | Papel | Tarefa |
|---|---|---|
| `analise-reembolso` | Analista de reembolso | `UT_AnaliseReembolso` (negativa/reducao SO aqui), `UT_DecidirPendenciaExpirada` |
| `medico-auditor` | Medico auditor | `UT_RevisaoAuditorMedico` (merito clinico — `requer_avaliacao_clinica=true` ou `SOLICITAR_AUDITOR`) |
| `coordenacao-reembolso` | Coordenacao de reembolso | `UT_CoordenacaoReembolso` (SLA de analise estourado — assume e decide) |

Campos obrigatorios por decisao adversa (validacao de formulario/listener da User Task —
sem eles a task NAO completa):
- `decisao_reembolso == NEGAR` → `justificativa` + `fundamentacao_contratual` + (se merito clinico) `cid10_referencia`/`parecer_auditor`.
- `decisao_reembolso == APROVAR_PARCIAL` → `justificativa` + `fundamentacao_contratual` + `valor_reembolso_aprovado_cents` (< `valor_solicitado_cents`).

`decisao_reembolso == SOLICITAR_INFO` → reexecuta `operadora.reembolso.request_documents`
e a instancia aguarda em `GW_AguardarDocs` (mesmo sub-fluxo de pendencia do AUTH-001).

## SLAs

> Todos os prazos sao **DRAFT/verify** com regulatorio/juridico antes de qualquer timer em
> producao. Prazos legais sao em dias uteis; ISO 8601 usa dias corridos — usar valores
> conservadores e resolver o calendario util no worker (`operadora.reembolso.check_prazo`).

| Timer | Valor (DRAFT) | Tipo | Fonte |
|---|---|---|---|
| Analise (padrao) | P30D | interruptivo → `UT_CoordenacaoReembolso` assume | prazo de reembolso ~30 dias — **DRAFT/verify** RN vigente / Lei 9.656 art. 12 |
| Analise (urgencia/emergencia) | P5D ou menor | idem | atendimento de urgencia — **DRAFT/verify** |
| Alerta de risco | 50–70% do SLA (DMN `sla_alerta`) | nao-interruptivo → `operadora.reembolso.notify_sla_risk` | politica interna |
| Pendencia de documentacao | P5D (ref AUTH-001) | event gateway → `UT_DecidirPendenciaExpirada` (humano decide destino) | **DRAFT/verify** (suspensao de prazo durante pendencia: confirmar regra RN) |
| Pagamento do reembolso aprovado | embutido no worker `issue_payment` (ciclo CNAB) | — | prazo de pagamento pos-aprovacao — **DRAFT/verify** |

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` | guard do worker `operadora.reembolso.send_reembolso_denial` | recusa se `decisao_reembolso ∉ {NEGAR, APROVAR_PARCIAL}` setado por humano **ou** se faltar `analista_id`/`auditor_id`. Materializa a invariante L0: nenhum efeito adverso de reembolso sem User Task humana na trilha (ADR-0007). Declarar como `Error_ReembolsoDenialNotHuman` para uso do worker |
| `ERR_REEMBOLSO_INVALID_PROTOCOLO` | declarado (`Error_ReembolsoProtocoloInvalido`) para uso dos workers | worker `check_coverage` lanca BPMN error se protocolo/guia inconsistente na origem (ex.: `protocolo_reembolso` ausente); capturado por boundary event `BE_ReembolsoProtocoloInvalido` em `ST_CheckCoverage` -> `End_ReembolsoProtocoloInvalido` (GAP-REEMBOLSO-2; fail-safe tecnico, NAO decisao de pagar/negar) |

## Reuso do sub-fluxo de pendencia de documentacao (AUTH-001)

O sub-fluxo de pendencia de documentacao e portado **estruturalmente** do AUTH-001 (nao
reimplementado): `operadora.reembolso.request_documents` abre a pendencia e publica
`reembolso.pended`; a instancia aguarda em `GW_AguardarDocs`; a message BPMN
`msg.reembolso.docs_received` (correlacionada por `REEMB-{tenant}-{protocolo}`) destrava e
reavalia `BRT_Admissibilidade`; no estouro do timer `ICE_PrazoPendencia` (ref `P5D`) cria-se
`UT_DecidirPendenciaExpirada` (`analise-reembolso`) — humano decide
`cancelar_solicitacao` | `conceder_prazo_extra` | `seguir_analise`. **A expiracao da
pendencia nunca auto-nega** (espelha AUTH-001 `UT_DecidirPendenciaExpirada`).

## Pendencias para promocao a FINAL

- DI (diagrama) do BPMN; corpo `.bpmn`/`.dmn` (wave de autoria posterior, contra este contrato).
- Confirmacao de **todos** os prazos com regulatorio/juridico (prazo de reembolso, urgencia, pendencia, pagamento) e a conversao dias-uteis→ISO.
- Confirmacao das fontes RN/Lei (RN 259, Lei 9.656 art. 12, RN de reembolso vigente — podem ter sido consolidadas/substituidas).
- Tabela de referencia de reembolso (multiplo/limite por categoria/segmentacao) e o teto de auto-aprovacao L2 do tenant (tenants-amh.yaml) — sign-off de regulatorio + atuarial.
- Confirmacao dos nomes de candidateGroups (`analise-reembolso`, `medico-auditor`, `coordenacao-reembolso`) contra a taxonomia organizacional do operador.
- Politica de suspensao de prazo em pendencia; anexos/comprovantes obrigatorios por `tipo_reembolso`.
- Integracao de pagamento (CNAB/conciliacao port) — semantica de `issue_payment` e correlacao do comprovante.

## Registro de topicos exigido (REPORTE — nao editar `topic_registry.yaml` aqui)

Adicionar a `config/topic_registry.yaml` (dono: W0.2) — Kafka `agents.events.reembolso.*`:
`received`, `pended`, `sla_breached`, `completed`. External-task `operadora.reembolso.*`:
`check_coverage`, `check_prazo`, `calculate_amount`, `analyze_request`, `request_documents`,
`issue_payment`, `send_reembolso_denial`, `notify_sla_risk`. Message BPMN
`msg.reembolso.docs_received`. (`operadora.events.publish` ja registrado.)

## Anti-pattern invertido (ADR-0011, reference READ-ONLY)

O `../maezo-reference` so possui um `doctor_reimbursement_summary_worker.py` (notificacao de
extrato ao prestador — **nao** e adjudicacao ao beneficiario) e stubs
`revenue_cycle.validate_procedure` ("validate procedure eligibility for reimbursement") com
`error_strategy: fail_safe`. Nenhum fluxo de reembolso ao beneficiario foi importado: este
processo e **greenfield**. O padrao a evitar (presente no reference glosa/recurso) e
auto-aplicar efeito adverso por timeout ou por regra — **INVERTIDO** aqui: a DMN so calcula e
roteia; toda negativa/reducao e User Task humana.
