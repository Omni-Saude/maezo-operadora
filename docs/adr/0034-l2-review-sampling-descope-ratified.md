# ADR-0034: L2 review sampling — intencionalmente fora do v2 (descope ratificado); a autonomia e enforce-ada estruturalmente (ADR-0018), nao por um PEP em runtime

**Status:** Accepted · **Data:** 2026-07-26 · **Area:** Seguranca / Governanca de Autonomia
Owner: policy-guardian. Resolve: gap #24 (L2 ReviewQueue + sample_rate).

Zero-trust note: cada afirmacao verificavel abaixo carrega uma citacao `path:line` para o codigo real,
conferida nesta sessao. Nao confie em docstring/comentario — onde um comentario afirma comportamento,
ele e sinalizado como comment-only.

---

## Contexto

`docs/Tarefas_Pendentes.md:120` (§1.4) lista, como feature *gated* "com o codigo pronto, falta so o
insumo/flag", a tarefa: construir `L2ReviewSampler(sample_rate=…)` + `ReviewQueue` concreto e
**injeta-los nos 2 `PolicyEnforcementPoint(...)` em `runtime/agent_runtime/service.py`** (gap #24).
Esta ADR resolve #24 apurando, por leitura do codigo real, se essa construcao e *root-cause-correct*
ou se o descope e arquiteturalmente correto.

Fatos apurados (todos `path:line`-verificados nesta sessao):

1. **A amostragem L2 pressupoe um chokepoint PEP por-tool-call.** ADR-0008 define a matriz de
   autonomia como "avaliada pelo PEP a cada tool call" (`0008-autonomy-levels.md:11`) e L2 como
   "agente executa, revisao por amostragem" (`0008-autonomy-levels.md:15`). O donor v1 materializa
   isso: `PolicyEnforcementPoint.evaluate` (async) chama `_maybe_flag_l2_review` **apenas** no ramo
   L2-ALLOW (v1 `gateway/pep.py:355-372`), via um `L2ReviewSampler` deterministico (BLAKE2b, default
   `sample_rate=0.0`, v1 `gateway/l2_sampling.py`) e um `ReviewQueue` que e um **Protocol**, com
   default `None` que faz no-op silencioso (v1 `gateway/pep.py:175-184, 386-393`).

2. **No v2, o PEP NUNCA esta no caminho de request.** `PEP.evaluate` (`src/maezo/gateway/pep.py:412`)
   tem ZERO chamadores em runtime — verificado: so `tests/unit/gateway/test_pep.py` e
   `test_pep_policy_unification.py` o invocam. Os dois unicos sitios `build_pep()`
   (`src/maezo/runtime/agent_runtime/service.py:368` e `src/maezo/gateway/service.py:79`) usam o PEP
   construido **apenas como readiness probe de startup**: `state.pep is not None` vira o health-check
   `policies_loadable` (`agent_runtime/service.py:136-141`; `gateway/service.py:66-71`). O docstring
   do gateway afirma, e o grep confirma, que nao ha "PEP-evaluation endpoint ... today" e que o
   `maezo.gateway` "has no HTTP business surface today" (`gateway/service.py:13-14, 90`). Nao existe
   `agents/base.py` nem um gate de dispatch de tool que chame o PEP.

3. **A autonomia/HITL e enforce-ada ESTRUTURALMENTE, nao por um PEP em runtime (ADR-0018).** A
   garantia no-adverse e o padrao BPMN no-denial de cinco partes (tipos de rota/decisao sem variante
   adversa; DMN sem saida de negativa; fail-safe a User Task humana por allowlist fechada; worker
   guard `ERR_*_NOT_HUMAN`; teste de integracao na historia do engine REAL) — `0018-...:47-94`. Todo
   efeito L0-hard/adverso e roteado a um humano por construcao, enforce-ado em CI.

4. **A Tarefa §1.4, portanto, esta STALE em tres pontos.** (a) Nomeia `PolicyEnforcementPoint(...)` —
   classe que nao existe no v2 (o v2 tem `build_pep()` → `PEP`, `pep.py:336,391`). (b) Afirma "codigo
   pronto" — falso: nem o sampler, nem um `ReviewQueue` concreto, nem o `evaluate` async com o ramo
   de amostragem existem no v2 (o `evaluate` do v2 e sync e nao amostra — `pep.py:412-450`; ADR-0025
   D6/Q4 ja havia *descopado* isso como follow-up sinalizado — `0025-...:150-156, 283-284`). (c)
   Pressupoe que injetar sampler+queue na construcao do PEP amostraria os L2-ALLOW — mas, como o PEP
   nunca avalia nada em runtime (fato 2), o gatilho da amostragem (`_maybe_flag_l2_review`, so
   alcancavel a partir de `evaluate`) executaria **zero vezes** em producao, qualquer que seja o
   `sample_rate`.

5. **O donor v1 tampouco entregou um `ReviewQueue` concreto.** E um Protocol default-`None` que faz
   no-op; o sampler default e `0.0`. "Codigo pronto" nunca foi verdade nem no v1.

6. **A necessidade de governanca que a amostragem L2 serviria ja esta atendida.** As acoes L2
   autonomas *nao-adversas* (aprovacao de auth sob DMN-favoravel + teto; roteamento de glosa padrao)
   ja sao: gated por DMN deterministica (ADR-0012); limitadas por tetos que fail-close a revisao
   humana quando ausentes (T1.9 `CeilingResolver`, `tools/workers/ceilings.py`); e integralmente
   registradas na cadeia de auditoria append-only (ADR-0007/0027). Um revisor ja pode fazer
   spot-check de qualquer acao L2 pela cadeia de auditoria. A amostragem L2 adicionaria uma *fila
   assincrona de spot-check pre-selecionado* — conveniencia de governanca que "NUNCA bloqueia"
   (v1 `l2_sampling.py` docstring; comment-only), nao um gate de seguranca.

## Decisao

**Ratificamos o descope: a "L2 review sampling" (L2ReviewSampler + ReviewQueue) NAO faz parte do
v2.** Nao construimos o subsistema. A razao e mais forte do que a de ADR-0025 D6 ("uma mudanca maior,
adiada"): o v2 **nao** enforca autonomia por um PEP em runtime — enforca estruturalmente (ADR-0018).
Logo a amostragem L2 nao tem chokepoint em que se apoiar e nao cumpre nenhum papel de seguranca que a
estrutura no-denial + DMN + tetos + auditoria ja nao cubram. Injetar sampler+queue no `build_pep()`
produziria um subsistema morto (tabela + migration + sampler ligados a um `evaluate` nunca invocado) —
exatamente o subsistema especulativo que o principio de "nao gold-plate" veda.

**Precondicao de revisita (unica).** A amostragem L2 so volta a ser *root-cause-correct* SE e QUANDO
o v2 introduzir um **chokepoint de dispatch PEP por-tool-call em runtime** (portando o
`PolicyEnforcementPoint` async do donor v1 — `evaluate(ToolCall)` com auditoria de recusa + o ramo
L2-ALLOW). Nesse cenario, o `L2ReviewSampler` deterministico (v1 `l2_sampling.py`) e um `ReviewQueue`
concreto e persistente devem ser construidos **como parte desse chokepoint**, com `sample_rate` gated
por config/env, `sample_key` nao-PHI (hash de tool_input ja calculado), enqueue best-effort que nunca
bloqueia (v1 `pep.py:374-393`), e uma nova ADR ratificando a re-introducao. Nao antes: sem o
chokepoint, o sampler nao tem o que amostrar.

**Correcoes documentais desta ADR.** (a) `docs/Tarefas_Pendentes.md` §1.4: a linha de #24 passa a
registrar "intencionalmente fora do v2 (ADR-0034)" com a razao, removendo a instrucao stale de injetar
em `PolicyEnforcementPoint(...)`. (b) O `review-queue.md` do repo e OUTRO artefato (fila de revisao
humana de artefatos clinicos/regulatorios em DRAFT) — nao e a fila L2 e nao e tocado.

Esta ADR **amends** ADR-0008 (a clausula "avaliada pelo PEP a cada tool call" descreve o modelo
donor/intencao; no v2 a avaliacao de autonomia e estrutural via BPMN — ADR-0018 — e o PEP e um objeto
de validacao/readiness de politica, nao um gate por-call) e ADR-0025 (converte o follow-up sinalizado
de D6/Q4 de "adiado" para "descopado, com precondicao de revisita explicita"). Nao supersede nenhum
dos dois.

## Consequencias

**Positivas:**
- Elimina uma tarefa que produziria um subsistema morto (sampler+queue+tabela+migration ligados a um
  `PEP.evaluate` sem chamadores em runtime) — sem custo de manutencao, sem falsa sensacao de
  cobertura de governanca.
- Torna explicito e versionado que o v2 enforca autonomia ESTRUTURALMENTE (ADR-0018), nao por um PEP
  em runtime — corrige um mal-entendido latente (Tarefas §1.4, e o `autonomous-completion-report.md`
  historico) que tratava a amostragem L2 como "wiring que falta".
- Deixa a porta aberta de forma disciplinada: a precondicao de revisita (chokepoint PEP por-call) e
  concreta e testavel, entao a decisao e reversivel sem perda de contexto se o modelo de runtime
  mudar.
- Mantem intacta a garantia real de seguranca: nenhuma acao adversa L0-hard escapa do humano
  (ADR-0018, CI-enforced), independente de amostragem.

**Negativas (aceitas):**
- Nao ha fila de spot-check assincrono pre-selecionado para acoes L2 nao-adversas. Mitigacao: toda
  acao L2 ja e auditavel pela cadeia append-only (ADR-0007/0027) e limitada por DMN + teto
  fail-closed (ADR-0012, T1.9); a revisao por amostragem seria conveniencia, nao um gate ausente.
- O donor v1 carrega `l2_sampling.py` + o ramo async do PEP que o v2 deliberadamente nao porta; um
  leitor do donor pode presumir paridade. Mitigacao: esta ADR + o scope-note em `pep.py:34-40`
  registram a divergencia explicitamente.

## Supersedes

— (amends ADR-0008 e ADR-0025; nao substitui nenhum. Complementa ADR-0018.)
