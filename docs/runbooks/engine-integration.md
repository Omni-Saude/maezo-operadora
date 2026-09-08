# Integração local com engine real e checkout fixado

Este runbook opera o CIB Seven usado pelas suítes `integration` sem compartilhar estado com
outros checkouts ou stacks locais. O runner executa exatamente uma suíte por invocação em um
projeto Compose novo, derivado de arquivos rastreados no próprio checkout alvo.

## Pré-requisitos e limites

- Docker/Colima precisa estar disponível. Para um laptop de 8 GiB, reserve 6 GiB ao Colima e
  não rode a suíte unitária em paralelo.
- O checkout precisa estar em um SHA completo de 40 caracteres e sem alterações tracked. As
  entradas de execução em `src/`, `spec/`, `tests/integration/`, `scripts/dev/`,
  `docker-compose.yml`, `pyproject.toml` e `uv.lock` também não podem ser untracked.
- O runner remove apenas o projeto Compose `maezo-completion-engine`. Ele não usa `docker system
  prune`, não enumera nem remove outros worktrees e não atua sobre containers de outros projetos.
- Não apague uma trava antiga automaticamente. Se `engine.lock` existir após um processo morto,
  examine `engine.lock/owner.json`, o processo indicado e os recursos do projeto antes de uma
  decisão humana de recuperação.

Portas publicadas pelo override rastreado, todas vinculadas somente a `127.0.0.1`:

| Serviço | Host | Container/uso |
|---|---:|---|
| CIB Seven 2.1.0 | 18080 | 8080, `/engine-rest` |
| Postgres 16 | 15433 | 5432 |
| Kafka 7.7.0 | 19092 | listener EXTERNAL 9092 |
| HAPI FHIR 7.4.0, quando requerido | 18081 | 8080 |

O listener anunciado ao host é `localhost:19092`; o listener interno continua
`kafka:29092`. A prontidão do broker é medida dentro do container com
`kafka-broker-api-versions --bootstrap-server kafka:29092`.

## Descoberta sem iniciar containers

Use um diretório de resultados fora do checkout. Remova `VIRTUAL_ENV` do ambiente para impedir
que outro worktree forneça o pacote importado.

```bash
CHECKOUT=/Users/familia/code/maezo-completion-wt/engine-runner
TARGET_SHA=$(git -C "$CHECKOUT" rev-parse HEAD)
RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/discovery-$TARGET_SHA

env -u VIRTUAL_ENV uv run --directory "$CHECKOUT" --locked python \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" discover \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --results-dir "$RESULTS"
```

`discovery.json` parte da coleta canônica `pytest tests -m integration` e contém cada nodeid. Seu
`execution_manifest` divide a coleção por arquivo e informa grupo, contagem esperada e serviços
necessários. Cada entrada é uma execução separada em stack fresca. Ele
falha se a coleta for vazia, se algum arquivo `tests/integration/**/test_*.py` não gerar nodeid,
se houver teste nessa árvore sem `integration`, se a união das três suítes não for exata ou se
LGPD/escalation desaparecerem. Assim os testes live-PG que vivem sob `tests/unit/` não ficam fora
do censo. As decisões de subir CIB, Kafka e HAPI são refeitas sobre os arquivos coletados; no
estado atual um teste `db-unit` exige CIB real e HAPI não é necessário. Os logs de coleta
preservam stderr e stdout.

## Execução serial

O comando abaixo adquire `/Users/familia/code/maezo-operadora/engine.lock` por `mkdir` atômico.
O arquivo `owner.json` registra PID, token, checkout, SHA, suíte e checkpoint. Falha de aquisição
retorna 73 e não chama `docker compose down`. Somente o processo cujo PID/token ainda coincide
com o arquivo pode desmontar o projeto e liberar a trava.

```bash
RUN_RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/core-$TARGET_SHA

env -u VIRTUAL_ENV uv run --directory "$CHECKOUT" --locked python \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" run \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --suite core \
  --test-file tests/integration/processes/test_sp_op_lgpd_dsr_001.py \
  --results-dir "$RUN_RESULTS" \
  --lock-timeout 0
```

A entrada `core` sobe Postgres e Kafka, espera prontidão real, sobe o CIB Seven e então envia todos
os BPMN/DMN pelo CLI `maezo.platform.deploy --spec-dir "$CHECKOUT/spec"`. Antes de pytest, o
runner consulta cada definição latest no engine e compara o SHA-256 do XML devolvido com o
arquivo do checkout. `deployment-provenance.json` fixa imagem declarada, versão respondida,
deployment IDs, versões, hashes dos dois Compose e hashes de todos os artefatos.

A suíte `chaos` é um ciclo separado e fresco, seguindo a classificação atual do CI: ela sobe
apenas Postgres e recebe `MAEZO_TEST_DATABASE_URL` e `MAEZO_PG_HOST_PORT` explícitos. Seu código
injeta falhas na seam Python e não usa CIB Seven. Execute-a em outra invocação, depois que a
primeira tiver liberado a trava:

```bash
CHAOS_RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/chaos-$TARGET_SHA

env -u VIRTUAL_ENV uv run --directory "$CHECKOUT" --locked python \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" run \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --suite chaos \
  --test-file tests/integration/chaos/test_sink_down_failclosed.py \
  --results-dir "$CHAOS_RESULTS" \
  --lock-timeout 0
```

Os testes marcados `integration` fora de `tests/integration/` formam a terceira partição. Cada
arquivo também recebe sua própria stack fresca. No
checkout atual são testes sob `tests/unit/`, majoritariamente live-PG, mas a descoberta detecta
`tests/unit/gateway/test_audit_dmn_versions.py` como dependente do engine real. Por isso esta
suíte sobe Postgres+Kafka, espera os dois, sobe CIB, verifica/deploya o spec e só então executa os
nodeids. Isso impede que o caso DMN seja convertido em skip por uma classificação PG-only:

```bash
DB_UNIT_RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/db-unit-$TARGET_SHA

env -u VIRTUAL_ENV uv run --directory "$CHECKOUT" --locked python \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" run \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --suite db-unit \
  --test-file tests/unit/gateway/test_audit_dmn_versions.py \
  --results-dir "$DB_UNIT_RESULTS" \
  --lock-timeout 0
```

## Evidências e falhas

Cada subprocesso longo abre seu log antes de iniciar e escreve stdout/stderr diretamente nele;
assim timeout ou sinal preserva a saída anterior à falha. O runner cria um grupo de processo
separado para cada comando. Em timeout, `SIGINT` ou `SIGTERM`, envia TERM ao grupo, espera por até
10 segundos, escala para KILL se necessário e recolhe o filho antes de autorizar teardown ou
liberar a trava.

Cada execução grava `run-state.json` mesmo em erro ou interrupção. `suite-results.json` registra
o return code e contagens de `passed`, `failed`, `skipped`, `xfailed`, `xpassed` e `errors`;
`pytest.log` mantém razões de skip/xfail e `junit.xml` fornece os casos individuais. A contagem
`tests` do JUnit precisa ser idêntica a `expected_count` do manifest; seleção vazia ou parcial
transforma até um rc 0 do pytest em falha do runner. O runner não
imprime o ambiente nem copia `.env`, credenciais ou secrets.

No encerramento, inclusive após `SIGINT`/`SIGTERM`, o dono tenta apenas:

```text
docker compose --project-name maezo-completion-engine ... down -v --remove-orphans
```

Se o teardown falhar, o erro fica em `run-state.json` e `owner.json`, e a trava permanece para
impedir outra execução de assumir que os recursos estão limpos. Se a posse tiver mudado, o
processo não executa teardown nem remove a trava.
