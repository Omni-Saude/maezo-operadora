# Contrato — SP-OP-CONTAS-001 (Processamento de Contas / Glosa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 · **Wave:** WA.1 · **BPMN (alvo, autorado em wave posterior):** `src/maezo/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
**Gatilho regulatorio:** Padrao TISS / Componente de Comunicacao (RN 305/2012 e consolidacoes — **DRAFT/verify**); prazos de analise/recurso de conta (RN 424/2017 — **DRAFT/verify**); Lei 9.656/1998. Glosa = operadora negando/reduzindo pagamento de linha de conta ao prestador → `authorization_denial`-adjacent, **L0 hard** (embora o envelope clerical de roteamento seja `standard_glosa_processing`, L2).
**Inverte o anti-padrao:** `../maezo-reference/.archive/bpmn/glosa_management.bpmn` (que identifica→classifica→**auto-aplica** glosa via `Task_UpdatePaymentNotEligible`/`EndEvent_GlosaAccepted` e tem `Task_AutoApprove` no timeout de 48h). Em Maezo a DMN apenas **sinaliza candidata e roteia**; nenhum caminho automatizado **confirma** glosa substantiva. Ver §"Notas de design / inversao do reference".

## Invariante L0 hard (nao negociavel)

A aceitacao de uma glosa **substantiva** contra o prestador (efeito adverso `authorization_denial`-class)
SO nasce na User Task humana `UT_AnalistaContas` (grupo `auditoria-contas`), via
`decisao_contas == ACEITAR_GLOSA`. Nenhuma DMN deste processo possui coluna de saida que
confirme/aceite glosa. A DMN `glosa_triage` so produz `{SEM_GLOSA, RECORRER, ANALISE_HUMANA}` —
**nao existe variante ACEITAR/CONFIRMAR em branch automatizado**. Glosa candidata ambigua,
glosa tecnica/clinica, indicio de fraude, inelegibilidade aparente e estouro de SLA **todos
fail-safe para uma User Task humana** (catch-all → `ANALISE_HUMANA`). O efeito adverso e
materializado apenas pelo worker `operadora.contas.register_glosa_accept`, guardado por
`ERR_GLOSA_ACCEPT_NOT_HUMAN`. O terminal `End_GlosaAceitaHumano` so e alcancavel apos
`UT_AnalistaContas` concluida por humano.

Indicio de fraude (`fraud_accusation`, **L0 hard**) NUNCA e auto-sinalizado: roteia para User
Task `auditoria-contas` que decide encaminhar a SP-OP-FRAUDE-001 (Phase 3). Nenhum branch
auto-flagueia fraude.

## Business key (idempotencia)

```
CONTAS-{tenant_id}-{numero_lote_tiss}
```

Alternativa por adjudicacao por guia (quando a operadora analisa conta-a-conta):
`CONTAS-{tenant_id}-{numero_guia_tiss}-{numero_conta}`. Uma instancia por lote (ou por
conta/guia); reenvio do mesmo lote/conta retorna a instancia ativa. `mcp-cibseven.start_process`
DEVE consultar a business key antes de iniciar (start idempotente, sem reprocessar o lote).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_lote_tiss` | string | sim | Numero do lote TISS (chave de negocio) |
| `numero_guia_tiss` | string | nao | Guia (quando adjudicacao por guia) |
| `numero_conta` | string | nao | Conta (quando adjudicacao por guia/conta) |
| `prestador_id` | string | sim | Prestador cujo pagamento e analisado |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo (ADR-0006 — nunca CPF/nome) |
| `competencia` | string | sim | Competencia da conta (`YYYY-MM`) |
| `data_recebimento_lote` | string (date ISO `YYYY-MM-DD`) | sim | Recebimento do demonstrativo (**ancora dos prazos** — GAP-CONTAS-4: consumida pela DMN `contas_sla` p/ computar os deadlines ABSOLUTOS dos timers `timeDate`; o worker `identify_glosa` a normaliza/defaulta fail-safe p/ HOJE/UTC quando ausente/invalida, com warning) |
| `valor_apresentado_brl` | double | sim | Valor total apresentado pelo prestador (BRL; double — **nunca `number`**) |
| `tipo_lote` | string | sim | `consulta` \| `sadt` \| `internacao` \| `honorario` \| `opme` \| `misto` |
| `linhas_conta_refs` | json | sim | Linhas de conta TISS (itens do demonstrativo). Forma canonica por item: `valor_apresentado_centavos` (int) ou `valor_apresentado_brl`; `valor_glosado_centavos`/`valor_glosado_brl` (opc); `valor_pago_centavos`/`valor_pago_brl` (opc — glosado = apresentado−pago quando glosado ausente); `reason_codes_tiss` (lista) ou `reason_code_tiss` (opc). **Fonte dos fatos computados** (GAP-CONTAS-2); ausente/vazio/malformado ⇒ fail-closed conservador (roteia a humano, nunca auto-clear) |
| `reason_codes_tiss` | json | sim | Codigos de motivo de glosa TISS sinalizados no demonstrativo (entrada da normalizacao) |
| `item_conforme_tabela` | boolean | sim* | Pre-resolvido por worker: item bate com tabela contratada/TUSS |
| `documentacao_anexa` | boolean | sim* | Pre-resolvido por worker: anexos TISS presentes para o item |
| `indicio_fraude_sinalizado` | boolean | nao | Sinal **informativo** de worker de regras (NUNCA decide; so roteia a humano) |

\* Pre-resolvido por worker de fatos antes de `BRT_TriagemGlosa` (aritmetica/conferencia; **sem decisao adversa**).

**Fatos COMPUTADOS (GAP-CONTAS-2 — nunca seeded/ecoados):** `has_glosas` (boolean; `identify_glosa`),
`denial_ratio` (double 0..1), `divergencia_valor` (boolean), `glosa_count` (integer) e
`total_glosado_candidato_centavos` (integer/long — centavos; **Long acima de R$ 21,47M**, landmine
int32) sao computados por `identify_glosa`/`calculate_impact` a partir de
`linhas_conta_refs`/`reason_codes_tiss`/`valor_apresentado_brl` (aritmetica pura em
centavos-inteiros; thresholds de RULE vivem nas DMN `glosa_*`). Definicoes: linha glosada candidata
= `valor_glosado > 0` OU reason code na linha; `has_glosas` = alguma linha candidata OU
`reason_codes_tiss` (lote) nao-vazio; `denial_ratio` = total_glosado/total_apresentado (clamp 0..1);
`divergencia_valor` = total_glosado > 0 OU soma(linhas) ≠ `valor_apresentado_brl` declarado.
**FAIL-CLOSED:** sem detalhe de linha ⇒ `has_glosas=true`, `divergencia_valor=true`,
`denial_ratio=1.0` (a row `r_sem_glosa` da `glosa_triage` exige `divergencia_valor=false` — o lote
sem detalhe NUNCA alcanca `End_SemGlosa` automatico).

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_contas` | string | `RECORRER` \| `ACEITAR_GLOSA` \| `REENVIAR` (preenchida SO por `UT_AnalistaContas` humana) |
| `justificativa_glosa` | string | **Obrigatoria se `ACEITAR_GLOSA`** — fundamentacao da aceitacao da glosa |
| `codigo_glosa_aceito` | string | **Obrigatoria se `ACEITAR_GLOSA`** — reason code/categoria normalizada aceita |
| `valor_glosa_aceito_brl` | double | **Obrigatoria se `ACEITAR_GLOSA`** — valor aceito como glosado (BRL; double) |
| `analista_id` | string | Aprovador humano (cadeia de auditoria ADR-0007); carregado no worker do efeito adverso |
| `decisao_coordenacao` | string | `assumir_analise` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA — humano `coordenacao-contas`) |
| `encaminhar_fraude` | boolean | Preenchida por `UT_AnalistaContas`/`auditoria-contas`: encaminhar a SP-OP-FRAUDE-001 (Phase 3) |
| `glosa_id` | string | Identificador da glosa confirmada (handoff para SP-OP-RECURSO-001 quando `RECORRER`) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2 e o unico editor**; este contrato apenas declara o que precisa ser registrado).

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.contas.received` | produz | apos start (lote/demonstrativo recebido) |
| Kafka | `agents.events.contas.glosa_identified` | produz | glosas candidatas sinalizadas (payload `total_glosado_candidato_brl`, `glosa_count`) |
| Kafka | `agents.events.contas.sla_breached` | produz | SLA de triagem/analise estourado |
| Kafka | `agents.events.contas.completed` | produz | fim (payload.desfecho = `sem_glosa` \| `encaminhada_recurso` \| `glosa_aceita_humano` \| `reenviada`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso) |
| External task | `operadora.contas.identify_glosa` | consome (worker) | identifica linhas glosadas candidatas no demonstrativo (porta de `identify_glosa_worker`; **TASY write DROP** — consumimos CDC, nunca escrevemos, MEMORY/ADR-0013) |
| External task | `operadora.contas.analyze_reason` | consome (worker) | fatos do motivo (worker); narrativa do dossie → Marina (LLM) |
| External task | `operadora.contas.calculate_impact` | consome (worker) | aritmetica pura de impacto (`denial_ratio`, `divergencia_valor`; BRL em double/centavos-inteiros — **nunca `number`**) |
| External task | `operadora.contas.prepare_triage_dossier` | consome (worker→A2A) | convoca Marina: monta dossie de triagem (delegacao `glosa.analyze`; espelha AUTH→Rafael) |
| External task | `operadora.contas.notify_sla_risk` | consome (worker) | alerta `coordenacao-contas` (timer nao-interruptivo) |
| External task | `operadora.contas.register_glosa_accept` | consome (worker) | **efeito adverso gated** — registra aceitacao da glosa; recusa sem decisao humana (`ERR_GLOSA_ACCEPT_NOT_HUMAN`); carrega `analista_id` |
| External task | `operadora.contas.start_recurso` | consome (worker) | **handoff** — inicia SP-OP-RECURSO-001 quando `RECORRER` (passa `glosa_id`, `numero_guia_tiss`, `glosa_type`, `glosa_existe=true` — fato confirmado por CONTAS —, `documentacao_anexa`; GAP-XPROC-3, consumido por `notifications_bridge.recurso_variables`) |
| External task | `operadora.contas.reconcile_payment` | consome (worker) | concilia/registra reenvio quando `REENVIAR` (sem efeito adverso) |
| Message BPMN | `msg.contas.linhas_atualizadas` | recebe | correlacao por business key — reenvio/correcao de linhas chegou, destrava reavaliacao |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; BRL → `double` (ou inteiro-centavos); dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador. `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`. **Nenhuma DMN tem coluna de saida de negativa/aceite de glosa (negativa-like).**

### `glosa_reason_normalization` (hitPolicy FIRST — DRAFT)
in: `reason_code_tiss: string`
out: `categoria_normalizada: string` (ex.: `tecnica` | `administrativa` | `clinica` | `valor` | `documental` | `desconhecida`), `descricao: string`
Catch-all → `categoria_normalizada = "desconhecida"` (rota conservadora a humano a jusante). Mapa reason_code TISS → categoria; **DRAFT/verify** contra tabela de glosa TISS vigente.

### `glosa_classification` (hitPolicy FIRST — DRAFT)
in: `categoria_normalizada: string`, `denial_ratio: double` (fracao do item glosada, 0..1)
out: `glosa_type: string` (ex.: `parcial` | `total` | `valor` | `tecnica`), `glosa_extent: string` (ex.: `linha` | `conta` | `lote`)
**Apenas classifica** — sem saida que confirme/aceite. `denial_ratio` calculado por worker (aritmetica), nunca decide aceite.

### `glosa_triage` (hitPolicy FIRST — DRAFT; coracao negativa-like)
in: `tipo_item: string`, `categoria_normalizada: string`, `item_conforme_tabela: boolean`, `divergencia_valor: boolean`, `documentacao_anexa: boolean`
out: `roteamento: string` (`SEM_GLOSA` | `RECORRER` | `ANALISE_HUMANA`), `motivo: string`
**Sem saida `ACEITAR`/`CONFIRMAR`** por design. Catch-all (ultima row) → `ANALISE_HUMANA` (qualquer ambiguidade, glosa tecnica/clinica ou inelegibilidade aparente cai aqui — nunca em aceite). Glosa tecnica/clinica → `ANALISE_HUMANA` (R4: auto-route de glosa puramente formatacional **so** com sign-off de compliance; default conservador = humano). `SEM_GLOSA` (row 1) exige `categoria_normalizada` **NAO** `tecnica`/`clinica` (GAP-CONTAS-3): a row original ignorava `categoria_normalizada` e, sob `hitPolicy=FIRST`, preemptava a row de glosa tecnica/clinica sempre que `item_conforme_tabela=true` + `divergencia_valor=false` + `documentacao_anexa=true` — corrigido para so casar `SEM_GLOSA` em categorias fora de `{tecnica, clinica}`.

### `contas_sla` (hitPolicy FIRST — DRAFT v0.2.0; todos os prazos DRAFT/verify)
in: `tipo_lote: string`, `valor_apresentado_brl: double`, `data_recebimento_lote: string` (ancora — declarada p/ documentar a dependencia; nao participa do matching)
out: `sla_analise: string` (ISO 8601, duracao relativa — legado/observabilidade), `sla_analise_absoluto_iso: string` (deadline ABSOLUTO = `data_recebimento_lote` + duracao, FEEL `string(date and time(... + "T00:00:00") + duration(...))` — consumido pelo `timeDate` de `BT_SlaTriagem`), `sla_alerta: string` (ISO, relativa — legado), `sla_alerta_absoluto_iso: string` (alerta ABSOLUTO — `timeDate` de `BT_AlertaSlaContas`), `fonte_regulatoria: string`
Prazos como string ISO; converter dias uteis→ISO conservadoramente no worker. GAP-CONTAS-4: os
timers ancoram no RECEBIMENTO do lote (contrato regulatorio), nunca na criacao da User Task —
espelha `nip_sla` v0.2.0 (GAP-NIP-1). Ancora+prazo no passado (lote antigo reprocessado) dispara o
interruptivo imediatamente — correto (prazo de fato estourado; coordenacao humana assume).

## Papeis humanos (candidate groups)

| Grupo | Papel | Tarefa |
|---|---|---|
| `auditoria-contas` | Analista de contas medicas / auditoria de contas | `UT_AnalistaContas` (**aceite de glosa SO aqui**: `decisao_contas ∈ {RECORRER, ACEITAR_GLOSA, REENVIAR}`); tambem decide `encaminhar_fraude` no branch de indicio de fraude |
| `coordenacao-contas` | Coordenacao de faturamento/contas | `UT_CoordenacaoContasAssume` (SLA de triagem estourado — assume a analise; decisao continua humana) |

**Nota de taxonomia (OQ — ver Pendencias):** `auditoria-contas` e `coordenacao-contas` sao nomes **PROPOSTOS**; precisam confirmacao contra a taxonomia organizacional da operadora (o §3.1 do plano cita variantes `analista-contas-medicas` / `coordenacao-faturamento`). Manter como candidate groups DRAFT ate sign-off.

## SLAs

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaContas`) | `timeDate` = `${sla.sla_alerta_absoluto_iso}` (= `data_recebimento_lote` + duracao de alerta; DMN `contas_sla` v0.2.0 — GAP-CONTAS-4) | nao-interruptivo → `operadora.contas.notify_sla_risk` | politica interna |
| Triagem/analise (`BT_SlaTriagem`) | `timeDate` = `${sla.sla_analise_absoluto_iso}` (= `data_recebimento_lote` + **P30D** tipico — **DRAFT/verify** RN 424/contrato) | interruptivo → publica `contas.sla_breached` → cancela `UT_AnalistaContas`, cria `UT_CoordenacaoContasAssume` | RN 424/2017 (recurso/analise de conta) — **DRAFT/verify** |

Os dois timers ancoram no **recebimento do lote** (`data_recebimento_lote` — a ancora do contrato),
NUNCA no attach da User Task (GAP-CONTAS-4 resolvido; o attach so acontece apos identify/impact +
4 DMNs + dossie de Marina, e pode se repetir na reentrada por `msg.contas.linhas_atualizadas`).

Nota: prazos legais sao em dias uteis; ISO 8601 usa dias corridos — usar valores conservadores e resolver calendario util no worker. **Substitui o `Task_AutoApprove`/timeout de 48h do reference** — no estouro de SLA a coordenacao humana assume; **nunca** ha auto-passagem/auto-aceite por timeout (inversao do anti-padrao).

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_GLOSA_ACCEPT_NOT_HUMAN` | guard do worker `operadora.contas.register_glosa_accept` | o worker **recusa** registrar aceite de glosa se `decisao_contas != ACEITAR_GLOSA` setado por humano, ou se faltar `justificativa_glosa`/`codigo_glosa_aceito`/`valor_glosa_aceito_brl`/`analista_id`. Lanca BPMN error; instancia nao atinge `End_GlosaAceitaHumano`. (espelha `ERR_*_NOT_HUMAN` de AUTH/RECURSO) |
| `ERR_CONTAS_LOTE_INVALIDO` | declarado (`Error_ContasLoteInvalido`) para uso dos workers | worker lanca BPMN error se o lote/demonstrativo for inconsistente na origem; tratamento a detalhar na promocao a FINAL |

## Notas de design / inversao do reference

- **Anti-padrao invertido** (`glosa_management.bpmn`): no reference `Task_CheckEligibility` →
  `Gateway_EligibleForAppeal`; se **nao elegivel** → `Task_UpdatePaymentNotEligible`
  (`appealSuccessful=false`) → `EndEvent_GlosaAccepted` **automaticamente, sem humano**; e
  `Task_AutoApprove` aprova o recurso em 48h por timeout. Em Maezo: (1) a elegibilidade vira
  `glosa_triage` (DMN sem aceite); (2) "nao elegivel/ambiguo" → `ANALISE_HUMANA`
  (`UT_AnalistaContas`), nunca aceite automatico; (3) o estouro de SLA leva a coordenacao
  humana (`UT_CoordenacaoContasAssume`), nao a auto-passagem; (4) o aceite e materializado so
  pelo worker gated `register_glosa_accept`.
- **Terminais:** `End_GlosaAceitaHumano` (humano-gated, unico adverso); `End_SemGlosa`,
  `End_EncaminhadaRecurso`, `End_Reenviada` sao neutros. Nenhum fim ocorre sem evento de dominio
  publicado antes (auditoria dupla engine+Kafka, ADR-0007).
- **Marina (PHI zone)** e convocada via `operadora.contas.prepare_triage_dossier` para montar o
  dossie; **instrui, nao decide** — o analista/auditor humano decide na User Task (principio Rafael).
- **TASY write DROP** em todo worker (`identify_glosa`, `reconcile_payment`): consumimos o CDC do
  amh-data-platform, nunca escrevemos no Tasy (MEMORY/ADR-0013).

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e o BPMN/DMN bodies (autorados em wave posterior contra este contrato).
- Confirmacao de **todos** os prazos RN com regulatorio/juridico (SLA de triagem/analise; RN 424
  e consolidacoes; conversao dias uteis→ISO).
- Confirmacao da citacao de RN 305/2012 (TISS/glosa) contra texto vigente ANS (pode ter sido
  consolidada/substituida) — **DRAFT/verify**.
- **Confirmacao dos candidate groups** `auditoria-contas` / `coordenacao-contas` contra a
  taxonomia organizacional da operadora (OQ).
- Sign-off de compliance sobre a **excecao de glosa tecnica auto-route** (R4): default e humano;
  qualquer auto-route de glosa formatacional pura exige decisao registrada de compliance.
- Politica de indicio de fraude: confirmar que nenhum branch auto-flagueia (`fraud_accusation`
  L0 hard) e o handoff a SP-OP-FRAUDE-001 (Phase 3).
- Anexos TISS obrigatorios por `tipo_lote`; mapa completo reason_code TISS → categoria
  (`glosa_reason_normalization`).
