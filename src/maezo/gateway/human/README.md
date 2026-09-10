# HumanGateway — autorização D4 e admissão/recuperação D6

Fontes: ADR-0049 D3–D7/D9, ADR-0006/0008/0037, DL-0048/0005,
SP-OP-AUTH-001, SP-OP-ESCALATION-001 e SP-OP-PAGTO-001. Esta implementação
separa a autorização humana do gateway de agentes e preserva os limites hard.
Não depende de shadow, flip de política ou allowlist de testes.

`HumanGateway` resolve a sessão opaca pelo `HumanSessionResolver` atual em
cada entrada. Somente o público existente `staff` pode acessar estas tarefas
internas. Papéis e grupos devem coincidir na mesma membership; nenhum papel
administrativo recebe acesso implícito. Os vínculos de sujeito e consentimentos
exigidos pelo contrato são conferidos contra a projeção autoritativa atual.
Beneficiário/prestador precisa da futura superfície própria por recurso.

A composição injeta três portas fechadas, todas vinculadas a tenant, ambiente e
workload. Estas interfaces são **dependências confiáveis do servidor**, nunca
objetos ou endpoints que o browser pode fornecer:

- `HumanTaskTransport.read_task`: tarefa ativa e snapshot do deployment/catalogo
  pinado; grupos dinâmicos já resolvidos. Nenhum cliente REST genérico.
- `AuthorityProjection.current_authority`: principal imutável, pins completos de
  processo/formulário, revisões de membership/tarefa/evidência/autoridade e
  consentimentos atuais. O adaptador deve recusar fontes indisponíveis ou atrasadas.
- `DurableAdmission.admit`: `PostgresHumanAdmission` usa TX única tenant de audit intent + outbox humano,
  com dedup/conflito por comando e cadeia íntegra. Retorna `PendingAdmission`
  somente após commit. Não envia comando ao engine.

O gateway compara as duas fontes independentes, expectativas do browser,
atribuição e operações permitidas. Reconsulta a sessão após chamadas remotas
antes de devolver snapshot ou admitir claim/release. Essa reconsulta não promete
atomicidade entre IdP, banco de membership e engine: D5 deve validar novamente as
revisões no domínio de serialização do engine antes de executar.

`HumanCommandCredentialPartition` guarda **referências não secretas**, isoladas
do `CredentialVault`/`AgentCredentialView` existente. O locator é derivado de
`human-command/{environment}/{tenant}/{workload_ref}/{key_id}`. Toda chamada
confere o escopo. Não carrega chave, não assina e não reutiliza partição de agente,
admin, OIDC, PHI HMAC ou A2A. A exclusividade do material real em KMS/cofre precisa
da futura implementação/prova de provisionamento; um nome de referência não a prova.
Principal humano e workload executor continuam distintos. Não existe assinatura
pessoal humana. `PartitionedEd25519Signer` assina envelopes D5 somente com chave
humana explicitamente provisionada, validade operacional fornecida e partição atual.

## Limite funcional e dependências obrigatórias

`create_production_gateway` recusa configuração com
`production_capabilities_unavailable`: faltam composição autoritativa e provisionamento
verificado de credenciais, classificação de formulários e D7. A composição por portas permite testes unitários das cercas, mas
não é uma factory de produção alternativa. Não há fallback de persistência em
memória, noop de auditoria, credencial default ou emissão direta de `/complete`.
`PendingAdmission` só representa o retorno do adaptador; seu schema não prova
commit, e o gateway nunca sintetiza `HumanCommandReceipt` ou HTTP202.

Os snapshots projetam somente claim/release elegíveis neste recorte. Decisões não
aparecem como ação ativa. As seis combinações atualmente tipadas continuam com
estas dependências explícitas, que devem ser **implementadas antes das jornadas
verticais**, sem reduzir o escopo final do portal:

| Binding | Dependência de ativação de decisão |
|---|---|
| AUTH auditor, coordenação, junta | Contrato reconciliado de custódia PHI da justificativa/cid/fundamentação e projeção classificada/referência consumível pelo engine/guard |
| ESCALATION tratamento, supervisão | Contrato reconciliado de custódia PHI de `notas_resolucao` e projeção/referência; não copiar narrativa para Zona Geral |
| PAGTO admissibilidade | Reconciliar fonte `BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY` com contrato e classificação; lastro como evidência não vira confirmação |

Nenhum input obrigatório é descartado ou remapeado. `submit_decision` valida o
DTO, autorização e revisões, preserva o objeto integral na fronteira PHI e recusa
**antes da admissão**. Este módulo deve ser hospedado na Zona PHI enquanto receber
esses inputs. Não persistimos narrativa, não criamos referência PHI fictícia e não
criamos regra clínica/financeira nova. As outras 37 tarefas seguem dependentes dos
respectivos contratos/formulários, sem fallback de variáveis abertas.

Ainda obrigatórios: adaptadores autoritativos com pins/freshness verificáveis,
classificação e custódia por formulário, prova conjunta D5/D6 contra CIB real,
D7 permissões e migração dos callers,
composição BFF e autorização de documentos/recibos. Nenhuma rota BFF foi ativada.
Esta fundação não prova egress, credenciais reais, REST protegido, atomicidade,
Cognito live, jornadas completas, deploy ou prontidão de produção.

Verificação focal:

```sh
uv sync --frozen --extra dev
.venv/bin/python -m pytest tests/unit/gateway/human/test_gateway.py -vv
.venv/bin/ruff check src/maezo/gateway/human tests/unit/gateway/human
.venv/bin/mypy src/maezo/gateway/human
```

## Admissão, entrega e leitura D6

`projection.py` compara os pins e converte revisões Python estritas em strings
decimais canônicas D5, inclusive além de 4.300 dígitos, sem mudar limites globais.
`EvidenceReferenceSource` é uma dependência confiável obrigatória: a referência
de evidência não existe no snapshot D4 e não pode ser inventada a partir do digest.
Essa fonte resolve a referência já publicada no engine, com mesma revisão/digest.

`PostgresHumanOutbox` recebe pool tenant-bound da composição. Em cada transação
aplica `SET LOCAL search_path`, adquire o advisory lock original de auditoria antes
dos locks próprios e enlista `PostgresAuditSink.emit_once_on` na mesma conexão.
O ACK `PendingAdmission` é construído após commit confirmado. Seu `committed_at`
é a observação posterior do commit, não um timestamp fabricado do PostgreSQL.
Falha ou resultado incerto de commit não produz ACK; a mesma identidade reconcilia.

A migração 0013 adiciona `human_command_outbox` imutável e
`human_command_delivery` para lease/fence/resultado. Nenhuma tabela A2A ou WhatsApp
é reaproveitada. Payload, principal, workload, digest e referências não mudam em
retry; UPDATE/DELETE/TRUNCATE do comando e alterações de resultado terminal são
recusados. Não há TTL, retenção ou expurgo automático novo. A matriz DPO/segurança
deve definir eventual limpeza coordenada de payload, receipts e claims de auditoria.
O downgrade é uma operação destrutiva explícita, não um mecanismo de compensação.

`HumanCommandRelay` assume lease durável e encerra a TX antes do HTTP. Consulta
primeiro o receipt autenticado do mesmo tenant/task/command/digest/principal/workload;
somente `RECEIPT_NOT_FOUND` autenticado permite retry do payload idêntico. O
`MTLSHumanEngineTransport` exige HTTPS com CA/hostname/certificado cliente, não
segue redirects nem proxies de ambiente e limita resposta/envelope ao perfil D5.
`HumanTLSIdentity` e o signer ficam exclusivamente neste gateway. Os intervalos
de lease, retry, polling, timeout e envelope são configurações operacionais
positivas explícitas, sem defaults que inventem prazo regulatório ou retenção.

O relay revalida lease antes das chamadas e recusa a escrita de worker vencido.
Uma chamada de rede já em curso pode sobreviver à lease; o comando imutável e
a idempotência transacional D5 impedem duplicação do efeito. Somente receipt
autenticado validado e auditado vira `committed`. Resultado técnico 409 conhecido
vira `conflict` auditado, sem campos de execução. Falha no audit/mark mantém
reconciliação pendente; não afirma rollback de um efeito já commitado no engine.

`HumanGateway.read_receipt` exige `BoundReceiptPorts`: store com identidade exata
e `ReceiptResourceAuthority` atual por recurso (papel/vínculo/consentimento). A
autorização pode legitimamente permitir histórico após conclusão da tarefa; a
posse dos IDs e o grant histórico não bastam. A sessão/membership e a validade da
autoridade são conferidas após o último I/O. A auditoria ligada é recalculada com
o algoritmo existente; o resultado inclui digest dos bytes do receipt.

`PublicReceipt` tem schema explícito `human-public-receipt.v1` e revisões decimais
para o browser. É separado de `PendingAdmission`, `EngineReceipt` e do DTO D3
`HumanCommandReceipt`: D5 não fornece `engine_commit_ref`, e D6 não o inventa.
A reconciliação versionada desse contrato D3 permanece necessária. O timestamp
exposto é `engine_recorded_at`, não um instante exato de commit alegado.
Seu epoch deve ser uma string decimal canônica não negativa e representável em
UTC; a projeção pública preserva esse UTC. Não se exige ordenação desse metadado
contra o relógio independente do gateway. Essa comparação de clocks rejeitava
recibos válidos já commitados (D6-RELAY-EIR-01), inclusive na releitura durável.
Validades de envelope, chave, sessão, membership, evidência, autoridade e lease
continuam sendo controles próprios de autorização/entrega, sem tolerância nova.

Os dublês HTTP existem apenas nos testes unitários. Os testes em
`tests/integration/gateway/test_human_outbox_live_pg.py` e
`test_human_outbox_migration_live_pg.py` exercitam PostgreSQL real, com recibos
como entradas tipadas de storage, sem engine simulado. Usam o resolver canônico
de banco de testes (`MAEZO_TEST_DATABASE_URL` ou compose/`MAEZO_PG_HOST_PORT`).
O ROOT executa essa lane serialmente; ela não prova uma jornada HTTP/CIB real.
O plugin Java ainda recusa todos os bindings reais, inclusive claim/release;
o fixture sintético D5 não foi ativado na produção por este pacote.

### Q2 concrete read adapters

`engine_reads.py` composes the exact Q1 catalog/query/task/authority/disclosure ports
with `PortalReadClient` and `AeadQueueCursorCustody`. `EngineReadComposition.build`
allocates a fresh private bundle per BFF request; `create_app` accepts this explicit
composition and rejects an ambiguous simultaneous service factory. Existing Q1
routes, public records, final response guard, command credentials and admission
ports are unchanged. Read and publication signing capabilities are separate from
those existing command ports and never borrow their signers.

Supply qualified `ReadCredentialProvider`, `ReadDeploymentAdmission` and
`QueueCursorKeyProvider` implementations through gateway composition. Each private
bundle owns a random context, its immutable task/authority bytes and CT/CA chain,
and no more than 101 row slots. Reserve occurs before await. Equal Q1 revalidated
copies match canonical bytes; duplicate, foreign, changed, incomplete or failed
chains poison the bundle. No receipt/context/key material enters the public DTO or
cursor. Supply one reusable HTTP transport pool to the explicit composition and
borrow it in every request client. The application lifespan closes that pool through
the composition; closing a request client cannot close another request's pool.
Clients have separate cookie-free contexts and every stream is bounded/closed before
a port returns. Do not reuse a bundle between requests.

`queue_cursor.py` uses the pinned cryptography AESGCMSIV implementation with a
provisioned 256-bit key capability, random 12-byte nonce, scoped 16-byte tag, strict
encoding and complete binding/context AAD. A provisional token retains input and
key/admission limits. `finalize` performs no I/O; it authenticates and re-encrypts
with the exact shorter final deadline. A later resolve enforces the sealed deadline.
There is no local key generator, TTL default, stored permission boolean or no-op
metadata finalizer in production code.

`read_publisher.py` consumes source-owned snapshots. Membership reads the actual
uncached `PostgresIdentityStore` port under a separately qualified freeze/commit
handshake and preserves tuple identity, revision and review expiry. The publisher
keeps one exact uncertain publication for reconciliation; it neither refreshes
facts nor advances CAS after a lost acknowledgement. Only a matching native commit
receipt releases upstream acknowledgement. Dedicated read-only DB identity and
invalidation handshake qualification are required; the existing independent
identity put/delete methods do not supply them.

Concrete resource policy/consent/full-projection classification, committed deployment
catalog authority and PAGTO evidence-content providers are external gaps. Their
closed interfaces, native receiver, transport, CAS and rejection paths are built;
no canned positive provider is installed. Native read capability and native-only
HMAC key provisioning also remain unbound until separately qualified. Missing
providers refuse activation and do not constitute a delivered live queue.

Run the five new `tests/unit/portal/test_{read_engine_profile,read_engine_transport,
engine_queue_adapters,queue_cursor_custody,read_authority_publisher}.py` files with
the existing qualified Python environment, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src`
and pytest cache disabled. These synthetic controls prove local protocol/custody
behavior only. Actual engine, PostgreSQL, packaged mTLS and browser evidence are
separate ROOT gates; no skipped or merely compiled integration test counts as PASS.
