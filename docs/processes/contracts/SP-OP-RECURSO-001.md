# Contrato — SP-OP-RECURSO-001 (Análise de Recurso de Glosa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 (Wave A, §4-bis-F sync artifact) · **BPMN:** `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn` (autorado em wave posterior contra este contrato)
**Negativa-like:** SIM — `authorization_denial`-class L0 hard. **Indeferir o recurso (manter a glosa) e deferir parcialmente (manter parte dela) são os efeitos adversos contra o prestador.** Aplica o padrão estrutural no-denial de cinco partes (§4-bis-F).
**Gatilho regulatorio:** **prazo contratual de resposta ao recurso** (fonte primária do SLA de análise e do teto absoluto — **DRAFT/verify**); **RN 501/2022** (Padrão TISS — fluxo de glosa e recurso: **DRAFT/verify**); Lei 9.656/1998 art. 18 (relação operadora-prestador). **RN 424/2017 aplica-se APENAS se instaurada junta médica/odontológica** para dirimir divergência técnico-assistencial — nunca ao prazo de resposta ao recurso (`docs/compliance/rn-currency-review.md:87, 190-193`). Todas as citações **DRAFT/verify com jurídico/regulatório**.
**Perspectiva (ADR-0040, Proposed):** o dono do processo é a **OPERADORA**. Ela **recebe** o recurso que o prestador interpõe contra uma glosa que ela própria aplicou em SP-OP-CONTAS-001, julga a **admissibilidade**, analisa o **mérito** (administrativo pelo analista de recurso; técnico-clínico pelo médico auditor) e **emite** a resposta. A fase de recorrente que este contrato descrevia — submeter a peça, aguardar e reconciliar o crédito recebido — foi **removida sem shim**.
**Consome:** o **recurso que o prestador interpõe** contra uma glosa **aplicada** por SP-OP-CONTAS-001. A instância é iniciada pelo intake TISS do recurso, nunca por CONTAS — ver §Origem da instância.
**Inverte o reference** (`../maezo-reference/.archive/bpmn/glosa_management.bpmn` + `check_appeal_eligibility_worker.py` + `Task_AutoApprove` 48h): o reference auto-decide quando `isEligible==false` (raises `NotAppealable`) e auto-aprova por timeout de 48h. Em Maezo **NENHUM caminho automatizado indefere**: inelegibilidade/inadmissibilidade aparentes roteiam para User Task humana; o timeout não auto-passa, ele **interrompe → coordenação humana**.

## Invariante L0 hard (nao negociavel)

O **indeferimento** do recurso (manter a glosa contra o prestador), o **deferimento parcial**
(manter parte dela) e a **inadmissibilidade** SÓ nascem nas User Tasks humanas:
`UT_AnaliseRecursoAnalista` (`decisao_recurso ∈ {INDEFERIR, DEFERIR_PARCIAL}`),
`UT_RevisaoAuditorMedico` (`decisao_auditor_recurso ∈ {INDEFERIR, DEFERIR_PARCIAL}`, mérito
técnico/clínico), `UT_CoordenacaoRecursoAssume` (breach de SLA) ou `UT_EscalonamentoPrazo`
(estouro do teto). Nenhuma DMN deste processo possui saída de indeferimento:
`recurso_admissibility` roteia só `{SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}`;
`recurso_eligibility` só `{SEGUE_MERITO, ANALISE_HUMANA}`. Inadmissibilidade por prazo
procedural, inelegibilidade aparente e ambiguidade SEMPRE fail-safe para análise humana
(catch-all row → `ANALISE_HUMANA`). Não há auto-deferimento nem auto-indeferimento por timeout (o
auto-approve-on-timeout de 48h do reference foi **INVERTIDO** para SLA interruptivo → coordenação
humana). Os efeitos adversos são materializados só por `operadora.recurso.registrar_indeferimento`,
guardado por `ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`. Os terminais adversos
(`End_RecursoIndeferido`, `End_RecursoIndeferidoAuditor`, `End_RecursoDeferidoParcial`,
`End_RecursoInadmissivel`) são inalcançáveis sem uma User Task humana concluída na history do
engine (verificado por teste de invariante). Os **dois gateways decisórios** têm default de ERRO
fail-closed (`ERR_RECURSO_DECISAO_INVALIDA`) — nunca uma ação por omissão.

## Business key (idempotencia)

```
RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}
```

Um recurso por glosa por guia TISS. `mcp-cibseven.start_process` DEVE consultar a business key
antes de iniciar; instância ativa existente => retorna a existente (start idempotente, sem duplicar
recurso). Re-intake (o prestador retransmite, a operação abre manualmente, Marina redelega) retorna
a instância ativa.

## Origem da instancia

Três caminhos, todos convergindo na **mesma** business key — portanto na mesma instância:

1. **Intake TISS → `notification_bridge`** (canônico): a regra keyed em
   `agents.events.recurso.intake_recebido` inicia SP-OP-RECURSO-001 pelo chokepoint fenceado.
   **O adaptador de intake que emitiria esse evento NÃO existe em `main`** — a regra é registrada
   **DORMENTE de propósito**, para que o adaptador futuro nasça contra um contrato de âncora já
   fenceado (`_anchored` + `NotificationBridgeMissingBusinessKeyError`). Pendência **OQ-R1**.
2. **Marina** (`flow="recurso"`), pelo mesmo chokepoint e pela mesma chave.
3. **Abertura manual pela operação** (coordenação registra um recurso recebido fora do canal
   eletrônico).

A aresta de handoff CONTAS → RECURSO **foi deletada** (o tópico não existe mais): a operadora não
recorre da sua própria glosa.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_guia_tiss` | string | sim | Guia TISS da conta glosada (parte da business key) |
| `glosa_id` | string | sim | Id da glosa confirmada em CONTAS (parte da business key) |
| `numero_lote_tiss` | string | sim | Lote TISS de origem (rastreabilidade CONTAS→RECURSO) |
| `numero_conta` | string | nao | Conta/linha glosada, quando adjudicação por conta |
| `prestador_id` | string | sim | Prestador que interpôs o recurso e a quem a resposta será dirigida |
| `beneficiario_pseudo_id` | string | sim | Pseudônimo (ADR-0006 — NUNCA CPF/nome) |
| `glosa_type` | string | sim | Tipo de glosa (de CONTAS): `administrativa` \| `tecnica` \| `clinica` \| `linha_duplicada` \| `formatacao` |
| `glosa_reason_code` | string | sim | Código TISS de motivo de glosa normalizado (de CONTAS) |
| `valor_glosado_brl` | double | sim | Valor glosado em BRL (money = double; ou inteiro-centavos — NUNCA `"number"`) |
| `codigo_procedimento_tuss` | string | sim | Procedimento TUSS da linha glosada |
| `cid10` | string | nao | CID-10 da guia (quando glosa clínica) |
| `documentos_recurso_refs` | json | sim | Referências de anexos/justificativas do recurso (pode ser vazio) |
| `data_ciencia_alegada_prestador` | date | não | Data que o prestador **alega** no pleito como a de sua ciência — **registro do que ele declarou, jamais base do prazo da operadora**; usada só na aferição de tempestividade (`dentro_prazo_recurso`), que é fato pré-resolvido por worker. Renomeada no reparo do gate do PR-3 (M1): o nome anterior ancorava no relógio do **recorrente** e a cerca de perspectiva (PR-1, família `ancora-kpi`) o acusa como tal — o token antigo fica registrado no `evidence-ledger` e na mensagem do commit, não na prosa do contrato |
| `data_recebimento_recurso_iso` | date (ISO) | **sim** | Data em que a operadora **recebeu** o recurso — **âncora única do SLA e do teto absoluto** (GAP-RECURSO-1). Normalizada/defaultada fail-safe para HOJE/UTC **com warning** pelo worker `operadora.recurso.validate_recurso` (`ST_ValidarRecurso`, intake — primeiro em TODO caminho; espelha `identify_glosa`/GAP-CONTAS-4). **Não há mais fail-safe para a data de ciência do prestador** — essa é a âncora do recorrente |
| `data_vencimento` | date | sim* | Vencimento da conta de origem, herdado do envelope de intake. Exigido pelo handoff de pagamento da glosa revertida e **recusado em branco** (nunca defaultado) — de onde ele vem numa reversão de glosa é **OQ-3, DRAFT/verify** |
| `glosa_existe` | boolean | sim | Pré-resolvido por worker: a glosa referida está confirmada e ativa em CONTAS |
| `dentro_prazo_recurso` | boolean | sim* | Pré-resolvido por worker: dentro do prazo recursal (cálculo de prazo no worker; RN 424 — **DRAFT/verify**) |
| `documentacao_recurso_completa` | boolean | sim* | Pré-resolvido por worker: anexos mínimos do recurso presentes |

`*` Pré-resolvido por worker ANTES da `BRT_Admissibilidade` (a aritmética de prazo fica no worker;
a *decisão* de não-recorrer por prazo é sempre humana — ver R5).

**Fatos do intake, sem tautologias.** Para instâncias iniciadas pela ponte, os três fatos acima
são **ecoados do envelope** e **fail-closed para `False`** quando ausentes. `glosa_existe`
deixou de ser hard-coded `True`: essa premissa ("só alcança o handoff pela aresta deletada, sobre uma
glosa ativa") não sobrevive a um recurso interposto de FORA, cujo `glosa_id` pode não referenciar
nada que a operadora tenha cunhado — ausente ⇒ `False` ⇒ `ANALISE_HUMANA` pela row
`r_glosa_inexistente_humano`, nunca uma afirmação de que a glosa existe. `dentro_prazo_recurso`
deixou de ser a tautologia de dia-0 pela mesma razão. Variáveis ausentes ≠ `false` em FEEL, por
isso a ponte semeia os três explicitamente.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_recurso` | string | `DEFERIR` \| `DEFERIR_PARCIAL` \| `INDEFERIR` \| `SOLICITAR_INFO` \| `ESCALAR_AUDITOR` (origem: User Tasks humanas) |
| `fundamentacao_indeferimento` | string | **Obrigatória se `INDEFERIR` ou `DEFERIR_PARCIAL`** — fundamentação da resposta adversa |
| `valor_glosa_mantido_brl` | double | **Obrigatória se `INDEFERIR`/`DEFERIR_PARCIAL`** — valor da glosa **mantido** pela operadora (money = double) |
| `valor_deferido_brl` | double | **Obrigatória se `DEFERIR`/`DEFERIR_PARCIAL`** — valor da glosa **revertido**; alimenta o handoff a SP-OP-PAGTO-001. Em `DEFERIR_PARCIAL` a soma `valor_deferido_brl + valor_glosa_mantido_brl == valor_glosado_brl` é conferida em **centavos-inteiros, igualdade exata** — **invariante PERMANENTE do guard**, não default provisório (decisão do dono **R-155** de 2026-09-04, que fechou **OQ-R2**). Qualquer tolerância ou regra de arredondamento **afrouxa** o guard e por isso continua humana: entra como **regra nova assinada por finanças, em PR próprio**, nunca como edição deste contrato ou do worker. **Limite declarado** (grão da conversão, não folga da comparação): os **três** operandos passam por `recurso.py::_to_cents` (`int(round(brl * 100))`) de forma **independente**. Para entradas já em centavos a igualdade é exata e nada é absorvido. Para entradas **sub-centavo** cada operando é arredondado por conta própria e os resíduos se somam em lados opostos: uma discrepância real de até **1,5 centavo** (3 × meio centavo) ainda fecha a soma — teto **atingido**, não só aproximado (`0,005 + 0,025` contra `0,015` fecha com 1,5 centavo exato; `60,0049 + 40,0049` contra `99,9951` fecha com 1,47). Acima de 1,5 centavo a soma não fecha. Fixado em `tests/unit/tools/workers/test_recurso.py` (secção "R-155"). Regra sobre valores sub-centavo é igualmente regra nova assinada por finanças |
| `referencia_contratual` | string | **Obrigatória se `INDEFERIR`/`DEFERIR_PARCIAL`** — cláusula/fundamentação contratual |
| `parecer_auditor` | string | Obrigatória quando `UT_RevisaoAuditorMedico` decide o mérito (glosa técnica/clínica) |
| `decisao_auditor_recurso` | string | `DEFERIR` \| `DEFERIR_PARCIAL` \| `INDEFERIR` (auditor médico decide o mérito) |
| `analista_id` | string | Id do analista humano que setou `decisao_recurso` (cadeia de auditoria ADR-0007) |
| `auditor_id` | string | Id do auditor médico, quando houve `ESCALAR_AUDITOR` |
| `protocolo_resposta_recurso` | string | Protocolo da **resposta** da operadora ao recurso (emitido por `operadora.recurso.comunicar_resposta`; determinístico pela business key) |
| `desfecho` | string | `deferido_humano` \| `deferido_parcial_humano` \| `indeferido_humano` \| `inadmissivel_humano` |

## Variaveis de proveniencia do agente (Marina — ADR-0007/ADR-0015)

Convencao repo-wide de nao-repudio (ADR-0007) e delegacao A2A (ADR-0015) — nao especifica de
RECURSO (mirror `source_agent_id`/`source_agent_version` de
`docs/processes/contracts/SP-OP-ESCALATION-001.md`). Semeadas por `MarinaGraph._contract_variables`
(`src/maezo/agents/marina/graph.py`, flow `recurso`) junto com as variaveis de entrada; NENHUMA
delas e um indeferimento de recurso — so proveniencia, dossie instrutivo e roteamento humano
(CC-13 — Agent Fleet Audit: antes deste registro, `_contract_variables` as emitia sem declaracao
no contrato).

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `source_agent_id` | string | nao | Agente que preparou o dossie do recurso (`marina`) — cadeia de nao-repudio (ADR-0007) |
| `source_agent_version` | string | nao | Versao do agente Marina que preparou o dossie (auditoria ADR-0007) |
| `dossie_marina` | json | nao | Dossie factual de analise do recurso montado por Marina — instrui `UT_AnaliseRecursoAnalista`/`UT_RevisaoAuditorMedico`; carrega `decisao_recurso` sempre `None` (Marina NUNCA decide) |
| `marina_flow` | string | nao | Fluxo do grafo compartilhado de Marina que originou o start (`recurso` neste contrato — `contas` inicia SP-OP-CONTAS-001; `reembolso` nunca inicia processo, ver aquele contrato) |
| `marina_route` | string | nao | Roteamento do grafo do Marina (`auto_route` \| `human_review`) — espelha, nao decide, o roteamento do processo |
| `motivo_encaminhamento` | string | nao | Presente so quando `marina_route=human_review`; motivo do encaminhamento |
| `grupo_destino` | string | nao | Presente so quando `marina_route=human_review`; grupo humano sugerido por Marina (`analista-recurso-glosa` \| `medico-auditor`) |
| `dmn_decision_refs` | json | nao | Referencias auditaveis (tabela→regra) das DMN que Marina consultou (`recurso_admissibility`/`recurso_eligibility`/`recurso_sla`) — cadeia de decisao (ADR-0007/ADR-0012) |

## Topicos

Convenção `{dominio}.{contexto}.{acao}` (W0.2 é o único editor de `config/topic_registry.yaml`;
este contrato REPORTA os tópicos a registrar — ver "Pendências" e o relatório ao orquestrador).

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.recurso.received` | produz | após start (recurso solicitado registrado) |
| Kafka | `agents.events.recurso.pended` | produz | pendência de documentação do recurso aberta |
| Kafka | `agents.events.recurso.sla_breached` | produz | SLA de análise do recurso estourado (payload.fase = `analise` \| `prazo_max`) |
| Kafka | `agents.events.recurso.completed` | produz | fim (payload.desfecho = `deferido_humano` \| `deferido_parcial_humano` \| `indeferido_humano` \| `inadmissivel_humano`) |
| Kafka | `agents.events.recurso.intake_recebido` | **consome** (bridge) | intake TISS do recurso interposto pelo prestador → inicia a instância. **Sem publicador em `main`** (OQ-R1) |
| External task | `operadora.events.publish` | consome (worker) | publicador genérico de eventos de domínio (compartilhado) |
| External task | `operadora.recurso.analyze_request` | consome (worker) | convoca Marina: dossiê do analista de recurso (A2A `recurso.analyze`) |
| External task | `operadora.recurso.request_documents` | consome (worker) | worker `request_documents`: registra que a ETAPA de abertura da pendência de documentação ao prestador rodou e retorna `{}` — **NÃO afirma `notified`, não contata canal algum e não publica evento** (GAP-RECURSO-5 / FAB-NOTIFIED-TRIO; antes chamava-se `notify_prestador` e retornava `notified=True` constante). Nenhum canal externo ao prestador existe para workers nesta árvore — as seams são `engine`/`dmn`/`kafka`/`audit_sink`. O evento `agents.events.recurso.pended` é publicado pelo **próprio BPMN**, uma task adiante, em `ST_PublishRecursoPended` (T3.1 event-gap remedy B) |
| External task | `operadora.recurso.validate_recurso` | consome (worker) | **intake** (`ST_ValidarRecurso`): normaliza a âncora do SLA/teto e guarda `glosa_id` (`ERR_RECURSO_INVALID_GLOSA`). Os fatos `glosa_existe`/`dentro_prazo_recurso`/`documentacao_recurso_completa` chegam pré-resolvidos no escopo do processo e são **ecoados** (fail-closed para `false` quando ausentes, no eco do bridge) — o nome do elemento BPMN foi corrigido para não prometer mais do que isso (m7). Devolve `errors_validacao` como **escalar** (string), nunca uma lista (m8) |
| External task | `operadora.recurso.registrar_indeferimento` | consome (worker) | efeito adverso gated — registra o indeferimento / deferimento parcial / inadmissibilidade; recusa sem decisão humana (**`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`**) |
| External task | `operadora.recurso.comunicar_resposta` | consome (worker) | emite ao prestador a resposta ao recurso (TISS — **DRAFT/verify**, OQ-1); serve os 4 desfechos |
| External task | `operadora.recurso.handoff_pagamento` | consome (worker) | **handoff** a SP-OP-PAGTO-001 quando `DEFERIR`/`DEFERIR_PARCIAL` (`tipo_pagamento=glosa_revertida`) — ver §Handoff de pagamento |
| External task | `operadora.recurso.notify_sla_risk` | consome (worker) | worker `notify_sla_risk` + wrapper `make_notify_sla_risk_handler`: o wrapper publica o alerta em `operadora.notifications.internal` (`best_effort=False`) quando ha produtor Kafka, e AMBOS os caminhos retornam `{}` — **NAO afirmam `sla_risk_notified`** (FAB-SLA-RISK-NOTIFIED-SLICE4; antes o caminho `kafka is None` devolvia `True` sem ter publicado nada, e o registro interno e pedido de alerta, nunca prova de que a `coordenacao-recurso` foi avisada). Informativo e nunca adverso: `UT_AnaliseRecursoAnalista` segue aberta |
| External task | `operadora.recurso.escalate_ans_timeout` | consome (worker) | escalona estouro do teto de resposta (P30D — **DRAFT/verify**) → `UT_EscalonamentoPrazo`. O nome do tópico é histórico: **não** é integração com gateway ANS |
| Message BPMN | `msg.recurso.docs_received` | recebe | correlação por business key: documentação do prestador chegou (destrava pendência) |

## DMN referenciadas

Shape engine-deployável (§4-bis-A): typeRef ∈ {string, boolean, integer, long, double, date} —
**`"number"` é inválido**; money BRL → `double` (ou inteiro-centavos); dias/SLA → string ISO 8601.
Todo `decisionTable` com `hitPolicy`; toda tabela com **row catch-all → caminho humano conservador
(`ANALISE_HUMANA`)**. `camunda:historyTimeToLive="P###D"` namespaced em cada `<decision>` (autorado
no wave do .dmn). **Nenhuma DMN tem coluna de saída de indeferimento (no-denial).**

### `recurso_admissibility` (hitPolicy FIRST — DRAFT)
| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `glosa_existe` | boolean | a glosa referida está confirmada/ativa em CONTAS |
| in | `dentro_prazo_recurso` | boolean | dentro do prazo recursal (pré-resolvido no worker) |
| in | `documentacao_recurso_completa` | boolean | anexos mínimos presentes |
| out | `roteamento` | string | `SEGUE_ANALISE` \| `PENDENTE_DOCUMENTACAO` \| `ANALISE_HUMANA` |
| out | `motivo` | string | rótulo do motivo do roteamento |

**Sem saída de INDEFERIMENTO/INADMISSIBILIDADE por design.** `glosa_existe=false` ou
`dentro_prazo_recurso=false` NÃO indeferem — roteiam para `ANALISE_HUMANA` (inadmissibilidade
aparente é decidida por humano — R5). Catch-all (qualquer combinação não-mapeada) = `ANALISE_HUMANA`.

### `recurso_eligibility` (hitPolicy FIRST — DRAFT)
| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `glosa_type` | string | `administrativa` \| `tecnica` \| `clinica` \| `linha_duplicada` \| `formatacao` |
| in | `glosa_reason_code` | string | código TISS normalizado de motivo |
| in | `valor_glosado_brl` | double | valor glosado (money = double) |
| out | `roteamento` | string | `SEGUE_MERITO` \| `ANALISE_HUMANA` |
| out | `grupo_revisor` | string | `analista-recurso-glosa` \| `medico-auditor` (glosa técnica/clínica → auditor decide o mérito) |
| out | `motivo` | string | rótulo |

**Sem saída de INDEFERIMENTO.** Glosa técnica/clínica roteia para `medico-auditor` (humano decide
o mérito — espelha `JUNTA_MEDICA` de AUTH); o auditor decide
`decisao_auditor_recurso ∈ {DEFERIR, DEFERIR_PARCIAL, INDEFERIR}`. Catch-all = `ANALISE_HUMANA` /
`analista-recurso-glosa`.

### `recurso_sla` (hitPolicy FIRST — DRAFT, todos os prazos DRAFT/verify)
| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `glosa_type` | string | tipo de glosa |
| in | `valor_glosado_brl` | double | valor glosado |
| in | `data_recebimento_recurso_iso` | string (ISO date) | âncora **única** do teto P30D (documentada como input; não participa do matching; **sem fail-safe de outra parte** — normalizada pelo worker `validate_recurso`) |
| out | `sla_analise` | string (ISO 8601) | prazo de análise interna do recurso |
| out | `sla_alerta` | string (ISO 8601) | gatilho do alerta não-interruptivo (~50-70% do SLA) |
| out | `prazo_regulatorio` | string (ISO 8601, duração) | prazo máximo de resposta — **legado/observabilidade**; não alimenta mais nenhum timer (prazo contratual — **DRAFT/verify**) |
| out | `prazo_max_absoluto_iso` | string (ISO 8601, datetime) | **teto absoluto** = âncora + P30D (FEEL); consumido via `timeDate` pelos 3 boundary de teto (GAP-RECURSO-1) |
| out | `fonte_regulatoria` | string | fonte do prazo: **prazo contratual de resposta ao recurso + RN 501/2022** (DRAFT/verify). RN 424/2017 aparece **apenas** na row técnico-clínica, e ainda assim condicionada à instauração de junta médica/odontológica |

## Papeis humanos (candidate groups)

> **OQ (open question):** `analista-recurso-glosa` e `coordenacao-recurso` são nomes **PROPOSTOS** —
> precisam de confirmação contra a taxonomia org do operador. `medico-auditor` reusa o grupo já
> existente em SP-OP-AUTH-001/CONTAS-001.

| Grupo | Papel | Tarefa |
|---|---|---|
| `analista-recurso-glosa` *(PROPOSTO)* | Analista de recurso de glosa | `UT_AnaliseRecursoAnalista` — **única origem primária de `decisao_recurso`**; `INDEFERIR`/`DEFERIR_PARCIAL` só aqui, na coordenação ou no auditor |
| `medico-auditor` | Médico auditor | `UT_RevisaoAuditorMedico` — decide o mérito de glosa técnica/clínica (`ESCALAR_AUDITOR`) |
| `coordenacao-recurso` *(PROPOSTO)* | Coordenação de recurso de glosa | `UT_CoordenacaoRecursoAssume` (SLA de resposta estourado), `UT_EscalonamentoPrazo` (teto de resposta) — **ambas com o mesmo vocabulário de pagador do analista** |

## SLAs

Substituem o auto-approve-on-timeout (48h) do reference: **nenhum timer auto-passa**; o timer
interruptivo de análise → coordenação humana; o prazo máximo regulatório → escalonamento humano.

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaRecurso`) | ~50-70% de `sla.sla_analise` (DMN `sla_alerta`) | não-interruptivo → `operadora.recurso.notify_sla_risk` | política interna |
| Resposta ao recurso (`BT_SlaAnaliseRecurso`) | `${sla.sla_analise}` | **interruptivo** → `recurso.sla_breached` (fase=`analise`); cancela `UT_AnaliseRecursoAnalista` → `UT_CoordenacaoRecursoAssume` | prazo contratual de resposta (**substitui** o `Task_AutoApprove` 48h — INVERTIDO) |
| Prazo máximo de resposta (`BT_PrazoMaxRecurso` / `BT_PrazoMaxCoord` / `BT_PrazoMaxAuditor`) | `timeDate ${sla.prazo_max_absoluto_iso}` = âncora (`data_recebimento_recurso_iso`, normalizada pelo intake) + `P30D` — **uma única janela absoluta**: os 3 boundary (analista/coordenação/auditor) expiram no MESMO instante; a escalada não estende o teto (GAP-RECURSO-1) | boundary não-interruptivo → `operadora.recurso.escalate_ans_timeout` → `UT_EscalonamentoPrazo` (humano; sem boundary de teto próprio — alvo pós-estouro, reanexar o mesmo instante insta-dispararia em cascata) | prazo contratual — **DRAFT/verify** |
| Pendência de documentação (`ICE_PrazoPendencia`) | ref `P5D` | event gateway → análise humana (humano decide destino) | **DRAFT/verify** |

Nota: prazos legais são em dias úteis; ISO 8601 usa dias corridos — valores conservadores; resolver
calendário útil no worker. **Nenhum desses timers produz desfecho adverso** — sempre roteiam a humano.

## Desfecho de agente: falha de start (CC-01)

| Desfecho | Onde vive | Quem escreve | Significado |
|---|---|---|---|
| `erro_inicio_processo` | **estado do agente marina** — NAO e variavel de processo | no `notify_start_failure` do grafo, via o helper unico `maezo.runtime.start_outcome.notify_start_failure` | o agente TENTOU iniciar SP-OP-RECURSO-001 pelo chokepoint `start_process_idempotent` e o engine recusou (`CibSevenError`). NENHUMA instancia nasceu |

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
gravado a montante (o desfecho de sucesso do fluxo de recurso) sobrevivia — o estado do agente AFIRMAVA um fato que nao
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


## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN` | worker-guard em `operadora.recurso.registrar_indeferimento` | o worker **recusa** registrar indeferimento/deferimento parcial/inadmissibilidade se nenhum dos dois canais humanos casar (analista: `decisao_recurso ∈ {INDEFERIR, DEFERIR_PARCIAL}` + `analista_id`; auditor: `decisao_auditor_recurso ∈ {INDEFERIR, DEFERIR_PARCIAL}` + `auditor_id`), ou se faltar `fundamentacao_indeferimento`/`valor_glosa_mantido_brl`/`referencia_contratual` (e `valor_deferido_brl` no parcial). **Declarado-e-não-capturado**: levantado como `PermissionError` → incidente auditado (ADR-0030 §4; reconhecido pelo sufixo `_NOT_HUMAN`). É o 4º componente do padrão no-denial. |
| `ERR_RECURSO_DECISAO_INVALIDA` | throw-end `End_ErrRecursoDecisaoInvalida` | default fail-closed dos dois gateways decisórios: uma decisão ausente/fora do domínio termina em ERRO visível, nunca numa ação por omissão. Nenhum efeito é materializado |
| `ERR_RECURSO_INVALID_GLOSA` | worker-guard `_require_glosa_id` em **3** tópicos (`validate_recurso`, `request_documents`, `analyze_request`) | `glosa_id` ausente/vazio nas variáveis de processo — defeito **TÉCNICO** de origem, distinto do fato de negócio `glosa_existe`. Capturado por `BE_GlosaInvalidaValidacao`/`BE_GlosaInvalidaDocs`/`BE_GlosaInvalidaDossie` → terminal NEUTRO `End_RecursoGlosaInvalidaOrigem` (GAP-RECURSO-3). Consumption-covered nos 3 tópicos (gate ADR-0030) |

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) — autorado no wave posterior contra este contrato (§4-bis-F).
- **Confirmação de todos os prazos RN com jurídico/regulatório** (R2): prazo recursal e prazo máximo
  (RN 424/2017), fluxo de glosa/recurso (RN 305/RN 501 TISS), conversão dias-úteis→ISO conservadora.
- **Confirmação dos candidate groups propostos** (`analista-recurso-glosa`, `coordenacao-recurso`)
  contra a taxonomia org do operador (OQ).
- **R5 — inadmissibilidade por prazo procedural duro:** default = humano-gated (`ANALISE_HUMANA`);
  auto-route de prazo-expirado puro só com sign-off do jurídico — **não autorizado neste DRAFT**.
- Textos de **fundamentação da resposta ao recurso** (deferimento/indeferimento) entram em
  review-queue como DRAFT; Marina monta o dossiê **factual**, o humano fundamenta e aprova; nunca
  auto-arquiva.
- Registro dos tópicos em `config/topic_registry.yaml` (W0.2) e dos candidate groups onde aplicável.
- Política de suspensão de prazo durante pendência de documentação (confirmar regra RN).
- **Handoff de pagamento da glosa revertida** a SP-OP-PAGTO-001 (`operadora.recurso.handoff_pagamento`):
  confirmar com finanças + jurídico a origem de `data_vencimento` numa reversão de glosa (**OQ-3**).
  ~~E a regra de arredondamento em `DEFERIR_PARCIAL` (**OQ-R2**).~~ **FECHADA** pela decisão do dono
  **R-155** de 2026-09-04: a igualdade exata em centavos-inteiros é **invariante permanente** do
  guard de `registrar_indeferimento`, e **o sign-off deste contrato não espera mais por ela**.
  Uma tolerância futura só pode AFROUXAR o guard, e afrouxar segue humano — regra nova assinada
  por finanças, em PR próprio. Fixada por teste em
  `tests/unit/tools/workers/test_recurso.py` (secção "R-155"). As demais perguntas de finanças
  deste contrato (R-154, R-156, R-189, R-190) seguem no pacote datado por R-137.
- **Adaptador de intake TISS do recurso** (`agents.events.recurso.intake_recebido`): a regra de
  bridge existe e está fenceada, o publicador **não** (**OQ-R1**).
- Confirmação dos nomes TISS "Recurso de Glosa" / "Resposta ao Recurso de Glosa" (**OQ-1**).
- Confirmação de que `DEFERIR_PARCIAL` é adverso L0 para o prestador, por analogia ao precedente
  já ratificado de REEMBOLSO (`End_ReembolsoParcial`, "redução adversa") — **OQ-8**.

## Handoff de pagamento (RECURSO -> SP-OP-PAGTO-001)

Quando a decisão humana é `DEFERIR` ou `DEFERIR_PARCIAL`, `operadora.recurso.handoff_pagamento`
inicia SP-OP-PAGTO-001 pelo chokepoint `start_process_idempotent`:

| Item | Especificação |
|---|---|
| Business key | `PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` — uma ordem por glosa revertida. É uma **terceira forma documentada** da família `PAGTO-…`, ao lado de `PAGTO-{tenant}-{ordem_pagamento_id}` e `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`: uma glosa revertida não tem ordem pré-existente nem é escopada por lote — a identidade dela **é** a glosa Os três segmentos são normalizados por `maezo.agents.andre.keys.key_segment` — o mesmo composer STRICT da família PAGTO —, de modo que uma reentrega com `numero_guia_tiss` *whitespace-padded* converge na MESMA chave em vez de cunhar uma segunda ordem de pagamento (M2; `non_blank` recusa vazio/`None` mas **aceita** padding). A identidade normalizada é a que viaja no payload e nos logs, não a crua |
| `tipo_pagamento` | `glosa_revertida` — valor já previsto no contrato de PAGTO. Nenhum vocabulário novo em PAGTO |
| Valor | `valor_pagamento_cents` = `valor_deferido_brl` em centavos-inteiros; `fonte_valor="deferido"` declara a origem. O worker **recusa fail-closed** valor ausente/em branco/não-numérico/`<= 0` — nunca defaulta a `0` |
| `fonte_valor` | Domínio fechado `{deferido}` (`FONTES_VALOR_PERMITIDAS`). Era **ecoada sem validação** do escopo do processo e entrava em `AgentDecisionProvenance.decision_basis` enquanto o valor vinha sempre de `valor_deferido_brl` — proveniência que podia contradizer a fonte real (m1). Qualquer outro rótulo é **recusado**, nunca ecoado |
| `lastro_decisor_id` | `analista_id` ou, no ramo do mérito, `auditor_id` — **normalizado e obrigatório**. Uma ordem nascida de decisão humana não sai sem o decisor identificado (ADR-0007); em branco, o worker recusa (m2) |
| `data_vencimento` | Obrigatória em PAGTO e herdada do envelope de intake. **Recusada em branco**, nunca defaultada (**OQ-3**) |
| **Invariante I-PAGTO-1** | O handoff **NUNCA** semeia `lastro_confirmado`, `dados_pagamento_validos`, `duplicidade_suspeita` nem `dentro_teto_l2`. Semeia, em lugar disso, `lastro_origem="recurso_deferimento_humano"` e `lastro_decisor_id` (`analista_id`\|`auditor_id`) como **evidência**. Consequência: `pagto_admissibility` lê o lastro ausente ⇒ `ANALISE_HUMANA` ⇒ `UT_AnaliseAdmissibilidade` (`coordenacao-financeira`). Isso compra **segregação de funções**: quem julga o recurso não admite a ordem de pagamento |
| Dedup | **STRICT** — SP-OP-PAGTO-001 é a única família STRICT; o chokepoint exige `DedupReportingAuditSink` + `HistoryQueryingTransport` e recusa (`StartDedupGateUnavailableError`) sem eles. Um deferimento nunca vira pagamento duplicado |
