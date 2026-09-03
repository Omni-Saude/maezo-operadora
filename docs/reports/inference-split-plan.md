# Plano de split de `src/maezo/runtime/inference.py` (D2-02)

**Status deste documento:** PLANO, não execução. Nenhuma linha de `src/maezo/runtime/inference.py`
foi movida nesta PR (WP-BUILD-HYGIENE, gap `D2-02`, P2, `merge_gate: autonomous`) — o próprio
`GAP-REGISTER.md:146` pede "plano de split, não split" nesta onda. Todos os números abaixo foram
reconferidos nesta árvore (branch `fix/build-hygiene-pytest-god-file`, base `main`@`71dd4da`) com
`grep -n`/`sed -n`/`wc -l` — nenhum foi copiado do relatório de auditoria sem reconferência.

## 0. Estado atual (reconfirmado)

```
$ wc -l src/maezo/runtime/inference.py
    2820 src/maezo/runtime/inference.py
$ grep -cE '^\s*def ' src/maezo/runtime/inference.py
43
$ grep -cE '^\s*class ' src/maezo/runtime/inference.py
29
```

Bate exatamente com `GAP-REGISTER.md:146` (2.820 linhas / 43 defs / 29 classes) — sem deriva desde
a auditoria (`181fc82` tinha 2.635; o arquivo cresceu, não encolheu, no delta até `35cffd3`, e
continua no mesmo tamanho em `71dd4da`).

## 1. Por que este é o arquivo errado para split ingênuo (achado central desta análise)

Dois mecanismos de CI **pinam o caminho literal do arquivo `runtime/inference.py`**, não apenas o
caminho de import `maezo.runtime.inference`. Um split que mova classes para submódulos sem tocar
esses dois pontos **quebra os dois — e quebra RUIDOSAMENTE, não em silêncio**: a cerca
`effect-chokepoint-fence` é fail-closed e reprova o gate diante de um caminho relativo não listado
(§1.1), e as três asserções de completude do guard PHI que de fato comparam a população por
igualdade contra uma referência independente (§1.2) falham no `assert` assim que `cls.__module__`
deixa de bater. O achado mais importante deste documento não é que a quebra seja silenciosa — não
é —, e sim que ela é **inevitável e tem que ser corrigida na mesma PR do split**: qualquer split
real que mova essas classes tem que atualizar os dois pontos NA MESMA PR só para a suíte voltar a
ficar verde, o que muda a ordem e o custo de revisão de qualquer split real:

### 1.1 `scripts/ci/check_effect_chokepoint_fence.py` (CODEOWNED, junto com `/Makefile`, ver `.github/CODEOWNERS:246-247`)

- `CONSTRUCTION_ALLOWLIST_BY_NAME` (linhas 188-199) allowlista, POR NOME DE CLASSE, os arquivos que
  podem **chamar** (não definir — o comentário da linha ~144 é explícito: "a class's OWN defining
  module never needs a blanket entry here for that reason alone") `AnthropicInferenceProvider(...)`,
  `BedrockInferenceProvider(...)` e `InferenceProvider(...)`. Hoje o único arquivo listado (além dos
  4 composition-roots externos para `InferenceProvider`) é o literal `"runtime/inference.py"` —
  onde vivem hoje `_build_anthropic` (`:2562`), `_build_bedrock` (`:2570`) e a classe `InferenceProvider`
  (`:2653`).
- `_CONCRETE_PROVIDER_IMPORT_MODULES` (linhas 392-399) isenta, por MÓDULO DE IMPORT (`ImportFrom.module
  == "maezo.runtime.inference"`), as importações de tipo/teste de `AnthropicInferenceProvider`,
  `BedrockInferenceProvider`, `NoopInferenceProvider`, `PhiZoneMockProvider` da cerca §8.3 (que senão
  reprovaria todo `agents/*/graph.py` que faz apenas `from maezo.runtime.inference import InferenceProvider`
  para type-hint).

**Consequência para o split:** mover a chamada `AnthropicInferenceProvider(settings)`/
`BedrockInferenceProvider(settings)` (dentro de `_build_anthropic`/`_build_bedrock`) ou a classe
`InferenceProvider` para um arquivo cujo caminho relativo não seja `runtime/inference.py` (ex.:
`runtime/inference/__init__.py` num pacote) EXIGE, na MESMA PR, atualizar os dois dicionários acima
com o novo caminho literal. Como `/scripts/ci/` é CODEOWNED, **qualquer PR de split real que toque
esses dois construtores é `owner-review` por construção — a mesma classe de acoplamento que
`D2-01` tem com o `/Makefile`.** Isto não é evitável reorganizando o pacote de outro jeito; é
inerente a como a cerca pina por caminho de arquivo.

### 1.2 `tests/unit/runtime/test_inference_capabilities.py:46,251-256` — guarda estrutural anti-drift PHI

```python
_INFERENCE_MODULE = "maezo.runtime.inference"
...
def _concrete_providers_defined_in_module() -> set[type[BaseInferenceProvider]]:
    """Every provider class DEFINED in ``maezo.runtime.inference``, at ANY depth. ..."""
    return {cls for cls in _all_subclasses(BaseInferenceProvider) if cls.__module__ == _INFERENCE_MODULE}
```

Esta função filtra por `cls.__module__` — o atributo que o Python define como o módulo onde a
instrução `class X:` foi **textualmente executada**, não onde o nome foi reexportado. É o guard
anti-regressão do "grandchild blind spot" da Onda 2 (citado em `docs/evidence-ledger.md`, linha
`onda2-phi-inference-inert`): garante que TODO provider concreto (Anthropic, Bedrock, Noop,
PhiZoneMock, BrResident) seja varrido pelas checagens de completude PHI, mesmo netos de classe.

**Consequência para o split:** se `AnthropicInferenceProvider`/`BedrockInferenceProvider`/
`NoopInferenceProvider`/`PhiZoneMockProvider` tiverem sua definição física movida para, por
exemplo, `maezo.runtime.inference.providers`, `cls.__module__` passa a ser
`"maezo.runtime.inference.providers"` — DIFERENTE de `_INFERENCE_MODULE` — e
`_concrete_providers_defined_in_module()` para de enxergar essas 4 classes. **Isto NÃO passa em
silêncio.** Reproduzido ao vivo nesta sessão com um monkeypatch de `__module__` nas 4 classes (a
suíte real, não modificada, contra a árvore desta PR — script descartável fora do repo, não
commitado, disponível em
`/private/tmp/claude-501/-Users-familia-code-maezo-operadora/13ef4a80-e764-47e0-b6b2-0784b9747ef7/scratchpad/repbuild/repro_vacuity_4of5.py`):
o conjunto encolhe para
`{BrResidentInferenceProvider}` (a única das 5 que ainda não teria migrado neste ponto da
sequência §5) e a suíte de `test_inference_capabilities.py` responde com **`3 failed, 49 passed`**,
não um PASS silencioso:

- `test_capability_table_covers_every_provider_in_the_module` (linha 308) — compara o conjunto por
  igualdade contra `_EXPECTED`, um dict HARDCODED keyed pelos próprios objetos de classe (não
  recalculado a partir de `__module__`) — falha no `assert`;
- `test_registry_and_class_hierarchy_agree_on_the_provider_population` (linha 410) — compara contra
  a população construída INVOCANDO `_PROVIDER_FACTORIES` e lendo `type(...)` do objeto construído,
  também independente de `__module__` — falha no `assert`;
- `test_exactly_two_providers_are_phi_eligible_today` (`def` na linha 440, `assert` na linha 471) — `PhiZoneMockProvider` some do
  conjunto elegível — falha no `assert`.

Repetir o mesmo experimento com as 5 classes movidas (cenário em que o conjunto fica genuinamente
vazio, como aconteceria depois do passo 7 sem correção) dá o mesmo resultado — `3 failed, 49
passed`, as mesmas três. O único teste do arquivo que permaneceria vácuo nesse cenário é
`test_the_provider_population_is_walked_transitively` (linha 259), e ele não é um guard de
completude: prova apenas que a travessia é transitiva (netos incluídos), usando classes-sonda
definidas dentro do próprio arquivo de teste — nunca afirma que os providers REAIS estão presentes,
então não é ele quem "finge provar" nada aqui.

Isto continua sendo uma classe de regressão P0/P1 — não porque passe em silêncio, mas porque o
split NÃO FECHA (a suíte fica vermelha, `make test` reprova) até o filtro ser generalizado na MESMA
PR — o mesmo motivo, mudança de disciplina, pelo qual o `release-floor-check` do Makefile (linha
118-132) e a `REASONINGBANK` deste programa tratam sequenciamento de commit como parte do
contrato, não como detalhe.

**Correção obrigatória, na MESMA PR do split que move essas 4 classes:** trocar o filtro por
`cls.__module__.startswith("maezo.runtime.inference")` (ou por um frozenset explícito dos
submódulos alvo) E adicionar um controle negativo que prove que o guard ainda EXCLUI uma classe de
fora do pacote (o arquivo já tem o padrão de controle pareado em
`test_the_provider_population_is_walked_transitively`, linha 259 — reusar o mesmo padrão, não
inventar um novo).

### 1.3 Achado secundário, não bloqueante: `AGENTS.md:32`

> "6. Não importar SDK de LLM fora de `runtime/inference.py`..."

Cita o caminho de arquivo literal, não o pacote. Não é uma cerca executável (é prosa), mas deve ser
generalizada para "fora do pacote `runtime.inference`" no mesmo PR do split, para não ficar
descrevendo um arquivo que não existe mais. Fora de escopo desta PR (não é gap D2-02/03; registrado
aqui para o próximo implementador não perder).

## 2. Tabela de símbolos e faixas de linha (estado atual, `71dd4da`)

Todas as faixas abaixo foram lidas com `sed -n`/`grep -n` nesta sessão.

| Faixa (linhas) | Símbolo(s) | Natureza |
|---|---|---|
| 1-97 | docstring do módulo, imports (`anthropic`, `structlog`, `pydantic_settings`, `maezo.runtime.prompt_format.FormattedPrompt`) | cabeçalho |
| 102-145 | `DEFAULT_ANTHROPIC_MODEL`, `_ANTHROPIC_API_KEY_ENV_VARS`, `DEFAULT_BEDROCK_MODEL`, `DEFAULT_BEDROCK_REGION` | constantes de settings |
| 153 | `InferenceConfigError(ValueError)` | exceção pública |
| 163 | `InferenceProviderError(RuntimeError)` | exceção pública |
| 187 | `PhiZoneRoutingError(PermissionError)` | exceção pública (I-6, ADR-0006) |
| 199 | `BrEndpointNotApprovedError(PermissionError)` | exceção pública |
| 239 | `DeploymentRegion(StrEnum)` | enum de capability |
| 258 | `RetentionPolicy(StrEnum)` | enum de capability |
| 273 | `DataClassification(StrEnum)` | enum de capability |
| 298 | `_CLASSIFICATION_ORDER` | constante de capability |
| 306 | `CredentialSource(StrEnum)` | enum de capability |
| 332 | `ProviderCapabilities` (dataclass) | modelo de capability |
| 393-406 | `PHI_ELIGIBLE_REGIONS`, `PHI_DENIAL_*` (5 constantes) | vocabulário fechado PHI |
| 409 | `phi_zone_denial_reasons()` | função pura sobre `ProviderCapabilities` |
| 447-584 | `NOOP_CAPABILITIES`, `ANTHROPIC_CAPABILITIES`, `BEDROCK_CAPABILITIES`, `PHI_ZONE_MOCK_CAPABILITIES`, `BR_RESIDENT_CAPABILITIES` | instâncias-catálogo de capability por provider |
| 600 | `InferenceSettings(BaseSettings)` | settings pydantic |
| 637 | `BedrockSettings(BaseSettings)` | settings pydantic |
| 670 | `BaseInferenceProvider(ABC)` | contrato-base de provider |
| 768 | `NoopInferenceProvider(BaseInferenceProvider)` | provider mock |
| 809 | `_emit_llm_token_usage()` | telemetria T8 (consumida por Anthropic e Bedrock) |
| 895 | `AnthropicInferenceProvider(BaseInferenceProvider)` | provider real (1P) — classe fenced |
| 1025 | `BedrockInferenceProvider(BaseInferenceProvider)` | provider real (Bedrock) — classe fenced |
| 1243 | `PhiZoneMockProvider(BaseInferenceProvider)` | provider mock PHI |
| 1342-1409 | `BR_REGIONAL_ENDPOINT_HOST_SUFFIXES`, `BR_REGIONAL_ENDPOINT_SCHEME`, `HEADER_*` (5), `BR_REGIONAL_ATTESTED_REGION`, `ENV_PHI_*` (3), `ENDPOINT_DENIAL_*` (6) | vocabulário BR-regional |
| 1412 | `br_endpoint_denial_reasons()` | função pura |
| 1455 | `_fingerprint()` | helper privado |
| 1468 | `BrRegionalTokenUsage` (dataclass) | modelo de transporte |
| 1488 | `BrRegionalRequest` (dataclass) | modelo de transporte |
| 1543 | `BrRegionalResponse` (dataclass) | modelo de transporte |
| 1600 | `BrRegionalTransport(Protocol)` | contrato de transporte |
| 1619 | `BrRegionalTransportUnavailableError` | exceção |
| 1631 | `BedrockBrRegionalTransport` | transporte real (não fenced hoje — não está em `FORBIDDEN_CONSTRUCTION_NAMES`) |
| 1773 | `RefusingBrRegionalTransport` | transporte fail-closed (produção sem DPA) |
| 1804 | `FakeBrRegionalOutcome(StrEnum)` | vocabulário de teste |
| 1847-1864 | `FAKE_BR_REGIONAL_*` (3), `_FAKE_CHARS_PER_TOKEN` | constantes de teste |
| 1867 | `LabeledFakeBrRegionalTransport` | duplo de teste rotulado |
| 1976 | `resolve_br_regional_transport()` | factory de transporte |
| 2014-2017 | `RETRY_STOP_*` (4 constantes) | vocabulário de retry |
| 2021 | `RetryBudget` (dataclass) | modelo de retry |
| 2054 | `retry_denial_reason()` | função pura |
| 2082 | `_deterministic_jitter_unit()` | helper privado |
| 2093 | `_backoff_delay()` | helper privado |
| 2108 | `_RetryTokenBucket` | estado mutável de retry |
| 2138-2557 | `BrResidentInferenceProvider(BaseInferenceProvider)` | provider real PHI (Onda 2 W2 leg 2) — maior classe do arquivo, ~420 linhas |
| 2558-2652 | `_build_noop`, `_build_anthropic`, `_build_bedrock`, `_build_phi_zone_mock`, `_build_br_resident`, `_build_bedrock_br`, `_PROVIDER_FACTORIES` (dict), `_build_provider()` | factory fenced (ver §1.1) |
| 2653-2820 | `InferenceProvider` (facade pública, ADR-0009) | classe fenced (ver §1.1), ponto único de entrada |

## 3. Quem importa o quê (consumidores reais, `grep -rn` nesta árvore)

### 3.1 `src/` — 21 arquivos, quase todos só `InferenceProvider`

```
$ grep -rln "from maezo.runtime.inference import\|from maezo.runtime import inference" src --include="*.py" | wc -l
21
```
**Todos os 21** importam exclusivamente o símbolo `InferenceProvider` (a facade) — nenhum outro
símbolo de `maezo.runtime.inference` é consumido em `src/` fora do próprio pacote/seam. Duas
entradas merecem nota por USO (não por símbolo diferente):

- `src/maezo/runtime/__init__.py:12` — reexporta `InferenceProvider` em `__all__` (linha 15) para
  `maezo.runtime.InferenceProvider`.
- `src/maezo/gateway/seams/inference.py:42` — `from maezo.runtime.inference import InferenceProvider`;
  linha 48 faz `class GatedInferenceProvider(GatedSeam, InferenceProvider)` — **herança real**, não
  só uso. O seam depende dos atributos privados `_impl`/`_settings`/`_assert_phi_zone_capability`
  (docstring da própria classe, linha 56-64, explica por quê são privados) — qualquer refatoração
  do corpo de `InferenceProvider` tem que preservar esse contrato interno, não só a assinatura
  pública `{generate, health_check, model_id, provider_name}` que `test_seam_proofs.py` pina.

Os 4 composition-roots que **constroem** `InferenceProvider()` (e por isso aparecem no allowlist
de construção §1.1) são: `platform/webhooks/service.py:121`,
`runtime/agent_runtime/service.py:496`, `runtime/agent_runtime/a2a_composition.py:591`,
`gateway/tool_registry.py:311` (este último coberto pela allowlist BASE do registry, não por
entrada nomeada).

Os 10 `agents/*/graph.py` (beatriz, helena, gustavo, rafael, carolina, fernando, valentina, marina,
andre, lucas) e 3 `agents/*/delegation.py` (rafael, carolina, andre) importam `InferenceProvider`
só para type hint / repassar ao seam — nenhum constrói diretamente (a construção é sempre nos 4
composition-roots acima, que entregam a instância já gateada por `build_inference_seam`).

### 3.2 `tests/` — 15 arquivos, com a superfície bem mais ampla (ver tabela completa abaixo)

| Arquivo | Símbolos importados de `maezo.runtime.inference` |
|---|---|
| `tests/unit/runtime/test_inference.py` | `AnthropicInferenceProvider`, `InferenceConfigError`, `InferenceProvider`, `InferenceProviderError`, `InferenceSettings`, `NoopInferenceProvider`, `PhiZoneMockProvider`, `PhiZoneRoutingError` |
| `tests/unit/runtime/test_inference_bedrock.py` | `DEFAULT_BEDROCK_MODEL`, `DEFAULT_BEDROCK_REGION`, `BedrockInferenceProvider`, `BedrockSettings`, `InferenceConfigError`, `InferenceProvider`, `InferenceProviderError`, `InferenceSettings`, `PhiZoneRoutingError` |
| `tests/unit/runtime/test_inference_br_resident.py` | 31 símbolos — praticamente todo o vocabulário BR-regional + `BrResidentInferenceProvider` + `InferenceSettings`/`InferenceProvider`/erros |
| `tests/unit/runtime/test_inference_bedrock_br.py` | `BR_REGIONAL_ATTESTED_REGION`, `HEADER_VENDOR_DPA_REF`, `BedrockBrRegionalTransport`, `BrRegionalRequest`, `BrRegionalTransportUnavailableError`, `br_endpoint_denial_reasons` |
| `tests/unit/runtime/test_inference_retry_budget.py` | vocabulário `RETRY_STOP_*` + `RetryBudget`/`retry_denial_reason` + `BrRegionalRequest/Response/TokenUsage` + `BrResidentInferenceProvider` + `InferenceProvider/Settings/ProviderError` |
| `tests/unit/runtime/test_phi_inference_canary.py` | mistura BR-regional + retry + `BrResidentInferenceProvider` + `NoopInferenceProvider` |
| `tests/unit/runtime/test_inference_capabilities.py` | `_PROVIDER_FACTORIES` (acesso privado, `noqa: PLC2701`), todo o vocabulário `PHI_DENIAL_*`/`PHI_ELIGIBLE_REGIONS`, todas as classes de provider + `ProviderCapabilities`/enums — **é o arquivo que hospeda o guard estrutural do §1.2** |
| `tests/unit/runtime/test_inference_live.py` | `InferenceProvider`, `InferenceSettings` (teste `llm_live`, não roda em CI padrão) |
| `tests/unit/a2a/test_a2a_edge_live_pg.py` | `InferenceProvider`, `InferenceSettings` |
| `tests/unit/gateway/seams/test_seam_proofs.py` | `PhiZoneRoutingError` (2x), `InferenceProvider` |
| `tests/evals/conftest.py`, `test_classifier_evals.py`, `test_dossier_adverse_evals.py`, `test_dossier_admin_evals.py` | `InferenceProvider`, `InferenceSettings`, `PhiZoneRoutingError` |
| `tests/unit/ci/test_check_effect_chokepoint_fence.py` | não importa de verdade — gera FIXTURES SINTÉTICAS de texto contendo a string `"from maezo.runtime.inference import InferenceProvider"` (linhas 350, 368) para testar o PRÓPRIO scanner da cerca; não quebra com o split, mas é o teste que prova que a cerca reconhece esse padrão de import |

## 4. Limites de módulo propostos (pacote `src/maezo/runtime/inference/`)

Reorganização em 8 arquivos dentro de um pacote (hoje é 1 arquivo de 2.820 linhas):

| Novo arquivo | Conteúdo (faixas da tabela §2) | ~linhas | Depende de |
|---|---|---|---|
| `errors.py` | 153, 163, 187, 199, 1619 | ~85 | nada (stdlib) |
| `capabilities.py` | 239-406, 409, 447-584 | ~410 | `errors.py`? não — puro |
| `settings.py` | 102-145, 600, 637 | ~90 | `pydantic_settings` |
| `retry_budget.py` | 2014-2136 | ~125 | nada (stdlib) |
| `br_regional.py` | 1342-1409 (constantes), 1412 (`br_endpoint_denial_reasons`), 1455-2013 (dataclasses/Protocol/transportes) | ~575 | `capabilities.py` (DeploymentRegion), `errors.py` |
| `providers.py` | 670-1341 | ~670 | `capabilities.py`, `settings.py`, `errors.py`, `maezo.runtime.prompt_format`, `maezo.platform.observability` |
| `br_resident_provider.py` | 2138-2557 | ~420 | `br_regional.py`, `retry_budget.py`, `capabilities.py`, `settings.py`, `errors.py` |
| `__init__.py` (facade) | docstring do módulo, 2558-2820 + reexport de tudo acima em `__all__` | ~270 | todos os anteriores |

Nenhum arquivo novo passa de ~670 linhas (o maior, `providers.py`, ainda cabe 3 providers reais +
1 mock + o helper de telemetria — poderia ser fatiado de novo em `anthropic_provider.py` +
`bedrock_provider.py` numa segunda rodada, mas isso é refinamento opcional, não necessário para
sair do território de god-file).

O `__init__.py` final reexporta TODOS os símbolos públicos hoje acessíveis via
`from maezo.runtime.inference import X` — nenhum import externo muda de texto. O único efeito
observável fora do pacote é `cls.__module__` das 5 classes de provider concretas (§1.2).

## 5. Sequência de moves preservando comportamento

Cada passo é um commit isolado; depois de CADA passo, rodar o gate completo (abaixo) antes de
avançar — nunca empilhar dois moves sem gate verde entre eles.

1. **`errors.py`** — mover as 5 exceções puras. Zero acoplamento com as cercas (§1.1/§1.2 não citam
   nenhuma delas). Risco: nulo.
2. **`capabilities.py`** — mover enums + `ProviderCapabilities` + `phi_zone_denial_reasons` +
   constantes `PHI_*` + as 5 instâncias `*_CAPABILITIES`. Nenhuma classe aqui está em
   `FORBIDDEN_CONSTRUCTION_NAMES`. Risco: nulo.
3. **`settings.py`** — mover `InferenceSettings`/`BedrockSettings` + defaults. Risco: nulo (não são
   classes fenced).
4. **`retry_budget.py`** — mover `RetryBudget`/`_RetryTokenBucket`/funções de backoff. Independente
   de (5); pode ser feito em paralelo. Risco: nulo.
5. **`br_regional.py`** — mover o transporte BR-regional. `BedrockBrRegionalTransport` não está em
   `FORBIDDEN_CONSTRUCTION_NAMES` hoje (conferido — só os providers LLM diretos e o `InferenceProvider`
   estão). Risco: baixo, mas checar `test_inference_br_resident.py`/`test_inference_bedrock_br.py`
   continuam verdes (usam `resolve_br_regional_transport` e `LabeledFakeBrRegionalTransport`
   extensivamente).
6. **`providers.py`** — mover `BaseInferenceProvider`, `NoopInferenceProvider`,
   `_emit_llm_token_usage`, `AnthropicInferenceProvider`, `BedrockInferenceProvider`,
   `PhiZoneMockProvider`. **NESTE COMMIT, obrigatório no mesmo diff:**
   - atualizar `tests/unit/runtime/test_inference_capabilities.py:46` (o filtro `__module__`, §1.2)
     com o controle negativo pareado;
   - **NÃO** atualizar ainda `scripts/ci/check_effect_chokepoint_fence.py` — as chamadas
     `AnthropicInferenceProvider(...)`/`BedrockInferenceProvider(...)` continuam em `_build_anthropic`/
     `_build_bedrock`, que só migram no passo 8. Enquanto a definição da classe muda de arquivo mas a
     CHAMADA de construção continua em `runtime/inference.py` (futuro `__init__.py` do pacote), a
     cerca §8.1 permanece satisfeita — ela pina o arquivo que CHAMA, não o que DEFINE (confirmado no
     comentário de `check_effect_chokepoint_fence.py:144-147`).
7. **`br_resident_provider.py`** — mover `BrResidentInferenceProvider`. Mesma ressalva do passo 6
   quanto ao guard `__module__` (já generalizado no passo 6, cobre este também se o filtro virar
   `startswith`).
8. **Colapsar o resto em `__init__.py`** — `_build_*`/`_PROVIDER_FACTORIES`/`_build_provider`/
   `InferenceProvider` continuam fisicamente no arquivo que os chama entre si; o único arquivo que
   muda de caminho é o antigo `runtime/inference.py` → `runtime/inference/__init__.py`. **NESTE
   COMMIT, obrigatório no mesmo diff (CODEOWNED, owner-review por construção — ver §1.1):**
   - `scripts/ci/check_effect_chokepoint_fence.py` linhas 188, 191, 192-197: trocar a string
     `"runtime/inference.py"` por `"runtime/inference/__init__.py"` nas 3 entradas de
     `CONSTRUCTION_ALLOWLIST_BY_NAME`;
   - linhas 392-399 (`_CONCRETE_PROVIDER_IMPORT_MODULES`): a chave `"maezo.runtime.inference"`
     continua correta sem mudança (é o caminho de IMPORT do pacote, que não muda — só o caminho de
     ARQUIVO muda);
   - `AGENTS.md:32` (§1.3): trocar "fora de `runtime/inference.py`" por "fora do pacote
     `runtime.inference`".

Depois do passo 8, rodar TODA a suíte (`make test`), `make type`, `make effect-chokepoint-fence`,
`make check-start-process-fence`, e os 15 arquivos de teste da tabela §3.2 isoladamente com
`-v --tb=short` para conferir contagem de teste-a-teste idêntica ao antes.

## 6. Testes que pinam cada seam (não podem regredir silenciosamente)

| Seam | Teste(s) que prova(m) | O que quebra se o split for feito errado |
|---|---|---|
| Construção de `Anthropic/BedrockInferenceProvider`/`InferenceProvider` só via registry/composition-roots sancionados | `tests/unit/ci/test_check_effect_chokepoint_fence.py` (roda o scanner contra a árvore real) + `make effect-chokepoint-fence` | Passo 8 sem atualizar os 2 dicts → false positive (`make effect-chokepoint-fence` reprova PR legítimo) OU, pior, se a entrada for apagada em vez de atualizada, false negative (buraco na cerca) |
| Toda subclasse concreta de `BaseInferenceProvider` é varrida pelo guard PHI, mesmo netos | `tests/unit/runtime/test_inference_capabilities.py::test_capability_table_covers_every_provider_in_the_module` + `::test_registry_and_class_hierarchy_agree_on_the_provider_population` + `::test_exactly_two_providers_are_phi_eligible_today` | Passo 6/7 sem generalizar `_INFERENCE_MODULE` → guard varre uma população encolhida/vazia, e as 3 asserções de completude acima FALHAM NO ASSERT (`3 failed, 49 passed`, reproduzido nesta sessão) — ruidoso, não um PASS falso; a suíte só fecha de novo depois do filtro ser generalizado |
| Superfície pública do facade gateado (`generate`/`health_check`/`model_id`/`provider_name`) | `tests/unit/gateway/seams/test_seam_proofs.py` (linha 827 e a suíte de `GatedInferenceProvider`) | Qualquer passo que renomeie/remova um desses 4 membros de `InferenceProvider` quebra o seam — nenhum passo desta sequência toca a assinatura, só o arquivo |
| `PhiZoneRoutingError` continua sendo a MESMA classe (identidade), independente/não-curto-circuitada pelo PEP (I-6) | `test_seam_proofs.py:233,678` (`test_phi_zone_routing_fail_close_is_independent_of_the_pep`) | Mover a exceção para `errors.py` é seguro (import por nome, não por caminho) — mas se por engano ela for REDEFINIDA em vez de movida (2 classes com o mesmo nome em módulos diferentes), o `except PhiZoneRoutingError` deixa de casar entre o produtor e o consumidor. Regra: mover é `git mv`-like (cortar e colar o texto), nunca reescrever |
| Todos os providers reais mantêm `phi_capable`/`capabilities` corretos por classe | `tests/unit/runtime/test_inference_capabilities.py` (bateria completa, ~20 testes) | Qualquer reordenação de import que crie um ciclo (`providers.py` importando `br_resident_provider.py` por engano, por exemplo) quebra na importação, não silenciosamente — mypy/pytest pegam isso no primeiro `make test` do passo |
| `_PROVIDER_FACTORIES` acessível como hoje (`maezo.runtime.inference._PROVIDER_FACTORIES`) | `test_inference_capabilities.py` (`noqa: PLC2701`, acesso direto) | Fica em `__init__.py` no passo 8 — caminho de import não muda, sem ação extra |
| Retry budget determinístico (sem retry após bytes enviados) | `tests/unit/runtime/test_inference_retry_budget.py` (bateria RED-control) | Isolado, sem acoplamento às cercas — só precisa que `br_resident_provider.py` (passo 7) importe `retry_budget.py` (passo 4) corretamente |

## 7. Gate a rodar após CADA passo da sequência (não só no final)

```bash
env -u VIRTUAL_ENV make lint
env -u VIRTUAL_ENV make type
env -u VIRTUAL_ENV make test
env -u VIRTUAL_ENV make effect-chokepoint-fence
env -u VIRTUAL_ENV make check-start-process-fence
env -u VIRTUAL_ENV uv run python -m pytest tests/unit/runtime/test_inference*.py tests/unit/runtime/test_phi_inference_canary.py tests/unit/gateway/seams/test_seam_proofs.py tests/unit/ci/test_check_effect_chokepoint_fence.py -v --tb=short
```

Nenhum passo desta sequência deve reduzir a contagem de `passed` da suíte unitária abaixo do piso
vigente no commit anterior (mesma disciplina do `release-floor-check`, Makefile:118-132).

## 8. Fora de escopo desta PR

Nenhuma linha de `src/maezo/runtime/inference.py` foi tocada. Este documento é o único artefato
desta PR para o gap D2-02. A execução real do split é trabalho de uma PR futura, dividida nos 8
commits da §5, cada um com seu próprio gate e — a partir do passo 6 — revisão de dono nos passos
que tocam `scripts/ci/check_effect_chokepoint_fence.py` (CODEOWNED).

---

## Apêndice — D2-03: reconfirmação do mypy --strict

**Gap:** `GAP-REGISTER.md:147` — "mypy --strict com 4 erros: artefato do ambiente de execução da
auditoria, não defeito de código", status `UNREPRODUCED` (não `CLOSED-BY`: a arbitragem A-02 do
próprio registro já concluía que a premissa original nunca foi um defeito de código — ver
`GAP-REGISTER.md:201`). Esta seção reconfirma isso numa árvore sincronizada nesta sessão, com
versões de ferramenta explícitas, em vez de reafirmar a arbitragem sem rodar nada.

**Ambiente:**

```
$ env -u VIRTUAL_ENV uv run mypy --version
mypy 1.20.2 (compiled: yes)
$ env -u VIRTUAL_ENV uv run python --version
Python 3.12.13
```

`pyproject.toml` (`[tool.mypy]`, linhas 193-197): `python_version = "3.12"`, `strict = true`,
`packages = ["maezo"]`, `mypy_path = "src"` — byte-idêntico ao que `main`@`71dd4da` já tinha (sem
mudança de configuração nesta PR).

**Comando do `make type` (`Makefile:19`, `uv run mypy`, resolve via `[tool.mypy]` acima):**

```
$ env -u VIRTUAL_ENV uv run mypy
Success: no issues found in 223 source files
```

**Comando alternativo citado no prompt desta tarefa (`uv run mypy --strict src/maezo`):**

```
$ env -u VIRTUAL_ENV uv run mypy --strict src/maezo
Success: no issues found in 221 source files
```

As duas invocações resolvem para **0 erros** — nenhuma reproduz os "4 erros" do relatório de
auditoria original. A diferença de contagem de arquivos entre as duas (223 vs 221) vem do modo de
descoberta (`packages = ["maezo"]` via instalação editável do pacote vs. varredura direta do
diretório `src/maezo`) — não de nenhum arquivo com erro sendo silenciado num modo e não no outro
(ambos terminam em `Success`, que é fail-closed: qualquer erro real apareceria em QUALQUER um dos
dois modos).

**Confirmação de que o `pyproject.toml`'s bloco de stubs de dev não mudou desde a auditoria** (a
arbitragem A-02 do registro cita isso como a segunda perna da prova):

```
$ git diff 181fc82..HEAD -- pyproject.toml | grep -A3 -B3 "lxml-stubs\|types-PyYAML\|mypy>="
```

confirma que as linhas `types-PyYAML>=6.0,<7.0`, `lxml-stubs>=0.5,<1.0` e `mypy>=1.10,<3.0`
aparecem em ambos os lados do diff com o MESMO texto — o diff de 358 linhas do arquivo inteiro é
reflow/reordenação de outras seções (`[project]`, `[project.optional-dependencies]`,
`[project.scripts]`), não uma mudança na declaração de stubs de tipo que alimentam o mypy strict.

**Veredito:** `D2-03` reconfirma a arbitragem A-02 do `GAP-REGISTER.md` — os "4 erros" citados pelo
relatório de auditoria original não reproduzem em nenhuma árvore sincronizada com
`uv sync --extra dev` desta sessão, com `mypy 1.20.2`/`Python 3.12.13`. Não há root-cause fix a
fazer porque não há defeito de código a corrigir; nenhum `type: ignore` foi adicionado (nem seria
necessário). Ação residual do próprio registro ("garantir que o CI instale as dev extras") já é o
caso hoje — confirmado por leitura (sem edição; `.github/workflows/` é intocável por este WP) de
`.github/workflows/ci.yml`, que roda `uv sync --locked --extra dev` em todos os jobs relevantes
(linhas 41, 94, 180, 216, 292, 366, 500, 744, 833) — o mypy strict do CI não é decorativo.
