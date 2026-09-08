# Integração local com engine real e checkout fixado

Este runbook opera o CIB Seven usado pelas suítes `integration` sem compartilhar estado com
outros checkouts ou stacks locais. O runner executa exatamente uma suíte por invocação em um
projeto Compose novo, derivado de uma cópia própria dos blobs do SHA alvo. O checkout original
não recebe reset, clean, remoção de caches ou sincronização de ambiente.

## Pré-requisitos e limites

- Docker/Colima precisa estar disponível no contexto local chamado `colima`. O runner ignora o
  contexto corrente, recusa endpoints que não sejam socket Unix em `~/.colima/` e nunca aceita
  `DOCKER_HOST`/`DOCKER_CONTEXT` do ambiente. Para um laptop de 8 GiB, reserve 6 GiB ao Colima e
  não rode a suíte unitária em paralelo.
- O checkout precisa estar em um SHA completo de 40 caracteres e sem alterações tracked. As
  entradas de execução em `src/`, `spec/`, toda a árvore `tests/`, toda a árvore `scripts/`,
  `config/`, arquivos Python/configuração de raiz, `docker-compose.yml`, `pyproject.toml` e
  `uv.lock` também não podem ser untracked ou ignored. `.env` é recusado sem ler seus valores.
  Caches reconhecidos e a `.venv` original são preservados e não entram na cópia.
- O runner remove apenas o projeto Compose `maezo-completion-engine`. Ele não usa `docker system
  prune`, não enumera nem remove outros worktrees e não atua sobre containers de outros projetos.
- Não apague uma trava antiga automaticamente. Se `engine.lock` existir após um processo morto,
  examine `engine.lock/owner.json`, o processo indicado e os recursos do projeto antes de uma
  decisão humana de recuperação.
- O Compose é sempre chamado com os dois arquivos rastreados, projeto/perfil explícitos e
  `--env-file /dev/null`. A aplicação Python também não recebe `.env`: a origem o recusa e a
  cópia de execução contém somente arquivos do SHA, sem symlinks ou submódulos externos.

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

Use um diretório de resultados fora do checkout e um intérprete CPython 3.12+ confiável.
`-I -S` impede que a inicialização do próprio runner execute `.pth`, sitecustomize ou user site
da venv de origem. Cada cópia cria sua própria venv a partir do lockfile antes da coleta.

```bash
CHECKOUT=/Users/familia/code/maezo-completion-wt/engine-runner
TARGET_SHA=$(git -C "$CHECKOUT" rev-parse HEAD)
RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/discovery-$TARGET_SHA

"$CHECKOUT/.venv/bin/python" -I -S \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" discover \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --results-dir "$RESULTS"
```

`discovery.json` parte da coleta canônica `pytest tests -m integration` e contém cada nodeid. Seu
`execution_manifest` divide a coleção por arquivo e informa grupo, contagem esperada e serviços
necessários, identidades JUnit, hashes de fontes e estados canônicos dos marcadores.
`execution-source.json` identifica o checkout original, a cópia, importação real e hashes de
todos os arquivos rastreados. As fontes são conferidas antes/depois da coleta e dos testes,
e novamente após teardown. Plugin e validador vêm do núcleo dessa mesma cópia: o pai compara
os bytes com o digest autenticado, compila exatamente o conteúdo conferido e passa o digest
ao subprocesso, que o verifica antes de executar o plugin. Não há fallback à origem do
launcher nem carregamento de pyc do núcleo. Cada entrada é uma execução separada em stack fresca. Ele
falha se a coleta for vazia, se algum arquivo `tests/integration/**/test_*.py` não gerar nodeid,
se houver teste nessa árvore sem `integration`, se a união das três suítes não for exata ou se
LGPD/escalation desaparecerem. Assim os testes live-PG que vivem sob `tests/unit/` não ficam fora
do censo. As decisões de subir CIB, Kafka e HAPI são refeitas sobre os arquivos coletados; no
estado atual um teste `db-unit` exige CIB real e HAPI não é necessário. Os logs de coleta
preservam stderr e stdout.

### Mapa de controles externos

Descoberta e execução usam a mesma fronteira hermética. Esta tabela é o inventário dos controles
que poderiam alterar seleção, import, dependências, daemon ou endpoints e a disposição do runner:

| Superfície | Controles externos | Disposição |
|---|---|---|
| pytest | `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, plugins instalados por entry point | ambiente removido; autoload desativado; somente `pytest_asyncio.plugin`, declarado no extra `dev`, é carregado; `pyproject.toml` rastreado é passado por `-c`; `--confcutdir` limita conftests à cópia |
| Python | `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, user site e `sitecustomize` do usuário | variáveis removidas por allowlist, venv nova na cópia e intérprete chamado com `-I`; código/venv de origem e conftests ancestrais não entram |
| uv | `VIRTUAL_ENV` e todos os `UV_*` (`UV_PROJECT`, `UV_PROJECT_ENVIRONMENT`, `UV_CONFIG_FILE`, índices e seleção de Python incluídos) | removidos; `--no-config --no-env-file --locked --extra dev --project <cópia>`; cutoff de resolução lido do lockfile |
| Git | `GIT_DIR`, `GIT_WORK_TREE`, configs e alternates injetados por ambiente | ambiente externo removido; configuração global/sistema, hooks, fsmonitor, external diff e textconv desativados nas provas |
| Docker | `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, TLS | removidos; CLI fixa `--context colima` e valida previamente que o endpoint é socket Unix local em `~/.colima/` |
| Compose | `.env`, `COMPOSE_FILE`, `COMPOSE_PROFILES`, `COMPOSE_PROJECT_NAME`, overrides implícitos | removidos; `--env-file /dev/null`, dois `-f`, perfil `core`, diretório e projeto são explícitos |
| pytest de mutação | `MAEZO_CHAOS_MUTATE` | removido. Companions de mutação ficam registrados como `inactive_companion`; a execução normal nunca os ativa nem os conta como prova positiva |
| banco | `DATABASE_URL`, `MAEZO_TEST_DATABASE_URL`, `MAEZO_TEST_A2A_EDGE_DATABASE_URL`, `MAEZO_TEST_AMH_INBOX_DATABASE_URL`, `MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL`, `MAEZO_TEST_CHECKPOINT_DATABASE_URL`, `MAEZO_PG_*` | todos fixados no Postgres isolado em `127.0.0.1:15433`; nenhum DSN do usuário é herdado |
| engine/eventos/FHIR | `ENGINE_REST_URL`, `CIBSEVEN_BASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `FHIR_BASE_URL`, `HAPI_FHIR_BASE_URL`, proxies HTTP | endpoints fixados em loopback nas portas 18080/19092/18081; proxies não são herdados; HTTP no pai usa ProxyHandler vazio, portas fixas e recusa redirects |
| credenciais externas | chaves LLM e demais variáveis não enumeradas | não entram no ambiente filho. Logs passam por redação de senha em URI e campos usuais de segredo antes de serem persistidos/propagados |

As variáveis do sistema preservadas são `PATH`, `HOME`, locale e `TMPDIR`. O sistema operacional,
CPython, Git, uv, cache de distribuições e binários Docker são a base de confiança da máquina;
o runner não é uma sandbox contra adulteração desses binários pelo próprio usuário.
Configurações SSL/proxy/pytest/uv do ambiente não são herdadas. O extra `dev` é sincronizado na
venv própria com o lockfile, e a importação de `maezo` deve resolver em `src/maezo` dessa cópia.

## Execução serial

O comando abaixo adquire `/Users/familia/code/maezo-operadora/engine.lock` por `mkdir` atômico.
O arquivo `owner.json` registra PID, token, checkout, SHA, suíte e checkpoint. Falha de aquisição
retorna 73 e não chama `docker compose down`. Somente o processo cujo PID/token ainda coincide
com o arquivo pode desmontar o projeto e liberar a trava.

```bash
RUN_RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/core-$TARGET_SHA

"$CHECKOUT/.venv/bin/python" -I -S \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" run \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --suite core \
  --test-file tests/integration/processes/test_sp_op_lgpd_dsr_001.py \
  --results-dir "$RUN_RESULTS" \
  --lock-timeout 0
```

A entrada `core` sobe Postgres e Kafka, espera prontidão real, sobe o CIB Seven e então envia todos
os BPMN/DMN pelo CLI `maezo.platform.deploy`, com `--spec-dir` apontando à mesma cópia do SHA. Antes de pytest, o
runner consulta cada definição latest no engine e compara o SHA-256 do XML devolvido com o
arquivo do checkout. `deployment-provenance.json` fixa imagem declarada, versão respondida,
deployment IDs, versões, hashes dos dois Compose e hashes de todos os artefatos.

A suíte `chaos` é um ciclo separado e fresco, seguindo a classificação atual do CI: ela sobe
apenas Postgres e recebe `MAEZO_TEST_DATABASE_URL` e `MAEZO_PG_HOST_PORT` explícitos. Seu código
injeta falhas na seam Python e não usa CIB Seven. Execute-a em outra invocação, depois que a
primeira tiver liberado a trava:

```bash
CHAOS_RESULTS=/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/engine-runs/chaos-$TARGET_SHA

"$CHECKOUT/.venv/bin/python" -I -S \
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

"$CHECKOUT/.venv/bin/python" -I -S \
  "$CHECKOUT/scripts/dev/run_engine_integration.py" run \
  --checkout "$CHECKOUT" \
  --sha "$TARGET_SHA" \
  --suite db-unit \
  --test-file tests/unit/gateway/test_audit_dmn_versions.py \
  --results-dir "$DB_UNIT_RESULTS" \
  --lock-timeout 0
```

## Evidências e falhas

Cada subprocesso longo cria seu log antes de iniciar. Saída bruta fica num descritor temporário
anônimo; o finally publica o conteúdo redigido, inclusive em timeout, sinal ou EPERM. O JUnit é
produzido em staging privado e publicado após redação dos atributos/textos XML, preservando sua
sintaxe. Um XML parcial inválido é retido da publicação e faz a validação falhar. O runner cria um grupo de processo
separado para cada comando. Em timeout, `SIGINT` ou `SIGTERM`, envia TERM ao grupo, espera por até
1 segundo, escala para KILL e exige que o grupo deixe de existir em até mais 3 segundos antes de
autorizar teardown ou liberar a trava. Essa verificação usa o PGID criado pelo próprio `Popen`,
nunca enumera ou sinaliza grupos alheios. Vale também quando o líder retorna 0 deixando um
descendente e durante `SIGINT`/`SIGTERM`. Se a quiescência não puder ser comprovada, o estado vira
`subprocess_cleanup_unconfirmed` e a trava permanece. `owner.json` registra quiescência falsa
antes do spawn, lista os PGIDs pendentes e só registra quiescência após ESRCH e reaping. Sinais
pendentes/repetidos ou o tipo de exceção não podem substituir essa prova durável.

O mapa de propagação é: runner → `Popen(start_new_session=True)` (PID do líder = PGID exclusivo) →
uv/Python/pytest ou Docker CLI → descendentes que herdam o mesmo PGID. O daemon Docker não é um
descendente do CLI; sua contenção é o projeto Compose fixo e o teardown autorizado pela trava.
Por isso a liberação depende de duas provas separadas: grupo de subprocesso vazio e, quando a
stack foi tocada, `docker compose down -v --remove-orphans` concluído no contexto local validado.

Cada execução grava `run-state.json` mesmo em erro ou interrupção. O núcleo compartilhado
`scripts/ci/pytest_execution_evidence.py` captura a coleção e as fases setup/call/teardown por
item; o profiler na fase call observa entrada no código do corpo, inclusive corrotinas.
O schema 2 compara fingerprints das identidades completas em staging privado antes da
publicação. Parâmetros são projetados em rótulos opacos e únicos; o prefixo do arquivo continua
usável para particionar o censo, mas o rótulo parametrizado não é seletor pytest. O runner
preserva `nodeid_sha256` e `junit_identity_sha256` fornecidos pelo núcleo, sem recalculá-los dos
rótulos. `public_junit_identity(classname, name)` do mesmo núcleo autenticado projeta os
atributos XML; seu fingerprint vira `maezo_identity_sha256`, correlacionado aos JSONs. O XML
projetado não é revalidado como se fosse o XML cru original. O leitor de apresentação sem
projetor autenticado omite classname/name e não autoriza resultados.
`pytest-execution.json`, `junit.xml` e `suite-results.json` permitem confrontar exatamente
identidades únicas, fontes/marcadores, fases, outcomes e totais XML. Header sozinho, substituição,
duplicação, XML ausente/parcial, XPASS (inclusive não estrito), skip de infraestrutura e xfail
em setup ou `run=False` produzem falha mesmo se o pytest retornar zero.

`xfailed_executed` exige marcador estrito e corpo observado; `inactive_companion` é uma omissão
explícita com obrigação `separate_opt_in_RED_required`. A exceção de companion vem do catálogo
canônico do núcleo: identidade, origem, token, declaração completa e helper autenticados precisam
corresponder. A presença de uma referência `broken_*`, token, razão ou nome parametrizado não
concede a exceção. Expansão/refatoração exige revisão explícita do catálogo no núcleo e prova
negativa, sem substituir o RED separado. O baseline examinado tem dois companions core e quatro
chaos; o runner recebe isso do núcleo no SHA, sem catálogo alternativo ou exceção local. Os leitores numéricos legados usados
nos testes antigos são auxiliares de apresentação e não participam da decisão de aceite.

Logs, stdout/stderr, razões e estados publicados redigem senhas em URIs/campos usuais de segredo,
incluindo chaves citadas em JSON/repr e valores com espaços. Objetos aninhados são projetados com
o contexto das chaves antes da serialização; hashes de correlação já fornecidos são preservados.
O token operacional de `owner.json` é mantido somente no caminho privado de posse, sob diretório
0700, para que acquire/update/release continuem verificando PID/token. Um `token` em diagnóstico
público permanece redigido. Variáveis de credencial externas não entram no filho; o runner não
imprime o ambiente nem copia `.env`.

As fases de mutação continuam sendo provas RED explícitas fora deste runner e nunca resultados
aprovados. Use o comando declarado no docstring do módulo, com `MAEZO_CHAOS_MUTATE=<id>`, e espere
falha. Não passe essa variável ao runner: a fronteira hermética a remove deliberadamente.

No encerramento, inclusive após `SIGINT`/`SIGTERM`, o dono tenta apenas:

```text
docker compose --project-name maezo-completion-engine ... down -v --remove-orphans
```

Antes do primeiro efeito de teardown, `run-state.json` registra `state=teardown` e rc não zero;
o resultado pytest fica separado em `pytest_return_code`. `passed` somente é persistido após
teardown confirmado, fonte revalidada e liberação da trava. Erro, timeout ou SIGTERM durante
teardown deixa estado/rc duráveis não verdes e a trava retida; quiescência incerta tem precedência
como `subprocess_cleanup_unconfirmed`. O erro fica em `run-state.json` e `owner.json`, impedindo
outra execução de assumir que os recursos estão limpos. Se a posse tiver mudado, o processo
não executa teardown nem remove a trava. Esse protocolo não afirma sucesso de engine quando a
execução não ocorreu: descoberta, xfails apenas coletados e companions inativos são evidências
com limites distintos.
