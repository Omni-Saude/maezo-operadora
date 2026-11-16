# ADR-0076: Emenda ao ADR-0075 — aceite parcial dos itens que a Onda 1 implementou; errata ao ADR-0040 D4 (UNIQUE e órfãs sob `rco/`)

**Status:** Accepted — ordem do dono de 15/11/2026 (Programa Circuito Fechado, Onda 1), restrita aos itens do ADR-0075 listados em §1 (ver "Decisão de aceite" ao final). Redigido por AGENTE (unidade MZO-0d); um agente não aceita ADR e nenhum campo de assinatura foi preenchido (ADR-0007). `/docs/adr/` é CODEOWNERS. O ADR-0075 continua Proposed em tudo o que não está em §1.
**Data:** 2026-11-15
**Área:** Integração / Regras de negócio / Motor de processos / Governança de ADR

## Contexto

O ADR-0075 (Proposed, 14/11/2026) desenhou a entrada do pacote de regras K1 do
`regras-contratos-operadora` (rco) no Maezo. A Onda 1 do Programa Circuito Fechado implementou
parte desse desenho, já na `main`:

| Unidade | PR | Merge |
|---|---|---|
| MZO-1a — pasta `rco/`, varredura explícita, nome de recurso, isenções por pasta | #1223 | `9a4a7d21` |
| MZO-1d — pin `rules.lock.json` e verificador fail-closed | #1224 | `faf14087` |
| MZO-1b — lint estático do vocabulário de saída DMN | #1229 | `e70b5efc` |
| MZO-1c — falha determinística de avaliação DMN é permanente | #1230 | `a813918c` |

A unidade MZO-5a (`dmn_versions` com `shaDmn`/`versaoRegra`/`idRegra`) está **em revisão (#1232)** e não
entrou na `main` em `a813918c`. Por isso não é aceita aqui.

Ordem do dono de 15/11/2026, ao abrir a Onda 1: mover de Proposed para Accepted **só** os itens que a
Onda 1 implementa, por emenda curta que liste o que foi implementado e o que segue proposto; a saída
adversa executável e as ações L2 ficam para a Onda 3; a aprovação em lote depende de P7; errata ao
ADR-0040 D4 sobre `UNIQUE` no pacote.

**Por que um ADR novo, e não a troca do Status do ADR-0075.** O índice tem um único Status por ADR
(`scripts/docs/contagens_canonicas.py:77`, primeira linha `**Status:**`), e o ADR-0075 tem itens que
continuam propostos. Trocar o Status inteiro aceitaria também o que o dono deixou de fora. O molde é o
das emendas do repositório (ADR-0069, aceito, emenda o ADR-0023; ADR-0074, ainda proposto, emenda o ADR-0068): ADR próprio, e o
ADR-0075 recebe só uma linha de remissão no cabeçalho.

## Decisão

### §1 — Itens do ADR-0075 que passam a Accepted (implementados na Onda 1)

1. **Pasta e varredura (ADR-0075 §(b)).** O K1 entra em `spec/processes/dmn/rco/`, hoje com
   `README.md` e o exemplo sintético `rco_exemplo_sintetico.dmn`. A varredura é uma segunda
   passada explícita de um nível, nunca `rglob`: `list_dmn_files`
   (`src/maezo/platform/validation/dmn.py:42-53`), usada pelo deploy
   (`src/maezo/platform/deploy/engine_deploy.py:179`), pela validação
   (`src/maezo/platform/validation/cli.py:104-105`), pelas aprovações
   (`scripts/ci/check_aprovacao_de_regras.py:89-95`), pelas contagens
   (`scripts/docs/contagens_canonicas.py:122`) e pelo runner de integração
   (`scripts/dev/run_engine_integration.py:1484-1491`).
2. **Nome do recurso no deploy** = `rco/<arquivo>.dmn` (`src/maezo/platform/validation/dmn.py:60-70`,
   usado em `src/maezo/platform/deploy/engine_deploy.py:335-337`).
3. **Prefixo obrigatório `rco_`** nos ids de decisão sob `rco/`
   (`src/maezo/platform/validation/dmn.py:39,126-130`), com a unicidade de id na árvore inteira.
4. **Regras por pasta nos gates:**
   - órfãs: decisão sob `rco/` não é erro de órfã, é governada pelo pin
     (`src/maezo/platform/validation/crossref.py:106-107`);
   - entradas mortas: `UNIQUE` aceito só sob `rco/` (`tests/unit/spec/test_dmn_dead_inputs_fence.py:350-377`);
   - aprovações: `rco/` fora do registro por isenção **PROVISÓRIA até a decisão P7**
     (`scripts/ci/check_aprovacao_de_regras.py:80-83`). A isenção é o que está aceito; a aprovação
     em lote, não (ver §2).
5. **Contagem `dmn_rco` separada** (`scripts/docs/contagens_canonicas.py:122`; `README.md:134`).
6. **CODEOWNERS** para `/spec/processes/dmn/rco/` e `/config/integrations/rco/` (`.github/CODEOWNERS:107-108`).
7. **Pin e verificador (ADR-0075 §(b)).** `config/integrations/rco/rules.lock.json` com
   `estado` ∈ {`sem_pacote`, `fixado`} (hoje `sem_pacote`, `:28`), `manifest_pin`, `artifacts` e
   `exemplos_sinteticos` (`:36`); verificador fail-closed, só biblioteca padrão, sem rede,
   `scripts/ci/verify_rco_rules_pin.py:16`; alvo `Makefile:168` e passo de CI
   `ci.yml:927-928` (`.github/workflows/`).
8. **Lint estático do vocabulário de saída (ADR-0075 §(d), só a parte estática)** em
   `validate-artifacts` (`src/maezo/platform/validation/vocabulario_saida.py:560-581`), lendo o dado
   `config/integrations/rco/vocabulario-saida.yaml` (`estado: provisorio`, `:24`; a seção `k1` será
   congelada pela unidade PROG-RCO-1 no rco):
   - (a) nenhuma saída adversa em tabela do Maezo: ADR-0018 parte 2 como gate estático;
   - (b) vocabulário fechado do K1 para tabelas sob `rco/`.
9. **Erro de avaliação determinística é permanente (ADR-0075 §(c), primeiro item).**
   `DmnDeterministicEvaluationError` (`ERR_DMN_EVALUATION_DETERMINISTIC`) para a forma REST real do
   CIB Seven 2.1.0 (HTTP 500 `RestException` "Cannot evaluate decision <id>: Exception while
   evaluating decision with key 'null'"; a REST esconde o DMN-03001, que só aparece no log do
   servidor), e a subclasse `DmnHitPolicyViolationError` (`ERR_DMN_HIT_POLICY_VIOLATION`) para a forma
   estrita do upstream (`src/maezo/tools/workers/dmn_transport.py:173-174,177,224`). Sem retry:
   incidente na primeira entrega (humano) e linha de auditoria `REFUSED`
   (`src/maezo/tools/workers/harness.py:143,165-175`). Prova no motor real em
   `tests/integration/dmn/test_unique_multimatch_engine.py` (marcador `integration`).

   **Correção ao texto do ADR-0075 §(c).** O §(c) diz que a sobreposição "vira `DmnEvaluationError`,
   classificado como TRANSIENTE e repetível". Isso descrevia o código anterior ao #1230. Vale agora o
   item 9: a sobreposição é erro permanente e vai para humano.

### §2 — O que segue Proposed no ADR-0075

| Item | ADR-0075 | Quando |
|---|---|---|
| Saída adversa executável e a emenda ao ADR-0018 parte 2 (worker guardado, `classeAdverso`) | §(d) | Onda 3 |
| Ações automáticas L2 (negativa administrativa, glosa determinística), interruptor por `idRegra` | decisão 2; §(e) | Onda 3 |
| Aprovação em lote por versão do manifesto (`dono` + `auditor-neutro`) e domínios de `avaliacao_dmn` | §(g) | depende da decisão do dono P7 |
| Leitor do manifesto K1 e pin real (`estado: fixado`) | §(b) | Onda 2 |
| Deploy de `rco/` condicionado ao pin verificado (hoje `collect_artifacts` lista `rco/` sem consultar o pin) | §(b), ponto 1 | Onda 2 |
| `dmn_versions` com `shaDmn`/`versaoRegra`/`idRegra` (MZO-5a; o runtime recusa `fixado` até a Onda 2) | §(f) | em revisão (#1232) |
| Registro de sombra por `idRegra` | §(e) | sem onda marcada |
| K6: filas do rco × `candidateGroups`; grupos para `auditoria_enfermagem` e `regulacao` | §(h) | sem onda marcada; decisão do dono |
| Ausência de linha casada em `UNIQUE` como encaminhamento humano; Q-1 do ADR-0028 | §(c), segundo item | aberto |
| Modo `UNIQUE` no leitor de testes `tests/support/dmn_first_hit.py` | §(c) | aberto |
| Qualquer mudança em `spec/policies/autonomy/` | §(g), §(i) | nunca nestas ondas |

Os 5 itens hard (`spec/policies/autonomy/_hard_frozen.yaml`) e `authorization_denial` L0 não mudam
(ADR-0075 §(i)).

### §3 — Errata ao ADR-0040 D4 (tabelas sob `spec/processes/dmn/rco/`)

O ADR-0040 é Accepted (DEC-27) e não é reescrito (`docs/adr/README.md`, regra 2). A errata mora aqui;
o ADR-0040 recebe só uma linha de remissão no cabeçalho, como a que o ADR-0018 tem para o ADR-0040.

1. **Hit policy.** "Hit policy `FIRST` com row catch-all conservadora em toda tabela"
   (`docs/adr/0040-perspectiva-operadora-contas-recurso.md:178`) vale para as tabelas do Maezo
   (primeiro nível de `spec/processes/dmn/`). As tabelas sob `spec/processes/dmn/rco/` são `UNIQUE`,
   como o rco publica, sem catch-all: converter reescreveria regra do fornecedor e quebraria o
   `shaDmn` do pin. A sobreposição é erro permanente (§1, item 9).
2. **Órfãs.** "`orphans-allowlist.yaml` tem delta ZERO" (`docs/adr/0040-perspectiva-operadora-contas-recurso.md:176`)
   continua verdadeiro para as 7 decisões do D4. Mas decisão sob `rco/` não entra na allowlist: a
   regra é por pasta, governada pelo `rules.lock.json` (§1, item 4).
3. **Não muda.** "Nenhuma DMN emite o adverso" (`docs/adr/0040-perspectiva-operadora-contas-recurso.md:167`)
   segue em vigor sem exceção. O lint de §1, item 8 aplica essa regra como gate estático. O adendo ao
   ADR-0040 sobre saída adversa do K1 é da Onda 3.

### §4 — Citações do ADR-0075 que se moveram (nota, sem edição)

O gate `scripts/ci/check_doc_symbol_citations.py` passa: linha em citação simples é informativa.
Algumas linhas do ADR-0075 se moveram com a Onda 1, por exemplo `engine_deploy.py:172` → `:179`,
`engine_deploy.py:328` → `:335`, `contagens_canonicas.py:117` → `:122`, `dmn_transport.py:94-103` →
`:104` em diante. O ADR-0075 não foi editado por isso.

## Consequências

**Positivas:** o aceite cobre só o que tem código e teste na `main`; o resto continua visível como
proposto, com a onda ou a decisão de que depende. A errata impede que o D4 do ADR-0040 seja lido
como proibição de `UNIQUE` sob `rco/`.

**Negativas (aceitas):** o ADR-0075 fica dividido entre dois documentos até ser aceito por inteiro;
quem lê o 0075 precisa seguir a remissão do cabeçalho.

## Supersedes

—. Amends ADR-0075 (§(b), §(c) primeiro item, §(d) parte estática) e corrige ADR-0040 D4 por errata,
sem substituir nenhum.

## Decisão de aceite (ordem do dono, 15/11/2026)

- Fonte: ordem do dono de 15/11/2026 à orquestradora do Programa Circuito Fechado, ao abrir a Onda 1,
  transmitida à unidade MZO-0d.
- Escopo: só §1 e a errata de §3. §2 continua Proposed no ADR-0075.
- Status anterior dos itens de §1: Proposed, dentro do ADR-0075 (histórico preservado no próprio
  ADR-0075).
- Nenhum campo de assinatura foi preenchido por agente (ADR-0007).
