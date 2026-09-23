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
5 s e `search_path=pg_catalog` local, para que nenhum operador ou função resolva por um schema
onde alguém cria objetos (CVE-2018-1058). A leitura da linha exige `tableoid` igual ao OID pinado:
o nome não é resolvido duas vezes.

**Pin da tabela: lista fechada, numa só consulta** (`InstalledReadProviders.PIN_SQL`). Recusa se:
- a tabela não é a pinada: OID, dono, `relkind='r'` e schema exato;
- `relacl` tem qualquer entrada além das do próprio dono e de **exatamente uma** `SELECT` do login
  do engine concedida pelo dono, sem `GRANT OPTION`. Isso pega escrita concedida a qualquer outro
  papel, inclusive um papel alcançável só por `SET ROLE` (`WITH INHERIT FALSE`), e também `PUBLIC`;
- existe ACL por coluna (`pg_attribute.attacl`), que `has_table_privilege` não enxerga;
- existe trigger ou regra na tabela (tornariam a revogação impossível), ou RLS ligado;
- o login do engine pode `INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER/REFERENCES`, é o dono ou membro
  dele, é membro de `pg_write_all_data`, ou tem `SUPERUSER`, `BYPASSRLS` ou `CREATEROLE`.

Além do pin, recusa se: a assinatura não é da raiz; os bytes não são JCS; qualquer campo diverge
dos parâmetros do plugin; o propósito ou o escopo não foram admitidos; a janela não vale; a linha
está revogada; a revisão é menor que a maior já vista neste processo (anti-rollback em memória);
ou os `code_digests` não são os dos JARs carregados. Devolve `generation = providerRevision =
revisão`, `capabilityDigest = SHA-256(RECORD_)`, `observedAt = agora`, `validUntil =
min(valid_until, agora + observation_seconds)`.

`source_ref_prefix` tem que terminar em `:` ou `/`: o prefixo é um segmento inteiro, e `…:amh:`
nunca admite `…:amhx:…`.

**Linha pública de boot (âncora independente da raiz).** O arquivo que nomeia a raiz é montado pela
engenharia, então o pin dele sozinho não prova nada: quem monta pode trocar o par e o pin juntos.
No primeiro `acquire` bem-sucedido de cada revisão, o provedor emite **uma** linha INFO
(`java.util.logging`, logger `br.com.maezo.human.readprovider.InstalledReadProviders`):

```
portal_read_provider root_sha256=<hex> admission_ref=<ref> revision=<n> capability_digest=<hex> engine_code=<hex> provider_code=<hex>
```

Só valores públicos e fechados; nunca chave, compromisso, DSN, assinatura ou corpo do registro. O
aprovador confere `root_sha256` contra a chave dele e `capability_digest` contra o registro que ele
assinou (plano, §4, linha `portal_read_root_sha256` / `capability_digest`).

## `portal-read-continuity-keys.v1` (arquivo de chaves, segredo)

`{"schema":"portal-read-continuity-keys.v1","keys":[{"key_id","generation","secret_base64"(32
bytes),"not_before","not_after"}]}`, dono = usuário do processo, modo exatamente `0400`, sem link
simbólico. Carregado uma vez no boot. Cada chave do arquivo tem que estar comprometida na admissão
vigente: `commitment = hex(HMAC-SHA256(chave, "maezo/portal-native-read-continuity/v1/commitment"))`.
`NativeKey.digest` é esse compromisso, nunca um hash da chave.

**Nota operacional (montagem, decisão da T1.2).** Um volume `Secret` do Kubernetes monta cada
chave como link simbólico para `..data/<arquivo>`, com dono root. O provedor **recusa** esse
arquivo: link simbólico, dono diferente do usuário do processo. É fail-closed de propósito, e o
engine não sobe. A montagem precisa entregar um arquivo regular, dono o usuário do engine (uid
1000) e modo `0400`. Um caminho é um init container que copia o segredo para um `emptyDir` com
`chown`/`chmod`, como o init de materiais do portal faz no ECS. Quem decide é a T1.2.

## Testes

- `*Test` (sem banco): formato, assinatura, janela, limites, registro, custódia do arquivo.
- `*JarIT` (depois do `package`, contra o JAR construído, com PostgreSQL real):
  `ReadProviderBootJarIT` sobe o `PortalReadPlugin` de verdade pelo `ServiceLoader` e chama
  `PortalReadPlugin.staffLease` (critério de pronto da T1.7a; a rota HTTP depende da T1.1).
  Negativos de boot: provedor ausente, duplicado, configuração ausente, linha ausente, outra raiz,
  revogada, vencida/ainda não válida, `trust_configuration_digest` trocado, `code_digests` de outro
  JAR, login do engine com escrita. Negativos do pin fechado, cada um revertido até o positivo
  voltar: `UPDATE` por coluna, privilégio `TRIGGER`, papel com escrita alcançável só por
  `SET ROLE`, `pg_write_all_data`, `CREATEROLE`, trigger ou regra na tabela, RLS com política,
  `SELECT` para `PUBLIC` ou com `GRANT OPTION`, leitura por `pg_read_all_data` fora do ACL, e um
  operador `=` plantado no `search_path` do login. `InstalledReadProvidersJarIT` cobre expiração da admissão
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
