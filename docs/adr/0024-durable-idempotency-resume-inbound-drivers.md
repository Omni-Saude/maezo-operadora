# ADR-0024: Idempotencia DURAVEL dos drivers inbound/resume reusa o PADRAO Postgres (schema-por-tenant), nao Redis

**Status:** Accepted (2026-07-05) · **Data:** 2026-07-04 · **Area:** Orquestracao / Runtime

## Contexto

Os dois drivers Kafka->grafo do agent-runtime deduplicam fatos at-least-once ANTES de dirigir o grafo (dupla resposta ao beneficiario e' um LANDMINE explicito no codigo):

- `InboundDriver` (whatsapp.message-received -> grafo) dedup por `wamid`.
- `ResumeDriver` (agents.events.process_completed -> re-entra o thread) dedup por `resume:{business_key}:{resultado}`.

Ambos consomem o MESMO seam estrutural `IdempotencyGuard` (`runtime/inbound_driver.py:114`) e ambos estao fiados HOJE com `InMemoryIdempotencyStore()` — `service.py:807` (inbound) e `service.py:855` (resume). Os comentarios de seam (`service.py:806`, `:854`; docstrings `:780-784`, `:839-840`) dizem literalmente "em PRODUCAO troque o InMemoryIdempotencyStore por um store COMPARTILHADO (Redis/Postgres)".

**O gap (DL-0014).** Dedup in-memory e' POR-PROCESSO: em restart do pod OU entre replicas, a re-entrega do MESMO fato re-dirige o grafo — dupla resposta ao beneficiario (inbound) / conversa retomada duas vezes (resume). Exactly-once TEM de sobreviver a restart. DL-0025 diferiu `res-resume-idempotency-durable` para ESTA ADR justamente porque a escolha e' de INFRA (redis-vs-postgres), nao um micro-brief.

**Fatos verificados no `src/` (julho/2026):**

1. **Postgres JA e' dependencia DURA nos DOIS sitios de spawn.** `_spawn_inbound_driver` (`service.py:790`) e `_spawn_resume_driver` (`:846`) sao GATEADOS em `state.checkpointer is not None` — e o checkpointer e' um `AsyncPostgresSaver` (`:131`, aberto em `:406-418`). Sem Postgres, os drivers nem sobem. O DSN esta em maos: `settings.database_url` (`settings.py:39`, `SecretStr`), usado incondicionalmente pelo dispatcher A2A no MESMO service em `:715` (`settings.database_url.get_secret_value()`).

2. **Ja existe um store duravel de idempotencia em Postgres, prod-fiado.** `PostgresIdempotencyStore` (`a2a/idempotency.py:146`, asyncpg) e' construido em producao pelo dispatcher A2A (`service.py:714-718`) e pelo worker_runtime (`a2a_assembly.py:257`). Ele estabelece o PADRAO da casa: `schema_for_tenant` + `normalize_conn_string`, pool asyncpg lazy, e o fix #55 `setup=_set_search_path` (`:184-191`) — o pool asyncpg faz RESET ALL no release, entao o search_path por-tenant tem de ser re-fixado a CADA acquire (senao o 2o+ acesso numa conexao reusada cai no schema `public` e falha `relation does not exist` — bug REAL de producao).  A tabela `a2a_idempotency` vem da migracao `0004_a2a_idempotency.py` (schema-por-tenant, DDL nao-qualificada).

3. **Redis existe mas esta fiado em LUGAR NENHUM.** `RedisIdempotencyStore` (`platform/webhooks/whatsapp/idempotency.py:55`) e' uma classe que LEVANTA `ImportError` sem `redis>=5.0` (`:71-74`) e nao e' instanciada em nenhum ponto do runtime. Adota-la e' introduzir uma dependencia de infra NOVA.

**Achado PIVOTAL do code-reading (contradiz a leitura ingenua do seam).** `PostgresIdempotencyStore` NAO e' protocolo-compativel com `IdempotencyGuard`. O "swap de uma linha" que os comentarios de seam sugerem NAO type-checa e falharia em runtime (`AttributeError: 'PostgresIdempotencyStore' object has no attribute 'is_processed'`). Lado a lado:

```
# O que os drivers CONSOMEM — IdempotencyGuard (inbound_driver.py:114-123):
class IdempotencyGuard(Protocol):
    async def is_processed(self, key: str) -> bool: ...
    async def mark_processed(self, key: str, ttl_seconds: int = ...) -> None: ...

# O que PostgresIdempotencyStore OFERECE — IdempotencyStore (a2a/idempotency.py:111-120):
class IdempotencyStore(Protocol):
    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None: ...
    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None: ...
```

Divergencia total: nomes diferentes (`claim_or_get`/`complete` vs `is_processed`/`mark_processed`); args keyword-only `tenant`+`task_id` vs `key` posicional; `complete` EXIGE um objeto `DelegationResult` (rejection_reason/output_ref/detail) que os drivers NAO possuem (so tem uma string `key`); retorno `StoredResult | None` vs `bool`; sem nocao de TTL. "Reuso", portanto, e' reuso do PADRAO (asyncpg + schema-por-tenant + `setup=search_path`), NAO da classe.

## Decisao

1. **A idempotencia duravel dos drivers inbound/resume e' backed por Postgres — a MESMA base do tenant — e NAO por Redis.** Exactly-once tem de sobreviver a restart e a multi-replica; o Postgres ja esta no caminho da requisicao (checkpointer), enquanto Redis seria uma dependencia de infra NOVA para durabilidade ESTRITAMENTE MENOR (eviction/maxmemory pode descartar chaves).

2. **Introduzimos um store novo e pequeno `PostgresDedupeStore` que implementa `IdempotencyGuard` DIRETAMENTE** (`is_processed`/`mark_processed`), reusando VERBATIM o padrao asyncpg de `PostgresIdempotencyStore`: `schema_for_tenant(tenant)` fixa o schema na construcao, `normalize_conn_string(dsn)`, pool lazy, e `setup=_set_search_path` (o fix #55 — obrigatorio, senao o 2o dedup na conexao reusada falha). NAO um adaptador sobre a classe A2A (protocolos incompativeis) — um store irmao no mesmo padrao.

3. **Backing store = tabela NOVA `driver_idempotency`, NAO a `a2a_idempotency`.** Nova migracao `0008_driver_idempotency` (down_revision `0007_audit_chain_partitioning` — a ultima), no schema do tenant, DDL nao-qualificada (espelha 0004):

   ```sql
   CREATE TABLE driver_idempotency (
       key        text PRIMARY KEY,
       tenant     text NOT NULL,
       expires_at timestamptz NOT NULL,
       created_at timestamptz NOT NULL DEFAULT now()
   );
   CREATE INDEX ix_driver_idempotency_expires ON driver_idempotency (expires_at);
   ```

   Reusar `a2a_idempotency` foi REJEITADO: seus campos `task_type`/`origin`/`target` sao `NOT NULL` com semantica de delegacao (Guard 4), obrigariam placeholders, compartilhariam o namespace de PK, e tornariam o docstring/o `ix_a2a_idempotency_tenant` mentirosos — re-emaranhando a preocupacao que DL-0014 deliberadamente ISOLOU, sem historia de retencao.

4. **Honramos o `ttl_seconds` do protocolo.** `mark_processed(key, ttl)` faz `INSERT ... (key, tenant, expires_at) VALUES ($1, $2, now() + $3) ON CONFLICT (key) DO UPDATE SET expires_at = EXCLUDED.expires_at`; `is_processed(key)` e' `SELECT 1 FROM driver_idempotency WHERE key = $1 AND expires_at > now()`. TTL default 86400s (24h, igual ao whatsapp idempotency). Retencao = sweep periodico `DELETE FROM driver_idempotency WHERE expires_at < now()` (DELETE-por-idade, espelha a retencao de auditoria) — mantem a tabela bounded.

5. **Fiacao nos dois sitios** (`service.py:807` inbound, `:855` resume), construido de `settings.database_url` + `settings.tenant_id`, com o pool fechado via `AsyncExitStack` — espelho EXATO do dispatcher A2A (`:714-718`).

### Alternativas consideradas

- **Redis (`RedisIdempotencyStore`, existe unwired).** TTL-nativo (`SET ... EX`), mas adiciona uma dependencia de infra NOVA (cluster Redis por-env, secret novo, dominio de falha novo, `redis>=5`) para durabilidade MENOR que a base ja no caminho — eviction pode dropar chaves antes de garantir restart-safety. **Rejeitada.**
- **Adaptador sobre `a2a_idempotency` (sem migracao nova).** Evita a migracao, mas sobrecarrega uma tabela de semantica de delegacao (placeholders em colunas NOT NULL, PK compartilhada, sem retencao) e re-emaranha o que DL-0014 isolou. **Rejeitada como layer-overload** — oferecida apenas como fallback SE o dono vetar uma migracao nova.
- **Manter `InMemoryIdempotencyStore` (status quo).** Por-processo apenas; DL-0014 = fork/duplicata em restart/multi-replica (dupla resposta ao beneficiario, conversa retomada 2x). **Rejeitada — e' exatamente o bug que esta ADR fecha.**

### Fiacao + recomendacao GO/DEFER

**Recomendacao: GO — LAND ESTA SESSAO, como UMA PR vertical-slice dedicada.** Nao ha bloqueador: DSN + tenant ja em maos, Postgres ja e' dep dura nos sitios, e o template de teste ja existe. **Caveat obrigatorio:** NAO e' o one-liner que os comentarios de seam (`:806`/`:854`) sugerem — e' um slice de ~4 arquivos (store + migracao + teste de integracao + fiacao), porque a classe A2A e' protocolo-incompativel e a migracao toca o schema deployado do tenant.

Mudancas exatas:

1. **Novo `src/maezo/runtime/idempotency_pg.py`** — `class PostgresDedupeStore` implementando `is_processed`/`mark_processed`, copiando o esqueleto asyncpg de `a2a/idempotency.py:146-238` (`_ensure_pool`, `setup=_set_search_path`, `aclose`). Pool LAZY: construir NAO toca a rede (igual ao comentario A2A `:711-713`); a 1a falha so aparece no 1o `is_processed` — e o loop nao-fatal do driver ja manda o fato para a DLQ nesse caso.

2. **Nova migracao `0008_driver_idempotency.py`** (DDL acima; `downgrade` = `raise NotImplementedError` forward-only, igual 0004:59-61).

3. **`service.py` — assinaturas + call sites.** `_spawn_inbound_driver` e `_spawn_resume_driver` hoje recebem so `state` (`:770`, `:819`); precisam receber `stack: AsyncExitStack` (espelho de `_wire_a2a_dispatcher(state, stack)`, `:632`). Atualizar os call sites `:637` -> `_spawn_inbound_driver(state, stack)` e `:642` -> `_spawn_resume_driver(state, stack)`.

4. **Dentro de cada spawn**, trocar a construcao do guard (mirror de `:714-718`):

   ```python
   # ANTES (service.py:807 e :855):
   idempotency=InMemoryIdempotencyStore(),

   # DEPOIS:
   idempotency = PostgresDedupeStore(
       dsn=settings.database_url.get_secret_value(),
       tenant=settings.tenant_id,
   )
   stack.push_async_callback(idempotency.aclose)
   # ... e passar `idempotency=idempotency,` ao construtor do driver.
   ```

**O que o verifier DEVE checar:**
- Novo teste de integracao contra Postgres REAL (docker compose; espelha `tests/integration/platform/test_a2a_idempotency_durable.py`, que aplica `alembic upgrade head` via a fixture `bootstrapped_tenant`): (a) `mark_processed` persiste a linha; (b) `is_processed` retorna True antes do TTL e False depois; (c) **DURABILIDADE cross-restart** — um store NOVO (pool novo) contra o MESMO banco ainda ve a chave (a prova de que fecha DL-0014); (d) **regressao do fix #55** — 2o `is_processed`/`mark_processed` numa conexao REUSADA do pool NAO cai no schema `public` (com `init=` em vez de `setup=`, falharia `relation "driver_idempotency" does not exist`).
- `PostgresDedupeStore` satisfaz `IdempotencyGuard` estruturalmente (mypy/type-check do wiring em `service.py` verde nos dois sitios).
- A migracao 0008 encadeia de 0007 (`alembic upgrade head` + a suite de integracao existente verdes — sem drift de revisao).
- Slice-gate da casa: a migracao vem com o teste de integracao real-Postgres verde (nao so unit).

## Consequencias

**Positivas:**
- **Exactly-once sobrevive a restart e a multi-replica** nos dois legs (inbound + resume) — fecha DL-0014 (fork/duplicata) para o caminho dos drivers, nao so para A2A.
- **Zero infra nova.** A base do tenant ja e' dependencia dura (checkpointer); nenhum cluster Redis, secret ou dominio de falha novo.
- **Um so PADRAO duravel** de idempotencia atravessa A2A + drivers (mesmo asyncpg + schema-por-tenant + fix #55) — menos superficie conceitual, um so modo de falha a entender.
- **Retencao limpa e explicita** (tabela dedicada + `expires_at` + sweep por-idade) — a tabela nao cresce sem limite.

**Negativas (aceitas):**
- **~1 round-trip a Postgres por mensagem** (um SELECT + um UPSERT por wamid/resume-key, ambos por PK indexada). Bounded e dominado pelo custo do `graph.ainvoke` — aceitavel. Uma indisponibilidade transitoria do DB manda o fato para a DLQ (reprocessavel), nao derruba a liveness (o loop ja e' nao-fatal).
- **Uma migracao nova no schema deployado do tenant** (0008) — precisa rodar em todo tenant. Forward-only (ADR-0011).
- **Exige um sweep de retencao** (DELETE-por-idade) operacionalizado; sem ele, `expires_at` ainda torna leituras corretas mas a tabela acumula linhas expiradas.
- **A migracao/store sao trabalho real**, nao o swap de construtor que os comentarios de seam (`:806`/`:854`) implicam — esses comentarios devem ser atualizados quando a PR landar (ou removidos, apontando para esta ADR).

## Supersedes

— (Concretiza o seam duravel que ADR-0003/DL-0014 anteciparam para os drivers; reusa o padrao Postgres de `a2a/idempotency.py` sem reusar a classe A2A — protocolos distintos. Resolve o deferral DL-0025 `res-resume-idempotency-durable`. ADR-0023 esta alocada para a politica de merge/union-green do programa predeploy.)

---

## Emenda 2026-09-03 — o `PostgresDedupeStore` nunca existiu e o SUJEITO da ADR foi removido (GAP AF-02)

**Status:** Proposto (amendment) — DRAFT/verify · **Data:** 2026-09-03 · **Autor:** `adr-reconciler` (R1, AGENTE)
**Marcadores:** `amended-by`: esta Emenda 2026-09-03 (WP-ADR-RECONCILIACAO, GAP AF-02) · `obsolete-section`: Contexto §"Fatos verificados" 1-3 + Decisao 2, 3 e 5 + "Fiacao + recomendacao GO/DEFER" 1-4
**Base de verificacao:** worktree em `71dd4da` (branch `fix/adr-reconciliacao-emendas`).

> APPEND-ONLY. Nenhuma linha do texto original acima foi alterada ou removida — a decisao de 2026-07-04
> continua sendo o registro do que foi decidido naquela data. Um AGENTE nao ratifica nada: enquanto este
> bloco carregar `DRAFT/verify`, ele e um fato reconciliado com o codigo, nao uma decisao ratificada.
> Assinatura humana pendente (`.github/CODEOWNERS:61` — `/docs/adr/` e dono-gated).

### 1. O que a ADR afirma

1. `:44` (Decisao 2) — "Introduzimos um store novo e pequeno `PostgresDedupeStore` que implementa
   `IdempotencyGuard` DIRETAMENTE".
2. `:46` (Decisao 3) — "Nova migracao `0008_driver_idempotency` (down_revision `0007_audit_chain_partitioning`
   — a ultima)".
3. `:62` (Decisao 5) — "Fiacao nos dois sitios (`service.py:807` inbound, `:855` resume)".
4. `:7-12` (Contexto) — o sujeito da ADR sao os dois drivers Kafka->grafo `InboundDriver`/`ResumeDriver`,
   consumindo o seam `IdempotencyGuard` (`runtime/inbound_driver.py:114`) com `InMemoryIdempotencyStore()`.
5. `:22` (Contexto, fato 3) — "`RedisIdempotencyStore` (`platform/webhooks/whatsapp/idempotency.py:55`)".

### 2. O que e verdade hoje

**(a) A classe nunca existiu, em nenhum ponto da historia deste repo.**

- `grep -rn PostgresDedupeStore src/` -> 0 linhas; `grep -rn --include='*.py' PostgresDedupeStore .` -> 0.
- `git log --all -S PostgresDedupeStore --oneline -- src/` -> 0 commits.

**(b) A tabela existe, mas na migracao 0003 — nao na 0008 — e o `down_revision` citado nomeia uma revisao
inexistente.**

- `driver_idempotency` e criada em `src/maezo/platform/migrations/versions/0003_a2a_idempotency.py:58-72`,
  e o comentario `:58` credita explicitamente esta ADR ("driver_idempotency — InboundDriver/ResumeDriver
  dedup (ADR-0024)").
- A migracao `0008` real e `0008_a2a_fact_outbox.py` (`revision: "0008"`, `:120-121`) — outro assunto.
- Os identificadores de revisao deste repo sao numericos nus (`grep -n '^revision' .../versions/*.py` ->
  `"0001"`..`"0008"`). Nao existe, nem nunca existiu, `0007_audit_chain_partitioning`: a `0007` real e
  `0007_amh_inbox.py:113-114`.

**(c) O SUJEITO da ADR foi REMOVIDO do codigo — os dois drivers nao existem mais.**

- `src/maezo/runtime/inbound_driver.py` NAO EXISTE (`ls src/maezo/runtime/` -> `__init__.py`,
  `agent_runtime/`, `checkpoint.py`, `harness.py`, `inference.py`, `metrics.py`, `prompt_format.py`,
  `worker_runtime/`).
- `grep -rn 'class InboundDriver\|class ResumeDriver\|IdempotencyGuard\|InMemoryIdempotencyStore' src/
  --include='*.py'` -> 0 linhas.
- `platform/webhooks/whatsapp/idempotency.py` (o `RedisIdempotencyStore` do Contexto fato 3) tambem nao
  existe mais.
- O caminho inbound hoje e dispatch IN-PROCESS do webhook:
  `src/maezo/platform/webhooks/whatsapp/dispatch.py:161` (`async def dispatch`).

**(d) `driver_idempotency` e hoje uma tabela ORFA.** Fora das proprias migracoes, a unica referencia em
`src/` e o inventario de retencao LGPD `src/maezo/platform/lifecycle/erasure_plan.py:415-420` — que ja a
classifica `SEM_COLUNA_DE_TITULAR` (`:420`). Nenhum dedup a le ou escreve.

**(e) Residuo documental: dois documentos afirmam uma entrega que nao existe.**

- `docs/archive/predeploy-dl-rows-STAGED.md:6` (DL-0028): "Slice landou como PR #181 (T3-verificado ...)".
- `docs/reports/predeploy-audit-report.md:317`: "ADR-0024 ... -> PR #181, T3-PASS. New `PostgresDedupeStore`
  ... + migration `0008_driver_idempotency` + wiring at both ... call sites."
- O PR #181 real deste repo e `03c6437` — "Item 9 wave-2: pagto bucket-1 (12/12) + ADR-0030 guard migration
  (nip/programa) (#181)" — conteudo nao relacionado. Ambos os documentos recebem, nesta mesma leva, uma
  NOTA DE CORRECAO in-loco (append-only, sem apagar o texto original).

### 3. Consequencia

1. **Decisao 2, 3 e 5 sao `obsolete-section`.** Nao ha o que implementar como escrito: a interface alvo
   (`IdempotencyGuard`), os dois call sites (`service.py:807`/`:855`) e a numeracao de migracao prescrita
   nao existem mais. Reabrir esta ADR "como esta" produziria codigo sem consumidor.
2. **O PROBLEMA de DL-0014 nao esta provado fechado — mudou de forma.** O risco original (re-entrega
   at-least-once do Kafka re-dirigindo o grafo) desapareceu com os drivers; o risco equivalente no
   desenho atual e a re-entrega HTTP do webhook da Meta. Nesta reconciliacao NAO foi encontrado nenhum
   dedup por `message_id` no caminho inbound:
   `grep -rniE 'idempot|dedup|wamid' src/maezo/platform/webhooks/ --include='*.py'` -> 2 linhas, ambas
   nao relacionadas (`service.py:100`, `:297`); o `message_id` e capturado
   (`dispatch.py:65`, `:94`, `:231`) mas nao e usado como chave de dedup. A idempotencia que existe hoje
   e a de INICIO DE PROCESSO (`start_process_idempotent`, business key) e a continuidade de thread do
   checkpointer — nenhuma das duas e um dedup por mensagem. **Isto e um item de DECISAO DO DONO
   (`OWNER-DECISIONS.md`), nao uma correcao de documentacao**, e esta fora do escopo deste WP.
3. **A tabela orfa `driver_idempotency` fica registrada como tal.** Remover, reaproveitar ou manter e
   decisao do dono; ate la, `erasure_plan.py:415-420` continua sendo a unica coisa que a conhece.
4. Nenhum comportamento de runtime muda com esta emenda: ela e documental.
