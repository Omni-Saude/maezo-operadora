# ADR-0005: HITL como garantia arquitetural (nao prompt)

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Seguranca/Compliance

## Contexto
RN 259 proibe negativa de cobertura sem medico auditor. Prompt nao e mecanismo de enforcement.

## Decisao
Quatro mecanismos independentes:
1. **PEP** no Tool Gateway avalia a matriz de autonomia (ADR-0008) antes de TODO tool call efetivo.
2. Acoes mandatorias-humanas = **User Task BPMN** com timer+escalation; processo nao avanca sem humano.
3. **Separacao de credenciais:** a credencial da acao proibida nao existe no runtime do agente.
4. Toda recusa do PEP gera evento auditavel.

Negativa: agente produz recomendacao estruturada + dossie; medico auditor decide; a decisao sai
em nome do humano. **Decisao clinica/diagnostica por agente: proibida, hard-coded, nao configuravel.**

## Consequencias
**Positivas:** violar exige comprometer infraestrutura, nao convencer um LLM; ANS-defensible.
**Negativas (aceitas):** latencia humana no caminho — o design otimiza o dossie, nao remove o humano.

## Supersedes
—

---

## Emenda 2026-09-03 — mecanismo 1 (PEP por tool call): CONSTRUIDO, mas em `modo: shadow` (GAP AF-03)

**Status:** Proposto (amendment) — DRAFT/verify · **Data:** 2026-09-03 · **Autor:** `adr-reconciler` (R1, AGENTE)
**Marcadores:** `amended-by`: ADR-0034 (via ADR-0008 e ADR-0025) + esta Emenda 2026-09-03
(WP-ADR-RECONCILIACAO, GAP AF-03) · **sem** `obsolete-section`: o mecanismo 1 NAO e falso — e inerte.
**Base de verificacao:** worktree em `71dd4da`.

> APPEND-ONLY. Nenhuma linha do texto original acima foi alterada ou removida. Um AGENTE nao ratifica
> nada: enquanto este bloco carregar `DRAFT/verify`, ele e um fato reconciliado com o codigo, nao uma
> decisao ratificada. Assinatura humana pendente (`.github/CODEOWNERS:61`).

### 1. O que a ADR afirma

`:10` (Decisao, mecanismo 1): "**PEP** no Tool Gateway avalia a matriz de autonomia (ADR-0008) antes de
TODO tool call efetivo."

### 2. O que e verdade hoje

**(a) O chokepoint por-efeito EXISTE e CONSULTA a matriz de autonomia a cada chamada.**

- Ponto de entrada: `src/maezo/gateway/effect_pep.py:814` (`decide_effect(...)`), chamado por todo seam
  gateado — `src/maezo/gateway/seams/_base.py:77` (import) e `:369` (a chamada dentro de `gate`).
- A camada L-2 consulta a matriz: `src/maezo/gateway/effect_pep.py:596-614`, em particular `:604`
  (`verdict = ctx.autonomy.evaluate(...)`).
- `ctx.autonomy` e o PEP real: `src/maezo/gateway/tool_registry.py:194` e `:234`
  (`autonomy=_build_autonomy(tenant)`), com `_build_autonomy` = `build_pep(tenant=...)` em `:242-247`;
  `build_pep` carrega `spec/policies/autonomy/` via o mecanismo unico T0.3
  (`src/maezo/gateway/pep.py:327-333`).
- Os seams so podem ser construidos pelo `tool_registry` (`src/maezo/gateway/seams/__init__.py:14`), e a
  inevitabilidade e cercada por gate de CI: `Makefile:66` (`effect-chokepoint-fence`).
- Raizes de composicao que montam os seams: `src/maezo/runtime/agent_runtime/service.py:371`,
  `src/maezo/runtime/worker_runtime/service.py:47`, `src/maezo/platform/webhooks/service.py:52`,
  `src/maezo/platform/integrations/notifications_bridge.py:67`,
  `src/maezo/platform/evidence/dmn_sweep.py:30`.
- A perna dos workers tem gate proprio por topico: `src/maezo/tools/workers/harness.py:1576`
  (`_evaluate_action_gate`), chamado em `:1633`.

**(b) Mas NADA BLOQUEIA hoje: o plano de controle esta em sombra.**

- `spec/policies/autonomy/action-approvals.yaml:99` -> `modo: shadow`.
- O seam so recusa quando `decision.enforced and not decision.allow`
  (`src/maezo/gateway/seams/_base.py:379`). Sob `modo: shadow`, `enforced` e `False` para toda classe
  (`action-approvals.yaml:160-165`: `enforced` e a CONJUNCAO `modo == enforcing AND` enforcement por
  classe). Logo: o PEP AVALIA e REGISTRA telemetria a cada chamada, e nao altera execucao nenhuma.
- O enforcement vinculante hoje continua sendo ESTRUTURAL — User Task BPMN / no-denial (ADR-0018) —
  exatamente como ADR-0034 ratificou (ver o amend in-loco em
  `docs/adr/0008-autonomy-levels.md:4-7` e `docs/adr/0025-pep-policy-unification.md:6-10`).

**(c) Correcao de uma leitura anterior.** O achado de auditoria que originou esta emenda (AF-03) afirmava
"`PEP.evaluate` tem ZERO chamadores em runtime". Isso e artefato de um grep pelo nome literal
`PEP.evaluate`: a chamada de producao passa pelo protocolo estrutural `PepEvaluator`
(`src/maezo/gateway/effect_pep.py:401-404`), nao por esse nome pontilhado. O chokepoint ja existia no
proprio HEAD auditado — `git show 1804877:src/maezo/gateway/effect_pep.py | grep -n 'ctx.autonomy.evaluate'`
-> `604`. **O documento estava desatualizado; o codigo nao estava errado.**

### 3. Consequencia

1. O mecanismo 1 lido literalmente ("antes de TODO tool call efetivo") continua **impreciso em duas
   dimensoes**, e e isso que esta emenda registra: (i) o escopo real e o conjunto de operacoes
   CATALOGADAS que passam por um seam gateado, nao "todo tool call"; (ii) o resultado e observacao, nao
   bloqueio, enquanto `modo: shadow` estiver vigente.
2. A garantia HITL desta ADR **nao depende** do mecanismo 1 estar enforcing: ela e sustentada pelo
   mecanismo 2 (User Task BPMN) + ADR-0018, que sao estruturais e estao vivos.
3. O flip `shadow -> enforcing` e decisao humana com prazo ja registrado
   (`spec/policies/autonomy/action-approvals.yaml`, bloco `deviation` do
   `enforcement_padrao_nao_mapeado`, cobrado por `make deviation-expiry-check`). Esta emenda nao o
   antecipa e nao o recomenda.
4. Nenhum comportamento de runtime muda com esta emenda: ela e documental.
