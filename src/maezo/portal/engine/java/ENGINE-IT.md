# Rodando os 15 `*EngineIT` do plugin humano

`mvn -B -f src/maezo/portal/engine/java/pom.xml verify` **nunca** roda estas classes: o surefire
exclui `**/*EngineIT.java` (`pom.xml:19`) e não há plugin failsafe. O job `java-plugin` do
`ci.yml` cobre compilação + testes finitos; os `EngineIT` são executados explicitamente pelo
runner do motor, com `-Dtest=<Classe>`, contra um CIB Seven 2.1 **embarcado** sobre JDBC (não a
REST engine :18080). Cada classe cria e derruba o próprio schema aleatório.

Este documento é a receita de fixture que o runner precisa. Ele não afrouxa nenhuma cerca: as
classes continuam falhando de forma explícita quando um pré-requisito falta
(`AtomicEngineIT.java:29-33` `explicit integration setting missing: <NOME>`).

## 1. As 15 classes e o que cada grupo exige

| Grupo | Classes | Pré-requisito além do Postgres comum |
|---|---|---|
| A (5) | `AtomicEngineIT`, `EnlistmentEngineIT`, `FreshnessEngineIT`, `ReviewerEngineIT`, `WorkloadEngineIT` | nenhum — só §2 |
| B (3) | `PortalReadEngineIT`, `PortalReadPublisherEngineIT`, `StaffCaseReadEngineIT` | §2 + `maezo.repo.root` / `MAEZO_PORTAL_READ_IT_REPO` |
| C (3) | `ClassifiedDecisionEngineIT`, `ConsumerLineageEngineIT`, `ConsumerContinuationEngineIT` | §3 **Postgres com TLS ligado** + papel de runtime dedicado + perfil `phi-consumer-engine-it` |
| D (1) | `ClassifiedConsumerFenceEngineIT` | §3 + perfil `phi-consumer-engine-it` |
| E (3) | `NativeV2AcquisitionEngineIT`, `NativeV2AdmissionEngineIT`, `NativeV2ReceiptEngineIT` | §4 `MAEZO_NATIVE_V2_IT_FIXTURE` (diretório privado do dono) |

O V3-Q3 deixou as 6 classes originais de C+D+E UNVERIFIED; agora esse conjunto também inclui
`ConsumerContinuationEngineIT`, com 13 casos de regressão. As 14 classes originais continuam
obrigatórias em V11, junto com essa nova classe. A + B foram executadas (134 testes
Java passaram em A; B falhava pelo cap de leitura, reparado neste PR — ver §6).

## 2. Postgres comum (grupos A e B)

```sh
export MAEZO_HUMAN_IT_JDBC_URL='jdbc:postgresql://localhost:5433/maezo'   # sem currentSchema/options
export MAEZO_HUMAN_IT_DB_USER='maezo'
export MAEZO_HUMAN_IT_DB_PASSWORD="${MAEZO_PG_PASSWORD:?defina MAEZO_PG_PASSWORD antes de rodar a IT}"
export MAEZO_PORTAL_READ_IT_REPO="$PWD"
mvn -B -o -f src/maezo/portal/engine/java/pom.xml test \
    -Dmaezo.repo.root="$PWD" -DfailIfNoSpecifiedTests=false \
    -Dtest='AtomicEngineIT,EnlistmentEngineIT,FreshnessEngineIT,ReviewerEngineIT,WorkloadEngineIT,PortalReadEngineIT,PortalReadPublisherEngineIT,StaffCaseReadEngineIT'
```

**Os 3 do grupo B estão nesse mesmo comando de propósito**: são eles que exercitam o reparo do cap
de leitura (§6) — a coisa mais importante a confirmar contra motor real. Rodar só o grupo A deixa o
reparo sem prova.

A URL admin é recusada se trouxer `currentSchema=` ou `options=` (`AtomicEngineIT.java:41-42`):
cada classe acrescenta o próprio `currentSchema`.

### 2.1 Contagens esperadas (baseline V3-Q3 § 3, motor real)

Sem elas não dá para distinguir "rodou tudo" de "coletou nada em silêncio".

| Classe | Testes | Baseline V3-Q3 |
|---|---|---|
| `AtomicEngineIT` | 18 | PASS |
| `EnlistmentEngineIT` | 22 | PASS |
| `FreshnessEngineIT` | 27 | PASS |
| `ReviewerEngineIT` | 24 | PASS |
| `WorkloadEngineIT` | 43 | PASS |
| `PortalReadEngineIT` | 8 | **FAIL → deve virar PASS** com o reparo §6 |
| `PortalReadPublisherEngineIT` | 4 | **FAIL → deve virar PASS** |
| `StaffCaseReadEngineIT` | 2 | **FAIL → deve virar PASS** |

Grupo A = **134 testes**, todos PASS na baseline; grupo B = **14 erros**, todos
`Rejected: READ_DEPENDENCY_UNAVAILABLE`, uma só causa. Qualquer total menor que o da tabela
significa coleta vazia, não sucesso.

## 3. Fixture Postgres **com TLS** (grupos C e D)

`ConsumerEdgeInstallation.session()` (`…/human/ConsumerEdgeInstallation.java:60-73`) recusa a
instalação com `EngineStore.unavailable()` se qualquer item abaixo falhar. O Postgres do
`docker-compose.yml:29-36` é `postgres:16` de estoque, com `ssl` **off** — por isso a linha 69
(`SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()`) falha e `installSchema` nunca conclui.
**A causa é só essa**: os OIDs de banco/schema e os GRANTs a própria fixture descobre e aplica em
tempo de execução (`ConsumerLineageEngineIT.java:38-48`).

Pré-condições verificadas por `session()`:

1. conexão **não** autocommit e produto `PostgreSQL` (`:61-62`);
2. `current_database()`/`current_schema()` e seus OIDs batem com o bundle; dono do schema ==
   `owner_role`; `session_user == current_user`; login == `owner_role`/`runtime_role`;
   `pg_my_temp_schema() == 0` (`:63-68`);
3. **`pg_stat_ssl.ssl = true` na sessão** (`:69`);
4. `runtime_role`: `rolsuper/rolcreatedb/rolcreaterole/rolbypassrls` todos falsos, `rolcanlogin`
   verdadeiro e **nenhuma** linha em `pg_auth_members` para ele (`:70-72`);
5. `runtime_role` sem privilégio `CREATE` no schema (`:73`).

### 3.1 Subir um Postgres descartável com TLS

Use uma porta e um projeto próprios; **não** altere o Postgres do compose (ele é compartilhado e
pertence ao `engine.lock`).

**Pré-requisitos desta seção:** `docker`, `openssl`, `psql` e **`sudo`** — o `chown 999:999` abaixo
é obrigatório porque o `postgres:16` recusa subir se a chave do servidor não pertencer ao uid do
processo. Sem `sudo`, use um Postgres já provisionado com TLS pelo dono e pule para §3.2; não
existe caminho sem TLS (§3, item 3).

Material efêmero, gerado na hora, nunca commitado:

```sh
FIXTURE="$(mktemp -d)"; chmod 700 "$FIXTURE"
openssl req -new -x509 -days 1 -nodes -subj '/CN=localhost' \
  -keyout "$FIXTURE/server.key" -out "$FIXTURE/server.crt"
chmod 600 "$FIXTURE/server.key"; sudo chown 999:999 "$FIXTURE/server.key" "$FIXTURE/server.crt"

docker run -d --name maezo-consumer-it-pg -p 5434:5432 \
  -e POSTGRES_USER=maezo -e POSTGRES_PASSWORD="${MAEZO_PG_PASSWORD:?defina MAEZO_PG_PASSWORD antes de rodar a IT}" -e POSTGRES_DB=maezo \
  -v "$FIXTURE":/tls:ro postgres:16 \
  -c ssl=on -c ssl_cert_file=/tls/server.crt -c ssl_key_file=/tls/server.key
```

Prove o TLS **antes** de rodar a suíte — sem essa prova o diagnóstico se repete:

```sh
docker exec maezo-consumer-it-pg psql -U maezo -d maezo -Atc 'SHOW ssl'          # espera: on
psql "postgresql://maezo@localhost:5434/maezo?sslmode=require" \
     -Atc 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()'               # espera: t
```

### 3.2 Papel de runtime dedicado

`NOLOGIN` não serve (item 4 exige `rolcanlogin`), e o papel não pode herdar nada:

```sh
# `-v pw=…` é obrigatório: o bloco abaixo referencia :'pw'. A senha vem do ambiente, nunca do
# arquivo nem do histórico do shell.
psql "postgresql://maezo@localhost:5434/maezo?sslmode=require" -v pw="$MAEZO_CONSUMER_IT_RUNTIME_PASSWORD" <<'SQL'
DROP ROLE IF EXISTS consumer_runtime;
CREATE ROLE consumer_runtime LOGIN PASSWORD :'pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
-- nenhuma GRANT <papel> TO consumer_runtime: pg_auth_members precisa ficar vazio para ele
SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname='consumer_runtime';
SELECT count(*) AS memberships FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='consumer_runtime';
SQL
```

Esperado: `f f f f t` e `memberships = 0`. Exporte
`MAEZO_CONSUMER_IT_RUNTIME_PASSWORD` antes (§3.3), para que o `-v pw=` acima resolva.

### 3.3 Rodar C e D

O JDBC precisa de `sslmode=require`, senão a sessão volta a ser não-TLS e o item 3 falha:

```sh
export MAEZO_HUMAN_IT_JDBC_URL='jdbc:postgresql://localhost:5434/maezo?sslmode=require'
export MAEZO_HUMAN_IT_DB_USER='maezo'
export MAEZO_HUMAN_IT_DB_PASSWORD="${MAEZO_PG_PASSWORD:?defina MAEZO_PG_PASSWORD antes de rodar a IT}"
export MAEZO_CONSUMER_IT_RUNTIME_USER='consumer_runtime'
export MAEZO_CONSUMER_IT_RUNTIME_PASSWORD='…'      # fora do shell history
mvn -B -o -f src/maezo/portal/engine/java/pom.xml verify -Pphi-consumer-engine-it \
    -Dmaezo.repo.root="$PWD" \
    -Dtest='ClassifiedDecisionEngineIT,ConsumerLineageEngineIT,ClassifiedConsumerFenceEngineIT,ConsumerContinuationEngineIT' \
    -DfailIfNoSpecifiedTests=true
```

`-Pphi-consumer-engine-it` é obrigatório: o perfil roda o surefire contra o JAR empacotado e
define `maezo.consumer.jar` (`pom.xml:24-25`); sem ele as classes recusam explicitamente
(`ConsumerLineageEngineIT.java:26`). Como o perfil usa a fase `package`, use `verify`, não `test`.

Derrube tudo ao fim: `docker rm -f maezo-consumer-it-pg && rm -rf "$FIXTURE"`.

## 4. Fixture real do native v2 (grupo E)

`MAEZO_NATIVE_V2_IT_FIXTURE` aponta para um diretório **absoluto e canônico, só do dono**, com
`native-v2-fixture.json` e material real de CA/certificado cliente e senhas de banco. O conteúdo
exato, os campos do JSON e os casos sintéticos exigidos estão em
`deploy/cibseven/secured/NATIVE-V2.md`, seção "Checks and real fixture" — que também determina:
**nunca imprimir nem commitar esses arquivos**. Nenhum agente pode fabricá-lo; sem ele as três
classes falham explicitamente (`NativeV2AdmissionEngineIT.java:20`), e essa falha é o
comportamento correto.

## 5. Fixture D7/pacote (contexto)

`deploy/cibseven/secured/ACCEPTANCE.md` descreve a fixture descartável do lane D7
(`prepare_fixture.py prepare|seed`, `MAEZO_D7_PACKAGE_FIXTURE`, `MAEZO_D7_COMPOSE_PROJECT`). Ela
serve as suítes Python de integração do portal, não os `EngineIT` acima.

## 6. Reparo de landing deste PR (grupo B)

`PortalReadCommand.resourceDigest` limitava o modelo BPMN/DMN **implantado** com `MAX` (64 KiB),
que é a cerca de *entrada não confiável*. 7 dos 16 BPMN de produção passam de 64 KiB, então
`verifyCatalog` recusava o próprio catálogo da operadora com `READ_DEPENDENCY_UNAVAILABLE`. O
limite do recurso implantado agora é `RESOURCE_MAX` (1 MiB), o **mesmo** que o leitor Python já
aplica aos mesmos bytes de `ACT_GE_BYTEARRAY`
(`src/maezo/gateway/human/decision_binding.py:707,758`). `MAX` continua 65536.

A leitura limitada existe **uma vez só**, em `PortalReadModels.resourceMatches`: os 7 pontos do
pacote que leem `getProcessModel`/`getDecisionModel` passam por ela (V9-Q4 F2 — antes, o
`AuthRuntime` repetia os literais `1048577`/`1048576` e o `AtomicHumanCommand` lia sem limite
nenhum). Cada chamador mantém o próprio código de recusa (503 / 403 / 409), que é contrato dele.

Fixado por `PortalReadResourceBoundTest` e `tests/unit/portal/test_read_dependency_resource_bound.py`
— ambos varrem o pacote inteiro, então um leitor novo não consegue reintroduzir limite próprio.
