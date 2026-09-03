# ADR-0018: Padrao estrutural no-denial — replicacao vinculante para todo SP-OP negativa-like

**Status:** Accepted · **Data:** 2026-06-13 · **Area:** Seguranca/Compliance · Orquestracao

**Amended by ADR-0040:** o padrao estrutural de cinco partes permanece INTACTO — ADR-0040 emenda
apenas a **instancia** do efeito adverso em SP-OP-CONTAS-001/SP-OP-RECURSO-001, nao a garantia.
A instancia muda de `decisao_contas == ACEITAR_GLOSA` (worker `register_glosa_accept`, guard
`ERR_GLOSA_ACCEPT_NOT_HUMAN`, terminal `End_GlosaAceitaHumano` — §Contexto `:27-34`, §Decisao
partes 1 e 4 `:51-56`, `:71-77`) para `decisao_contas ∈ {GLOSAR, PAGAR_PARCIAL}` (worker
`registrar_glosa`, guard `ERR_CONTAS_GLOSA_NOT_HUMAN`); o mesmo padrao se aplica a
SP-OP-RECURSO-001 (`decisao_recurso == INDEFERIR`, worker `registrar_indeferimento`, guard
`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`). A **Pre-condicao operacional** (`:104-108`, «nenhum
processo de Phase 2 esta start-habilitado em `main`») esta **OBSOLETA**: `process_allowlist.py:21-41,60`
ja contem as 15 chaves (CONTAS `:28`, RECURSO `:29`) com `DEFAULT_ALLOWED_PROCESS_KEYS =
KNOWN_PROCESS_KEYS`; ver `docs/review-queue.md`. ADR-0040 amends, nao supersede.

## Contexto

ADR-0005 estabeleceu o HITL como garantia *arquitetural* (nao prompt): a negativa nasce de um
humano, atraves de quatro mecanismos independentes (PEP, User Task BPMN, separacao de credenciais,
auditoria). ADR-0008 classificou negativa de cobertura, acusacao de fraude, cancelamento de
contrato e qualquer decisao clinica como **L0 hard** (so humano, nao rebaixavel por tenant; CI
rejeita o rebaixamento). ADR-0012 fixou a DMN como unica fonte de regra deterministica — o LLM
raciocina sobre o RESULTADO da DMN, nunca a substitui.

Esses tres ADRs dizem *o que* deve ser verdade. Faltava codificar **como** cada processo SP-OP
que produz um efeito adverso ("negativa-like" — negativa de autorizacao, aceite de glosa contra o
prestador, negativa de recurso, resposta NIP desfavoravel, cancelamento de contrato, indeferimento
de reembolso) torna a violacao *estruturalmente impossivel*, e nao apenas proibida por convencao.

Duas provas concretas ja existem em `main`:

- **SP-OP-AUTH-001** (Autorizacao Previa, merged em Phase 1): a negativa de cobertura so nasce nas
  User Tasks `UT_AnaliseMedicoAuditor` / `UT_CoordenacaoAssume` / `UT_RegistrarParecerJunta`;
  nenhuma DMM (`auth_admissibility`, `auth_auto_approval`, `auth_sla`) tem saida de negativa;
  inelegibilidade/carencia aparentes roteiam para analise humana; o worker `operadora.auth.send_denial_notice`
  e guardado por `ERR_AUTH_DENIAL_NOT_HUMAN` / `ERR_DENIAL_NOT_HUMAN` (transmite a negativa do
  auditor, nunca a decide).
- **SP-OP-CONTAS-001** (Processamento de Contas / Glosa, merged em Phase 2 via PR #30): inverte o
  anti-padrao do reference hospitalar (`glosa_management.bpmn`, que auto-aplicava glosa e tinha
  `Task_AutoApprove` por timeout de 48h). A DMN `glosa_triage` tem dominio de saida `roteamento`
  EXATAMENTE `{SEM_GLOSA, RECORRER, ANALISE_HUMANA}` — **sem variante ACEITAR/CONFIRMAR**; o
  aceite substantivo de glosa so nasce na User Task `UT_AnalistaContas`
  (`decisao_contas == ACEITAR_GLOSA`), materializado pelo worker `operadora.contas.register_glosa_accept`
  guardado por `ERR_GLOSA_ACCEPT_NOT_HUMAN`; o terminal `End_GlosaAceitaHumano` so e alcancavel
  apos a UT humana.

Em ambos, o mesmo desenho de cinco partes foi *provado contra o engine real* (CIB Seven, sem mock —
ADR-0011) e e **enforce-ado em CI**: a suite de integracao varre todas as combinacoes de input da
DMN negativa-like e consulta a historia do engine para provar que o terminal adverso nunca co-ocorre
sem uma User Task humana no historico (ex.: `test_nenhum_caminho_automatizado_aceita_glosa` varre as
48 combinacoes de `glosa_triage` em `tests/integration/processes/test_sp_op_contas_001.py`).

O padrao precisa deixar de ser uma boa pratica oral (§4-bis-F do `docs/swarm-execution-prompt-v2.md`)
e virar **invariante vinculante e versionada** para todo SP-OP negativa-like atual e futuro.

## Decisao

Todo processo SP-OP que possa produzir um efeito adverso L0-hard ("negativa-like") DEVE implementar
o **padrao estrutural no-denial de cinco partes**. As cinco partes sao obrigatorias e cumulativas —
nenhuma sozinha basta:

1. **Tipos de rota/decisao sem variante adversa (agente + BPMN).** O dominio das variaveis de
   roteamento/decisao do agente e do BPMN NAO contem nenhum valor `deny`/`negar`/`aceitar-glosa`/
   `indeferir`. O efeito adverso simplesmente nao e expressavel por um caminho automatizado — ele so
   existe como valor que uma User Task humana pode setar (ex.: `decisao_auditor=NEGAR`,
   `decisao_contas=ACEITAR_GLOSA`), nunca um service task.

2. **Tabelas DMN sem coluna/saida de negativa.** Nenhuma `decisionTable` do processo possui saida
   que confirme/aceite/negue o efeito adverso. A DMN apenas *sinaliza candidata e roteia*
   (ADR-0012: a DMN nao decide o adverso; o LLM tampouco). Toda DMN obedece a allowlist de shape
   engine-deployavel (`typeRef in {string, boolean, integer, long, double, date}`; `number` invalido;
   BRL em `double`; SLA em string ISO 8601; `camunda:historyTimeToLive` namespaced).

3. **Fail-safe a uma User Task humana (allowlist fechada).** Toda ambiguidade, inelegibilidade/
   carencia aparente, expiracao de prazo, indicio de fraude e indisponibilidade da DMN roteia para
   uma User Task humana. O roteamento e por *allowlist fechada*: a row catch-all (ultima) de cada
   tabela negativa-like mapeia para o caminho humano conservador (ex.: `glosa_triage` catch-all →
   `ANALISE_HUMANA`). Nao existe rota de "default permissivo" que escape o humano. O estouro de SLA
   leva a coordenacao humana assumir — **nunca** ha auto-passagem/auto-aceite por timeout (inversao
   explicita do `Task_AutoApprove`/48h do reference).

4. **Worker guard `ERR_*_NOT_HUMAN` (o worker transmite, nunca decide).** O efeito adverso e
   materializado por exatamente um worker (ex.: `register_glosa_accept`, `send_denial_notice`),
   guardado por um codigo de erro `ERR_*_NOT_HUMAN`. O worker lanca BPMN error e recusa registrar o
   efeito a menos que (a) a variavel de decisao adversa tenha sido setada por humano numa User Task,
   e (b) os campos obrigatorios de fundamentacao/aprovador humano (`analista_id`/`human_approver`,
   justificativa, codigo, valor) estejam presentes. O worker e *defesa em profundidade*: mesmo se o
   BPMN regredisse, a instancia nao atinge o terminal adverso sem decisao humana completa.

5. **Teste de integracao invariante na historia do engine (engine REAL).** Cada processo
   negativa-like tem um teste de integracao que (a) varre as combinacoes de input da DMN
   negativa-like e prova que nenhuma combinacao atinge o terminal adverso automaticamente, e (b)
   consulta a historia do engine (`history/activity-instance`) para provar que, *se* o terminal
   adverso esta no historico, *entao* pelo menos uma User Task humana tambem esta. O teste roda
   contra o engine real (ADR-0011, sem mock) e e bloqueante em CI. Os terminais neutros e o adverso
   so ocorrem apos publicacao do evento de dominio correspondente (auditoria dupla engine+Kafka,
   ADR-0007).

**Estado de implementacao (preciso — nao superestimar):**

- **PROVADO + enforce-ado em CI no `main` (@ `a2dabda`):** SP-OP-AUTH-001 e SP-OP-CONTAS-001
  implementam as cinco partes. Os workers guardados (`ERR_AUTH_DENIAL_NOT_HUMAN`,
  `ERR_GLOSA_ACCEPT_NOT_HUMAN`), as DMN sem saida adversa (`auth_admissibility`/`auth_auto_approval`,
  `glosa_triage` com dominio `{SEM_GLOSA, RECORRER, ANALISE_HUMANA}`) e os testes invariantes
  (`test_sp_op_auth_001.py`, `test_sp_op_contas_001.py`) estao mergeados.
- **Autorado + in-flight (PR #38, em CI — NAO mergeado):** o mesmo padrao esta replicado em cinco
  bodies SP-OP: **SP-OP-RECURSO-001** (recurso de glosa), **SP-OP-NIP-001** (resposta NIP),
  **SP-OP-CANCEL-001** (cancelamento de contrato) e **SP-OP-REEMBOLSO-001** (reembolso ao
  beneficiario) — todos negativa-like; e **SP-OP-ANS-SUBMIT-001** (envios periodicos ANS), que e
  *nao-negativa* (sem terminal adverso L0) e portanto aplica apenas as partes pertinentes (DMN sem
  saida adversa, fail-safe a humano em ambiguidade). Estes cinco sao descritos aqui como **em
  desenvolvimento, ainda nao em `main`**; sua conformidade so e considerada *provada* quando os
  testes invariantes rodarem verdes contra o engine real em `main`.

**Pre-condicao operacional (preciso):** o allowlist de inicio de processo (W0.1 — adicao de
process_keys a `KNOWN_PROCESS_KEYS`, ADR-0016) e um **PR humano CODEOWNERS aberto e nao mergeado**
(PR #32). Logo, **nenhum processo de Phase 2 esta start-habilitado em `main`** ainda — nem mesmo o
CONTAS-001 mergeado. As provas de integracao deste padrao rodam em ambiente de teste deployando os
artefatos diretamente no engine; a habilitacao de start em runtime continua atras de gate humano.

**Mandato.** O padrao e **vinculante para todo SP-OP negativa-like futuro**, incluindo (nao
exaustivo) **SP-OP-FRAUDE** (com `fraud_accusation` L0 hard — nenhum branch auto-acusa fraude; o
indicio so roteia a humano, espelhando `indicio_fraude_sinalizado` em CONTAS-001) e
**SP-OP-INADIMPLENCIA**. A revisao no-denial de cada novo SP-OP negativa-like e parte do DoD do
processo; um body sem as cinco partes nao e mergeavel.

Este ADR nao introduz mecanismo novo de runtime — codifica como ADR-0005/0008/0012 sao
*materializados estruturalmente por processo*, transformando a garantia HITL em invariante
verificavel por construcao (a row catch-all, o guard `ERR_*_NOT_HUMAN`, o teste de historia) em vez
de convencao ("lembre de rotear ao humano").

## Consequencias

**Positivas:**
- Violar o HITL exige comprometer o engine/CI, nao convencer um LLM nem esquecer uma convencao — a
  negativa automatizada e *inexpressavel* (sem tipo, sem coluna DMN, sem rota) e *irregistravel*
  (worker guard) e *detectavel* (teste de historia). ANS-defensible por construcao.
- Simetria entre processos: AUTH, CONTAS e os cinco bodies in-flight compartilham o mesmo desenho,
  reduzindo carga cognitiva de revisao e tornando o no-denial review do DoD mecanico.
- Inverte estruturalmente o anti-padrao do reference hospitalar (auto-aplicacao de glosa / aprovacao
  por timeout) em todo processo, nao caso a caso.
- Defesa em profundidade: BPMN (sem rota), DMN (sem saida), worker (guard) e teste (historia) sao
  quatro camadas independentes; a falha de uma nao produz violacao silenciosa.

**Negativas (aceitas):**
- Custo fixo por processo: cada SP-OP negativa-like carrega cinco artefatos coordenados (tipos, DMN,
  fail-safe, worker guard, teste de integracao real). Build-to-contract-sheet (§5-bis) mitiga, mas a
  conformidade so e *provada* quando o teste invariante esta verde contra o engine real em `main` —
  ate la, e in-flight.
- Latencia humana no caminho adverso (herdada de ADR-0005): o desenho otimiza o dossie, nao remove o
  humano. Todo adverso passa por User Task, mesmo em escalonamento de SLA.
- Os cinco bodies de PR #38 ainda nao gozam da garantia *provada* — sao conformidade autorada
  pendente de merge + verde no engine real; revisores nao devem trata-los como enforce-ados ate la.

## Supersedes

— (complementa e operacionaliza ADR-0005, ADR-0008 e ADR-0012; nao substitui nenhum)
