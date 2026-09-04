# ADR-0044: `task_kind` catalogado e OBRIGATORIO em toda chamada LLM, com gate de conformance no CI (ADR-P-005 / CC-12, BEA-01, HEL-02, GUS-07, AF-12)

**Status:** **Proposed — DRAFT/verify. NADA AQUI ESTA RATIFICADO.** Redigido por AGENTE
(`adr-drafter`, R1) no programa de remediacao do Agent Fleet Audit; um agente nao ratifica ADR.
`/docs/adr/` e dono-gated (`.github/CODEOWNERS:61`). · **Data:** 2026-09-04
· **Area:** Inteligencia / CI / Observabilidade

> **Enquadramento.** Este ADR e um **reforco de cumprimento** da ADR-0009, decisao 2 (`0009:10-12`,
> «Routing por tarefa»). Ele nao cria portfolio de modelos, nao escolhe modelo por tier e nao
> reabre nenhuma decisao aceita. Ele fecha a distancia entre um mecanismo de runtime que EXISTE e
> nove grafos que nao o alimentam — e diz, de forma honesta, que o efeito HOJE e de telemetria, nao
> de custo.

---

## Contexto

### O mecanismo existe e esta vivo

`src/maezo/runtime/inference/__init__.py::InferenceProvider.generate` aceita `task_kind` como quinto
keyword (`task_kind: str | None = None`) e o repassa a
`::InferenceProvider._resolve_task_model`, que:

- resolve `kind = task_kind or DEFAULT_TASK_KIND` (`DEFAULT_TASK_KIND = "task_default"`);
- busca o tier no mapa declarado pelo agente (`self._model_tiers`), caindo em `TIER_UNDECLARED`
  (`"nao_declarado"`) quando a chave nao esta no mapa e em `TIER_NO_MAP` (`"sem_mapa"`) quando nao ha
  mapa nenhum;
- emite o log `llm_tier_resolved` e a metrica `record_llm_tier_resolution(task_kind=, tier=,
  resolution="modelo_unico")`.

O mapa vem da spec: `src/maezo/agents/__init__.py::AgentDefinition.model_tiers` le o bloco `model:`
do `agent.yaml`, e os onze arquivos declaram `task_default: { tier: fast }` /
`reasoning: { tier: frontier }` (`grep -l task_default spec/agents/*/agent.yaml` -> 11). O mapa e
validado em `runtime/inference/__init__.py::_validated_model_tiers` e injetado pelos dois composition
roots vivos: `platform/webhooks/service.py::_helena_model_tiers` (linha 157) e
`runtime/agent_runtime/service.py::_declared_model_tiers` (linha 571).

### Nove grafos nao passam `task_kind`

```
for a in andre beatriz carolina fernando gustavo helena lucas marina rafael valentina _template; do
  echo "$a: $(grep -c task_kind src/maezo/agents/$a/*.py | awk -F: '{s+=$2} END{print s+0}')"
done
```

devolve `lucas: 4` e **zero para os outros dez** (nove agentes + `_template`). As tres ocorrencias de
chamada em Lucas sao o padrao a copiar: `src/maezo/agents/lucas/graph.py:755` e `:832`
(`task_kind="task_default"`) e `:806` (`task_kind="reasoning"`).

Consequencia: em nove agentes, toda chamada resolve para `task_default` por omissao — inclusive as
chamadas que a propria spec classificou como `reasoning`. O tier declarado no `agent.yaml`, que
`AgentDefinition.model_tiers()` passou a consumir no AF-12, volta a ser inerte para eles.

### O efeito HOJE e telemetria, e isso precisa estar escrito

Este repo configura **um unico modelo**. `_resolve_task_model` diz isso na propria docstring
(`__init__.py:584-590`) e o registra na metrica: `TIER_RESOLUTIONS` e um vocabulario FECHADO de dois
valores (`{"modelo_unico", "modelo_por_tier"}`) e **so `modelo_unico` e produzido hoje** — o valor e
literal em `__init__.py:612` e `:619`. Portanto:

- **Nao ha, hoje, economia de custo nem mudanca de qualidade** decorrente de passar `task_kind`. Quem
  vender este trabalho como reducao de custo estara errado.
- O que ha e **observabilidade**: a serie `maezo_llm_tier_resolution_total` deixa de dizer
  "task_default" para 100% das chamadas de nove agentes e passa a refletir a intencao declarada.
- E ha **preparo estrutural**: no dia em que o dono fornecer um mapa real tier->modelo, o roteamento
  passa a valer para todo o fleet sem tocar em dez grafos sob pressao. `TIER_RESOLUTIONS` ja
  distingue os dois regimes na MESMA serie de metrica.

O risco que isso fecha nao e de custo, e de **latencia de adocao**: um portfolio de modelos ligado com
nove grafos mudos rotearia tudo para o tier barato, inclusive raciocinio critico — um modo de falha
silencioso e caro em qualidade, exatamente na direcao em que ADR-0009 se preocupa.

### Precedente do harness de evals

O harness de evals precisou aceitar o kwarg antes de qualquer grafo passa-lo:
`tests/evals/conftest.py::ReplayInferenceProvider.generate` hoje declara `task_kind: str | None = None`
e documenta, no proprio corpo, o defeito que a ausencia causou — antes disso, cada chamada de Lucas
levantava `TypeError: generate() got an unexpected keyword argument 'task_kind'`, **engolido pelo
`except Exception` fail-safe dos call sites**, de modo que toda eval Tier-A de Lucas exercitava apenas
o texto de fallback e nunca uma resposta `recorded_llm` real (achado `EVAL-REPLAY-EXHAUSTION-SWALLOWED`).
Essa e a licao operacional deste ADR: **um seam de teste que nao aceita o kwarg transforma a adocao em
regressao silenciosa de cobertura**. O seam ja esta corrigido; a propagacao pode prosseguir.

### O que ja foi implementado como perna agent-buildable

A propagacao de `task_kind` aos nove grafos e agent-buildable e nao depende deste ADR: e mecanica,
com o padrao de Lucas como referencia, e cada no recebe o `task_kind` que a natureza da chamada indica
(classificacao/frase -> `task_default`; raciocinio sobre caso -> `reasoning`). **O que NAO e
agent-buildable e o gate de CI**, porque ele vive em `scripts/ci/` — diretorio dono-gated
(`.github/CODEOWNERS:223-242`, que registra explicitamente que dois contextos exigidos de CI tem sua
substancia nesse diretorio).

---

## Decisao proposta

**Toda chamada LLM de um grafo de agente passa um `task_kind` catalogado, e um gate de CI recusa uma
chamada sem ele.**

### Parte 1 — o catalogo

`task_kind` deixa de ser string livre e passa a ser um vocabulario FECHADO, na mesma forma que
`TIER_RESOLUTIONS` ja usa: um `Final[frozenset[str]]` em `runtime/inference`, hoje
`{"task_default", "reasoning"}` — as duas chaves que os onze `agent.yaml` declaram. Um `task_kind`
fora do catalogo e erro, nao `TIER_UNDECLARED` silencioso.

### Parte 2 — obrigatoriedade

Tres opcoes, com trade-off real:

| Opcao | Mecanismo | Custo | Risco |
|---|---|---|---|
| **A (recomendada)** | `task_kind` permanece opcional na ASSINATURA (compatibilidade com seams de teste e com `HandlerOutput`), e um **gate de CI** recusa qualquer chamada `\|.generate(` dentro de `src/maezo/agents/**` que nao passe `task_kind=` literal do catalogo | Um script novo em `scripts/ci/` + alvo de Makefile (ambos dono-gated) | Baixo; o gate e sintatico (AST), sem falso-negativo por indirecao trivial |
| B | Tornar `task_kind` **obrigatorio** na assinatura de `InferenceProvider.generate` | Quebra todo seam duck-typed de teste de uma vez (`_FakeInference`, `ReplayInferenceProvider`) e todo call site simultaneamente | Medio-alto; e um big-bang, e o precedente do harness mostra o que uma incompatibilidade de assinatura faz quando cai num `except Exception` |
| C | Apenas propagar nos nove grafos, sem gate | Zero | **Alto** — nada impede o decimo grafo, ou um no novo, de nascer mudo; e exatamente assim que o `model:` do yaml ficou inerte por meses |

**Recomendacao de engenharia (NAO VINCULANTE): A.** O gate e o que torna a correcao permanente; sem
ele, a propagacao e uma limpeza que expira. B tem o mesmo efeito final com um modo de falha pior. C
nao e oferecida como resultado aceitavel.

### Parte 3 — o gate

Tier A (sintatico, sem engine, sem rede): AST sobre `src/maezo/agents/**/*.py`, procurando chamadas a
`.generate(` cujo receptor seja o seam de inference do grafo, exigindo um keyword `task_kind` com
valor literal pertencente ao catalogo. Falha lista arquivo, linha e o no. Roda no contexto de CI ja
existente (nao cria um quarto/quinto contexto exigido) — a forma exata e decisao do dono, porque o
arquivo mora em `scripts/ci/` e o alvo no `Makefile`, ambos dono-gated.

---

## Consequencias

**Positivas**
- `maezo_llm_tier_resolution_total` passa a medir a intencao real do fleet, nao um `task_default`
  universal.
- O bloco `model:` dos onze `agent.yaml` deixa de ser config viva-mas-ignorada por nove agentes.
- No dia do portfolio real, o roteamento vale para todo o fleet sem tocar em dez grafos.
- O gate impede a regressao — que e a unica parte deste trabalho que nao expira.

**Negativas (aceitas)**
- Um gate a mais no CI, num diretorio dono-gated, com o custo de manutencao correspondente.
- Um `task_kind` novo passa a exigir edicao do catalogo (fricao deliberada — e o ponto de um
  vocabulario fechado).
- Enquanto houver um unico modelo configurado, o beneficio e observabilidade e preparo, **nao custo**.
  Este ADR nao promete economia.

---

## Invariantes preservados

- **D2 (particao determinismo/LLM):** intacta e **reforcada**. `task_kind` diz QUAL MODELO atende a
  chamada; nao diz o que o modelo pode decidir. Nenhuma regra de negocio se move para prompt ou para
  o roteador (regra dura 5, `AGENTS.md:31`).
- **Fail-closed:** um `task_kind` fora do catalogo e ERRO, nunca degradacao para `task_default`. A
  ausencia de `task_kind` num grafo passa a ser CI vermelho, nao um default silencioso.
- **Regra dura 7 (AGENTS.md:33):** intacta. Nenhum item `hard` da matriz
  (`spec/policies/autonomy/L0-core.yaml:6-13`) e tocado; roteamento de modelo nao e nivel de autonomia.
- **Regra dura 6 (AGENTS.md:32 — SDK de LLM so em `runtime.inference`):** preservada; toda a mudanca
  fica no seam existente.
- **ADR-0009:** este ADR **reforca o cumprimento** da decisao 2 (`0009:10-12`); nao emenda, nao
  supersede.

---

## O que e decisao do DONO (explicito)

1. **Aprovar o gate de conformance em `scripts/ci/` + o alvo no `Makefile`** — ambos dono-gated
   (`.github/CODEOWNERS:223-242`). Sem isso, a propagacao e uma limpeza que expira.
2. **A opcao A, B ou C** da Parte 2 (a recomendacao de engenharia e A; C nao e recomendada).
3. **O catalogo inicial de `task_kind`** — hoje `{task_default, reasoning}`; se um terceiro (`batch`,
   citado em `0009:12`) deve entrar ja.
4. **O mapa real tier->modelo** — permanece EM ABERTO e fora deste ADR; e decisao de dono/financeira
   («precos variam 10x», `0009:6-7`) e ja esta registrada em `docs/review-queue.md`, conforme
   `runtime/inference/__init__.py:592-595`.

---

## Criterio de aceite testavel

1. Para cada um dos dez grafos de agente, toda chamada ao seam de inference passa `task_kind=` com
   valor no catalogo. Comando de verificacao:
   `grep -c 'task_kind' src/maezo/agents/<agente>/*.py` > 0 para os dez.
2. O gate de CI (Tier A, AST) e **VERMELHO** num commit que remove um `task_kind=` de qualquer no, e
   **VERDE** na arvore corrigida. As duas provas, em dois commits, sao o criterio — nao a segunda
   sozinha.
3. Um `task_kind` fora do catalogo levanta erro tipado em `_resolve_task_model` (teste unitario), em
   vez de resolver `TIER_UNDECLARED`.
4. `ReplayInferenceProvider` registra o `task_kind` recebido, e um golden Tier-A por agente afirma o
   `task_kind` esperado no no de raciocinio — de modo que uma regressao para `task_default` seja
   eval-vermelha, nao invisivel.
5. Metrica: apos a adocao, `maezo_llm_tier_resolution_total` apresenta pelo menos dois valores
   distintos de `task_kind` para os agentes que declaram os dois tiers.
6. Nenhum eval Tier-A muda de resultado pela adocao com modelo unico (prova de que o efeito hoje e de
   telemetria — e a afirmacao honesta deste ADR, e ela e testavel).

## Supersedes

—. **Reforca o cumprimento** de `ADR-0009` decisao 2 (`0009:10-12`); nao emenda, nao supersede.
Relacionado ao achado AF-12 (ja fechado no lado do runtime).
