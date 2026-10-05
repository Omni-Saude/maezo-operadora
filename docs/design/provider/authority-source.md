# Fonte contratual operadora — D1 / ADR-0063

Estado: construção sem factory/binding produtivo. Fonte jurídica/profissional/contratual externa permanece ausente até evidência real. O módulo não cria credenciais, designações, signatures, schema, roles ou grants em runtime. Código deriva do contrato OP12 admitido no ADR0063 e de PW0/PW2; reusa `ContractReadScope`, `ContractSnapshot` e `ContractSnapshotProof` de `gateway/capabilities/contract_authority.py`, sem DTO/core paralelos.

## Contrato executável

`PostgresProviderAuthoritySource(engine, descriptor, publisher=False)` implementa reader `resolve(scope, timeout_seconds=...)` e verifier `verify_snapshot(scope,snapshot)`/`check_current(scope,proof)`. Fonte resolvedora independente relê snapshot/publication/head/proof/binding no DB; não valida resultado simplesmente ecoando o caller. Scope vincula tenant, entidade, prestador, principal, task, purpose/policy, source authority e data classification. Proof liga bytes hash, snapshot key tenant+OP12+instrumento+business revision, receipt/publication/currentness e ceiling de validade. Projection por finalidade pertence ao OP12 compartilhado; snapshot inteiro não varia por purpose nem oferece preço inline entre prestadores.

`receive(snapshot)` retorna **CustodyReceipt(state=received)** depois de commit. Campos do snapshot recebido continuam declarações não admitidas: até referências de receipt/publication/admission_state do documento são afirmações sem autoridade. Mesmo key/bytes retorna mesma custódia; bytes diferentes recusam. Este método nunca confirma instrumento vigente, representação, assinatura ou efeito habilitado.

`publish_validated(scope, business_revision, proof_ref, expected_head_revision)` só aceita prova **resolvida no DB** instalada por validation source independente. Reader/publisher não podem INSERT/UPDATE proof/binding. A prova liga digest/evidence/mandato/purpose/currentness e validity; fonte validation deve resolver atos/assinaturas/representação verdadeiros antes de gravá-la. Este componente não implementa nem simula o emissor profissional. Um papel SQL e um UUID não demonstram sozinho que um contrato de terceiro foi assinado.

A função `publish_validated(jsonb,text,text,bigint)` é SECURITY DEFINER, criada pelo owner de instalação distinto, com search_path fixado exclusivamente no schema próprio. Owner bloqueia proof/binding/head e faz CAS/publication atômicos, sem conceder ao publisher UPDATE necessário para FOR SHARE nos registros de autoridade. Grants do publisher são INSERT evidence, SELECT das relações e EXECUTE dessa função; não há DML direto em publication/head. Leitor conserva apenas SELECT; sua observação é um JOIN consistente e currentness é relido antes da divulgação pelo contrato OP12/core. O leitor não recebe UPDATE disfarçado para bloquear rows.

## Schema e qualificação

`provider-authority-schema.sql` é template de instalação DBA explícita em schema da fonte, com owner próprio; **não** é migration Alembic aplicada pelo runtime. A cadeia tenant permanece no head0017 e a reserva0018 não foi consumida. ADR0060 permanece intacto: nenhuma relação de negócio em maezo_native/mzo, nenhum ajuste do path do CIB ou privilégio native.

SourceDescriptor fechado vem da instalação verificada: database/schema OIDs, owner/publisher/validator/reader distintos, relation pins completos, OIDs/digests de immutable/history/publication functions, source contract publication, tenant/entity/source e validade. Hash de descriptor usa JSON estável com números OID explícitos do schema de instalação; operação/subject wire continua o perfil canônico existente que proíbe números. Descriptor não pode ser selecionado pela chamada de operação; parser revalida forged models e alterações posteriores do mapa de pins.

Qualifier consulta PostgreSQL real em cada transação: produto/session/current user, TLS, database OID, schema/relation owner e OIDs, relkind, RLS, privilégios de tabela/coluna, PUBLIC/ACLs inesperadas, role memberships/elevated flags, CREATE/TEMP, funções/digests/security-definer/search_path/EXECUTE, triggers e seus enabled types. Role grant, function body, objeto recriado com mesmo nome ou schema errado recusam. Não confiar apenas em enum/manifesto. TEMP é revogado no DB da fonte; função não executa sob pg_temp criado pelo runtime.

Relações de fonte: `evidence` imutável (bytes/revision/refs recebidos), `source_binding` instalado pelo validador independente, `authority_proof` de validação verdadeira, `publication` imutável com receipt e `instrument_head` CAS. Histórico de binding/proof não permite editar bytes/digest/ref/validity nem reativar estado revogado; revogação é monotônica, novo mandato/prova requer identidade nova. Expiry/invalid/revoked restringem novas leituras/publicações; rows/receipts históricos permanecem preservados.

## Composição e limites

Não há factory/root, source de assinatura/mandato nem principal real instalados neste lote. ROOT precisa receber descriptor/control de deployment, qualificar validation source, signed task/Card/action/policy enforcing e audit-before-effect por gateway. Factory deve fornecer os mesmos server AdmissionBindings para reader/source/admission; nenhuma flag admite efeitos. Ingestão administrativa usa actor/session/PEP reais e não recebe claims de grants/validated pelo browser. A escrita da fonte continua por publisher instalado; não entregar sua credencial ao consumidor de operação.

Este lote implementa durabilidade de snapshot/receipt/CAS da fonte; não implementa D2 de outbox de negócio, ingress/resume ou qualifier completo da jornada. Audit intent de core não substitui source receipt. Registrar ingestão/publicação pelo audit boundary admitido é requisito da futura composição; este módulo isolado não atesta source effect produtivo ou audit-before-effect end-to-end. Falta de material externo restringe somente o efeito correspondente e mantém a construção dos controles concluível.

OP12 usa o mesmo core para CONTAS/PAGTO, e PW5 ainda exige JR1/JR2/JR3 operacionais com mesmo código/versão. Teste que chama apenas esse adapter não fecha esses gates. Contrato externo ausente permanece SOURCE_UNAVAILABLE/unknown pelo OP12; nenhuma receipt fictícia é emitida para satisfazer um DTO de sucesso.

## Verificação

Unit negativos cobrem namespaces proibidos, injection, roles confladas, pins incompletos/duplicados, forged/mutated descriptors, digest/JSON duplicado, purpose combinado e expiry; não qualificam PostgreSQL. Integração marcada integration em `test_provider_authority_source_live_pg.py` cria apenas seu PostgreSQL16 TestOnly descartável em loopback porta aleatória, TLS certificado próprio verificado, roles e schema separados. Docker objects têm label/nome exclusivos e somente o próprio container é removido no finally; infraestrutura alheia não é usada. Não há skip/xfail/fallback nem nova env de credencial exigida.

Atos/grants de fixture são explicitamente sintéticos e validam mecânica de software. Não contam como assinatura, manifestação profissional, produtor ou deployment de produção. Oito casos vivos: custódia sem authority, TLS/roles/CAS/replay concorrente e dois purposes, ausência de DML runtime/publisher e immutable history, revoke/cross actor, conflicting bytes/forged result, ACL drift, OID replacement e prova de evidence errada com zero publication. Execução desses testes pertence à lane ROOT/CI; autoria não iniciou serviços ou DB.

Nenhum PASS operacional antes dessa integração real, gate independente, fontes reais quando necessárias e checks do SHA de entrega. Preserve o bundle/provider audit histórico e seus pareceres.

## Addendum — terceiro reparo do codec de trigger PostgreSQL

Reparador: pw0_authority_architect, distinto do autor D1 engine_readiness_specialist e dos gatekeepers. Base runtime `7f82b2b2cd67b304265d13c0c8142ad4884cd064`. A lane ROOT entrou nos oito casos reais, com infraestrutura/TLS disponíveis, e recusou a fonte na qualificação. Diagnostic v2 localizou linha275; metadata PostgreSQL/asyncpg confirmou `tgenabled` do tipo interno `"char"` retornando bytes `b'O'`, enquanto o guard contratual exige texto `O`/`A`. A primeira tentativa de diagnóstico com TypeError foi preservada e não foi gate.

As três projeções de pg_trigger agora usam `tgenabled::text AS tgenabled` na própria SQL, convertendo o tipo do catálogo para a representação de texto contratada. Nenhum decode permissivo/fallback foi adicionado ao Python. Os enums permitidos continuam exatamente O/A; disabled D, replica-only R, desconhecido/ausente e bitmask diferente continuam recusados. Quantidade de triggers, OIDs, funções, privilégios, histórico e source snapshot v1 permanecem os mesmos.

UNIT modela somente metadata selecionada do catálogo, com o codec observado no diagnóstico real: quatro positivos O/A em reader/publisher ficaram RED antes do cast. Depois do reparo atravessam todos os seis guards; negativos atingem explicitamente cada família de trigger alterada para disabled/replica/unknown e preservam os bitmasks 19/42/58. Metadata UNIT não qualifica banco/mandato/receipt real. Os oito testes de integração originais não foram alterados; ROOT precisa reproduzi-los integralmente no novo candidato e obter delta do autor original e gates independentes. Não inferir oito PASS a partir da correção tipada.

Beforeimages, runtime RED preservado por hashes/referências e comandos/codecs/red-green ficam em `evidence/d1-trigger-codec-third-repair/`. Sem runtime DDL, source snapshot/business changes, novo grant, mudança de schema/manifest/v1, serviço, DB ou Git pelo reparador.
