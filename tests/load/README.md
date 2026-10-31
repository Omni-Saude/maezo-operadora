# Carga, p95 e failover do AUTH (R-086 / F-120)

Entrega do item M13 de PLANS.md ("Load tests (locust)", "Chaos tests", "Latência p95 < 200ms para
AUTH") para o caminho de autorização prévia `SP-OP-AUTH-001`. Tudo roda **só contra a stack local**
(`assert_local_url` recusa qualquer host que não seja `localhost`/`127.0.0.1`/`::1`) e com **dados
sintéticos** (tenant descartável `r086perf_<hex>`, guia `SINTR086<hex>`; nenhum CPF, nome ou dado
clínico).

| Peça | Arquivo | Onde roda |
|---|---|---|
| Cenários AUTH (fonte única, stdlib) | `tests/load/auth_scenarios.py` | importado pelos demais |
| Teste de carga Locust | `tests/load/locustfile_auth.py` | manual (comando abaixo) |
| Teste de p95 do AUTH | `tests/integration/perf/test_auth_p95.py` | lane `integration` (shard `restante`) + `-m performance` |
| Failover de worker (SIGKILL no meio do fluxo) | `tests/integration/resilience/test_auth_worker_failover.py` | lane `integration` (shard `restante`) |
| Failover de engine (restart no meio do fluxo) | `tests/load/chaos_engine_restart.py` | manual, stack descartável própria |
| Guardas sem engine | `tests/unit/load/test_auth_load_guards.py` | `make test` |

## Escopo de "AUTH" (declarado — ver NEEDS-HUMAN)

Os trechos **automáticos e síncronos** do processo, que rodam no engine:

- `start` de `SP-OP-AUTH-001` até o primeiro estado de espera (`ST_PublishReceived`), com a business
  key contratual `AUTH-{tenant}-{guia}`;
- as quatro DMNs do AUTH (`auth_admissibility`, `auth_sla`, `auth_auto_approval`,
  `auth_criteria_contratual`), avaliadas pelo mesmo cliente dos workers
  (`CibSevenDmnTransport.evaluate`, ADR-0028).

Ficam **fora por construção** as User Tasks humanas (médico auditor, coordenação, junta): negativa e
decisão clínica são `hard` (L0) e não têm meta de latência. Nada aqui automatiza decisão.

## Meta de p95

`p95 < 200 ms` — **PLANS.md:923** (Verificação do M13). É a única fonte encontrada na spec:
`docs/observability/SLO.md` não tem SLI de AUTH (o único p95 de lá é o DRAFT de 5 s do worker,
SLO.md:168) e não existe baseline de volume por tenant (SLO.md:231-232, G3). Por isso concorrência e
tamanho de amostra são **parâmetros de medição**, não metas: os tiers C=1 e C=10 repetem os que a
auditoria já mediu (`docs/audits/FORENSIC-COMPLETENESS-2026-10/PERFORMANCE.md`), e nenhum resultado
aqui é afirmação de capacidade.

**Onde a meta é asserida:** em C=1 (latência de uma requisição, sem fila) — a leitura que
PLANS.md:923 sustenta sem acrescentar requisito. Em C=10 o teste **só mede e reporta** (falha por
erro/resposta errada, nunca por latência): a spec não diz sob qual concorrência a meta vale, e
asserir ali inventaria um requisito de carga (NEEDS-HUMAN 1). O locustfile aplica a meta na carga
que o operador escolher (`-u`/`-r`); com `-u 10 -r 5` e o tempo de espera do `AuthUser` ela passou.

Medido em 31/10/2026, engine compartilhado local, máquina com **load average 17–31 em 10 núcleos**
(dezenas de agentes paralelos — números valem só como ordem de grandeza):

| cenário | p95 C=1 (rodada 1 / 2) | p95 C=10 (rodada 1 / 2) | Locust `-u 10` p95 |
|---|---|---|---|
| `dmn:auth_admissibility` | 39,8 / 72,1 ms | 254,0 / 311,5 ms | 33 ms |
| `dmn:auth_sla` | 37,6 / 66,2 ms | 148,1 / 205,5 ms | 29 ms |
| `dmn:auth_auto_approval` | 40,6 / 69,7 ms | 162,9 / 222,3 ms | 29 ms |
| `dmn:auth_criteria_contratual` | 47,8 / 73,6 ms | 237,2 / 267,7 ms | 31 ms |
| `start` | 40,9 / 34,4 ms | 320,6 / 233,6 ms | 41 ms |

A SLI de produção dos workers AUTH continua sendo `maezo_worker_task_duration_seconds`
(SLO.md:99); este teste mede o trecho engine-side que ela não cobre.

## Como rodar

Pré-requisito: stack local de pé (`docker compose --profile core up -d`; engine em
`http://localhost:8080/engine-rest`). Num engine compartilhado entre sessões, segure o mutex do
engine durante as execuções:

```bash
until mkdir /tmp/maezo-engine.lock 2>/dev/null; do sleep 30; done
echo r086 > /tmp/maezo-engine.lock/owner
trap 'rm -rf /tmp/maezo-engine.lock' EXIT
```

**p95 + failover de worker** (os testes implantam os próprios artefatos sob um tenant descartável e
apagam a implantação em cascata no fim — não precisam de `make deploy-artifacts`):

```bash
.venv/bin/python -m pytest tests/integration/perf tests/integration/resilience -m integration -q -rP
```

Cada caso de p95 imprime uma linha `R086_P95 {json}` (p50/p95/p99/máx/RPS) e grava a mesma coisa como
`record_property` no JUnit.

**Carga Locust** — Locust **não** é dependência do projeto (não entra em `uv.lock` nem nas lanes que
fazem `uv sync --all-extras`). `uv run --with` resolve o pin num ambiente efêmero **respeitando o
`[tool.uv] exclude-newer` do pyproject**; `2.46.3` é anterior ao corte (verificado: pedir uma versão
mais nova falha com "filtered by exclude-newer"):

```bash
uv run --extra dev --with 'locust==2.46.3' locust -f tests/load/locustfile_auth.py \
    --host http://localhost:8080/engine-rest --headless -u 10 -r 5 -t 60s \
    --csv /tmp/r086-locust
```

O locustfile recusa host não-local antes da primeira requisição, implanta os artefatos AUTH sob um
tenant descartável no `test_start`, apaga a implantação (cascata) no `test_stop` e sai com código 1
se o p95 de qualquer requisição `auth_*` ficar ≥ 200 ms ou houver falha. Não existe alvo no
`Makefile` (arquivo CODEOWNERS); este é o comando canônico. O código de saída do Locust é um
portão do OPERADOR para a carga escolhida, não a asserção do M13 — esta vive em
`tests/integration/perf/test_auth_p95.py` (C=1).

**Failover de engine** (destrutivo, por isso **isolado**): sobe rede + postgres + cibseven próprios
(prefixo `r086chaos<hex>`, porta efêmera fixa em 127.0.0.1), reinicia só o próprio engine e remove
só os próprios containers (e seus volumes anônimos, `docker rm -f -v`) e a rede no fim. Nunca toca a stack do `docker compose` nem faz `down -v`. Não
precisa do mutex:

```bash
uv run --extra dev python -m tests.load.chaos_engine_restart
```

Saída: `R086_CHAOS {json}` com `boot_s`, `engine_indisponivel_s` e o veredito; código 0/1.

## Runbook de failover — o que cada drill prova

| Drill | Injeção | Invariante (fica vermelho se quebrar) |
|---|---|---|
| Worker (B1c, forma engine-level) | `SIGKILL` do subprocesso que trava `ST_PublishReceived` | a mesma task volta só após a expiração do lock; conclusão do zumbi recusada; fluxo retoma e termina em `End_NaoRequerAutorizacao` com a atividade executada 1 vez e 1 log de sucesso (do worker B) |
| Engine (B3) | `docker restart` do engine da stack descartável com a task travada | instância, task e **lock** sobrevivem ao restart (B não consegue a task); A completa depois do restart; fluxo termina com a atividade executada 1 vez |

Limites honestos: o drill de worker prova a semântica de re-entrega do engine + a transport real
dos workers, **não** a cadeia de auditoria (`verify_chain`) de um handler `WorkerBase` real — esse é
o B1c completo do design T3.3, que pertence ao job noturno W2 (container-control, ainda não
existe; `.github/workflows` é CODEOWNERS). Partição de rede (C1-part) e failover de banco
(C2-failover) continuam bloqueados por infraestrutura (toxiproxy / PG HA), como o design registra.

## NEEDS-HUMAN

1. **Escopo da meta de 200 ms.** PLANS.md:923 diz "Latência p95 < 200ms para AUTH" sem definir a
   superfície (start do processo? DMN? resposta ao prestador no portal?), a concorrência nem a
   janela. Este pacote adota o trecho engine-side síncrono descrito acima e assere só em C=1. A
   meta (superfície + concorrência + janela) precisa ser ratificada pelo dono do serviço e
   registrada como SLI em `docs/observability/SLO.md`; com ela, o caso C=10 passa a asserir.
2. **Job noturno W2** para rodar `chaos_engine_restart` (e o B1c completo) em CI: exige editar
   `.github/workflows/ci.yml` (CODEOWNERS security-team).
