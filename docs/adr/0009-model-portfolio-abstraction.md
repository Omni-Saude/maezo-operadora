# ADR-0009: Portfolio de modelos com abstracao de provider + eval gates

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Inteligencia

## Contexto
Modelos evoluem em ciclos de meses; precos variam 10x; PHI restringe onde cada chamada roda.
Travar em um provider e risco estrategico.

## Decisao
1. Abstracao de inference em `maezo.runtime.inference` — nenhum agente importa SDK de provider.
2. Routing por tarefa: classificacao -> modelo rapido/barato; raciocinio critico -> fronteira;
   lote -> batch tier.
3. Config de modelo por tenant+agente na Agent Definition.
4. **Golden dataset por agente** (`tests/evals/golden/`) rodado a cada mudanca de prompt/grafo/modelo —
   gate de CI obrigatorio para promover.

## Consequencias
**Positivas:** troca de modelo vira rotina com gate de qualidade; custo controlavel.
**Negativas (aceitas):** abstracao deve expor capabilities opcionais (caching, tool-use nativo),
nao nivelar por baixo.

## Supersedes
—
