# Provedor Q2 instalado (`PortalReadTrust.Providers`) — T1.7a

Desenho: `docs/plans/portal-autoridade-nativa-dev.md` §3.1. Este módulo é o "separately qualified
provider package" de `src/maezo/portal/engine/README.md`: o JAR
`maezo-portal-read-provider-1.0.0.jar` só entra na imagem `INSTALL_PORTAL_READ=true` (o `COPY` no
`deploy/cibseven/Dockerfile.human` fica para depois da T1.2, que é a dona do arquivo).

**Escopo recortado ao staff.** `acquire`, `Admission.requireCurrent`, `verifySource`
(`membership`, `catalog-designate`), `verifyCatalog` e `continuity` estão implementados.
`qualifyPublication` recusa sempre (T1.7b). `verifyIdentityPolicy`, `verifyClassification` e a
publicação `resource` recusam sempre (Onda 8). Toda recusa é `503 READ_DEPENDENCY_UNAVAILABLE`.

## Registro

`META-INF/services/br.com.maezo.human.PortalReadTrust$Providers` →
`br.com.maezo.human.readprovider.InstalledReadProviders`. O construtor sem argumentos nunca lança:
configuração, arquivo de chaves ou digest de código inválidos deixam o provedor "quebrado", e todo
método recusa. O `PortalReadPlugin` transforma isso em boot recusado.

## `portal-read-provider.v1` (`MAEZO_PORTAL_READ_PROVIDER_FILE`, público, montado read-only)

JSON sem números, chaves fechadas:

| chave | valor |
|---|---|
| `schema` | `portal-read-provider.v1` |
| `root_public_key_spki_base64` / `root_public_key_sha256` | SPKI Ed25519 da raiz de instalação do aprovador (D-F) e o SHA-256 dessa SPKI |
| `admission_ref` / `minimum_admission_revision` | qual admissão ler e a revisão mínima aceita (piso do anti-rollback) |
| `datasource_jndi` | `java:jdbc/ProcessEngine` (o do descritor) |
| `native_schema` | `maezo_native` (comparação exata, ADR-0060) |
| `admission_table_oid` / `admission_table_owner` | pin da tabela: OID e dono (`maezo_native_schema_owner`) |
| `continuity_keys_file` | caminho absoluto do arquivo de chaves |
| `membership_source` | `dsn_file`, `ca_file`, `source_schema` (`amh`), `publisher_ref` — só o formato é fechado aqui; quem consome é a T1.7b |

## `portal-read-admission.v1` (linha de `maezo_native.mzo_portal_read_admission`)

DDL: `src/main/resources/portal-read-admission-postgres.sql`. Só o dono grava; o login do engine
recebe `SELECT` (T1.4). `RECORD_` são os bytes JCS exatos; `SIGNATURE_` é Ed25519 da raiz sobre
`"maezo/portal-read-admission/v1\0" ‖ RECORD_` (T1.3 `sign-admission`). Chaves do registro:
`schema`, `admission_ref`, `admission_revision` (= `REVISION_`), `scope` (`tenant`, `environment`,
`workload_ref`), `engine_name`, `database_incarnation`, `read_deployment_ref`,
`read_deployment_digest`, `trust_configuration_digest` (SHA-256 do `portal-read-trust.v1`
parseado), `purposes` (⊆ `portal-task-read`, `portal-read-publication`), `code_digests`
(`engine`, `provider`: SHA-256 dos JARs **carregados**), `continuity_keys` (`key_id`, `generation`,
`commitment`, `not_before`, `not_after`), `catalog` (`catalog_ref`, `publisher_ref`,
`catalog_digest`), `publishers` (`kind` ∈ `membership`/`catalog-designate`, `publisher_ref`,
`source_ref_prefix`), `statement_timeout_seconds` (1–10), `observation_seconds` (60–900),
`not_before`, `valid_until` (no máximo 14 dias depois de `not_before`). Revogar é
`UPDATE … SET REVOKED_ = true` pelo dono; uma revisão nova supera as anteriores.

**`acquire`** lê a linha de maior revisão numa conexão própria do DataSource (fora de comando do
engine), em transação `REPEATABLE READ, READ ONLY` com `statement_timeout`/`lock_timeout` locais de
5 s. Recusa se: a tabela não é a pinada (OID, dono, schema exato), o login do engine pode
`INSERT/UPDATE/DELETE/TRUNCATE` nela, é membro do dono, é superusuário ou `BYPASSRLS`; a assinatura
não é da raiz; os bytes não são JCS; qualquer campo diverge dos parâmetros do plugin; o propósito
ou o escopo não foram admitidos; a janela não vale; a linha está revogada; a revisão é menor que a
maior já vista neste processo (anti-rollback em memória); ou os `code_digests` não são os dos JARs
carregados. Devolve `generation = providerRevision = revisão`, `capabilityDigest =
SHA-256(RECORD_)`, `observedAt = agora`, `validUntil = min(valid_until, agora + observation_seconds)`.

## `portal-read-continuity-keys.v1` (arquivo de chaves, segredo)

`{"schema":"portal-read-continuity-keys.v1","keys":[{"key_id","generation","secret_base64"(32
bytes),"not_before","not_after"}]}`, dono = usuário do processo, modo exatamente `0400`, sem link
simbólico. Carregado uma vez no boot. Cada chave do arquivo tem que estar comprometida na admissão
vigente: `commitment = hex(HMAC-SHA256(chave, "maezo/portal-native-read-continuity/v1/commitment"))`.
`NativeKey.digest` é esse compromisso, nunca um hash da chave.

## Testes

- `*Test` (sem banco): formato, assinatura, janela, limites, registro, custódia do arquivo.
- `*JarIT` (depois do `package`, contra o JAR construído, com PostgreSQL real):
  `ReadProviderBootJarIT` sobe o `PortalReadPlugin` de verdade pelo `ServiceLoader` e chama
  `PortalReadPlugin.staffLease` (critério de pronto da T1.7a; a rota HTTP depende da T1.1).
  Negativos de boot: provedor ausente, duplicado, configuração ausente, linha ausente, outra raiz,
  revogada, vencida/ainda não válida, `trust_configuration_digest` trocado, `code_digests` de outro
  JAR, login do engine com escrita. `InstalledReadProvidersJarIT` cobre expiração da admissão
  retida, supersessão, revogação matando chaves, custódia e compromisso, `verifySource`,
  `verifyCatalog` e os pins.

```sh
mvn -B -f src/maezo/portal/engine/java/pom.xml install
MAEZO_READ_PROVIDER_IT_JDBC_URL=jdbc:postgresql://localhost:5432/postgres \
MAEZO_READ_PROVIDER_IT_DB_USER=postgres MAEZO_READ_PROVIDER_IT_DB_PASSWORD=... \
  mvn -B -f src/maezo/portal/engine/read-provider/pom.xml verify
```

O usuário do IT precisa de `CREATEROLE` e `CREATE` no banco: cada teste cria e remove o próprio
schema e os dois papéis.

**NÃO VERIFICADO** (só o boot na imagem prova): o `ServiceLoader` pelo classloader do Tomcat
(`/camunda/lib`) e o `java:jdbc/ProcessEngine` resolvido por `new InitialContext()` dentro do
`preInit`. Os testes usam o classloader do surefire e uma raiz JNDI de teste.
