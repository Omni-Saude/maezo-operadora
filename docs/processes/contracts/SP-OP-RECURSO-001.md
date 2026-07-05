# Contrato — SP-OP-RECURSO-001 (Recurso de Glosa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 2 (Wave A, §4-bis-F sync artifact) · **BPMN:** `src/maezo/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn` (autorado em wave posterior contra este contrato)
**Negativa-like:** SIM — `authorization_denial`-class L0 hard. Desistir/não-recorrer = aceitar a glosa contra o prestador. Aplica o padrão estrutural no-denial de cinco partes (§4-bis-F).
**Gatilho regulatorio:** RN 424/2017 (recurso/junta médica — prazo recursal: **DRAFT/verify**), RN 305/2016 e RN 501/2022 (padrão TISS / fluxo de glosa e recurso: **DRAFT/verify**), Lei 9.656/1998 art. 18 (relação operadora-prestador). Todas as citações **DRAFT/verify com jurídico/regulatório** — RN podem ter sido consolidadas/substituídas (R2).
**Consome:** uma glosa CONFIRMADA produzida por SP-OP-CONTAS-001 (handoff `operadora.contas.start_recurso` / delegação A2A a Marina). Depende do contrato de CONTAS, não do seu BPMN (§4-bis-F).
**Inverte o reference** (`../maezo-reference/.archive/bpmn/glosa_management.bpmn` + `check_appeal_eligibility_worker.py` + `Task_AutoApprove` 48h): o reference auto-aceita a glosa quando `isEligible==false` (raises `NotAppealable`) e auto-aprova o recurso por timeout de 48h. Em Maezo **NENHUM caminho automatizado produz desistência**: inelegibilidade/inadmissibilidade aparentes roteiam para User Task humana; o timeout não auto-passa, ele **interrompe → coordenação humana**.

## Invariante L0 hard (nao negociavel)

Manter-a-glosa (negar o recurso = não interpor / desistir) SÓ nasce nas User Tasks humanas
(`UT_AnaliseRecursoAnalista` com `decisao_recurso=NAO_RECORRER`, ou `UT_RevisaoAuditorMedico`
quando o mérito é técnico/clínico, ou `UT_CoordenacaoRecursoAssume` no breach de SLA).
Nenhuma DMN deste processo possui saída de negativa/desistência: `recurso_admissibility` e
`recurso_eligibility` roteiam apenas para `SEGUE_ANALISE` / `RECORRIVEL` / `PENDENTE_DOCUMENTACAO`
/ `ANALISE_HUMANA`. Inadmissibilidade por prazo procedural, inelegibilidade aparente e
ambiguidade SEMPRE fail-safe para análise humana (catch-all row → `ANALISE_HUMANA`). Não há
auto-aprovação nem auto-desistência por timeout (o auto-approve-on-timeout de 48h do reference
foi **INVERTIDO** para SLA interruptivo → coordenação humana). Os terminais adversos
(`End_RecursoNaoInterposto` / `End_GlosaMantida`) são inalcançáveis sem uma User Task humana
concluída na history do engine (verificado por teste de invariante).

## Business key (idempotencia)

```
RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}
```

Um recurso por glosa por guia TISS. `mcp-cibseven.start_process` DEVE consultar a business key
antes de iniciar; instância ativa existente => retorna a existente (start idempotente, sem duplicar
recurso). Reenvio (re-handoff de CONTAS, redelegação a Marina) retorna a instância ativa.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_guia_tiss` | string | sim | Guia TISS da conta glosada (parte da business key) |
| `glosa_id` | string | sim | Id da glosa confirmada em CONTAS (parte da business key) |
| `numero_lote_tiss` | string | sim | Lote TISS de origem (rastreabilidade CONTAS→RECURSO) |
| `numero_conta` | string | nao | Conta/linha glosada, quando adjudicação por conta |
| `prestador_id` | string | sim | Prestador recorrente (autor do recurso) |
| `beneficiario_pseudo_id` | string | sim | Pseudônimo (ADR-0006 — NUNCA CPF/nome) |
| `glosa_type` | string | sim | Tipo de glosa (de CONTAS): `administrativa` \| `tecnica` \| `clinica` \| `linha_duplicada` \| `formatacao` |
| `glosa_reason_code` | string | sim | Código TISS de motivo de glosa normalizado (de CONTAS) |
| `valor_glosado_brl` | double | sim | Valor glosado em BRL (money = double; ou inteiro-centavos — NUNCA `"number"`) |
| `codigo_procedimento_tuss` | string | sim | Procedimento TUSS da linha glosada |
| `cid10` | string | nao | CID-10 da guia (quando glosa clínica) |
| `documentos_recurso_refs` | json | sim | Referências de anexos/justificativas do recurso (pode ser vazio) |
| `data_ciencia_glosa` | date | sim | Data de ciência da glosa pelo prestador (base de contagem de prazo) |
| `data_recebimento_recurso_iso` | date (ISO) | não | Data de recebimento do recurso — **âncora regulatória do teto absoluto P30D** (GAP-RECURSO-1); seedada pelo intake (`notifications_bridge.recurso_variables`); fail-safe quando ausente: `data_ciencia_glosa` |
| `glosa_existe` | boolean | sim | Pré-resolvido por worker: a glosa referida está confirmada e ativa em CONTAS |
| `dentro_prazo_recurso` | boolean | sim* | Pré-resolvido por worker: dentro do prazo recursal (cálculo de prazo no worker; RN 424 — **DRAFT/verify**) |
| `documentacao_recurso_completa` | boolean | sim* | Pré-resolvido por worker: anexos mínimos do recurso presentes |

`*` Pré-resolvido por worker ANTES da `BRT_Admissibilidade` (a aritmética de prazo fica no worker;
a *decisão* de não-recorrer por prazo é sempre humana — ver R5).

**GAP-XPROC-3 (resolvido):** para instâncias iniciadas pela ponte (`notifications_bridge`), os
três fatos acima — mais `glosa_type` — são seedados por `recurso_variables()`
(`src/maezo/platform/integrations/notifications_bridge/consumer.py`): `glosa_type`/`glosa_existe`
ecoados do envelope `contas.start_recurso` (CONTAS os publica; `glosa_existe` sempre `True` — só
alcança o handoff após `RECORRER` sobre uma glosa ativa na própria instância CONTAS);
`documentacao_recurso_completa` mapeado (FIELD-DRIFT FIX) do fato próprio de CONTAS
`documentacao_anexa`; `dentro_prazo_recurso` sempre `True` (tautologia de dia-0: a janela P30D
começa em `data_recebimento_recurso_iso`, seedada na mesma chamada como "hoje"). Fail-closed=False
quando o envelope não carrega o campo — nunca `SEGUE_ANALISE`/`ANALISE_HUMANA` indevido. Antes
desta correção, toda instância iniciada pela ponte caía no catch-all `ANALISE_HUMANA` e nunca
alcançava `BRT_Eligibility` (variáveis ausentes ≠ `false` em FEEL).

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_recurso` | string | `RECORRER` \| `NAO_RECORRER` \| `SOLICITAR_INFO` \| `ESCALAR_AUDITOR` (origem: User Tasks humanas) |
| `justificativa_desistencia` | string | **Obrigatória se `NAO_RECORRER`** (manter glosa) |
| `valor_glosa_aceito` | double | **Obrigatória se `NAO_RECORRER`** — valor da glosa aceito contra o prestador (money = double) |
| `referencia_contratual` | string | **Obrigatória se `NAO_RECORRER`** — cláusula/fundamentação contratual da aceitação |
| `parecer_auditor` | string | Obrigatória quando `UT_RevisaoAuditorMedico` decide o mérito (glosa técnica/clínica) |
| `decisao_auditor_recurso` | string | `MANTER_RECURSO` \| `ACEITAR_GLOSA` \| `RECURSO_PARCIAL` (auditor médico decide o mérito) |
| `analista_id` | string | Id do analista humano que setou `decisao_recurso` (cadeia de auditoria ADR-0007) |
| `auditor_id` | string | Id do auditor médico, quando houve `ESCALAR_AUDITOR` |
| `protocolo_recurso` | string | Protocolo do recurso interposto (emitido por `operadora.recurso.submit_appeal`) |
| `resposta_operadora` | string | `deferido` \| `parcialmente_deferido` \| `indeferido` (default — resposta de terceiro) — resposta da operadora entregue via `Msg_RecursoRespostaRecebida` (`msg.recurso.resposta_recebida`) em `ICE_RespostaRecebida`; consumida por `GW_RecursoResolvido` (GAP-RECURSO-4) |
| `desfecho` | string | `deferido` \| `indeferido` \| `parcialmente_deferido` \| `inadmissivel` \| `nao_interposto_humano` |

## Topicos

Convenção `{dominio}.{contexto}.{acao}` (W0.2 é o único editor de `config/topic_registry.yaml`;
este contrato REPORTA os tópicos a registrar — ver "Pendências" e o relatório ao orquestrador).

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.recurso.received` | produz | após start (recurso solicitado registrado) |
| Kafka | `agents.events.recurso.pended` | produz | pendência de documentação do recurso aberta |
| Kafka | `agents.events.recurso.sla_breached` | produz | SLA de análise do recurso estourado (payload.fase = `analise` \| `prazo_max`) |
| Kafka | `agents.events.recurso.completed` | produz | fim (payload.desfecho = `deferido` \| `indeferido` \| `parcialmente_deferido` \| `inadmissivel` \| `nao_interposto_humano`) |
| External task | `operadora.events.publish` | consome (worker) | publicador genérico de eventos de domínio (compartilhado) |
| External task | `operadora.recurso.analyze_request` | consome (worker) | convoca Marina: dossiê do analista de recurso (A2A `recurso.analyze`) |
| External task | `operadora.recurso.request_documents` | consome (worker) | pendência de documentação ao prestador (publica também `recurso.pended`) |
| External task | `operadora.recurso.submit_appeal` | consome (worker) | interpõe o recurso à operadora (TISS) — **só após `UT_AnaliseRecursoAnalista` com `RECORRER`** |
| External task | `operadora.recurso.track_status` | consome (worker) | acompanha o status do recurso interposto (dentro do timer-loop) |
| External task | `operadora.recurso.register_desistencia` | consome (worker) | registra a desistência/manutenção de glosa — **GUARDADO por `ERR_DESISTENCIA_NOT_HUMAN`** |
| External task | `operadora.recurso.reconcile_payment` | consome (worker) | concilia re-pagamento ao prestador no deferimento (port `update_payment`) |
| External task | `operadora.recurso.notify_sla_risk` | consome (worker) | alerta `coordenacao-recurso` (timer não-interruptivo) |
| External task | `operadora.recurso.escalate_ans_timeout` | consome (worker) | escalona estouro do prazo máximo (P30D ANS — **DRAFT/verify**) → `UT_EscalonamentoPrazo` |
| Message BPMN | `msg.recurso.resposta_recebida` | recebe | correlação por business key: resposta da operadora ao recurso chegou (destrava `ICE_AguardarResposta`) |
| Message BPMN | `msg.recurso.docs_received` | recebe | correlação por business key: documentação do prestador chegou (destrava pendência) |

## DMN referenciadas

Shape engine-deployável (§4-bis-A): typeRef ∈ {string, boolean, integer, long, double, date} —
**`"number"` é inválido**; money BRL → `double` (ou inteiro-centavos); dias/SLA → string ISO 8601.
Todo `decisionTable` com `hitPolicy`; toda tabela com **row catch-all → caminho humano conservador
(`ANALISE_HUMANA`)**. `camunda:historyTimeToLive="P###D"` namespaced em cada `<decision>` (autorado
no wave do .dmn). **Nenhuma DMN tem coluna de saída de negativa/desistência (no-denial).**

### `recurso_admissibility` (hitPolicy FIRST — DRAFT)
| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `glosa_existe` | boolean | a glosa referida está confirmada/ativa em CONTAS |
| in | `dentro_prazo_recurso` | boolean | dentro do prazo recursal (pré-resolvido no worker) |
| in | `documentacao_recurso_completa` | boolean | anexos mínimos presentes |
| out | `roteamento` | string | `SEGUE_ANALISE` \| `PENDENTE_DOCUMENTACAO` \| `ANALISE_HUMANA` |
| out | `motivo` | string | rótulo do motivo do roteamento |

**Sem saída NEGAR/DESISTIR/INADMISSIVEL por design.** `glosa_existe=false` ou `dentro_prazo_recurso=false`
NÃO produzem desistência — roteiam para `ANALISE_HUMANA` (inadmissibilidade aparente é decidida por
humano — R5). Catch-all (qualquer combinação não-mapeada) = `ANALISE_HUMANA`.

### `recurso_eligibility` (hitPolicy FIRST — DRAFT)
| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `glosa_type` | string | `administrativa` \| `tecnica` \| `clinica` \| `linha_duplicada` \| `formatacao` |
| in | `glosa_reason_code` | string | código TISS normalizado de motivo |
| in | `valor_glosado_brl` | double | valor glosado (money = double) |
| out | `roteamento` | string | `RECORRIVEL` \| `ANALISE_HUMANA` |
| out | `grupo_revisor` | string | `analista-recurso-glosa` \| `medico-auditor` (glosa técnica/clínica → auditor decide o mérito) |
| out | `motivo` | string | rótulo |

**Sem saída NAO_RECORRIVEL/NEGAR.** Glosa técnica/clínica roteia para `medico-auditor` (humano decide
o mérito — espelha `JUNTA_MEDICA` de AUTH). Catch-all = `ANALISE_HUMANA` / `analista-recurso-glosa`.

### `recurso_sla` (hitPolicy FIRST — DRAFT, todos os prazos DRAFT/verify)
| Direcao | Campo | Tipo | Dominio |
|---|---|---|---|
| in | `glosa_type` | string | tipo de glosa |
| in | `valor_glosado_brl` | double | valor glosado |
| in | `data_recebimento_recurso_iso` | string (ISO date) | âncora regulatória do teto P30D (documentada como input; não participa do matching; fail-safe `data_ciencia_glosa`) |
| out | `sla_analise` | string (ISO 8601) | prazo de análise interna do recurso |
| out | `sla_alerta` | string (ISO 8601) | gatilho do alerta não-interruptivo (~50-70% do SLA) |
| out | `prazo_regulatorio` | string (ISO 8601, duração) | prazo máximo regulatório do recurso — **legado/observabilidade**; não alimenta mais nenhum timer (RN 424 — **DRAFT/verify**) |
| out | `prazo_max_absoluto_iso` | string (ISO 8601, datetime) | **teto absoluto** = âncora + P30D (FEEL); consumido via `timeDate` pelos 3 boundary de teto (GAP-RECURSO-1) |
| out | `fonte_regulatoria` | string | fonte da RN do prazo (DRAFT) |

## Papeis humanos (candidate groups)

> **OQ (open question):** `analista-recurso-glosa` e `coordenacao-recurso` são nomes **PROPOSTOS** —
> precisam de confirmação contra a taxonomia org do operador. `medico-auditor` reusa o grupo já
> existente em SP-OP-AUTH-001/CONTAS-001.

| Grupo | Papel | Tarefa |
|---|---|---|
| `analista-recurso-glosa` *(PROPOSTO)* | Analista de recurso de glosa | `UT_AnaliseRecursoAnalista` — **única origem de `decisao_recurso`**; `NAO_RECORRER` (manter glosa) só aqui |
| `medico-auditor` | Médico auditor | `UT_RevisaoAuditorMedico` — decide o mérito de glosa técnica/clínica (`ESCALAR_AUDITOR`) |
| `coordenacao-recurso` *(PROPOSTO)* | Coordenação de recurso de glosa | `UT_CoordenacaoRecursoAssume` (SLA de análise estourado), `UT_EscalonamentoPrazo` (prazo máx ANS) |

## SLAs

Substituem o auto-approve-on-timeout (48h) do reference: **nenhum timer auto-passa**; o timer
interruptivo de análise → coordenação humana; o prazo máximo regulatório → escalonamento humano.

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaRecurso`) | ~50-70% de `sla.sla_analise` (DMN `sla_alerta`) | não-interruptivo → `operadora.recurso.notify_sla_risk` | política interna |
| Análise do recurso (`BT_SlaAnaliseRecurso`) | `${sla.sla_analise}` | **interruptivo** → `recurso.sla_breached` (fase=`analise`); cancela `UT_AnaliseRecursoAnalista` → `UT_CoordenacaoRecursoAssume` | política interna (**substitui** o `Task_AutoApprove` 48h — INVERTIDO) |
| Aguardar resposta da operadora (`ICE_AguardarResposta`) | ref `P5D` | timer intermediário (loop TISS) com `GW_RecursoResolvido` + `loopCounter` limitado (ref `< 6`) | TISS — **DRAFT/verify** |
| Prazo máximo do recurso (`BT_PrazoMaxRecurso` / `BT_PrazoMaxCoord` / `BT_PrazoMaxAuditor`) | `timeDate ${sla.prazo_max_absoluto_iso}` = âncora (`data_recebimento_recurso_iso`; fail-safe `data_ciencia_glosa`) + `P30D` — **uma única janela absoluta**: os 3 boundary (analista/coordenação/auditor) expiram no MESMO instante; a escalada não estende o teto (GAP-RECURSO-1) | boundary não-interruptivo → `operadora.recurso.escalate_ans_timeout` → `UT_EscalonamentoPrazo` (humano; sem boundary de teto próprio — alvo pós-estouro, reanexar o mesmo instante insta-dispararia em cascata) | RN 424/2017 — **DRAFT/verify** |
| Pendência de documentação (`ICE_PrazoPendencia`) | ref `P5D` | event gateway → análise humana (humano decide destino) | **DRAFT/verify** |

Nota: prazos legais são em dias úteis; ISO 8601 usa dias corridos — valores conservadores; resolver
calendário útil no worker. **Nenhum desses timers produz desfecho adverso** — sempre roteiam a humano.

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_DESISTENCIA_NOT_HUMAN` | worker-guard em `operadora.recurso.register_desistencia` | o worker **recusa** registrar desistência/manutenção de glosa se `decisao_recurso != NAO_RECORRER` setado por humano (ou se `analista_id`/`justificativa_desistencia`/`valor_glosa_aceito`/`referencia_contratual` ausentes). Lança BPMN error; carrega `analista_id` na trilha (ADR-0007). É o 4º componente do padrão no-denial. |
| `ERR_RECURSO_INVALID_GLOSA` | declarado (`Error_RecursoGlosaInvalida`) para uso dos workers | worker lança BPMN error se `glosa_id` não referencia glosa confirmada/ativa em CONTAS; tratamento a detalhar na promoção a FINAL |

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) — autorado no wave posterior contra este contrato (§4-bis-F).
- **Confirmação de todos os prazos RN com jurídico/regulatório** (R2): prazo recursal e prazo máximo
  (RN 424/2017), fluxo de glosa/recurso (RN 305/RN 501 TISS), conversão dias-úteis→ISO conservadora.
- **Confirmação dos candidate groups propostos** (`analista-recurso-glosa`, `coordenacao-recurso`)
  contra a taxonomia org do operador (OQ).
- **R5 — inadmissibilidade por prazo procedural duro:** default = humano-gated (`ANALISE_HUMANA`);
  auto-route de prazo-expirado puro só com sign-off do jurídico — **não autorizado neste DRAFT**.
- Textos de carta/petição de recurso (port de `generate_appeal_documentation`) entram em review-queue
  como DRAFT; Marina monta dossiê, humano autora/aprova; nunca auto-arquiva.
- Registro dos tópicos em `config/topic_registry.yaml` (W0.2) e dos candidate groups onde aplicável.
- Política de suspensão de prazo durante pendência de documentação (confirmar regra RN).
- Detalhamento da semântica do loop de acompanhamento (`ICE_AguardarResposta` / `loopCounter`) e da
  conciliação de re-pagamento no deferimento (`operadora.recurso.reconcile_payment`).
