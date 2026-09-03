# Contrato — SP-OP-CONTAS-001 (Análise e Adjudicação de Conta Médica / Glosa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 · **Wave:** WA.1 · **BPMN (alvo, autorado em wave posterior):** `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`
**Gatilho regulatorio:** Padrao TISS — fluxo lote de guias / analise / demonstrativo de analise de conta (**RN 501/2022**, que revogou a RN 305/2012 — **DRAFT/verify**, `docs/compliance/rn-currency-review.md:39,48,83); **prazo contratual** de analise de conta (**DRAFT/verify** — ADR-0040 OQ-2); Lei 9.656/1998 art. 18 (relacao operadora↔prestador credenciado, **DRAFT/verify** com juridico). A citacao anterior a RN 424/2017 foi **RETIRADA**: aquela norma rege junta medica/odontologica e estava MISATRIBUIDA aqui (`rn-currency-review.md:45,85-87,190-193`). Glosa = a operadora **nega ou reduz** o pagamento de linha de conta ao prestador → `authorization_denial`-adjacent, **L0 hard** (embora o envelope clerical de roteamento seja `standard_glosa_processing`, L2).
**Inverte o anti-padrao:** `../maezo-reference/.archive/bpmn/glosa_management.bpmn` (que identifica→classifica→**auto-aplica** glosa via `Task_UpdatePaymentNotEligible`/`EndEvent_GlosaAccepted` e tem `Task_AutoApprove` no timeout de 48h). Em Maezo a DMN apenas **sinaliza candidata e roteia**; nenhum caminho automatizado **glosa**. Ver §"Notas de design / inversao do reference".

**Inversao de PERSPECTIVA (ADR-0040, Proposed — não ratificada):** além do anti-padrão de
auto-aceite, este processo invertia o **ATOR**. A versão doada modelava a conferência que o
**faturamento do prestador** faz de um demonstrativo *recebido*: a sua única User Task decisória
tinha as três saídas de quem **sofre** a glosa. Aqui o dono é a **operadora**, que recebe o lote de
guias transmitido pelo prestador, **adjudica** a conta apresentada e **emite** o demonstrativo de
análise. A operadora não contesta, não aceita e não reapresenta a própria glosa: ela a aplica, e
responde por ela (a resposta ao recurso do prestador é SP-OP-RECURSO-001).

## Invariante L0 hard (nao negociavel)

A **aplicacao** de uma glosa **substantiva** contra o prestador (efeito adverso
`authorization_denial`-class) SO nasce na User Task humana `UT_AnalistaContas` (grupo
`auditoria-contas`) ou `UT_CoordenacaoContasAssume` (grupo `coordenacao-contas`, no estouro de
SLA), via `decisao_contas ∈ {GLOSAR, PAGAR_PARCIAL}`. Nenhuma DMN deste processo possui coluna de
saida que glose. A DMN `glosa_triage` so produz `{PAGAR, ANALISE_HUMANA}` — **nao existe variante
GLOSAR em branch automatizado**, e por isso a parte 2 de ADR-0018 e trivialmente verificavel aqui:
o dominio nao contem nenhum valor adverso. Glosa candidata ambigua, glosa tecnica/clinica,
divergencia de valor, indicio de fraude e estouro de SLA **todos fail-safe para uma User Task
humana** (catch-all → `ANALISE_HUMANA`). O efeito adverso e materializado apenas pelo worker
`operadora.contas.registrar_glosa`, guardado por `ERR_CONTAS_GLOSA_NOT_HUMAN`. Os terminais
`End_GlosaAplicadaHumano` e `End_PagamentoParcialHumano` so sao alcancaveis apos User Task humana
concluida.

O **default** de `GW_DecisaoContas` NAO e uma acao: e `End_ErrContasDecisaoInvalida`
(`ERR_CONTAS_DECISAO_INVALIDA`). Uma decisao ausente ou fora do dominio declarado produz um **erro
visivel** e NENHUM efeito — nem glosa, nem demonstrativo, nem ordem de pagamento.

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
| `data_recebimento_lote` | string (date ISO `YYYY-MM-DD`) | sim | Data em que a operadora **recebeu** o lote do prestador (**ancora contratual do prazo de analise** — GAP-CONTAS-4: consumida pela DMN `contas_sla` p/ computar os deadlines ABSOLUTOS dos timers `timeDate`; o worker `identify_glosa` a normaliza/defaulta fail-safe p/ HOJE/UTC quando ausente/invalida, com warning) |
| `valor_apresentado_brl` | double | sim | Valor total apresentado pelo prestador (BRL; double — **nunca `number`**) |
| `tipo_lote` | string | sim | `consulta` \| `sadt` \| `internacao` \| `honorario` \| `opme` \| `misto` |
| `linhas_conta_refs` | json | sim | Linhas de conta TISS (itens da conta **apresentada pelo prestador**). Forma canonica por item: `valor_apresentado_centavos` (int) ou `valor_apresentado_brl`; `valor_glosado_centavos`/`valor_glosado_brl` (opc); `valor_pago_centavos`/`valor_pago_brl` (opc — glosado = apresentado−pago quando glosado ausente); `reason_codes_tiss` (lista) ou `reason_code_tiss` (opc). **Fonte dos fatos computados** (GAP-CONTAS-2); ausente/vazio/malformado ⇒ fail-closed conservador (roteia a humano, nunca auto-clear) |
| `reason_codes_tiss` | json | sim | Codigos TISS de motivo de glosa **apurados pela analise de contas da operadora** sobre a conta apresentada (entrada da normalizacao; podem vir pre-computados pelo processamento de contas a montante — **nunca de terceiro**) |
| `item_conforme_tabela` | boolean | sim* | Pre-resolvido por worker: item bate com tabela contratada/TUSS |
| `documentacao_anexa` | boolean | sim* | Pre-resolvido por worker: anexos TISS presentes para o item |
| `indicio_fraude_sinalizado` | boolean | nao | Sinal **informativo** de worker de regras (NUNCA decide; so roteia a humano) |
| `data_vencimento` | string (date ISO `YYYY-MM-DD`) | sim* | Vencimento da obrigacao de pagamento, vindo do lote/termo contratual. **Obrigatoria em SP-OP-PAGTO-001** (`SP-OP-PAGTO-001.md`, "sim") e o handoff a **RECUSA em branco** — um vencimento inventado e um prazo falso. Origem contratual **DRAFT/verify** (ADR-0040 OQ-2). **Produzida no runtime pelo INTAKE** (`operadora.contas.identify_glosa` / `ST_ApurarDivergencias`, a primeira tarefa de TODO caminho, inclusive na reentrada por `BME_LinhasAtualizadas`): ecoada **verbatim** (so `strip`) do lote/termo contratual e escrita de volta como variavel de processo, com **warning** quando ausente — nunca defaultada. Assim a falta do dado contratual fica visivel na trilha ja na primeira tarefa, em vez de so aparecer quatro tarefas adiante como um handoff recusado. ⚠️ **LIMITE DECLARADO do fail-closed (VER-PR4 MINOR-A):** o ponto que RECUSA continua sendo `operadora.contas.handoff_pagamento`, que fica **a jusante** de `ST_EmitirDemonstrativoIntegral`/`ST_EmitirDemonstrativoAprovado`/`ST_EmitirDemonstrativoParcial` e, na perna `PAGAR_PARCIAL`, tambem de `ST_RegistrarGlosaParcial`. Numa conta sem vencimento a operadora portanto **ja emitiu o demonstrativo ao prestador — e, no parcial, ja registrou o efeito adverso — quando a ordem de pagamento e recusada**: o prestador recebe a comunicacao de uma adjudicacao cuja ordem nunca nasce, e a instancia para num incidente. O intake torna a ausencia VISIVEL (warning na primeira tarefa) mas nao a bloqueia. A alavanca conservadora — **validar `data_vencimento` no proprio intake e rotear a `ANALISE_HUMANA` em vez de seguir para a perna automatica**, de modo que nenhum artefato ao prestador seja emitido antes de a conta ter um vencimento — e uma **decisao do dono, em aberto sob OQ-2**: ela muda o roteamento de um caso hoje automatico e depende de saber de que clausula contratual o vencimento vem. Registrada aqui, nao tomada |
| `conta_origem_ref` | string | nao | Referencia da conta de origem; **ecoada verbatim** ao handoff de pagamento e deixada em branco quando o lote nao a carrega (nunca preenchida com um valor plausivel) |
| `instrumento_pagamento` | string | nao | Instrumento de pagamento; mesma regra de eco verbatim de `conta_origem_ref` |

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
`denial_ratio=1.0` (a row permissiva da `glosa_triage` exige `divergencia_valor=false` — o lote
sem detalhe NUNCA alcanca `End_ContaAprovadaIntegral` automatico).

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_contas` | string | `PAGAR` \| `GLOSAR` \| `PAGAR_PARCIAL` \| `DEVOLVER` \| `ENCAMINHAR_FRAUDE` (preenchida SO por `UT_AnalistaContas` / `UT_CoordenacaoContasAssume` humanas) |
| `justificativa_glosa` | string | **Obrigatoria se `GLOSAR` ou `PAGAR_PARCIAL`** — fundamentacao da glosa aplicada |
| `codigo_glosa_tiss` | string | **Obrigatoria se `GLOSAR` ou `PAGAR_PARCIAL`** — codigo TISS de motivo de glosa apurado (**DRAFT/verify** contra a tabela vigente, OQ-5) |
| `valor_glosado_brl` | double | **Obrigatoria se `GLOSAR` ou `PAGAR_PARCIAL`** (`> 0`) — valor glosado (BRL; double) |
| `valor_liberado_brl` | double | **Obrigatoria se `PAGAR_PARCIAL`** (`> 0`; um parcial que libera R$ 0,00 e, materialmente, um `GLOSAR` integral — `registrar_glosa` recusa, e sem essa recusa a instancia registrava o efeito adverso, emitia o demonstrativo de "pagamento parcial" e depois travava no handoff, que ja recusa `<= 0`) e se `PAGAR` — valor liberado ao prestador; e a fonte do valor no handoff a SP-OP-PAGTO-001 (`fonte_valor=liberado`). Uma reducao declara as **duas** metades, de modo que o demonstrativo feche `apresentado = liberado + glosa` |
| `justificativa_devolucao` | string | **Obrigatoria se `DEVOLVER`** — motivo que o prestador precisa para corrigir a conta |
| `motivo_devolucao` | string | Codigo do motivo da devolucao (**DRAFT/verify** — OQ-1: existe artefato TISS proprio para devolucao?) |
| `analista_id` | string | Decisor humano (cadeia de auditoria ADR-0007); carregado no worker do efeito adverso e semeado como `lastro_decisor_id` no handoff de pagamento |
| `decisao_coordenacao` | string | `assumir_analise` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA — humano `coordenacao-contas`) |
| `glosa_id` | string | Identificador da glosa **aplicada pela operadora**, cunhado DETERMINISTICAMENTE por `registrar_glosa`; publicado em `contas.completed` e impresso no demonstrativo. E a chave que o prestador cita ao interpor recurso (entrada de SP-OP-RECURSO-001) |
| `ordem_pagamento_id` | string | Identificador deterministico da ordem que o handoff cunha em SP-OP-PAGTO-001 |
| `protocolo_demonstrativo` | string | Protocolo **sintetico e deterministico** da comunicacao emitida ao prestador (TASY write DROP, ADR-0013 — nao e numeracao de sistema externo; **DRAFT/verify** OQ-1) |

**Variavel removida:** `encaminhar_fraude` (boolean). A decisao passou a ser um valor de
`decisao_contas` (`ENCAMINHAR_FRAUDE`), que e o que o BPMN sempre fez; a booleana era redundancia
documental.

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2 e o unico editor**; este contrato apenas declara o que precisa ser registrado).

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.contas.received` | produz | apos start (lote de guias recebido do prestador) |
| Kafka | `agents.events.contas.glosa_identified` | produz | glosas candidatas sinalizadas (payload `total_glosado_candidato_brl`, `glosa_count`) |
| Kafka | `agents.events.contas.sla_breached` | produz | SLA de analise da conta estourado |
| Kafka | `agents.events.contas.completed` | produz | fim (payload.desfecho = `pagar_integral` \| `pagamento_aprovado_humano` \| `glosa_aplicada_humano` \| `pagamento_parcial_humano` \| `conta_devolvida_humano` \| `encaminhada_fraude`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso) |
| External task | `operadora.contas.identify_glosa` | consome (worker) | apura divergencias e linhas de glosa candidatas na conta **apresentada pelo prestador** (porta de `identify_glosa_worker`; **TASY write DROP** — consumimos CDC, nunca escrevemos, MEMORY/ADR-0013) |
| External task | `operadora.contas.analyze_reason` | consome (worker) | fatos do motivo (worker); narrativa do dossie → Marina (LLM) |
| External task | `operadora.contas.calculate_impact` | consome (worker) | aritmetica pura de impacto (`denial_ratio`, `divergencia_valor`; BRL em double/centavos-inteiros — **nunca `number`**) |
| External task | `operadora.contas.prepare_triage_dossier` | consome (worker→A2A) | convoca Marina: monta dossie de triagem (delegacao `glosa.analyze`; espelha AUTH→Rafael) |
| External task | `operadora.contas.notify_sla_risk` | consome (worker) | alerta `coordenacao-contas` (timer nao-interruptivo) |
| External task | `operadora.contas.registrar_glosa` | consome (worker) | **efeito adverso gated** — **aplica** a glosa; recusa sem decisao humana (`ERR_CONTAS_GLOSA_NOT_HUMAN`); carrega `analista_id`; cunha `glosa_id` deterministico. Serve `ST_RegistrarGlosa` e `ST_RegistrarGlosaParcial` |
| External task | `operadora.contas.emitir_demonstrativo` | consome (worker) | **comunicacao ao prestador** — emite o demonstrativo de analise da conta (TISS; **DRAFT/verify** OQ-1). Serve as **5** tarefas comunicantes, discriminado por `tipo_comunicacao ∈ {demonstrativo_analise, devolucao_para_correcao}` (`camunda:inputParameter` do elemento chamador, **nunca inferido**); um tipo nao declarado e recusado, jamais defaultado. Protocolo sintetico DETERMINISTICO (business key + tipo); **TASY write DROP** |
| External task | `operadora.contas.devolver_conta` | consome (worker) | registra a devolucao da conta para correcao quando `DEVOLVER` (**nao e glosa e nao e adjudicacao**; exige `justificativa_devolucao` + `analista_id`, fail-closed). Se a devolucao e ou nao adverso e **OQ-7** |
| External task | `operadora.contas.handoff_pagamento` | consome (worker) | **handoff** — inicia SP-OP-PAGTO-001 quando `PAGAR`/`PAGAR_PARCIAL` ou nas duas pernas automaticas. Business key `PAGTO-{tenant_id}-{numero_lote_tiss}-{prestador_id}`. **I-PAGTO-1 (ADR-0040): NUNCA semeia `lastro_confirmado`/`dados_pagamento_validos`/`duplicidade_suspeita`/`dentro_teto_l2`** — semeia `lastro_origem`/`lastro_decisor_id` (EVIDENCIA). `fonte_valor` (`apresentado`\|`liberado`) e declarado pelo elemento chamador. Recusa fail-closed valor ausente/em branco/nao-numerico/`<= 0` e `data_vencimento` em branco. `lastro_origem` e **validado contra o enum fechado** `{contas_adjudicacao_automatica, contas_adjudicacao_humana}` e os dois pares contraditorios sao **recusados** (humana sem `analista_id`; automatica com `analista_id`): o campo alimenta o formulario de `UT_AnaliseAdmissibilidade` em SP-OP-PAGTO-001 — e o que o revisor humano le como proveniencia — logo um rotulo desconhecido, o da cadeia RECURSO ou texto livre seria evidencia forjada. Mesma regra da metade RECURSO (`recurso.py`), que fixa a sua constante e recusa decisor em branco |
| Message BPMN | `msg.contas.linhas_atualizadas` | recebe | correlacao por business key — o prestador reapresentou linhas corrigidas; destrava a reavaliacao na MESMA instancia |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; BRL → `double` (ou inteiro-centavos); dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador. `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`. **Nenhuma DMN tem coluna de saida que glose ou libere pagamento (negativa-like).**

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
out: `roteamento: string` (`PAGAR` | `ANALISE_HUMANA`), `motivo: string`
**Sem saida que glose** por design (ADR-0040): na perspectiva do pagador o adverso **e** `GLOSAR`, portanto ele nao pode existir como valor de saida de DMN. Catch-all (ultima row) → `ANALISE_HUMANA` (qualquer ambiguidade, glosa tecnica/clinica ou divergencia de valor cai aqui). Glosa tecnica/clinica → `ANALISE_HUMANA` (R4: auto-route de glosa puramente formatacional **so** com sign-off de compliance; default conservador = humano). `PAGAR` (row 1) exige `categoria_normalizada` **NAO** `tecnica`/`clinica` (GAP-CONTAS-3): a row original ignorava `categoria_normalizada` e, sob `hitPolicy=FIRST`, preemptava a row de glosa tecnica/clinica sempre que `item_conforme_tabela=true` + `divergencia_valor=false` + `documentacao_anexa=true`. O conjunto NEGATIVO ainda admite `desconhecida` — **achado M-4, aberto** (`glosa-triage-shadow-candidate.yaml`, `docs/review-queue.md`); fecha-lo e ato do dono da tabela (ADR-0028 §7), registrado em **ADR-0040 OQ-10** junto com a variante conservadora pre-especificada.

### `contas_sla` (hitPolicy FIRST — DRAFT v0.2.0; todos os prazos DRAFT/verify)
in: `tipo_lote: string`, `valor_apresentado_brl: double`, `data_recebimento_lote: string` (ancora — a data em que a **operadora recebeu** o lote; declarada p/ documentar a dependencia, nao participa do matching)
out: `sla_analise: string` (ISO 8601, duracao relativa — legado/observabilidade), `sla_analise_absoluto_iso: string` (deadline ABSOLUTO = `data_recebimento_lote` + duracao, FEEL `string(date and time(... + "T00:00:00") + duration(...))` — consumido pelo `timeDate` de `BT_SlaAnaliseContas`), `sla_alerta: string` (ISO, relativa — legado), `sla_alerta_absoluto_iso: string` (alerta ABSOLUTO — `timeDate` de `BT_AlertaSlaContas`), `fonte_regulatoria: string`
`fonte_regulatoria` passa a citar **prazo contratual de analise de conta + RN 501/2022** (fluxo TISS) — **DRAFT/verify**. **Nenhum valor numerico mudou**: `P30D`/`P20D`/`P18D` inalterados; so a fonte declarada deixou de mentir.
Prazos como string ISO; converter dias uteis→ISO conservadoramente no worker. GAP-CONTAS-4: os
timers ancoram no RECEBIMENTO do lote pela operadora (ancora contratual), nunca na criacao da User Task —
espelha `nip_sla` v0.2.0 (GAP-NIP-1). Ancora+prazo no passado (lote antigo reprocessado) dispara o
interruptivo imediatamente — correto (prazo de fato estourado; coordenacao humana assume).

## Papeis humanos (candidate groups)

| Grupo | Papel | Tarefa |
|---|---|---|
| `auditoria-contas` | Analista de contas medicas / auditoria de contas | `UT_AnalistaContas` (**a glosa SO nasce aqui**: `decisao_contas ∈ {PAGAR, GLOSAR, PAGAR_PARCIAL, DEVOLVER, ENCAMINHAR_FRAUDE}`) |
| `coordenacao-contas` | Coordenacao de analise/auditoria de contas | `UT_CoordenacaoContasAssume` (SLA de analise estourado — assume a analise; **segundo canal humano do mesmo guard**, mesmo dominio de `decisao_contas`) |

**Nota de taxonomia (OQ-6 — ver Pendencias):** `auditoria-contas` e `coordenacao-contas` sao nomes **PROPOSTOS**; precisam confirmacao contra a taxonomia organizacional da operadora. `faturamento` NAO e uma variante aceitavel aqui: e a funcao de **cobranca do prestador**; a operadora tem analise/auditoria de contas. Manter como candidate groups DRAFT ate sign-off.

## SLAs

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaContas`) | `timeDate` = `${sla.sla_alerta_absoluto_iso}` (= `data_recebimento_lote` + duracao de alerta; DMN `contas_sla` v0.2.0 — GAP-CONTAS-4) | nao-interruptivo → `operadora.contas.notify_sla_risk` | politica interna |
| Analise da conta (`BT_SlaAnaliseContas`) | `timeDate` = `${sla.sla_analise_absoluto_iso}` (= `data_recebimento_lote` + **P30D** tipico — **DRAFT/verify**) | interruptivo → publica `contas.sla_breached` → cancela `UT_AnalistaContas`, cria `UT_CoordenacaoContasAssume` | **prazo contratual de analise de conta** + RN 501/2022 (fluxo TISS) — **DRAFT/verify** (`docs/compliance/rn-currency-review.md:188-193`) |

Os dois timers ancoram no **recebimento do lote** (`data_recebimento_lote` — a ancora do contrato),
NUNCA no attach da User Task (GAP-CONTAS-4 resolvido; o attach so acontece apos identify/impact +
4 DMNs + dossie de Marina, e pode se repetir na reentrada por `msg.contas.linhas_atualizadas`).

Nota: prazos legais sao em dias uteis; ISO 8601 usa dias corridos — usar valores conservadores e resolver calendario util no worker. **Substitui o `Task_AutoApprove`/timeout de 48h do reference** — no estouro de SLA a coordenacao humana assume; **nunca** ha desfecho automatico por timeout (inversao do anti-padrao).

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_CONTAS_GLOSA_NOT_HUMAN` | guard do worker `operadora.contas.registrar_glosa` | o worker **recusa** aplicar a glosa se `decisao_contas` nao estiver em `{GLOSAR, PAGAR_PARCIAL}` setado por humano, se faltar `justificativa_glosa`/`codigo_glosa_tiss`/`analista_id`, se `valor_glosado_brl <= 0`, ou se `PAGAR_PARCIAL` vier com `valor_liberado_brl <= 0` (ausente, zero ou negativo — `> 0`, coerente com `:96` e com o codigo). Levantado como `PermissionError` → **incidente auditado** (ADR-0030 §5), **nunca** `bpmnError`; reconhecido por `harness.is_guard_refusal_code` pelo sufixo `_NOT_HUMAN`. Instancia nao atinge `End_GlosaAplicadaHumano`/`End_PagamentoParcialHumano`. Substitui o guard anterior deste processo, que protegia o *aceite* de uma glosa alheia: mesma mecanica, objeto invertido |
| `ERR_CONTAS_DECISAO_INVALIDA` | throw-end modelado (`End_ErrContasDecisaoInvalida`) | default fail-closed de `GW_DecisaoContas`: decisao humana ausente ou fora do dominio. **NAO** e boundary catch — fora do escopo de `scripts/ci/check_bpmn_error_allowlist.py`, igual a `End_ErrDecisaoInvalida` de SP-OP-AUTH-001. O caso **decisao AUSENTE** so e alcancavel porque `decisao_contas` e inicializada com `${""}` em `ST_PublishReceived`: o CIB Seven 2.1.0 avalia as `conditionExpression` ANTES do `default` e lanca `Cannot resolve identifier` (HTTP 500 no complete da User Task) para uma variavel nunca setada, deixando a instancia PARADA na UT em vez de terminar no erro visivel |
| `ERR_CONTAS_LOTE_INVALIDO` | catalogo do BPMN (`Error_ContasLoteInvalido`), **declarado-e-nao-capturado** | o lote transmitido pelo prestador chega inconsistente na origem (campo obrigatorio de `GlosaInput` ausente/vazio — `tenant_id`, `numero_lote_tiss`). Levantado como `ContasLoteInvalidoError`, subclasse de **`ValueError`** (`_build_glosa_input` traduz o `TypeError` de construcao), logo percorre o ramo `except ValueError` do harness → `failure(retries=0)` → **incidente auditado**, **nunca** `bpmnError`. **Tratamento RESOLVIDO, nao pendente:** e exatamente o que ADR-0030 §1 prescreve para entrada invalida/imutavel (*«bad/immutable input as `ValueError` → incident»*). NAO ha — e nao deve haver — `boundaryEvent`/`errorEventDefinition` para este codigo |

### Por que dois codigos do catalogo nao tem `errorEventDefinition` (nao sao entradas mortas)

`Error_ContasLoteInvalido` e `Error_ContasGlosaNotHuman` sao declarados na raiz do BPMN e **nenhum**
`errorEventDefinition` os referencia; so `Error_ContasDecisaoInvalida` e referenciado, e ainda assim
por um **throw-end** (`EED_ContasDecisaoInvalida` em `End_ErrContasDecisaoInvalida`), nao por um
boundary catch. Isso e **deliberado e ratificado**, nao um resto de modelagem:

- **Fonte da verdade do gate e o boundary, nao o catalogo.** ADR-0030 §2: o conjunto de codigos
  legais de `WorkerBpmnError` deriva de *«every `bpmn:error@errorCode` **on an error boundary event
  attached to an external task**»*. Uma entrada de catalogo sem boundary e, por construcao, invisivel
  para `scripts/ci/check_bpmn_error_allowlist.py` — ela nao entra em `spec_codes`, logo nao aciona a
  clausula (c) (dead model) nem a clausula (b) (raise nao coberto).
- **`declared-uncaught` e um estado nomeado pelo ADR.** ADR-0030 §5: *«a guard code with **no**
  modeled boundary (e.g. auth's `ERR_DENIAL_NOT_HUMAN` …; cancel's `ERR_CANCELLATION_NOT_HUMAN`,
  declared-uncaught) → **incident**, unchanged. The L0 invariant holds identically either way —
  neither path performs the adverse action.»*
- **`ERR_CONTAS_GLOSA_NOT_HUMAN` e nomeado no proprio ADR-0030.** A emenda de ADR-0040 diz que ele
  *«remain[s] Tier-3 **declared-and-uncaught** (no `bpmn:boundaryEvent`/`errorEventDefinition` models
  the guarded service task …), keep[s] raising `PermissionError` on the audited-incident path (never
  `WorkerBpmnError`/`bpmnError`), and enable[s] no new production allowlist entry»*.
- **Modelar o boundary seria REGRESSAO de visibilidade, nao melhoria.** ADR-0030 §4: ativar um codigo
  de guard antes de T-E *«converts today's **guaranteed-human-visible incident** into a **clean,
  silent end** at the neutral terminal … no incident, no audit row, no notification»*. Enquanto T-E
  nao habilitar a allowlist de producao, o incidente e o desfecho MAIS protetivo — e o unico que a
  invariante HITL de nao-negativa tolera aqui.
- **Nao e anomalia de CONTAS.** 20 das 43 declaracoes `bpmn:error` de raiz em `spec/processes/bpmn/**`
  (13 dos 16 arquivos) nao sao referenciadas por nenhum `errorEventDefinition` — e a convencao do
  repositorio, e CONTAS a segue. Remover as duas de CONTAS a dessincronizaria dos pares e tornaria
  falsa a descricao que o ADR-0030 faz dela pelo nome.

Invariante fixada em teste (para que a leitura «entrada morta» nao seja re-derivada e executada):
`tests/unit/spec/test_sp_op_contas_001_artefatos.py::test_catalogo_de_erros_e_declarado_e_nao_capturado`
e `tests/unit/tools/workers/test_contas.py::test_contas_nunca_levanta_worker_bpmn_error`.

**Erros de worker sem elemento BPMN** (fail-closed → incidente auditado, nunca `bpmnError`):
`ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO` (ancora de business key, valor ou `data_vencimento`
invalidos no handoff), `ERR_CONTAS_COMUNICACAO_INVALIDA` (`tipo_comunicacao` nao declarado),
`ERR_CONTAS_DEVOLUCAO_INVALIDA` (devolucao sem motivo ou sem autor), `ERR_CONTAS_FRAUDE_SEM_ALVO`
(inalterado). CONTAS continua com **zero** error-boundaries sobre external task — exatamente o
estado que o censo do proprio ADR-0030 registra.

## Notas de design / inversao do reference

- **Anti-padrao invertido** (`glosa_management.bpmn`): no reference `Task_CheckEligibility` →
  `Gateway_EligibleForAppeal`; se **nao elegivel** → `Task_UpdatePaymentNotEligible`
  (`appealSuccessful=false`) → `EndEvent_GlosaAccepted` **automaticamente, sem humano**; e
  `Task_AutoApprove` aprova o recurso em 48h por timeout. Em Maezo: (1) a elegibilidade vira
  `glosa_triage` (DMN sem saida que glose); (2) "nao elegivel/ambiguo" → `ANALISE_HUMANA`
  (`UT_AnalistaContas`), nunca desfecho adverso automatico; (3) o estouro de SLA leva a coordenacao
  humana (`UT_CoordenacaoContasAssume`), nao a auto-passagem; (4) a glosa e materializada so
  pelo worker gated `registrar_glosa`.
- **Terminais:** `End_GlosaAplicadaHumano` e `End_PagamentoParcialHumano` (humano-gated,
  **adversos**); `End_ContaAprovadaIntegral`, `End_ContaAprovadaHumano`,
  `End_ContaDevolvidaPrestador` e `End_EncaminhadaFraude` sao neutros/L1;
  `End_ErrContasDecisaoInvalida` e **tecnico** (erro). Nenhum fim **de negocio** ocorre sem evento
  de dominio publicado antes (auditoria dupla engine+Kafka, ADR-0007); o terminal tecnico segue a
  mesma excecao de `End_ErrDecisaoInvalida` de AUTH.
- **Comunicacao ao prestador em todo terminal que o afeta (ADR-0040 M6):** **cinco dos seis**
  terminais de negocio emitem por `operadora.contas.emitir_demonstrativo`, discriminados por
  `tipo_comunicacao`. A ordem canonica de todo terminal e
  `registrar/decidir → comunicar ao prestador → handoff de pagamento (se houver) → publicar evento
  → end`. A UNICA excecao e `End_EncaminhadaFraude`: notificar um prestador sob investigacao de
  fraude e alerta-lo. A consequencia e declarada e **nao resolvida** — o terminal fecha a
  instancia, entao o prestador fica sem resposta sobre um lote apresentado (**OQ-14**: existe dever
  de comunicar, e em que momento? fraude + juridico + compliance).
- **I-PAGTO-1 (ADR-0040):** as tres pernas de handoff **nunca** semeiam os quatro fatos de
  admissibilidade de SP-OP-PAGTO-001. Consequencia por construcao: `pagto_admissibility` le
  `lastro_confirmado` ausente ⇒ `false` ⇒ row `r_sem_lastro` ⇒ `ANALISE_HUMANA` ⇒
  `UT_AnaliseAdmissibilidade` (`coordenacao-financeira`). As **duas pernas sem User Task** deste
  processo (`has_glosas == false` e `glosa_triage == PAGAR`) produzem um demonstrativo e uma ordem
  de pagamento **admissivelmente pendente** — nunca uma liberacao de caixa. O gate real deste
  caminho **nao** e `ERR_PAYMENT_RELEASE_NOT_HUMAN`, que guarda apenas `ST_ReleaseHighValue` e que
  este caminho nunca alcanca.
- **Marina (PHI zone)** e convocada via `operadora.contas.prepare_triage_dossier` para montar o
  dossie; **instrui, nao decide** — o analista/auditor humano decide na User Task (principio Rafael).
- **TASY write DROP** em todo worker (`identify_glosa`, `devolver_conta`,
  `emitir_demonstrativo`): consumimos o CDC do amh-data-platform, nunca escrevemos no Tasy
  (MEMORY/ADR-0013). Nenhuma chamada TISS real e feita nesta fase; o `protocolo_demonstrativo` e
  sintetico e **deterministico** (business key + `tipo_comunicacao`), nunca `time`/`uuid`/`random`
  — a fence de pureza e `tests/unit/tools/workers/test_worker_handler_purity.py`.

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e o BPMN/DMN bodies (autorados em wave posterior contra este contrato).
- Confirmacao de **todos** os prazos com regulatorio/juridico/financas (SLA de analise de conta;
  origem contratual de `data_vencimento`; conversao dias uteis→ISO) — **ADR-0040 OQ-2**.
- Confirmacao dos nomes e da estrutura dos artefatos TISS ("Demonstrativo de Analise de Conta",
  "Lote de Guias", `tipo_comunicacao`, e se a devolucao e um artefato TISS proprio) contra o padrao
  vigente — **ADR-0040 OQ-1**. `docs/compliance/` nao contem o dicionario TISS.
- Confirmacao de que **RN 501/2022** e a norma corrente do fluxo TISS e de que a retirada de
  RN 424/2017 esta correta — a propria revisao interna esta marcada `[verify SME]`
  (`docs/compliance/rn-currency-review.md:193`) — **DRAFT/verify**.
- **OQ-7:** `DEVOLVER` e efeito adverso? Hoje e classificado neutro→prestador-adjacente, espelhando
  `End_PagamentoRecusadoHumano` de PAGTO, e por isso `devolver_conta` nao tem guard `_NOT_HUMAN`.
- **OQ-10:** o achado M-4 da `glosa_triage` (conjunto negativo admite `desconhecida`) continua
  **aberto**; a variante conservadora esta pre-especificada e e ato do dono da tabela.
- **OQ-14:** dever e momento de comunicar o prestador no terminal de fraude.
- Tabela TISS de motivos de glosa (`codigo_glosa_tiss`) — **ADR-0040 OQ-5**.
- **Confirmacao dos candidate groups** `auditoria-contas` / `coordenacao-contas` contra a
  taxonomia organizacional da operadora (OQ).
- Sign-off de compliance sobre a **excecao de glosa tecnica auto-route** (R4): default e humano;
  qualquer auto-route de glosa formatacional pura exige decisao registrada de compliance.
- Politica de indicio de fraude: confirmar que nenhum branch auto-flagueia (`fraud_accusation`
  L0 hard) e o handoff a SP-OP-FRAUDE-001 (Phase 3).
- Anexos TISS obrigatorios por `tipo_lote`; mapa completo reason_code TISS → categoria
  (`glosa_reason_normalization`).
