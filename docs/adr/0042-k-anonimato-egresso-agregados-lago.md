# ADR-0042: Piso de k-anonimato e allowlist de features no egresso de agregados do lago (ADR-P-003 / AND-02, AND-S2)

**Status:** **Proposed — DRAFT/verify. NADA AQUI ESTA RATIFICADO.** Redigido por AGENTE
(`adr-drafter`, R1) no programa de remediacao do Agent Fleet Audit; um agente nao ratifica ADR.
`/docs/adr/` e dono-gated (`.github/CODEOWNERS:61`). · **Data:** 2026-09-04
· **Area:** Seguranca / Dados / Privacidade (LGPD)

> **Enquadramento.** Este ADR pede ao dono DOIS numeros de politica que o codigo hoje nao tem
> autoridade para escolher: (a) o valor do piso de k-anonimato, e (b) o conteudo da allowlist
> FECHADA de features de metricas admissiveis no egresso. Ele NAO reabre nenhuma decisao aceita —
> ADR-0019 §4 ja EXIGE um piso; o que falta e o VALOR e o mecanismo de allowlist. A perna
> agent-buildable (parametrizacao do piso + validacao chave-a-chave de `metrics`) ja esta
> implementada e e descrita abaixo pelo que ela e, nao pelo que se gostaria que fosse.

---

## Contexto

### O achado (AND-02 / AND-S2, P1, familia PHI)

Andre e o chokepoint de egresso de agregados do lago para a Zona Geral (ADR-0006/ADR-0019). O gate
estrutural e `src/maezo/agents/andre/graph.py::_scrub_aggregate`, chamado por
`AndreGraph.gather` nos dois caminhos de agregado (`analytics.actuarial` e
`analytics.population`). Na base `origin/main` deste ADR o gate tem tres verificacoes:

```
grep -n 'k_anonymity < 1' src/maezo/agents/andre/graph.py
1271:    if agg.k_anonymity < 1:
```

1. `CohortAggregate.has_resolvable_phi()` — varre APENAS `cohort_id` e `dataset_ref`
   (`graph.py::CohortAggregate.has_resolvable_phi`, `suspect = (self.cohort_id, self.dataset_ref)`).
2. `agg.k_anonymity < 1` — ou seja, **k = 1 passa**. k=1 e a ausencia de k-anonimato: uma "coorte"
   de um individuo satisfaz o gate.
3. `agg.suppressed` — apenas registra nota; nao bloqueia.

O que o gate NAO verificava: o dicionario `metrics` (`dict[str, float]`,
`graph.py::CohortAggregate.metrics`) atravessava **inteiro e sem inspecao** para o dossie
(`return {... "metrics": dict(agg.metrics) ...}`). Como `has_resolvable_phi()` nao olhava as CHAVES
de `metrics`, um produtor que colocasse um identificador onde deveria haver um nome de feature de
coorte (o confirmador exercitou exatamente isso, com a chave `paciente_CPF-...`) veria o
identificador atravessar o chokepoint e aterrar no dossie — e, dali, no prompt do aprovador humano.

### O que a ADR aceita ja exige

`docs/adr/0019-amh-lake-of-record.md:74-79` (clausula 4) e explicita:

> «k-anonimato / supressao de celula pequena como parametro de compliance. Toda feature agregada
> servida obedece a um piso de k-anonimato (`k` minimo por celula) e supressao de small-cell (...)
> O valor de `k` e a politica de supressao sao um **parametro de compliance versionado** — DRAFT —
> requires human review (DPO/juridico/regulatorio) before any deploy; o codigo le o piso de config,
> nunca hardcoda um `k` baixo.»

O codigo na base `origin/main` deste ADR hardcodava exatamente o `k` mais baixo possivel (1) e nao
lia piso nenhum de config. O achado nao e uma decisao nova: e a distancia entre ADR-0019 §4 e a
arvore. O KPI do agente que essa distancia falsifica e `phi_egress_violations == 0`
(`spec/agents/andre/agent.yaml`).

### O que ja foi implementado como perna agent-buildable

Implementado em `fleet/and02-metrics-egress-gate` (**nao mergeado**), estritamente dentro do que um
agente pode decidir sozinho — mecanismo, nunca o valor de politica:

- `graph.py::DEFAULT_MIN_K_ANONYMITY` — o default permanece **1**, deliberadamente: mudar o piso
  efetivo e a decisao do dono, e um agente que o elevasse por conta propria estaria legislando.
- `graph.py::_validated_min_k_anonymity` — parse fail-closed de `config["min_k_anonymity"]`,
  levantado em tempo de **build** (nao em runtime), junto das demais checagens fail-closed de
  `build()`. Um piso mal-formado derruba o build, nunca degrada silenciosamente.
- `graph.py::_scrub_aggregate(..., min_k_anonymity=...)` — o `1` literal deixa de existir; o gate
  compara contra o piso injetado (`AndreGraph._min_k_anonymity`, propagado por `build()` a partir de
  `cfg.get("min_k_anonymity", DEFAULT_MIN_K_ANONYMITY)`).
- `CohortAggregate.has_resolvable_phi()` passou a varrer tambem os NOMES das celulas de `metrics` e a
  lista `suppressed`, alem de `cohort_id`/`dataset_ref`.
- `graph.py::_metric_value_is_admissible` — todo VALOR de metrica precisa ser um real finito.
- Bloqueio de AGREGADO INTEIRO (nunca supressao por celula): qualquer falha marca `egress_blocked`,
  `assess` roteia para `human_review` com `phi_egress_risk`. A justificativa esta na docstring de
  `_scrub_aggregate` — um produtor que ja demonstrou colocar identificador onde vai feature nao e
  confiavel no resto do MESMO payload, e a alegacao de k-anon que ele anexou e a menos confiavel de
  todas.
- Nota de bloqueio por CLASS TOKEN: a chave ofensora **e** o PHI; ecoa-la em `gather_notes`
  egressaria exatamente o que o gate acabou de recusar.

Essa perna fecha o furo estrutural (`metrics` cego) e torna o piso **uma mudanca de uma linha de
config**. Ela deliberadamente NAO fecha o furo de politica: com o default 1, o comportamento
observavel do piso e identico ao da base `origin/main` deste ADR.

### Dependencia externa declarada

O produtor real dos agregados e o `mcp-datalake` (WB.4), **nao construido**. Hoje
`AndreGraph.gather` degrada com nota de disclosure quando `self._population is None`
(«cliente de populacao nao injetado (port WB.4 pendente — labeled boundary...)»). Consequencia
pratica: a decisao pedida aqui pode ser tomada ANTES do port, e deve — o piso e a allowlist sao
requisitos de aceitacao do port, nao um ajuste posterior a ele.

---

## Decisao proposta

**Adotamos o k-anonimato do egresso de agregados como parametro de compliance versionado, com duas
partes: um PISO numerico e uma ALLOWLIST FECHADA de nomes de features de metricas.** O codigo le
ambos de configuracao e falha fechado; nenhum dos dois e escolhido em codigo.

### Parte 1 — o valor do piso

| Opcao | Descricao | Custo | Risco |
|---|---|---|---|
| **A (recomendada pelo relatorio de auditoria)** | `min_k_anonymity = 5` como default de plataforma, sobrescrevivel por tenant apenas para CIMA | Coortes pequenas passam a exigir review humano; agregados atuariais de nicho ficam indisponiveis por automacao | Baixo em privacidade; medio em disponibilidade de feature |
| B | Piso por finalidade (ex.: 5 para `population_analytics`, 11 para features que se aproximem de dado sensivel) | Duas politicas para manter e auditar | Baixo; mais preciso, mais caro |
| C | Manter default 1 e exigir piso explicito por tenant (fail-closed apenas onde ha config) | Nenhum custo imediato | **Alto** — e o estado atual com outro nome; o default continua sendo o `k` mais baixo possivel |

**Recomendacao de engenharia (NAO VINCULANTE): opcao A.** k=5 e o piso que o proprio relatorio de
auditoria propoe, e a unica das tres que muda o comportamento por default. A escolha entre A e B, e
o numero em si, sao materia de DPO/juridico/regulatorio — ADR-0019:74-79 ja marca este parametro
como `DRAFT — requires human review (DPO/juridico/regulatorio) before any deploy`.

### Parte 2 — a allowlist FECHADA de features de metricas

Hoje qualquer nome de celula que sobreviva a varredura anti-PHI e admissivel. Uma allowlist FECHADA
inverte o default: apenas nomes de features previamente aprovados atravessam; qualquer nome
desconhecido bloqueia o agregado inteiro pelo mesmo caminho (`egress_blocked` -> `human_review`).

O CONTEUDO da allowlist (quais features de coorte a operadora aceita ver em Zona Geral) e uma
decisao de produto/compliance, nao de engenharia. Hoje os defaults de `AndreGraph.gather` sugerem
tres nomes (`sinistro_agregado`, `utilizacao`, `exposicao_atuarial`), mas eles sao a lista de
features **PEDIDAS** ao cliente de populacao, nao uma lista de features **ADMISSIVEIS** no retorno —
sao coisas diferentes e nao devem ser confundidas na ratificacao.

Ponto de partida ja escrito, para o dono aceitar, cortar ou ampliar: o proprio relatorio de auditoria
(spec AND-S2, snapshot:268) propoe um `_METRIC_KEY_ALLOW` de **cinco** nomes — `sinistro_agregado`,
`utilizacao`, `exposicao_atuarial`, `custo_medio_agregado`, `prevalencia`. Este ADR o registra como
proposta de terceiro, **nao** como recomendacao propria: a distincao pedidas-vs-admissiveis acima
continua valendo, e `prevalencia` em particular e a que mais se aproxima de feature clinica agregada,
o que e exatamente o tipo de julgamento que cabe a DPO/produto, nao a engenharia.

### Parte 3 — onde os parametros moram

Proposta: os dois parametros descem por `config` do build (o mecanismo que ja existe para
`min_k_anonymity`), alimentado de uma politica versionada sob `spec/policies/`.

**Ressalva de precisao, porque a diferenca e operacional:** `spec/policies/` NAO e dono-gated como
diretorio. `.github/CODEOWNERS` atribui dono a QUATRO subdiretorios e a DOIS arquivos —
`/spec/policies/autonomy/` (`:84`), `/spec/policies/privacy/` (`:116`), `/spec/policies/retention/`
(`:134`), `/spec/policies/ans/` (`:196`), mais `/spec/policies/autonomy/action-approvals.yaml`
(`:124`) e `/spec/policies/retention/erasure-plan.template.yaml` (`:138`) — e nao ha nenhuma linha
`/spec/policies/` cobrindo o diretorio inteiro
(`grep -n 'spec/policies' .github/CODEOWNERS`). Um arquivo novo em `spec/policies/<nome-novo>/` seria,
portanto, **nao-CODEOWNED**, e a propriedade que se quer aqui nao existiria.

Duas formas de obter de fato a revisao do dono, e a escolha e dele:
- **Colocar o parametro num subdiretorio que JA e CODEOWNED** — o candidato natural e
  `spec/policies/privacy/` (`.github/CODEOWNERS:116`, `@rodaquino-OMNI` + `@Omni-Saude/security-team`
  + `@lucasreisEvah`), coerente com a natureza LGPD do piso; ou
- **abrir um subdiretorio novo e adicionar a linha correspondente em `.github/CODEOWNERS`** — o que
  e, em si, uma alteracao dono-gated: o proprio arquivo tem dono (`.github/CODEOWNERS:209`,
  `@rodaquino-OMNI` + `@Omni-Saude/security-team`).

A alternativa (constantes em `src/`) permanece rejeitada: colocaria um parametro regulatorio fora do
gate de revisao do dono.

---

## Consequencias

**Positivas**
- `phi_egress_violations == 0` deixa de ser um KPI ancorado num gate que aceita k=1.
- O piso vira auditavel: um numero versionado, com dono e data, e nao um literal em Python.
- A allowlist fechada troca "bloqueia o que reconheco como PHI" (denylist, sempre incompleta) por
  "so passa o que foi aprovado" — a mesma inversao que `KNOWN_PROCESS_KEYS` ja aplica a process keys.

**Negativas (aceitas)**
- Disponibilidade cai: com k=5 e allowlist fechada, agregados legitimos de coorte pequena ou de
  feature nova param no `human_review` ate a politica ser atualizada. Esse e o custo escolhido.
- Toda feature nova passa a exigir uma alteracao dono-gated antes de poder ser consumida. Fricao
  deliberada.
- O default 1 permanece na arvore ate a ratificacao. **A ausencia de squiggle nao e resolucao**: se
  este ADR nao for ratificado, o piso efetivo continua sendo 1 e o furo de politica permanece aberto,
  ainda que o furo estrutural (`metrics` cego) esteja fechado.

---

## Invariantes preservados

- **D2 (particao determinismo/LLM):** nenhuma regra nova em prompt. O piso e a allowlist sao
  configuracao lida por codigo deterministico; o LLM nao ve nem decide sobre eles.
- **Fail-closed:** piso mal-formado derruba o BUILD; agregado suspeito bloqueia INTEIRO e roteia a
  humano. Nao ha caminho de degradacao silenciosa nem meia-emissao.
- **Regra dura 7 (AGENTS.md:33 — itens `hard` da matriz sao intocaveis):** intacta. Nada aqui toca
  `clinical_decision`, `authorization_denial`, `nip_manter_negativa`, `fraud_accusation` ou
  `contract_termination` (`spec/policies/autonomy/L0-core.yaml:6-13`). Andre nao decide; ele entrega
  ou bloqueia um agregado.
- **ADR-0006 / ADR-0019:** este ADR **implementa** ADR-0019 §4; nao a emenda nem a supersede.

---

## O que e decisao do DONO (explicito)

1. **O valor do piso de k-anonimato** — opcao A (5), B (por finalidade) ou C (status quo nomeado).
   Requer, por ADR-0019:74-79, revisao de DPO/juridico/regulatorio.
2. **O conteudo da allowlist FECHADA de features de metricas** — quais nomes de celula de coorte sao
   admissiveis em Zona Geral.
3. **Onde os dois parametros moram** — um subdiretorio de `spec/policies/` que JA seja CODEOWNED
   (`spec/policies/privacy/`, por exemplo) ou um subdiretorio novo COM a linha correspondente em
   `.github/CODEOWNERS`; ver a ressalva da Parte 3. Escolher `spec/policies/` "em geral" nao produz
   o gate de revisao pretendido.
4. **Se a allowlist entra antes ou junto do port `mcp-datalake` WB.4.**

Nenhum dos quatro pode ser decidido por agente: o item 1 e explicitamente marcado como
human-review na ADR-0019, e os itens 2-4 tocam diretorios dono-gated.

---

## Criterio de aceite testavel

1. `_scrub_aggregate` chamado com um `CohortAggregate` de `k_anonymity` igual a `PISO - 1` retorna
   `None` e o grafo termina em `route == "human_review"` com `phi_egress_risk`.
2. `_scrub_aggregate` chamado com `k_anonymity == PISO` e todas as chaves de `metrics` na allowlist
   emite o agregado.
3. Um `CohortAggregate` com UMA chave de `metrics` fora da allowlist bloqueia o agregado INTEIRO
   (nenhuma outra celula do mesmo payload aparece no dossie) e a nota registrada NAO contem a chave
   ofensora (class token apenas).
4. `build(config)` com `min_k_anonymity` ausente resolve para o valor RATIFICADO neste ADR — nao
   para 1 — e um `min_k_anonymity` mal-formado levanta `ValueError` em tempo de build.
5. Golden Tier-A do fluxo `population_analytics` com agregado abaixo do piso: desfecho de review
   humano, `phi_egress_violations` nao incrementado por emissao.
6. `grep -rn 'k_anonymity < 1' src/` retorna zero linhas.

## Supersedes

—. **Implementa** `ADR-0019` §4 (nao emenda, nao supersede). Complementar a `ADR-0006` (duas zonas
PHI) e a `ADR-0017` (enforcement de rede do egresso).
