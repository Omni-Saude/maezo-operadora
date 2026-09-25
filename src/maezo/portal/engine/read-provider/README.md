# Provedor Q2 instalado (`PortalReadTrust.Providers`) — T1.7a + T1.7b + H1

Desenho: `docs/plans/portal-autoridade-nativa-dev.md` §3.1. Este módulo é o "separately qualified
provider package" de `src/maezo/portal/engine/README.md`: o JAR
`maezo-portal-read-provider-1.0.0.jar` só entra na imagem `INSTALL_PORTAL_READ=true`: o estágio de build
do `deploy/cibseven/Dockerfile.human` compila este módulo e o copia para
`/camunda/lib/maezo-portal-read-provider.jar`, ao lado de `maezo-human-command.jar` (B1).

**Escopo recortado ao staff.** `acquire`, `Admission.requireCurrent`, `verifySource`
(`membership`, `catalog-designate`), `verifyCatalog` e `continuity` (T1.7a), e
`qualifyPublication` para `membership`, `catalog-designate`, `catalog-revoke` e `revoke-key`
(T1.7b, seção abaixo). Com o bloco `human` da admissão (H1, D-N, seção "Tarefas humanas"),
`verifyIdentityPolicy`, `verifyClassification` e a publicação `resource` verificam; sem ele
(a admissão staff-only), recusam sempre. Toda recusa é `503 READ_DEPENDENCY_UNAVAILABLE`.

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
| `membership_source` | `dsn_file` (segredo), `ca_file`, `source_schema` (`amh`), `publisher_ref` — a fonte viva de membership da T1.7b (seção abaixo) |

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

## Publicações do escopo staff — `qualifyPublication` (T1.7b)

Roda **fora** do comando do engine (o plugin chama depois do `acquire` e antes do lock do tenant) e
exige uma `Admission` **deste** provedor, adquirida para `portal-read-publication`. O pedido
(`portal-read-publication.v1`, chaves fechadas) tem que estar amarrado ao engine, encarnação,
deployment e escopo (`tenant`, `environment`) admitidos, com `scope.workload_ref =
source.publisher_ref`. A qualificação devolvida (`StaffScopeQualification`) só compara em
memória: `verify(kind, source, payload)` aceita **exatamente** o `(kind, source, payload)`
qualificado, e vale por no máximo `observation_seconds` (e nunca além da admissão, que continua
podendo ser revogada ou superada).

| kind | o que `verify` exige |
|---|---|
| `membership` | o payload **é** a projeção da linha viva de `<source_schema>.portal_memberships` (abaixo); `source_revision` = revisão da linha; `source_digest = SHA-256(JCS(payload))`; `receipt_ref = "portal-identity:" + tenant + ":membership:" + principal_ref + "@" + revisão`; `valid_until = observed_at + observation_seconds` (contrato com a T1.5); o `publisher_ref` da configuração é o publicador de `membership` admitido |
| `catalog-designate` | `catalog_ref`, `catalog_digest` e `source.publisher_ref` iguais aos admitidos |
| `catalog-revoke`, `revoke-key` | nada além do pedido qualificado: só reduzem autoridade, e o envelope já foi verificado com a chave de publicação |
| `resource` | só com o bloco `human` (seção "Tarefas humanas", H1) |
| qualquer outro | recusa |

**A fonte viva (`MembershipSourceObserver`).** A cada `membership`, o provedor relê a linha pelo
login observador (`portal_read_source_amh`), com o SQL de `src/maezo/portal/api/postgres.py`
`get_membership` qualificado pelo schema: `SELECT payload FROM "<source_schema>".portal_memberships
WHERE tenant=? AND issuer=? AND subject=?`. O `tenant` é o **admitido**, nunca o do pedido. A T1.4
concede `SELECT` só nas colunas `tenant`, `issuer`, `subject` e `payload`.
- `dsn_file`: `postgresql://<login>:<senha>@<host>[:<porta>]/<database>`, uma linha (um LF final
  é tolerado), **sem query string**: todo parâmetro de conexão é do provedor. A senha pode vir
  percent-encoded. É segredo: dono o usuário do processo, modo `0400`, sem link. Relido a cada
  observação, então a rotação da senha não pede restart.
- Conexão pgjdbc (o driver tem que ser `org.postgresql.Driver`), `sslmode=verify-full` contra o
  `ca_file`, `readOnly`, timeouts de conexão/socket e, na sessão, `REPEATABLE READ, READ ONLY`,
  `statement_timeout`/`lock_timeout` locais (`statement_timeout_seconds` admitido) e
  `search_path=pg_catalog`.
- Postura do login, numa consulta: a relação é tabela (`relkind='r'`, nunca uma view no lugar
  dela); a transação é read-only; o login não pode `INSERT`/`UPDATE` (nem por coluna),
  `DELETE`/`TRUNCATE`, o que cobre dono e superusuário; e o login **não é membro de papel
  nenhum**, o que cobre um papel com escrita alcançável só por `SET ROLE` (`WITH INHERIT FALSE`).
- A linha é validada como `MembershipRecord` (`records.py`: chaves fechadas, `revoked` opcional com
  default `false`, `revision` inteiro JSON ≥ 0, pelo menos um papel, regra de `audience` ×
  `subject_bindings`, prazo de revisão com fuso) e projetada como `read_publisher.py:111-121` +
  `read_profile.wire`. A leitura do `payload` aceita inteiros JSON e nada mais frouxo
  (`SourceJson`). Datas: só o formato que o `model_dump_json` escreve (`Z` ou `±HH:MM`, até
  microssegundos); o pydantic aceita mais (espaço no lugar do `T`, `+0300`, sete dígitos), e o
  provedor recusa esses, fail-closed.

**Paridade Java × Python.** `tests/fixtures/portal_read/jcs-membership-vector.json` (linhas como o
`model_dump_json` grava → bytes JCS e SHA-256 da projeção, mais linhas que os dois lados recusam) é
lido por `MembershipProjectionTest` e por `tests/unit/portal/test_jcs_membership_vector.py`, que
passa pelo caminho real do publicador Python.

**Limites conhecidos.** O `verify` é em memória por desenho: uma linha alterada **depois** do
`qualifyPublication` não é vista; a janela é `observation_seconds`. `catalog-revoke` e `revoke-key`
qualificam aqui, mas o `verifySource` da T1.7a só admite publicador para `membership` e
`catalog-designate` (`AdmissionRecord.SOURCE_KINDS`); ponta a ponta as duas revogações ainda
recusam. Admiti-las é mudança do formato da admissão (T1.3 `sign-admission`), não desta tarefa.

## Tarefas humanas — H1 (D-N)

**Admissão.** `portal-read-admission.v1` ganha UMA chave opcional, `human` (sem ela o registro é o
da T1.7a, byte a byte). Ela e o publicador `kind=resource` vêm juntos ou não vêm: um sem o outro é
recusado (Java `AdmissionRecord`, Python `approver._admission_shape`, espelhos).

```
"human": {"entries": [{                       // 1..256, (process_definition_id, task_definition_key) únicos
  "process_definition_id": ref, "task_definition_key": ref,
  "classification": {"classification_ref", "classification_digest", "policy_ref", "policy_digest",
                     "projection": "full_task_detail.v1", "fields_digest"},
  "identity_policy": {"artifact_ref", "digest"},  // = opaque_task_id_policy da entrada do catálogo
  "task_id_format": "decimal" | "uuid",          // medido no C1: a imagem Dockerfile.human gera UUID; o standalone do IT, decimal
  "candidate_groups": [ref, ...],                // 1..64, sem `${`/`#{` (o padrão de ref já recusa)
  "user_candidates": "refused" | "native_principal"}]}
```

| método | o que exige (tudo síncrono, sem I/O, depois do `requireCurrent`) |
|---|---|
| `verifyClassification(c, entry)` | entrada admitida para `(entry.process_definition_id, entry.task_definition_key)`; `c` com as 7 chaves fechadas; os 6 campos da classificação **iguais** aos admitidos; `c.valid_until` ≤ `valid_until` da admissão; `entry.disclosure_policy` = `{policy_ref, policy_digest}` admitidos |
| `verifyIdentityPolicy(policy, task, links)` | entrada admitida para a tarefa; `policy` = `identity_policy` admitida; `task_id` no formato admitido; `tenant_id` = tenant admitido; `active`; `assignee_ref` nulo ou ref; cada link `candidate` da MESMA tarefa e tenant, com grupo dentro de `candidate_groups` **ou** usuário só se `native_principal` (o engine resolve usuário e assignee em `MZO_HUMAN_PRINCIPAL`) |
| `qualifyPublication` `resource` | admissão com `human` e publicador `resource` = `source.publisher_ref`; a classificação do payload é a admitida de alguma entrada daquela definição (a da tarefa real é conferida de novo pelo engine, no comando, por `verifyClassification`) |
| `verify` `resource` | o contrato abaixo, derivado do payload |
| `verifySource("resource", …)` | publicador e prefixo admitidos (igual aos outros kinds) |

**Contrato de publicação `resource` (H2 publica por ele).** Vetor compartilhado:
`tests/fixtures/portal_read/jcs-resource-vector.json` (payload = `JCS(wire(ResourceProjection))`,
gerado pelo caminho real do Python; lido por `HumanAdmissionTest` e `ResourceQualificationJarIT`).
- `source_ref = <source_ref_prefix admitido> + task_id` (no C1: `portal-resource:amh:task:<id>`);
- `source_revision = resource_revision`;
- `source_digest = SHA-256(JCS(payload))` (o payload ASSINADO; o engine grava `observed_task_revision`
  pós-flush, por isso o recibo de `resource` não repete esse digest);
- `receipt_ref = "portal-resource:" + tenant + ":task:" + task_id + "@" + resource_revision`;
- `valid_until = observed_at + observation_seconds` (como a membership: a fonte é republicada);
- `evidence_ref` e cada `positive_grants[].decision_receipt_ref` **únicos por tarefa**: os tetos de
  continuidade da leitura são indexados por (kind, ref, revisão, digest), e dois recursos com o
  mesmo ref e `observed_at` distintos numa fila recusam a leitura inteira (medido no IT).

**Mudança no engine (mesmo PR).** `PortalReadCommand.state` indexava o teto `classification` pelo
`classification_ref`, que é o MESMO para toda tarefa de uma entrada: duas tarefas da fila, com
recursos publicados em instantes distintos, colidiam e `discover` dava 503. O teto passa a ser a
classificação observada através do recurso da tarefa (`source_ref = resource_ref`). Grupos A/B do
`ENGINE-IT.md` (`PortalReadEngineIT`, `PortalReadPublisherEngineIT`, `StaffCaseReadEngineIT`) verdes.

**`READ_HISTORY`.** Nenhum caminho Q2 humano consulta a autorização nativa do engine: a leitura de
tarefa, a publicação `resource` e o `staff_current_task.v1` leem `ACT_RU_*` por SQL dentro do
comando (`PortalReadStore.tuple`, `StaffCaseStore.taskRows`), e `READ_HISTORY` só é exigido pelo
D7 (`WorkloadPlugin`, `NativeAdmissionV2`). Não há grant a conceder no Java; se o dev pedir um, é
decisão de instalação (T1.4/H4), não do provedor.

**Testes H1.** `HumanAdmissionTest` (vetor, bloco fechado, identidade, classificação),
`ResourceQualificationJarIT` (provedor contra a linha real: contrato derivado, classificação não
admitida, admissão staff-only recusando, revogação), `HumanTaskReadJarIT` (engine CIB Seven real +
PG 17 + provedor do JAR + BPMN/DMN reais da escalação: a tarefa aparece na fila `team` do grupo da DMN
e não na do outro grupo, embora o recurso conceda os dois; `task` passa pelas duas verificações;
classificação não admitida e admissão staff-only recusam e não mudam nada; grupos fora do admitido
recusam a leitura).

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
- T1.7b: `SourceJsonTest`, `MembershipProjectionTest` (vetor compartilhado) e
  `MembershipSourceDsnTest` sem banco. `PublicationQualificationJarIT`: qualificação contra a linha
  viva, com TLS real — cada campo trocado, contrato de proveniência, amarração ao engine/escopo,
  linha ausente/de outro tenant/alterada depois da leitura do publicador/inválida, expiração e
  revogação, admissão de outro propósito ou provedor, `resource`, catálogo, postura do observador
  (escrita por tabela/coluna, papel via `SET ROLE`, view no lugar da tabela), CA e nome do
  servidor, custódia do DSN. `PublicationReceiptJarIT`: engine CIB Seven real com o plugin
  carregado do JAR; membership igual à linha viva gera receipt (e o replay devolve o mesmo), um
  campo trocado ou a linha alterada é recusado sem mudar nada, e o catálogo staff vazio
  (`entries=[]`, `policies=[]`, `forms=[]`) designa.

```sh
mvn -B -f src/maezo/portal/engine/java/pom.xml install
MAEZO_READ_PROVIDER_IT_JDBC_URL=jdbc:postgresql://localhost:5432/postgres \
MAEZO_READ_PROVIDER_IT_DB_USER=postgres MAEZO_READ_PROVIDER_IT_DB_PASSWORD=... \
  mvn -B -f src/maezo/portal/engine/read-provider/pom.xml verify
```

O usuário do IT precisa ser **superusuário de um PostgreSQL descartável** (o serviço do CI, um
container local): cada teste cria e remove os próprios schemas e papéis, e, uma vez por JVM, o
`ServerTls` gera uma CA sintética com o `keytool` do JDK, grava o certificado e a chave do servidor
no diretório de dados (`COPY … TO PROGRAM`) e liga TLS com `ALTER SYSTEM` + `pg_reload_conf()`. A
SAN do certificado é o host da URL do IT, e o teste de nome errado conecta pelo IPv4 do servidor.

**NÃO VERIFICADO** (só o boot na imagem prova): o `ServiceLoader` pelo classloader do Tomcat
(`/camunda/lib`), o `java:jdbc/ProcessEngine` resolvido por `new InitialContext()` dentro do
`preInit` e o `DriverManager.getDriver` achando o pgjdbc a partir do classloader do provedor. Os
testes usam o classloader do surefire e uma raiz JNDI de teste.
