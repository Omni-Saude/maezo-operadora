# ADR-0012: DMN como ferramenta deterministica unica

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Regras de negocio

## Contexto
O repo hospitalar provou que ~80% das regras vivem bem em DMN (federada, versionada, testavel).
No paradigma agents-first, quem consome a regra e primariamente o agente.

## Decisao
- DMN e a **fonte unica de regra de negocio deterministica**. Duas vias de consumo, mesma tabela:
  `mcp-dmn` (agentes) e service tasks (processos SP-OP).
- LLM **nunca** decide o que uma regra deterministica pode decidir; o agente raciocina sobre o
  RESULTADO da DMN (explica, pendencia, recomenda) — nao a substitui.
- Conteudo payer-side: DUT/ROL/carencia (novo) + porte re-semantizado do conteudo hospitalar
  (DOMED, elegibilidade, glosa) com SME.
- Pipeline `contract_extraction` (portado) gera DMN a partir de contratos do tenant.

## Consequencias
**Positivas:** regras auditaveis fora do LLM; simetria agente/processo; menos alucinacao em decisao.
**Negativas (aceitas):** porte de conteudo e esforco real de SME (semanas).

## Supersedes
—

---

## Emenda 2026-09-03 — o pipeline `contract_extraction` nunca foi portado (GAP AF-18)

**Status:** Proposto (amendment) — DRAFT/verify · **Data:** 2026-09-03 · **Autor:** `adr-reconciler` (R1, AGENTE)
**Marcadores:** `amended-by`: esta Emenda 2026-09-03 (WP-ADR-RECONCILIACAO, GAP AF-18) ·
`obsolete-section`: a clausula do pipeline em `:16`.
**Base de verificacao:** worktree em `71dd4da`.

> APPEND-ONLY. Nenhuma linha do texto original acima foi alterada ou removida. Um AGENTE nao ratifica
> nada: enquanto este bloco carregar `DRAFT/verify`, ele e um fato reconciliado com o codigo, nao uma
> decisao ratificada. Assinatura humana pendente (`.github/CODEOWNERS:61`).

### 1. O que a ADR afirma

`:16` (Decisao): "Pipeline `contract_extraction` (**portado**) gera DMN a partir de contratos do tenant."
O participio "portado" afirma que o porte JA ACONTECEU.

### 2. O que e verdade hoje

- **Zero implementacao.** `grep -rn contract_extraction src/ spec/ | wc -l` -> `0`. Nenhum modulo, nenhum
  script, nenhum artefato de spec com esse nome.
- **Os unicos hits no repo sao documentais**, e nenhum deles e um realizador:
  - `docs/adr/0011-greenfield-repo-hospital-as-reference.md:12` — lista `contract_extraction (porte)` como
    algo **a colher** do repo donor, ou seja, futuro, nao feito.
  - `docs/adr/0028-dmn-evaluation-engine-side.md:125` — cita o pipeline em uma frase condicional sobre
    tenant scoping.
  - `docs/reports/autonomous-completion-report.md:58,89` — afirma "contract_extraction DMN-driven
    scaffold (#21) | #81" e "**scaffolded** #81". **Esta afirmacao nao se sustenta neste repo:** o PR #81
    real e `b762b5c` — "feat(cancel): implement 3 BPMN-declared workers, reconcile registry drift [T3.1]
    (#81)" — conteudo nao relacionado. Esse arquivo pertence a outro work-package (WP-DOCS-HYGIENE) e NAO
    foi tocado por esta emenda; fica registrado como follow-up, da mesma especie do residuo documental do
    GAP AF-02.
- **Nao ha nota de adiamento (deferral) rastreada** para este item em `PLANS.md` nem em
  `docs/evidence-ledger.md` — e por isso que o achado e classificado "nao-governado": a ADR afirma uma
  entrega, e nao existe nem entrega, nem registro de que ela foi adiada.
- **O resto da Decisao permanece vigente e implementado:** DMN como fonte unica de regra deterministica,
  o LLM raciocinando sobre o RESULTADO da DMN e nao substituindo-a, e a avaliacao engine-side
  (ADR-0028). Nenhuma DMN em producao depende do pipeline inexistente: as DMNs vivem versionadas em
  `spec/processes/dmn/`.

### 3. Consequencia

1. `:16` e `obsolete-section`. Leia-a como uma INTENCAO nao realizada, nao como um estado do sistema.
2. **Todo conteudo DMN por tenant e, hoje, artesanal e versionado em `spec/`** — o que tambem significa
   que a governanca do conteudo (SME, ratificacao) continua sendo humana, exatamente como a
   Consequencia "porte de conteudo e esforco real de SME (semanas)" (`:20`) previa.
3. Construir ou descartar formalmente o pipeline e **decisao do dono** (`OWNER-DECISIONS.md`), fora do
   escopo deste WP: a alternativa honesta a construir e registrar o descarte com ADR ou nota de
   deferral rastreavel, para que a ADR pare de afirmar o que nao existe.
4. Nenhum comportamento de runtime muda com esta emenda: ela e documental.
