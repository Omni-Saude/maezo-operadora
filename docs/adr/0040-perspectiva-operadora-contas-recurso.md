# ADR-0040: Perspectiva de processo — a operadora e o dono de SP-OP-CONTAS-001 e SP-OP-RECURSO-001

**Status:** Proposed
**Data:** 2026-09-02
**Area:** Orquestracao / Compliance / Produto

> **Nota de proveniencia (obrigatoria).** Este ADR e uma **PROPOSTA NAO RATIFICADA**, autorada na
> fase de DESENHO do pacote de remediacao. Nenhum artefato de `spec/`, `src/` ou `tests/` foi
> alterado por ele. Ele consolida um achado de auditoria de perspectiva
> (`docs/audits/maezo-deep-audit/`, parte A da cadeia financeira) que e uma **reivindicacao
> verificada por um segundo agente em paralelo** — cada fato de estado-atual citado abaixo foi
> re-derivado pelo autor deste ADR a partir dos proprios artefatos, com `file:line`. Nenhuma
> citacao de RN foi validada por SME neste documento; os marcadores `DRAFT/verify` existentes
> permanecem e novos foram acrescentados onde a auditoria de regulacao ja aponta erro
> (`docs/compliance/rn-currency-review.md`).

---

## Contexto

### O fato estrutural

`SP-OP-CONTAS-001` e `SP-OP-RECURSO-001` foram portados do doador hospitalar (ADR-0011,
`docs/reports/phase2-plan.md:225`) com **inversao do anti-padrao de auto-aprovacao**, mas **sem
inversao do ATOR**. O plano de porte e explicito quanto ao escopo do que foi invertido — «inverte
auto-aceite no timeout» — e as `§Notas de design / inversao do reference` dos dois contratos
listam apenas itens de auto-aceite/timeout/gating (`docs/processes/contracts/SP-OP-CONTAS-001.md:163-170`;
`docs/processes/contracts/SP-OP-RECURSO-001.md:8`). **Nenhum item trata de quem e o dono do
processo.** O resultado e que os dois processos executam, hoje, o ciclo de vida do **prestador**
(a parte que SOFRE a glosa) dentro de uma plataforma cujo dono declarado e a **operadora** (a parte
que a APLICA).

A contradicao e literal e interna a cada arquivo:

| # | Evidencia | Onde |
|---|---|---|
| 1 | «Glosa = operadora negando/reduzindo pagamento de linha de conta ao prestador» | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:14-15` |
| 2 | …e a UNICA User Task decisoria do mesmo arquivo decide `decisao_contas in {RECORRER, ACEITAR_GLOSA, REENVIAR}` — as tres acoes de quem **sofre** a glosa | `…SP-OP-CONTAS-001_…bpmn:143` |
| 3 | O contrato de CONTAS define glosa como ato da operadora (`:5`) e o invariante L0 protege a «**aceitacao** de uma glosa substantiva **contra o prestador**» (`:10-12`) — so quem sofre a glosa a aceita | `docs/processes/contracts/SP-OP-CONTAS-001.md:5` vs `:10-12` |
| 4 | `End_RecursoIndeferido` = «Recurso indeferido **pela operadora** (resposta de terceiro)»; a documentacao do publish diz «Indeferimento pela operadora externa NAO e uma desistencia **da Maezo** — e resposta de terceiro» | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:526`, `:515` |
| 5 | `ST_SubmitAppeal` «**Interpor recurso a operadora (TISS)**» → `GW_AguardarResposta` «Aguardar resposta da operadora» → `ST_TrackStatus` «Acompanhar status do recurso interposto» → `GW_RecursoResolvido` «Resposta da operadora» (`${resposta_operadora == …}`) → `ST_ReconcilePaymentDeferido` «Conciliar re-pagamento ao prestador» | `…SP-OP-RECURSO-001_…bpmn:409, 423, 442, 459, 467, 659` |
| 6 | O contrato de RECURSO nomeia o prestador como autor do recurso (`prestador_id` = «Prestador **recorrente (autor do recurso)**») enquanto o BPMN do proprio processo o interpoe | `docs/processes/contracts/SP-OP-RECURSO-001.md:43` vs BPMN `:409` |
| 7 | O codigo declara que a plataforma interpoe recursos: «RECURSO-001 **owns the recurso filing** … CONTAS never files the recurso» | `src/maezo/tools/workers/contas.py:492` |
| 8 | KPI da agente Marina: `recurso_recovery_rate` — taxa de **recuperacao de receita glosada**, metrica de quem teve dinheiro retido | `spec/agents/marina/agent.yaml:34` |
| 9 | O prompt de Marina declara a operadora (`prompts.py:25-26`) e imediatamente instrui na voz do recorrente: «Voce NUNCA **desiste de um recurso** (manter a glosa)» e «Voce nao autora a **peticao de recurso**; so monta o dossie» | `src/maezo/agents/marina/prompts.py:37-38`, `:104-105` |
| 10 | Incoerencia transversal: PAGTO pressupoe um CONTAS **pagador** que nao existe — «SP-OP-CONTAS-001 (a conta adjudicada gera a obrigacao de pagamento)» | `spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn:26`; `docs/processes/contracts/SP-OP-PAGTO-001.md:5` |

`SP-OP-RECURSO-001` e **bifacial**: uma fase de julgamento com forma de pagador
(`BRT_Admissibilidade` `:110-118`, `ST_SolicitarDocumentos` «Abrir pendencia de documentacao **ao
prestador**» `:128-129`, `UT_RevisaoAuditorMedico`/`medico-auditor` `:382-383`,
`ST_RegisterInadmissivel`/`End_RecursoInadmissivel` `:577-595`) desagua, pelas arestas
`Flow_GWDec_Recorrer` (`:630`) e `Flow_GWMerito_Manter` (`:644`), num ciclo de vida integralmente
de recorrente. Essa juncao e o ponto exato do enxerto — e o achado estrutural.

### Por que isso e um problema de compliance, nao de redacao

1. **O efeito adverso guardado e o errado.** O padrao no-denial de cinco partes (ADR-0018) exige
   que o **efeito adverso** nasca so numa User Task humana e seja materializado por UM worker
   guardado. Hoje os dois guards protegem atos do **prestador**: `ERR_GLOSA_ACCEPT_NOT_HUMAN`
   protege o **aceite** da glosa (`contas.py:407-437`) e `ERR_DESISTENCIA_NOT_HUMAN` protege a
   **desistencia** do recurso (`recurso.py:434-497`). O ato que a operadora de fato pratica contra
   o prestador — **glosar** e **indeferir o recurso** — nao tem guard nenhum, porque nao existe
   como caminho no modelo.
2. **O SLA esta ancorado do lado errado em parte da cadeia.** `recurso_sla.dmn` usa como fail-safe
   `data_ciencia_glosa` (`:52-54, :68, :79, :90, :101`), que e a ancora do prazo do **recorrente**
   (contar da ciencia da glosa para interpor), enquanto a ancora primaria
   `data_recebimento_recurso_iso` e a do **pagador** (contar do recebimento para responder). O
   fail-safe troca de perspectiva em silencio.
3. **As citacoes de RN estao misatribuidas — e isso ja esta documentado.**
   `docs/compliance/rn-currency-review.md:39-40, 45, 48, 83, 85-87` e o redline `§3.8` em `:188-193`
   ja determinam: RN 305/2012 (CONTAS) e RN «305/2016» (RECURSO, ano inexistente) foram revogadas e
   resolvem para **RN 501/2022**; **RN 424/2017 e vigente mas so se aplica a JUNTA MEDICA**
   (divergencia tecnico-assistencial), **nao** a prazo de analise de conta nem a prazo de recurso de
   glosa. Todo `fonte_regulatoria` de `contas_sla.dmn` (`:72, 83, 94, 105`) e de `recurso_sla.dmn`
   (`:69, 80, 91, 102`) cita RN 424/2017 para prazo de conta/recurso — misatribuicao.
4. **Nao ha, hoje, nenhum artefato que produza a glosa que RECURSO-001 pressupoe.** O contrato de
   RECURSO declara consumir «uma glosa CONFIRMADA produzida por SP-OP-CONTAS-001»
   (`SP-OP-RECURSO-001.md:7`), mas CONTAS-001 nao emite glosa: ele a **aceita**. A cadeia inteira
   pende de um fato que nenhum processo da plataforma cria.

### O que ja esta certo e serve de espelho

`SP-OP-REEMBOLSO-001` e `SP-OP-PAGTO-001` sao **pagador correto** e sao o modelo interno:

- `SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:498-509` — `decisao_reembolso ∈ {APROVAR, NEGAR,
  APROVAR_PARCIAL, SOLICITAR_INFO, SOLICITAR_AUDITOR}`; o default do gateway e `APROVAR` (favoravel);
  o adverso (`NEGAR`, e tambem `APROVAR_PARCIAL` — reducao) e humano-gated e materializado por
  `send_reembolso_denial`, guard `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` (`reembolso.py:564-572`).
- `SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn:408-410` distingue corretamente recusa de ordem de glosa:
  «NAO e glosa (glosa nasce em CONTAS-001)».
- `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:565-568, 644` (GAP-AUTH-3) e o padrao mais endurecido de
  default de gateway decisorio: decisao humana ausente/desconhecida → terminal de **erro**
  fail-closed, nenhum efeito.

---

## Decisao

### D1 — O dono de SP-OP-CONTAS-001 e SP-OP-RECURSO-001 e a OPERADORA (pagador)

Os dois processos sao **redesenhados do zero na perspectiva do pagador**. Nao ha shim de
compatibilidade: o caminho de perspectiva-prestador e **removido**, nao desativado por flag nem
mantido em paralelo. As chaves de processo `SP-OP-CONTAS-001` e `SP-OP-RECURSO-001` **permanecem
inalteradas** (`src/maezo/tools/process_allowlist.py:21-42`, espelhadas em
`src/maezo/gateway/effect_pep.py:174-192`): `KNOWN_PROCESS_KEYS` continua com as mesmas 15 chaves.

- **SP-OP-CONTAS-001 — Analise e Adjudicacao de Conta Medica.** A operadora RECEBE o lote de guias
  TISS do prestador, apura divergencias, e **decide** pagar, glosar (total ou parcialmente) ou
  devolver a conta. Ela **EMITE** o demonstrativo de analise da conta com os codigos de glosa.
- **SP-OP-RECURSO-001 — Analise de Recurso de Glosa.** A operadora RECEBE o recurso de glosa
  interposto pelo **prestador** contra uma glosa que ela propria aplicou, julga a admissibilidade,
  analisa o merito e **EMITE** a resposta: deferir, deferir parcialmente ou indeferir.

### D2 — O teste de perspectiva e criterio de aceitacao, nao recomendacao

Todo elemento de BPMN, entrada/saida de DMN, entrada de YAML, frase de contrato, passo de
test-spec, handler/topico de worker e definicao de agente das duas cadeias DEVE passar nas quatro
perguntas:

1. **Q1 — Quem executa?** Areas da operadora (analise de contas, auditoria medica, coordenacao) ou
   sistemas dela. Nunca o prestador, nunca o beneficiario.
2. **Q2 — Direcao TISS.** A operadora **RECEBE** guias, lotes e recursos; **EMITE** demonstrativos,
   autorizacoes, negativas, glosas e respostas de recurso. Um ator que recebe demonstrativo,
   interpoe recurso, acompanha a resposta da operadora ou concilia um re-pagamento *para si* e
   perspectiva-prestador.
3. **Q3 — Quem sofre o adverso e de quem e o SLA.** O adverso recai sobre o **prestador**
   (`GLOSAR`, `INDEFERIR`, `DEFERIR_PARCIAL`) ou sobre o **beneficiario**, nunca sobre o dono do
   processo. O prazo que este repositorio cronometra e o prazo **da resposta do pagador**; o prazo
   de interposicao do recurso e do prestador e nao e responsabilidade nossa.
   **Ressalva metodologica (obrigatoria).** Este criterio **nao** pode ser fundamentado em «RN
   424/2017 vincula a resposta do pagador ao recurso». Essa atribuicao e classificada como ERRADA
   pelo proprio repositorio: RN 424/2017 e norma de **junta medica ou odontologica**
   (`docs/compliance/rn-currency-review.md:45, 85-87`), e o redline §3.8 (`:190-193`) determina que
   prazo de glosa/recurso resolve para **RN 501/2022** — ele proprio marcado `[verify SME]`. Q3 se
   sustenta por **quem sofre o efeito** e por **qual relogio o processo de fato conta**
   (`data_recebimento_lote` / `data_recebimento_recurso_iso`, ambos eventos de RECEPCAO pelo
   pagador), nunca por uma citacao normativa nao confirmada.
4. **Q4 — Vocabulario de saida.** As DMN e as User Tasks nomeiam decisoes do pagador — `PAGAR`,
   `GLOSAR`, `PAGAR_PARCIAL`, `DEVOLVER`, `DEFERIR`, `DEFERIR_PARCIAL`, `INDEFERIR`,
   `ANALISE_HUMANA` — nunca acoes do recorrente (`RECORRER`, `NAO_RECORRER`, `DESISTIR`,
   `REENVIAR`, `ACEITAR_GLOSA`, `MANTER_RECURSO`, `RECORRIVEL`).

### D3 — O efeito adverso guardado passa a ser o ato do pagador

| Cadeia | Efeito adverso (L0 hard) | Quem sofre | Worker unico que o materializa | Guard |
|---|---|---|---|---|
| CONTAS | **Aplicar glosa** (negar/reduzir pagamento de linha de conta) | prestador | `operadora.contas.registrar_glosa` | `ERR_CONTAS_GLOSA_NOT_HUMAN` |
| CONTAS | **Pagar parcial** (glosa parcial — reducao) | prestador | idem | idem |
| RECURSO | **Indeferir o recurso** (manter a glosa) | prestador | `operadora.recurso.registrar_indeferimento` | `ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN` |
| RECURSO | **Deferir parcialmente** (manter parte da glosa) | prestador | idem | idem |
| RECURSO | **Inadmitir o recurso** | prestador | idem | idem |

Os dois codigos novos terminam em `_NOT_HUMAN` e sao portanto reconhecidos automaticamente como
guard-refusal pelo predicado de padrao do harness (`src/maezo/tools/workers/harness.py:546-561`:
«ALL `*_NOT_HUMAN` guard codes … A NEW guard code is recognized automatically by the `_NOT_HUMAN`
suffix»). Eles substituem, um-para-um, `ERR_GLOSA_ACCEPT_NOT_HUMAN` e `ERR_DESISTENCIA_NOT_HUMAN`.

**Semantica ADR-0030 preservada sem alteracao:** ambos permanecem **declarados-e-nao-capturados**
(nenhum `bpmn:boundaryEvent` com `errorEventDefinition` sobre a service task guardada — exatamente
como hoje: `SP-OP-CONTAS-001_…bpmn` tem zero error-boundaries, conforme o censo do proprio ADR-0030
em `docs/adr/0030-worker-error-semantics-bpmn-boundary.md:118`; e `ERR_DESISTENCIA_NOT_HUMAN`
tambem nao aparece na tabela de boundaries `:113-142`). Os workers continuam levantando
`PermissionError` (nao `WorkerBpmnError`), o que os mantem no caminho de **incidente auditado**, e
nao no caminho de `bpmnError`. Isso respeita ADR-0030 §5 (`:379-382`: «a guard code with **no**
modeled boundary → **incident**, unchanged») e nao consome o co-requisito T-E (`:355-371`).

### D4 — Nenhuma DMN emite o adverso; o unico desfecho automatico e o favoravel

- `glosa_triage`: o dominio de `roteamento` passa a ser **EXATAMENTE `{PAGAR, ANALISE_HUMANA}`**.
  O unico desfecho nao-humano e `PAGAR` (favoravel ao prestador, nao-adverso). Toda candidata a
  glosa — inclusive a divergencia de valor que hoje emite `RECORRER` (`glosa_triage.dmn:83-92`) —
  roteia para a User Task humana. Catch-all → `ANALISE_HUMANA` (inalterado).
- `recurso_admissibility`: dominio inalterado `{SEGUE_ANALISE, PENDENTE_DOCUMENTACAO,
  ANALISE_HUMANA}` — ja e vocabulario legitimo de julgador. So a `<description>` muda.
- `recurso_eligibility`: `RECORRIVEL` → **`SEGUE_MERITO`**; `grupo_revisor` inalterado.
- Nenhuma DMN nova; nenhuma DMN removida; **`orphans-allowlist.yaml` tem delta ZERO** (nenhuma das
  7 decisoes e orfa hoje — todas resolvidas por `decisionRef` no BPMN — e nenhuma passa a ser).
- Hit policy `FIRST` com row catch-all conservadora em toda tabela: inalterado.

### D5 — Os gateways decisorios adotam o default fail-closed da GAP-AUTH-3

`GW_DecisaoContas`, `GW_DecisaoRecurso` e `GW_MeritoAuditor` passam a ter **todo ramo real com
`conditionExpression` propria** e o `default` apontando para um **end event de erro**
(`ERR_CONTAS_DECISAO_INVALIDA` / `ERR_RECURSO_DECISAO_INVALIDA`), espelhando
`SP-OP-AUTH-001_…bpmn:565-568` + `:644`. Uma decisao humana ausente ou desconhecida produz **efeito
nenhum** — nem glosa, nem pagamento, nem devolucao. Isso substitui o default de hoje, que e
`Flow_GWDec_Recorrer` em ambos os processos (`CONTAS:194, 304`; `RECURSO:370, 630`) — a acao do
recorrente como comportamento conservador padrao.

### D6 — Os handoffs sao redesenhados; a aresta CONTAS→RECURSO e DELETADA

| Handoff | Hoje | Depois |
|---|---|---|
| CONTAS → PAGTO-001 | **inexistente** (PAGTO ja o pressupoe: `PAGTO…bpmn:26`) | `operadora.contas.handoff_pagamento`, `tipo_pagamento=prestador_rede`, business key `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}` (a alternativa que o proprio contrato de PAGTO ja preve, `SP-OP-PAGTO-001.md:54-55`) |
| CONTAS → RECURSO-001 | `operadora.contas.start_recurso` a partir de `decisao_contas==RECORRER` | **DELETADO.** A operadora nao interpoe recurso contra a propria glosa. RECURSO-001 passa a ser iniciado pelo **recurso que o prestador transmite**, via intake TISS → `notification_bridge` → `start_process_idempotent` |
| CONTAS → FRAUDE-001 | `operadora.contas.start_fraude` | **MANTIDO integralmente**, sem alteracao |
| RECURSO → PAGTO-001 | `ST_ReconcilePayment*` (conciliar re-pagamento recebido) | `operadora.recurso.handoff_pagamento`, `tipo_pagamento=glosa_revertida` (valor ja previsto em `SP-OP-PAGTO-001.md:68`) |

`SP-OP-PAGTO-001` e familia **STRICT** de dedup de start
(`src/maezo/tools/mcp_cibseven/transport.py:873`): os dois novos handoffs exigem as seams
`DedupReportingAuditSink` + `HistoryQueryingTransport` (`transport.py:915-947`), sob pena de
`StartDedupGateUnavailableError` — fail-closed, nunca degradacao silenciosa.

### D6-bis — I-PAGTO-1: o processo de origem fornece a EVIDENCIA do lastro; so PAGTO resolve o FATO

> **Correcao de uma afirmacao ERRADA da versao anterior deste ADR.** D6 e as consequencias diziam
> que o gate financeiro dos dois handoffs e `ERR_PAYMENT_RELEASE_NOT_HUMAN` + tier-match. **Nao e.**
> Rastreado contra `main 35cffd3`: esse guard e declarado em
> `spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn:14` e guarda **exclusivamente**
> `ST_ReleaseHighValue` (`:384-387`; `src/maezo/tools/workers/pagto.py:92, :502, :559`). A faixa
> `DENTRO_TETO_L2` segue outro caminho — `Flow_GW_DentroTeto` (`:471`) → `ST_ReleaseLowValue`
> (`:227-232`) → `End_PagamentoLiberadoAutomatico` (`:245`) — **sem nenhuma User Task**. O unico
> fato que fecha essa faixa hoje e o default fail-closed `lastro_confirmado=False` em
> `pagto.py:128`, que `validate_pagto` ecoa da semente do start (`:121-147`); e
> `docs/processes/contracts/SP-OP-PAGTO-001.md:76` define `lastro_confirmado` como «ha obrigacao
> real por tras (**conta adjudicada** …)» — ou seja, **convida** o implementador a semea-lo.
> `dentro_teto_l2` nao e semeavel (`route_aprovacao` o computa pelo `CeilingResolver` e o escreve de
> volta, `pagto.py:274-276, :398, :434`), mas resulta `true` legitimamente para qualquer valor
> abaixo do teto do tenant.

**Decisao (vinculante).** `operadora.contas.handoff_pagamento` e
`operadora.recurso.handoff_pagamento` **NUNCA** semeiam `lastro_confirmado`,
`dados_pagamento_validos`, `duplicidade_suspeita` nem `dentro_teto_l2`. Semeiam, em lugar disso,
duas variaveis **aditivas e opcionais** de proveniencia — `lastro_origem` ∈
`{contas_adjudicacao_automatica, contas_adjudicacao_humana, recurso_deferimento_humano}` e
`lastro_decisor_id` (vazio na perna automatica) — que instruem o formulario humano.

Consequencia por construcao: `pagto_admissibility` le `lastro_confirmado` ausente ⇒ `False` ⇒ row
`r_sem_lastro` ⇒ `ANALISE_HUMANA` ⇒ `Flow_GWAdmiss_Humana` (`…PAGTO…bpmn:463`, **default** de
`GW_Admissibilidade` `:146`) ⇒ `UT_AnaliseAdmissibilidade` (`:155-169`, grupo
`coordenacao-financeira`). **Toda ordem originada em CONTAS ou RECURSO atravessa uma User Task
humana antes de qualquer roteamento de alcada.** Esse — e nao `ERR_PAYMENT_RELEASE_NOT_HUMAN` — e o
gate destes caminhos.

Em RECURSO a invariante compra algo a mais que HITL: **segregacao de funcoes.** Sem ela, quem julgou
o recurso teria confirmado o lastro da ordem de pagamento dele proprio.

**Amarras obrigatorias** (prosa nao basta — foi prosa que falhou): teste unitario de **igualdade de
conjunto** sobre as chaves semeadas; teste negativo nominal para as quatro proibidas; teste de
integracao no engine real de que uma ordem originada em CONTAS nao alcanca
`End_PagamentoLiberadoAutomatico` sem `UT_AnaliseAdmissibilidade` na history; e **correcao textual
da celula `SP-OP-PAGTO-001.md:76`**, que e a origem do convite (o rodape `:80` do proprio contrato
ja diz que os tres booleanos sao «pre-resolvidos por worker de fatos» — a celula e que o
contradiz). Especificacao completa em
`docs/audits/maezo-deep-audit/remediation/REDESIGN-SP-OP-CONTAS-001.md` §1.5-bis.

### D6-ter — Todo terminal que afeta o prestador emite comunicacao TISS

O demonstrativo de analise de conta e declarado como a mensagem de saida de CONTAS; a versao
anterior deste pacote o emitia em **2 dos 6** terminais. Passa a ser **5 dos 6**, por
`operadora.contas.emitir_demonstrativo` com discriminador `tipo_comunicacao ∈
{demonstrativo_analise, devolucao_para_correcao}` (um so topico, como
`operadora.recurso.comunicar_resposta` ja serve quatro desfechos). Ordem canonica de todo terminal:
**registrar/decidir → comunicar → handoff de pagamento (se houver) → publicar evento → end.**

A **unica** excecao e `End_EncaminhadaFraude`: notificar um prestador sob investigacao de fraude e
alerta-lo. A omissao e deliberada, e o custo — a conta fecha em CONTAS sem resposta ao prestador —
fica registrado em **OQ-14**, nao resolvido aqui.

Em particular, `DEVOLVER` deixa de ser silencioso. Sem `ST_ComunicarDevolucao` o prestador nunca
sabia que a conta voltou, e `Msg_ContasLinhasAtualizadas` — a mensagem que o processo mantem
justamente para receber as linhas corrigidas — ficava **sem gatilho**.

### D7 — Um gate de CI de **regressao de vocabulario** sobre `spec/`, com limite declarado

> **Reformulacao obrigatoria.** A versao anterior dizia que o gate «rejeita o vocabulario de
> perspectiva-prestador». Isso prometia mais do que foi construido: o gatekeeper R1 implementou o
> lexico proposto e o atacou com 15 frases — **8/8 frases legitimas do pagador foram rejeitadas** e
> **7/7 frases de perspectiva-prestador passaram**, entre elas
> `decisao_contas in {CONTESTAR, ACATAR_GLOSA, REAPRESENTAR}`, que e a **mesma inversao** que este
> ADR existe para eliminar, escrita com sinonimos. D7 passa a afirmar exatamente o que o gate faz.

**O que o gate e.** Um validador em `src/maezo/platform/validation/perspective.py` — modulo que
**nao** e CODEOWNED, escolha deliberada —, chamado de dentro do `validate_artifacts` que ja roda
BPMN/DMN/crossref (`src/maezo/platform/validation/cli.py:95-131, :134-140`). **Nenhuma alteracao em
`/Makefile`, em `/scripts/ci/` ou em `.github/workflows/`** — as tres sao superficies CODEOWNED ou
owner-gated, e o gate entra dentro de um alvo que ja existe. Ele **falha o build** quando encontra
vocabulario de perspectiva-prestador em duas superficies:

| Tier | Superficie | Como e lido | Classes aplicadas |
|---|---|---|---|
| **A** | `spec/processes/bpmn/*.bpmn`, `spec/processes/dmn/*.dmn` | linhas do arquivo bruto, **inclusive comentarios XML** | R1 + R2-CORE + R2-CTX |
| **B** | `spec/processes/dmn/*.yaml`, `spec/agents/*/agent.yaml`, `spec/policies/autonomy/*.yaml` | **nos do YAML parseado** (chaves e valores), sem comentarios | R1 + R2-CORE |

A assimetria e por **formato**, universal, e justificada: em BPMN/DMN o comentario e documentacao de
desenho dentro da definicao do processo (um dos hits mais diretos da inversao e um comentario XML,
`…RECURSO…bpmn:408`); num manifesto YAML de spec o bloco de comentario e a **narrativa de auditoria
de um achado**, que por design registra o que um artefato *dizia*. E o que governa comportamento em
YAML sao chaves e valores. **Nao ha allowlist de excecoes, nem por arquivo nem por bloco
`historico:`** — a hipotese de um bloco historico foi avaliada e **recusada**. As unicas
desambiguacoes sao lexicais (exigencia de objeto + pista de ator) e estruturais (qual tier le qual
superficie).

**O que o gate NAO e — limite declarado.** Ele e um **portao de regressao de vocabulario**, nao um
validador de perspectiva. Uma inversao **renomeada com vocabulario inedito** passa. O lexico refeito
fecha as familias de sinonimos conhecidas (`CONTESTAR`, `ACATAR_GLOSA`, `REAPRESENTAR`,
`CONTESTAVEL`, `fonte pagadora`, `pleito`, primeira pessoa do plural), mas o proximo par de
sinonimos ineditos passara igual. **O controle primario continua sendo o teste de perspectiva humano
de D2**; o gate e rede de seguranca. Superficies fora de `spec/` (`src/maezo/**`, `docs/**`) nao tem
gate estrutural — tem apenas um teste de unidade por regex (R1 + R2-CORE), mais fraco, e este ADR
diz que e mais fraco em vez de afirmar cobertura que nao existe.

**Medicao.** Com o lexico **anterior**, a varredura de Tier A produziu **286 hits em 7 arquivos** e
**0** nos outros 69 artefatos — medicao **reproduzida byte-a-byte** pelo gatekeeper independente:

| Arquivo | Hits (lexico anterior) |
|---|---|
| `SP-OP-RECURSO-001_Recurso_Glosa.bpmn` | 184 |
| `SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn` | 51 |
| `recurso_eligibility.dmn` | 21 |
| `glosa_triage.dmn` | 12 |
| `recurso_admissibility.dmn` | 10 |
| `recurso_sla.dmn` | 7 |
| `contas_sla.dmn` | 1 |
| **Total** | **286 em 7 arquivos** |
| **Todos os outros 14 BPMN + 55 DMN** | **0** |

Tier B, medido contra `main 35cffd3`: **6 hits em 3 arquivos** —
`spec/policies/autonomy/action-approvals.yaml` (4: `:259`, `:368`, `:649`, `:667`),
`spec/agents/marina/agent.yaml` (1: `:34`, `recurso_recovery_rate`) e
`spec/processes/dmn/glosa-triage-shadow-candidate.yaml` (1: `:337`). Os quatro do primeiro sao
exatamente as superficies penduradas que a delecao dos topicos torna obrigatorio remover.

> **`DRAFT/verify` — o total sob o lexico corrigido nao e afirmado aqui.** Ele muda (regras
> removidas por serem largas demais; regras novas antirregressao; exigencia de objeto e pista de
> ator) e **tem de ser medido** no primeiro PR, publicado no corpo dele e pinado em
> `test_two_chains_hit_count_is_pinned`. Depois do redesenho o alvo e **0** em Tier A e Tier B.
> Nao-vacuidade e medida **contra um corpus sintetico de fixtures**, nunca contra a arvore viva, e
> um pino de inventario de regras impede que alguem apague uma regra para ficar verde.
> Especificacao completa — lexico R1/R2-CORE/R2-CTX, fronteiras de identificador, janela de 40
> caracteres, plano de entrada em vigor e as 15 frases do ataque — em
> `docs/audits/maezo-deep-audit/remediation/REDESIGN-SP-OP-CONTAS-001.md` **§6**.

### D8 — Valores monetarios continuam atuarialmente gateados e vindos de DMN

Nenhuma tabela de valores em Python. O anti-padrao de referencia e `_BASE_VALUES_CENTS` em
`src/maezo/tools/workers/reembolso.py` (achado F-1 da auditoria). Toda aritmetica dos workers das
duas cadeias permanece **pura** (soma/subtracao/clamp sobre centavos-inteiros vindos das variaveis
de processo) e todo **threshold** ou **tabela** vive em DMN (`contas_sla.dmn:77` `> 50000.0`;
`recurso_sla.dmn:74` idem), nunca em codigo — regra ja declarada nas proprias DMN
(`contas_sla.dmn:14`, `recurso_sla.dmn:14`: «Prazo regulatorio vive AQUI (DMN) e no timer BPMN —
nunca em Python»). O valor efetivamente pago sai de PAGTO-001, sob a escada de alcada daquele
processo — na faixa de alto valor com `ERR_PAYMENT_RELEASE_NOT_HUMAN` + tier-match e, em **toda**
faixa, precedido pela admissao humana que **D6-bis** garante (a faixa `DENTRO_TETO_L2` nao tem User
Task propria, e e por isso que a invariante existe).

---

## Consequencias

**Positivas:**

- O invariante L0 passa a proteger o ato **que a operadora de fato pratica**. Hoje ha um guard
  robusto sobre um evento que nunca ocorre no mundo real da operadora (ela nao «aceita» a propria
  glosa) e **zero** guard sobre o ato que ela pratica todo dia (glosar). O redesign fecha essa
  lacuna sem afrouxar nada.
- A incoerencia transversal CONTAS↔PAGTO (`PAGTO…bpmn:26`) deixa de ser uma premissa falsa e passa
  a ser um handoff real e testado.
- `SP-OP-RECURSO-001` passa a ter uma origem coerente: o recurso chega do prestador, contra uma
  glosa que a plataforma emitiu e cujo `glosa_id` ela mesma cunhou. Hoje o `glosa_id` do ramo
  `RECORRER` vem de fora (`contas.py:517-521`), razao pela qual RECURSO-001 e classificado
  NON-strict em `_START_DEDUP_POLICY` (`transport.py:880`) — o redesign torna esse identificador
  interno e deterministico.
- As citacoes de RN param de contradizer `docs/compliance/rn-currency-review.md`, que ja carrega o
  redline (`§3.8`, `:188-193`).
- O gate de CI torna a regressao **detectavel por construcao**, e nao dependente de revisao humana
  atenta: os 286 hits medidos provam que ele nao e vacuo, e os 0 hits nos outros 69 artefatos
  provam que ele nao e ruidoso.

**Negativas (aceitas):**

- **Blast radius alto.** As duas suites de integracao, as duas suites unitarias de worker, os 4
  golden evals de Marina, o teste de paridade DMN, o manifesto shadow-candidate (incluindo o pin
  `tabela_viva.sha256`) e a regra CONTAS→RECURSO do `notification_bridge` sao **reescritos**, nao
  ajustados. O piso de `make test` (8075 passed) tem de ser reconquistado, nao preservado teste a
  teste.
- **Perda de cobertura declarada temporariamente.** ADR-0018 cita `test_nenhum_caminho_automatizado_aceita_glosa`
  como prova das cinco partes (`:41-42`); esse teste e substituido por
  `test_nenhum_caminho_automatizado_glosa` (varredura da nova `glosa_triage`). Ate ele estar verde
  contra o engine real, a conformidade das duas cadeias e **autorada, nao provada** — mesma regra
  que ADR-0018 aplica aos cinco bodies de PR #38 (`:97-103`).
- **O manifesto `glosa-triage-shadow-candidate.yaml` precisa ser reautorado** e continua
  `ratificado: false`. O achado M-4 que ele documenta (a row permissiva com conjunto negativo
  `not("tecnica","clinica")` admitindo `"desconhecida"`) **NAO e fechado por este redesign** — a
  edicao da tabela viva nesse ponto e ato do dono da tabela (ADR-0028 §7; `docs/review-queue.md:627`)
  e permanece aberta.
- **O redesign MUDA A CONSEQUENCIA de M-4, e isso e divulgado aqui e em OQ-10.** Hoje o input
  defeituoso (`categoria_normalizada="desconhecida"` com item conforme, sem divergencia de valor e
  com documentacao) casa `r_sem_glosa`, emite `SEM_GLOSA` e termina em `End_SemGlosa`
  (`…CONTAS…bpmn:102`) — **sem efeito e sem que ninguem olhe**. Depois do redesign o mesmo input
  emite `PAGAR`, emite um demonstrativo ao prestador e cria uma ordem em SP-OP-PAGTO-001, familia
  STRICT de dedup.
  **Sob D6-bis (I-PAGTO-1) essa ordem para numa User Task humana e nao vira dinheiro** — o raio de
  M-4 vai de *zero-efeito-e-invisivel* para *ordem visivel e humano-gated*, o que e **mais**
  visibilidade humana, nao menos. **O que nao melhora, e e o residuo aceito:** uma conta com
  categoria de glosa nao mapeada e adjudicada como «pagar integralmente» sem que nenhum analista de
  contas a veja — perda de receita da operadora, nao efeito adverso ao prestador.
  **Se a perna automatica pode entrar antes de OQ-10 ser fechada e decisao do dono da tabela.** A
  recomendacao deste ADR e **sim**, sob D6-bis, com a **variante conservadora pre-especificada**
  disponivel em uma linha: retargetar `Flow_GWTriagem_Pagar` de `ST_EmitirDemonstrativoIntegral`
  para `ST_PrepareTriageDossier`, o que transforma `glosa_triage=PAGAR` em **recomendacao ao
  analista** e deixa como unica perna automatica `has_glosas == false`, que nao consulta
  `glosa_triage` e portanto e imune a M-4. Os dois lados do balanco estao em
  `REDESIGN-SP-OP-CONTAS-001.md` §1.5-bis.
- **Latencia humana aumenta em CONTAS.** Com o dominio de `glosa_triage` reduzido a
  `{PAGAR, ANALISE_HUMANA}`, toda glosa candidata passa por User Task. Na pratica o comportamento
  hoje ja e esse (`RECORRER` e `ANALISE_HUMANA` compartilham o mesmo ramo default
  `Flow_GWTriagem_Humano`, `CONTAS…bpmn:295`), mas a partir daqui isso passa a ser estrutural e nao
  acidental.
- **Nenhuma citacao de RN aqui e validada.** Este ADR apenas troca uma misatribuicao ja documentada
  por outra fonte ja documentada; ambas continuam `DRAFT/verify` ate SME.

**Limite operacional do achado (dimensionamento honesto — RE-DERIVADO).**

> **Uma afirmacao da versao anterior deste ADR esta RETIRADA.** Ela dizia: «**nenhum processo de
> Phase 2 esta start-habilitado em `main`** (`docs/adr/0018-no-denial-structural-replication.md:106-110`)».
> **Isso e falso hoje.** A propria passagem citada enuncia a sua pre-condicao — «o allowlist de
> inicio de processo (W0.1 …, ADR-0016) e um **PR humano CODEOWNERS aberto e nao mergeado (PR
> #32)**» (`:106-108`) — e essa pre-condicao **caducou**: em `main 35cffd3`,
> `src/maezo/tools/process_allowlist.py:21-41` contem as **15** chaves, incluindo
> `SP-OP-CONTAS-001` (`:28`), `SP-OP-RECURSO-001` (`:29`) e `SP-OP-PAGTO-001` (`:40`), e `:60`
> declara `DEFAULT_ALLOWED_PROCESS_KEYS: Final[frozenset[str]] = KNOWN_PROCESS_KEYS`, com o
> comentario de `:44-59` dizendo que esse default «**IS NOW LOAD-BEARING, and it was not before**»
> (correcao **D-m2**, gate delta: citado como `:63`/`:45-62` na versao anterior — `:63` e
> `_PROCESS_KEY_PATTERN`, uma regex nao relacionada).
> A frase de ADR-0018 e um artefato obsoleto do seu proprio estado de merge. Ela **nao e reescrita
> por este pacote** (o corpo de um ADR vigente e ato do owner); recebe a nota de emenda abaixo e uma
> linha em `docs/review-queue.md`, para que o proximo leitor nao a reimporte como limite de risco.

O limite real, re-derivado do que continua verdadeiro e verificado:

| Fato | Evidencia |
|---|---|
| O ramo do recorrente nao transmite nada | `recurso.py:862-864` («TASY write DROP … **nenhuma chamada TISS real e feita aqui**»); o corpo (`:869-877`) so cunha `RECAPPEAL-{business_key}`; `track_status` (`:933-947`) so loga; `reconcile_payment` (`:978-1005`) e «purely clerical» |
| Nao existe adaptador de intake TISS que inicie CONTAS ou RECURSO a partir de uma transmissao real do prestador | OQ-R1. Hoje as instancias so nascem por Marina (`marina/graph.py:704`), pelo bridge (`notification_bridge.py:1034`) ou por abertura manual — **todos internos** |
| Nenhum efeito financeiro real sai da plataforma | `ST_ReleaseLowValue`/`ST_ReleaseHighValue` declaram «TASY write DROP (ADR-0013); a liberacao de caixa e sistema de tesouraria externo» (`…PAGTO…bpmn:229`) |

**Redimensionamento, sem diluicao.** A inversao continua **estrutural e textual, nao operacional** —
mas o argumento mudou de qualidade. Nao e mais «o start esta atras de um gate humano nao mergeado»
(falso); e «o start esta habilitado, e o que falta e o adaptador de intake e a integracao de
tesouraria». Esse limite **cai com um PR de integracao, nao com uma decisao de governanca**, e por
isso e **mais fragil** do que o que se afirmava. Duas consequencias diretas:

1. **D6-bis nao e opcional.** A caducidade de ADR-0018 remove exatamente o argumento que teria
   amortecido a perna automatica `PAGAR`.
2. **Deletar o caminho do recorrente continua sem desligar integracao viva** e sem criar janela de
   migracao de dados — o artefato e o que governa o comportamento no dia em que o adaptador existir.

---

## Supersedes

— (nao substitui nenhum ADR integralmente.)

### Emendas a ADRs vigentes

**ADR-0018 (`docs/adr/0018-no-denial-structural-replication.md`) — as secoes CONTAS e RECURSO ficam
EMENDADAS, o padrao de cinco partes fica INTACTO.** ADR-0018 nao e revogado nem enfraquecido: as
cinco partes continuam vinculantes e cumulativas. O que muda e **a instancia do adverso** nos dois
processos:

**Citacoes re-ancoradas em 2026-09-03** (WP-ADR0040-NOTES-RELOCATION) contra o estado atual de
`docs/adr/0018-...md`/`docs/adr/0030-...md`, apos o texto que vivia anexado neles ter sido movido
para a secao "Emendas a ADRs anteriores" acima — as linhas abaixo NAO coincidem com as citadas
quando esta tabela foi originalmente escrita (pre-imagem anterior a propria insercao de PR #274).

| Local em ADR-0018 | Texto vigente | Emenda proposta |
|---|---|---|
| `:17` | «aceite de glosa contra o prestador, negativa de recurso» na lista de negativa-like | «**aplicacao** de glosa contra o prestador, **indeferimento** de recurso» |
| `:29-36` | O exemplo CONTAS: dominio `{SEM_GLOSA, RECORRER, ANALISE_HUMANA}`; adverso = `decisao_contas == ACEITAR_GLOSA` via `register_glosa_accept`/`ERR_GLOSA_ACCEPT_NOT_HUMAN`; terminal `End_GlosaAceitaHumano` | dominio `{PAGAR, ANALISE_HUMANA}`; adverso = `decisao_contas ∈ {GLOSAR, PAGAR_PARCIAL}` via `registrar_glosa`/`ERR_CONTAS_GLOSA_NOT_HUMAN`; terminais `End_GlosaAplicadaHumano` / `End_PagamentoParcialHumano` |
| `:53-57` (parte 1) | «nenhum valor `deny`/`negar`/`aceitar-glosa`/`indeferir` … ex.: `decisao_contas=ACEITAR_GLOSA`» | O exemplo se inverte: o valor adverso que **so** uma User Task pode setar passa a ser `decisao_contas=GLOSAR` / `decisao_recurso=INDEFERIR`. A regra («o adverso nao e expressavel por caminho automatizado») e inalterada |
| `:68-69` (parte 3) | «ex.: `glosa_triage` catch-all → `ANALISE_HUMANA`» | inalterado (o catch-all continua `ANALISE_HUMANA`) |
| `:73-79` (parte 4) | «ex.: `register_glosa_accept`» | «ex.: `registrar_glosa`, `registrar_indeferimento`» |
| `:41-42`, `:97-104` | prova em CI = `test_nenhum_caminho_automatizado_aceita_glosa`, «48 combinacoes de `glosa_triage`» | o teste e renomeado e a varredura recomputada sobre o novo dominio; ate estar verde no engine real em `main`, as duas cadeias voltam ao estado **autorado, nao provado** (`:97-103`) |
| **`:106-110`** («Pre-condicao operacional») | «o allowlist de inicio de processo … e um **PR humano CODEOWNERS aberto e nao mergeado (PR #32)**. Logo, **nenhum processo de Phase 2 esta start-habilitado em `main`** ainda» | **OBSOLETO — nota de emenda, sem reescrita do corpo.** A pre-condicao caducou: `src/maezo/tools/process_allowlist.py:21-41` contem as 15 chaves (CONTAS `:28`, RECURSO `:29`, PAGTO `:40`) e `:60` faz `DEFAULT_ALLOWED_PROCESS_KEYS = KNOWN_PROCESS_KEYS` (correcao **D-m2**: `:63` e `_PROCESS_KEY_PATTERN`, nao relacionado). **Esta passagem nao pode ser citada como limite de risco.** A correcao do corpo de ADR-0018 e ato do owner; este ADR registra a obsolescencia e a fila de revisao a repete |

**ADR-0030 (`docs/adr/0030-worker-error-semantics-bpmn-boundary.md`) — NAO e alterado; e apenas
re-ancorado.** O censo de boundaries (`:113-142`) muda de conteudo mecanicamente, sem mudar nenhuma
regra:

- `ERR_RECURSO_INVALID_GLOSA` (linha `:137`) passa de **2** para **3** topicos: acrescenta-se
  `operadora.recurso.validate_recurso`, que passa a ter boundary catch declarado
  (`BE_GlosaInvalidaValidacao`) — permanece G2-val, continua consumption-covered pela «simple rule»,
  continua na `RECURSO_BPMN_ERROR_ALLOWLIST` (`recurso.py:78`).
- Os dois codigos `*_NOT_HUMAN` renomeados continuam **fora** do censo (sem boundary modelado),
  continuam levantados como `PermissionError` e continuam no caminho de incidente. Nenhuma
  habilitacao de allowlist de runtime e proposta — `:24` («ZERO allowlist enablements» para
  Tier-3) permanece verdade.
- Os end events de erro novos (`End_ErrContasDecisaoInvalida`, `End_ErrRecursoDecisaoInvalida`) sao
  **throw-ends modelados**, nao boundary catches sobre external task, e por isso ficam fora do
  escopo do gate (`scripts/ci/check_bpmn_error_allowlist.py`) — exatamente como
  `End_ErrDecisaoInvalida` de AUTH (`SP-OP-AUTH-001_…bpmn:565-568`) ja fica.

**ADR-0028 (avaliacao de DMN engine-side) — inalterado e reafirmado.** As 7 decisoes continuam
avaliadas pelo engine via `businessRuleTask`/`decisionRef` ou pelo seam `dmn=`; nenhuma regra migra
para Python.

**ADR-0016 / `KNOWN_PROCESS_KEYS` — inalterado.** As mesmas 15 chaves
(`process_allowlist.py:21-42`; espelho `effect_pep.py:174-192`).

---

## Emendas a ADRs anteriores (movidas de 0018/0030 em 2026-09-03)

> **Nota de governanca.** Entre a fusao deste ADR (`3ced9b9`, PR #274) e esta revisao, o texto
> abaixo vivia **anexado diretamente** dentro de `docs/adr/0018-no-denial-structural-replication.md`
> (+12 linhas apos o `Status`) e `docs/adr/0030-worker-error-semantics-bpmn-boundary.md` (+11
> linhas apos o `Status`). Uma revisao de governanca identificou isso como o mesmo padrao que a
> revisao adversarial da ADR-0041 rejeitou para as sete ADRs `Accepted` que ela reconcilia
> (`docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md`, secao "Convencao
> seguida": *"a convencao deste repo para corrigir uma ADR Accepted e uma ADR NOVA que a emenda,
> nao uma edicao in-loco"*) — a emenda pertence ao ADR QUE EMENDA, nao ao ADR emendado. O texto foi
> entao MOVIDO para ca, com o sentido preservado; `docs/adr/0018-...md:5` e
> `docs/adr/0030-...md:5` passam a carregar apenas um ponteiro de uma linha ("Emendado por
> ADR-0040"/"Amended by ADR-0040") para esta secao.
>
> **Diferenca deliberada do precedente da ADR-0041.** A propria ADR-0041 NAO deixa nenhum ponteiro
> nos sete arquivos que reconcilia — decisao explicita, registrada nas suas "Negativas (aceitas)",
> para nao deslocar as ancoras de linha que a auditoria cita neles. Aqui deixamos um ponteiro de
> uma linha porque o dono pediu exatamente isso e porque, ao contrario dos sete arquivos da
> ADR-0041, nem `docs/adr/0018-...md` nem `docs/adr/0030-...md` sao mantidos byte-identicos por
> nenhuma cerca de CI — nao ha `test_adr_amendments.py` equivalente para eles.
>
> **As citacoes de linha abaixo foram re-ancoradas** contra o estado de `docs/adr/0018-...md` e
> `docs/adr/0030-...md` **depois** desta relocacao (a insercao original, escrita antes de existir,
> citava as linhas do arquivo-base anterior a propria insercao — por isso as citacoes abaixo nao
> coincidem com as que estiveram, por um dia, dentro dos dois arquivos).

### ADR-0018 — padrao estrutural no-denial (Contexto CONTAS/RECURSO)

O padrao estrutural de cinco partes permanece INTACTO — esta emenda muda apenas a **instancia** do
efeito adverso em SP-OP-CONTAS-001/SP-OP-RECURSO-001, nao a garantia. A instancia muda de
`decisao_contas == ACEITAR_GLOSA` (worker `register_glosa_accept`, guard
`ERR_GLOSA_ACCEPT_NOT_HUMAN`, terminal `End_GlosaAceitaHumano` — ADR-0018 §Contexto `:29-36`,
§Decisao partes 1 e 4 `:53-57`, `:73-79`) para `decisao_contas ∈ {GLOSAR, PAGAR_PARCIAL}` (worker
`registrar_glosa`, guard `ERR_CONTAS_GLOSA_NOT_HUMAN`); o mesmo padrao se aplica a
SP-OP-RECURSO-001 (`decisao_recurso == INDEFERIR`, worker `registrar_indeferimento`, guard
`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`). A **Pre-condicao operacional** de ADR-0018 (`:106-110`,
«nenhum processo de Phase 2 esta start-habilitado em `main`») esta **OBSOLETA**:
`process_allowlist.py:21-41,60` ja contem as 15 chaves (CONTAS `:28`, RECURSO `:29`) com
`DEFAULT_ALLOWED_PROCESS_KEYS = KNOWN_PROCESS_KEYS`; ver `docs/review-queue.md`. ADR-0040 amends,
nao supersede.

### ADR-0030 — semantica de erro do worker vs boundary BPMN

ADR-0040 redesigns SP-OP-CONTAS-001 / SP-OP-RECURSO-001 into the operadora's (payer's)
perspective. Its two new guard codes, `ERR_CONTAS_GLOSA_NOT_HUMAN` and
`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`, replace one-for-one `ERR_GLOSA_ACCEPT_NOT_HUMAN` and
`ERR_DESISTENCIA_NOT_HUMAN` in the census (ADR-0030 `:113-142`, the zero-catches note at `:118`).
Both remain Tier-3 **declared-and-uncaught** (no `bpmn:boundaryEvent`/`errorEventDefinition`
models the guarded service task — same position as today's two codes, outside ADR-0030's
boundary-catch table), keep raising `PermissionError` on the audited-incident path (never
`WorkerBpmnError`/`bpmnError`), and enable no new production allowlist entry — "ZERO allowlist
enablements" for Tier-3 (`:24`, the ratification note quoting the T-E ledger row) remains true.
This decision (Option A, the census, ADR-0030 §5 `:379-382`) is NOT changed by ADR-0040; it is
only re-anchored to the renamed codes. ADR-0040 amends, does not supersede.

---

## O que permanece `DRAFT/verify` (SME / juridico / owner)

Nenhum item abaixo pode ser fechado por um agente. Todos entram em `docs/review-queue.md`.

| # | Item | Quem decide |
|---|---|---|
| OQ-1 | **Nomes de componentes TISS.** «Demonstrativo de Analise de Conta», «Recurso de Glosa», «Protocolo de recebimento», e a estrutura de campos por linha (`valor_apresentado`/`valor_processado`/`valor_liberado`/`valor_glosa`/`codigo_glosa`). **Acrescido por D6-ter:** o discriminador `tipo_comunicacao ∈ {demonstrativo_analise, devolucao_para_correcao}` e a pergunta de fundo — **a devolucao de conta para correcao e um demonstrativo, ou um artefato TISS proprio?** O discriminador existe justamente para tornar barata a separacao em dois topicos se a resposta for a segunda. O repositorio so cita hoje «Componente de Comunicacao» (`SP-OP-CONTAS-001.md:5`; `SP-OP-RECURSO-001` BPMN `:35`); nada mais foi confirmado contra o padrao vigente | regulatorio + faturamento/auditoria de contas |
| OQ-2 | **Prazo de analise da conta.** Substituir RN 424/2017 por «prazo contratual + RN 501/2022 (fluxo TISS)» e confirmar o valor (hoje P30D/P20D em `contas_sla.dmn:68-105`), o marco inicial (recebimento do lote vs. protocolo) e dias uteis vs. corridos. **Acrescido por D6-bis:** de onde vem `data_vencimento` — obrigatoria no contrato de PAGTO (`SP-OP-PAGTO-001.md:72`) e agora **exigida e recusada em branco** pelos dois handoffs, nunca defaultada. Do termo contratual de pagamento? Do lote? E, numa reversao de glosa (RECURSO), do vencimento da conta original ou de um prazo novo contado do deferimento? | juridico/regulatorio + financas |
| OQ-3 | **Prazo de resposta ao recurso de glosa.** Idem para `recurso_sla.dmn` (P10D/P15D analise, P30D teto). O teto P30D hoje e atribuido a RN 424/2017 (`:69, 80, 91, 102`), o que `rn-currency-review.md:85-87` refuta | juridico/regulatorio |
| OQ-4 | **Quando RN 424/2017 se aplica de fato.** So quando instaurada junta medica/odontologica para dirimir divergencia tecnico-assistencial. Confirmar se o merito de glosa tecnico-clinica decidido por `UT_RevisaoAuditorMedico` configura ou nao a hipotese da RN | medico-auditor + juridico |
| OQ-5 | **Tabela TISS de codigos de glosa.** `glosa_reason_normalization.dmn:31-72` e um mapa SINTETICO auto-declarado `DRAFT/verify` (`:21-22`). O redesign nao o valida | faturamento/auditoria de contas |
| OQ-6 | **Candidate groups.** `auditoria-contas`, `coordenacao-contas`, `analista-recurso-glosa`, `coordenacao-recurso` seguem PROPOSTOS (`SP-OP-CONTAS-001.md:139`; `SP-OP-RECURSO-001.md:159-161`). O redesign os mantem verbatim para nao introduzir uma segunda pendencia | PO/IdP + operacao |
| OQ-7 | **`DEVOLVER` e adverso?** A devolucao da conta ao prestador para correcao e classificada aqui como L1 neutro→prestador-adjacente, espelhando `End_PagamentoRecusadoHumano` de PAGTO (`SP-OP-PAGTO-001.md:38`). Se auditoria de contas entender que devolucao e uma glosa administrativa de fato, ela precisa do mesmo guard L0 de `GLOSAR` | auditoria de contas + compliance |
| OQ-8 | **`DEFERIR_PARCIAL` como adverso.** Classificado L0 por espelhar `APROVAR_PARCIAL` de REEMBOLSO (`…bpmn:444` «L0 — humano-gated, reducao adversa»; guard `reembolso.py:564-572`). Confirmar que a analogia se sustenta para prestador (e nao so para beneficiario) | compliance + juridico |
| OQ-9 | **Suspensao de prazo durante pendencia documental.** Ja aberta (`SP-OP-RECURSO-001.md:204`); nao fechada aqui | juridico/regulatorio |
| OQ-10 | **Achado M-4 de `glosa_triage`.** O conjunto negativo `not("tecnica","clinica")` na row permissiva (agora `r_pagar`) admite `"desconhecida"`. **Preservado verbatim de proposito** — corrigi-lo e ato do dono da tabela | auditoria de contas + compliance |
| OQ-11 | **`cid10` fora de `PHI_PROCESS_VARS`.** Marina semeia `cid10` nas variaveis de start de RECURSO (`graph.py:851-852`) e `PHI_PROCESS_VARS` (`src/maezo/tools/workers/phi_vars.py:52-63`) contem `cid10_referencia`, nao `cid10`. Achado pre-existente, **nao criado nem fechado** por este redesign | DPO/seguranca |
| OQ-12 | **MZO-040 / `action-approvals.yaml`.** As superficies novas sao declaradas, mas o arquivo continua `status: DRAFT` (`:73`) e `modo: shadow` (`:99`), com todas as aprovacoes `false`/`PENDENTE`. Nenhuma ratificacao e afirmada aqui | medica + ANS + seguranca (MZO-040) |
| OQ-13 | **`process_keys` de André está vazio e contradiz suas próprias declarações.** `spec/agents/andre/agent.yaml:16` concede a tool `mcp-cibseven.start_process # somente SP-OP-PAGTO-001` e `:28` a ação `start_compliance_process # SP-OP-PAGTO-001 … — L2`, mas o arquivo **não tem `process_keys`** ⇒ `allows_process_key("SP-OP-PAGTO-001")` é `False` (`src/maezo/gateway/effect_pep.py:481-488, 493-494`; alimentado por `gateway/tool_registry.py:181` ← `agents/__init__.py:397`). O efeito é **fail-closed** (nega), logo não é brecha — é lacuna funcional latente. **Achado adjacente, NÃO uma dependência deste redesenho** (os handoffs a PAGTO são worker-side, sob `AUDIT_AGENT_ID`). Correção proposta: `process_keys: [SP-OP-PAGTO-001]`. Mexer no allowlist de um PEP é ato de segurança/owner. **Correção D-m1 (gate delta):** o caminho de start de André é hoje **externo** ao binding de **I-PAGTO-1** (D6-bis) — `andre/graph.py:1088-1114` semeia os quatro fatos de admissibilidade de PAGTO a partir do payload A2A (`andre/delegation.py:146`), e `start_process` (`:929-990`) só faz no-op para `ORIGIN_PAGTO_WORKER`, ficando "plenamente funcional para qualquer OUTRA origem". Isso é **fail-closed hoje só porque `process_keys` está vazio** — exatamente a lacuna que esta linha propõe fechar. **Se OQ-13 for adotada, a mesma decisão deve, no mesmo PR, vincular `_contract_variables` de André a I-PAGTO-1** (nunca semear `lastro_confirmado`/`dados_pagamento_validos`/`duplicidade_suspeita`/`dentro_teto_l2`) e ampliar D6-bis de «os dois handoffs (CONTAS, RECURSO)» para «qualquer start de SP-OP-PAGTO-001 originado numa adjudicação ou num deferimento, seja por worker seja por agente». Conceder a chave sem essa amarra reabriria exatamente o caminho automático que I-PAGTO-1 fecha | segurança + PO |
| **OQ-14** | **Comunicacao ao prestador no terminal de fraude.** D6-ter faz 5 dos 6 terminais de CONTAS emitirem comunicacao TISS; `End_EncaminhadaFraude` e a excecao deliberada (notificar um prestador sob investigacao e alerta-lo). Mas o terminal **fecha a instancia**, deixando um lote apresentado sem resposta. Existe dever de comunicar? Em que momento? Ha forma de comunicar sem comprometer a investigacao? | fraude/investigacao + juridico + compliance |
| **OQ-15** | **Ma-classificacao em `action-approvals.yaml:649`.** O mapeamento `operadora.recurso.submit_appeal: submissao_regulatoria_ans` tratava **interpor recurso junto a operadora** como **submissao regulatoria a ANS**. E o mesmo artefato de perspectiva que este ADR corrige, e a matriz MZO-040 nao o apanhou. A remocao da linha (exigida pela delecao do topico) **nao e** a correcao da classificacao — e a remocao do objeto classificado. A lacuna de metodo na matriz fica registrada | seguranca + ANS (MZO-040) |
| **OQ-R1** | **O adaptador de intake TISS do recurso nao existe.** `REDESIGN-SP-OP-RECURSO-001.md` §3.1 especifica a forma do evento `agents.events.recurso.intake_recebido` e a regra de bridge que o consome, mas **nao constroi** o adaptador que o emitiria. A regra e registrada **DORMENTE de proposito**, com comentario de disclosure, um teste que assere a ausencia de publicador (e que fica **vermelho** quando o adaptador chegar) e um teste que prova que a regra funciona com payload ancorado. **Nenhum publicador sintetico, nenhum `event_published=True` sem publisher real.** Ate existir, RECURSO-001 e iniciado por Marina ou pela operacao — o mesmo estado de maturidade de hoje | PO + integracoes |
| **OQ-R2** | **Arredondamento em `DEFERIR_PARCIAL`.** A invariante `valor_deferido_brl + valor_glosa_mantido_brl == valor_glosado_brl` e imposta em **centavos-inteiros com igualdade exata** (a comparacao e feita apos conversao; comparar `double` por igualdade produziria falha espuria). Se a operacao usa tolerancia ou arredondamento contratual, o guard do worker precisa refletir isso. Ate la o guard **recusa** a soma que nao fecha, em vez de arredondar por conta propria | financas + auditoria de contas |

---

## Plano de merge — 4 PRs, tres slices de owner (correcao MAJOR **D-M1**, gate delta)

Este ADR **nao** e implementado num PR. A regra de corte e a superficie CODEOWNED: cada PR carrega
**um** slice de owner, ou nenhum.

> **Correcao MAJOR D-M1 (gate delta, revisao 2026-09-03).** A versao anterior desta tabela dava a
> PR-3 zero arquivos CODEOWNED e mandava `action-approvals.yaml` inteiro para o PR-4. O gatekeeper
> provou que isso deixa `tests/unit/sec/test_action_execution_fence.py:268-273` **vermelho no
> commit de merge do PR-3**: PR-3 deleta `ST_SubmitAppeal`/o topico `operadora.recurso.submit_appeal`
> da arvore, mas `action-approvals.yaml:649` continua mapeado ate o PR-4 chegar, e esse teste
> constroi `bpmn_topics` **ao vivo** a partir do BPMN. Inverter a ordem nao ajuda — PR-4-primeiro
> falha a mesma asserção pelo lado oposto. **A edicao de `action-approvals.yaml` passa a ser
> dividida por cadeia:** PR-3 remove `:257-259`+`:649` e declara as tres superficies RECURSO; PR-4
> remove `:367-369`+`:667` e declara as tres superficies CONTAS. Isso torna **PR-3 owner-review**
> (CODEOWNED so neste arquivo). Custo assumido: **3 PRs de owner-review (PR-2, PR-3, PR-4), nao 2.**

| PR | Escopo | Arquivos CODEOWNED | Revisao |
|---|---|---|---|
| **PR-1** | `perspective.py` + `test_perspective.py`, **ainda nao ligado** ao `cli.py` | **nenhum** | tecnica |
| **PR-2** | **Este ADR** + linha em `docs/adr/README.md` + notas de emenda a ADR-0018/0030 + `docs/review-queue.md` | `docs/adr/**` (`.github/CODEOWNERS:61`) | **owner** — a qualquer momento |
| **PR-3** | Cadeia SP-OP-RECURSO-001 de ponta a ponta **+ a metade RECURSO de `action-approvals.yaml`** | `action-approvals.yaml` (`:84`, `:124`) — **so as linhas RECURSO**: `:257-259`+`:649` removidas, `registrar_indeferimento`/`comunicar_resposta`/`handoff_pagamento` acrescentados. `recurso_admissibility.dmn`, `recurso_eligibility.dmn` e `recurso_sla.dmn` continuam fora de CODEOWNERS | **owner + security-team** |
| **PR-4** | Cadeia SP-OP-CONTAS-001 + as 3 linhas de wiring do gate **+ a metade CONTAS de `action-approvals.yaml`**; **rebase sobre PR-3** | `glosa_triage.dmn` (`:166`), `glosa-triage-shadow-candidate.yaml` (`:165`), `action-approvals.yaml` (`:84`, `:124`) — **so as linhas CONTAS**: `:367-369`+`:667` removidas, `registrar_glosa`/`emitir_demonstrativo`/`handoff_pagamento` acrescentados | **owner + security-team** |

**Ordem de merge (vinculante): PR-1 → PR-3 → PR-4, com PR-2 a qualquer momento.** PR-4 faz rebase
sobre PR-3 por dois motivos: a mesma edicao de `action-approvals.yaml` so fecha depois que a metade
RECURSO ja chegou, e `spec/agents/marina/agent.yaml`/`src/maezo/agents/marina/graph.py`/`prompts.py`
sao compartilhados pelas duas cadeias (Marina e o principal de start de ambos os handoffs a PAGTO).

**Retirados do pacote, e por que.** `spec/policies/privacy/phi-business-key-remediation.yaml` (linha
CODEOWNERS `:116`, que inclui o revisor **DPO**) — a edicao proposta era «um acrescimo de
documentacao numa politica inerte» (`modo: "off"`), e nao justifica o custo do gate; vira linha de
`docs/review-queue.md`. E o comentario em `/Makefile` (`:246`, gate de CI-integridade) — o validador
entra dentro do `validate-artifacts` que ja existe. **Slice de owner: 4 arquivos em 2 linhas de
CODEOWNERS, agora em 3 PRs de owner-review** (era 6 arquivos em 4 linhas, 2 PRs de owner-review, na
versao pre-gate).

**Nao tocados por construcao:** `.github/workflows/**` (owner-gated pelo nao-negociavel do owner) e
`/scripts/ci/**` (CODEOWNED `:247`) — nenhum step de CI novo e criado, e a consequencia disso (as
superficies fora de `spec/` ficam com um controle mais fraco) esta declarada em D7.

**O gate entra em vigor sem nunca ficar «desligado».** PR-1 entrega o modulo **medido** e o seu teste
de pino de contagem, sem chamar o validador; PR-3 e PR-4 fazem o pino descer; PR-4 liga as tres
linhas de `cli.py` no mesmo commit em que o pino chega a zero. Nenhuma flag, nenhum
`_CHAINS_UNDER_MIGRATION`, nenhuma regra desabilitada. **Risco residual declarado:** entre PR-1 e
PR-4 o `validate-artifacts` nao bloqueia vocabulario novo; o que impede deriva nessa janela e o pino
de contagem, que roda em `make test`.

---

## Registro de revisao (gate R1)

Origem: `DESIGN-GATE-CONTAS-RECURSO.md` (DESIGN-GATEKEEPER R1, **nao** autor do desenho; veredito
**REVISE** — 7 MAJOR, 10 MINOR). O que mudou **neste ADR**:

| # | Achado | Mudanca aplicada | Ancora |
|---|---|---|---|
| **M1** | D6 e as consequencias nomeavam `ERR_PAYMENT_RELEASE_NOT_HUMAN` como gate dos handoffs — o guard **nao esta** nesse caminho | Afirmacao **retirada** com o rastreamento que a desmente. Nova decisao vinculante **D6-bis (I-PAGTO-1)**: a origem fornece evidencia (`lastro_origem`/`lastro_decisor_id`), so PAGTO resolve o fato; guard real nomeado (`pagto_admissibility` fail-closed + `UT_AnaliseAdmissibilidade`); quatro amarras obrigatorias, incluindo a correcao textual de `SP-OP-PAGTO-001.md:76`, que e a origem do convite | **D6-bis** |
| **M2** | «nenhum processo de Phase 2 esta start-habilitado em `main`» (`ADR-0018:104-108`) e obsoleto | Afirmacao **retirada**; limite re-derivado de `process_allowlist.py:28,29,40,63` + ausencia de adaptador de intake + TASY write DROP; qualificado como **mais fragil** (cai com um PR de integracao). Nota de emenda a ADR-0018 `:104-108` acrescentada a tabela de emendas; `docs/review-queue.md` recebe a linha de obsolescencia | **«Limite operacional … RE-DERIVADO»**, tabela de emendas a ADR-0018 |
| **M3** | D7 prometia mais do que foi construido (8/8 falsos-positivos, 7/7 falsos-negativos no ataque do gatekeeper; `recurso_recovery_rate` num arquivo que o gate nao abria) | **D7 reescrito**: e um **portao de regressao de vocabulario**, com superficie Tier A / Tier B declarada, o que ele **nao** garante dito em texto, e a medicao Tier B (6 hits em 3 arquivos) acrescentada. Sem Makefile, sem workflow, sem `scripts/ci/` | **D7** |
| **M4** | A tensao «registro historico × AC-5» do shadow-candidate nao era confrontada | D7 declara a regra de superficie que a resolve (Tier B le **nos parseados**, comentario nao e no) e diz explicitamente que **nao ha bloco `historico:`** — a hipotese foi avaliada e recusada. O detalhe bloco-a-bloco esta em `REDESIGN-SP-OP-CONTAS-001.md` §2.4 | **D7** |
| **M5** | Superficie pendurada `operadora.recurso.submit_appeal` em `action-approvals.yaml` | Tratada nos documentos de desenho (delta exato + os consumidores + a prova de que e condicao de build verde). Aqui: **OQ-15** registra a ma-classificacao que a matriz MZO-040 nao apanhou, e o plano de merge poe o arquivo no PR-4 | **OQ-15**, «Plano de merge» |
| **M6** | `DEVOLVER` nao comunicava nada ao prestador; demonstrativo em 2 de 6 terminais | Nova decisao **D6-ter**: 5 dos 6 terminais comunicam, com ordem canonica de terminal declarada; `End_EncaminhadaFraude` e excecao deliberada com **OQ-14** | **D6-ter**, **OQ-14** |
| **M7** | A perna automatica `PAGAR` nao tinha fonte monetaria declarada | Tratada em `REDESIGN-SP-OP-CONTAS-001.md` §2.5/§3.1 (`fonte_valor` por elemento, `_parse_valor_monetario` fail-closed, recusa em `<= 0`). Aqui: **OQ-2 ampliada** com a origem de `data_vencimento`, que faltava no contrato de entrada de PAGTO | **OQ-2** |
| m4 | OQ-13 fora de ordem; OQ-R1/OQ-R2 so existiam numa nota de desenho | Tabela **reordenada** (OQ-12 antes de OQ-13); **OQ-14, OQ-15, OQ-R1 e OQ-R2 promovidas** para a tabela ratificavel; OQ-1 e OQ-2 ampliadas | **«O que permanece DRAFT/verify»** |
| m2 | 6 arquivos CODEOWNED, 2 evitaveis | Plano de merge com o slice reduzido a **4 arquivos / 2 linhas**, e a saida do revisor DPO registrada | **«Plano de merge»** |

**O que continua sendo reivindicacao nao verificada neste ADR:** (i) o total de hits do gate sob o
lexico corrigido — a medir no PR-1; (ii) toda ancora regulatoria e todo nome TISS, que permanecem
`DRAFT/verify`; (iii) o proprio veredito de perspectiva, que e claim de auditoria confirmado por um
verificador independente, nao ratificacao de SME. **Nenhum marcador `DRAFT/verify` foi removido por
esta revisao; quatro foram acrescentados.**

## Delta 2 (re-revisao do gate R1, 2026-09-03)

Origem: `DESIGN-GATE-CONTAS-RECURSO.md` §Delta (mesmo gatekeeper, **nao** autor do desenho; veredito
**REVISE (narrow)** — os 7 MAJOR originais fechados; 1 MAJOR novo (D-M1) + 6 MINOR novos). O que
mudou **neste ADR**:

| # | Achado | Mudanca aplicada | Ancora |
|---|---|---|---|
| **D-M1** | O «Plano de merge» dava a PR-3 zero arquivos CODEOWNED e mandava `action-approvals.yaml` inteiro para o PR-4 — mas PR-3 ja deleta `ST_SubmitAppeal`/o topico antes disso, deixando `test_action_execution_fence.py:268-273` vermelho no commit de merge do PR-3 | Tabela reescrita: a metade RECURSO do delta (`:257-259`+`:649`) viaja no **PR-3**, que se torna **owner-review**; a metade CONTAS continua no PR-4, que faz **rebase sobre PR-3**. Ordem de merge declarada: PR-1 → PR-3 → PR-4, PR-2 a qualquer momento. Custo assumido: 3 PRs de owner-review, nao 2 | **«Plano de merge»** |
| **D-m1** | OQ-13 propoe `process_keys: [SP-OP-PAGTO-001]` para Andre sem dizer que o caminho de start dele fica fora do binding de **I-PAGTO-1** (D6-bis) | Nota acrescentada a OQ-13: se adotada, a mesma decisao deve vincular `_contract_variables` de Andre a I-PAGTO-1 no mesmo PR, e D6-bis amplia de «os dois handoffs» para «qualquer start de SP-OP-PAGTO-001 originado numa adjudicacao ou num deferimento» | **OQ-13** |
| **D-m2** | `process_allowlist.py:63` citado para `DEFAULT_ALLOWED_PROCESS_KEYS`; linha certa e `:60`, comentario `:44-59` nao `:45-62` | Corrigido nas duas citacoes deste ADR («Isso e falso hoje» e a tabela de emendas a ADR-0018) | **Consequencias**, **Supersedes** |
| **D-m3** | Tier B media **6 hits/3 arquivos** nos documentos de desenho; a contagem real sob o lexico como escrito era **5/3**, e apos a correcao de lexico (`start_recurso` acrescentado a R1) e **7/3** | Sem edicao de conteudo tecnico neste ADR (a medicao vive em `REDESIGN-SP-OP-CONTAS-001.md` §6.4/§6.7); nenhuma citacao de «6 hits» neste ADR precisou de correcao — o numero 6 nao aparece aqui | `REDESIGN-SP-OP-CONTAS-001.md` §6.7 |
| **D-m4** | Contagem de teste-piso mistura funcao com teste coletado; `test_recurso.py` tem 67 funcoes, nao 68 | Sem edicao neste ADR (numeros de piso de teste nao aparecem aqui); tratado nos dois documentos de desenho | `REDESIGN-SP-OP-CONTAS-001.md` §8.4, `REDESIGN-SP-OP-RECURSO-001.md` §2.9/§8 |
| **D-m5** | `duplicidade_suspeita`: default divergente entre spec (`true`) e codigo (`false`) em PAGTO | Sem edicao neste ADR; achado registrado em `docs/review-queue.md` (PR-2) via `REDESIGN-SP-OP-CONTAS-001.md` §8.5 | `REDESIGN-SP-OP-CONTAS-001.md` §8.5 |
| **D-m6** | Argumento de §2.4(c) do documento de desenho CONTAS (X1/40 caracteres em vez da regra de tier) | Sem edicao neste ADR — o argumento vive so no documento de desenho | `REDESIGN-SP-OP-CONTAS-001.md` §2.4 |

**O que este ADR nao repete.** D-m3, D-m4 e D-m5 sao achados de medicao ou de codigo cuja correcao
integral esta nos dois documentos de desenho (`REDESIGN-SP-OP-{CONTAS,RECURSO}-001.md`); este ADR e
o documento de ratificacao e nao duplica numeros que la sao derivados e citados com `file:line`.
D-m6 e um argumento interno ao documento de desenho CONTAS e nao tem correspondente neste ADR.

## Nota de escopo (OQ-R1, 2026-09-06)

Origem: `OWNER-DECISIONS-REGISTER` id `R-194` (decisor: dono (produto) + integracoes; status
`APROVADO-APOS-REVISAO-HUMANA`). A pergunta fechada era: constroi agora o adaptador de intake TISS
do recurso, ou `SP-OP-RECURSO-001` continua iniciado por Marina e pela operacao com a regra de
ponte dormente (**OQ-R1**, `:581`)? Resposta do dono, citada verbatim:

> A — MANTER dormente e continuar iniciando por Marina/operacao, porque nao ha produtor TISS real
> para o adaptador consumir.

**O que esta nota declara, e o que ela nao faz.** Fecha OQ-R1 declarando o caminho de entrada de
`SP-OP-RECURSO-001`: por Marina (`marina/graph.py:704`) ou pela operacao — nao pelo adaptador de
intake TISS. A regra de bridge que OQ-R1 ja registra como **DORMENTE de proposito** (comentario de
disclosure + o teste que assere a ausencia de publicador + o teste que prova a regra com payload
ancorado) permanece exatamente como esta: **nenhum adaptador e construido, nenhuma costura e
removida.** Esta nota nao ratifica o ADR, que segue `Proposed`; o merge continua sob revisao
CODEOWNER de `/docs/adr/` (`.github/CODEOWNERS:61`).
