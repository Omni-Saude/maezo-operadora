# PW1-B — administração humana da membership prestador

Estado: construção de contrato/serviço/fonte administrativa para revisão independente; nenhum principal, tenant, IdP, provisionamento real ou acesso ativado qualificado. Base: ADR-0063 e PROVIDER-EXEC-V1, bundle a7c9d3722c2ba9f671b637c9740914a62aa3067ea46f508470cedd5e68fc593c.

## Contrato e autoridade

`ProviderMembershipCommand` é fechado: schema_version, command_id, operation grant/revoke, provider_ref, expected_revision e MembershipRecord. Cada registro é audience provider com único subject_binding provider exato, memberships/roles explícitos e revisão esperada+1. Reparse bloqueia model_copy/model_construct inconsistentes. Nenhuma claim/group do Cognito ou campo de saída vira grant. Registros staff são recusados; o plano staff e seu source/assignment/publicação permanecem nos mecanismos canônicos.

`AdministrationBinding` é composição servidor de ator humano e sessão autenticados, tenant, autoridade/política e ceiling original. Seu DTO não autentica nada. O serviço exige PEP do mesmo tenant e ação `provider_membership_administration` conhecida. A action ainda não está publicada na matriz global: unknown recusa. Proposal para ROOT é ação L1 humana sob contrato admin, sem grant de agente nem relaxamento de hard. PEP REQUIRE_HUMAN não dispensa prova: a fonte exige aprovação humana atual, vinculada ao digest inteiro do comando, ator/sessão/política/instalação e mandato publicados independentemente.

O digest deste contrato administrativo é SHA256 de JSON UTF-8 com keys sortidas, separadores compactos, ensure_ascii=False/allow_nan=False e integers fechados de revisão. `command_bytes` define um único preimage. Não é envelope humano v1 number-free nem wire AMH: não reinterpreta o canonicalize do perfil humano.

## Fonte protegida e efeito limitado

Fonte/schema dedicado `portal_provider_admin` proposto; nome real e pins OID/owner/roles/hash provêm de instalação DBA qualificada. Artefato SQL é template de instalação, nunca executado pelo runtime. Não usa `maezo_native`, `portal_auth`, `cibseven` ou public como storage genérico. Owner NOLOGIN, authority publisher e application writer são distintos; writer não pode herdar owner/publisher e não recebe SELECT/DML/CREATE nas relações. PUBLIC não recebe permissões. O qualifier reabre banco/schema/relações/owner/ACL/session_user/current_user e função SECURITY DEFINER pelo OID/hash exatos; drift recusa.

Autoridade humana e relação IdP↔principal↔prestador são publicações independentes da fonte, nunca inseridas pela application credential. Seus estados pending/received/validated não autorizam registrar ato. Somente fonte enabled com evidência própria/currentness autoriza grant; expiração/revogação é reaberta dentro da transação. O publisher deve efetivamente validar evidência/assinatura/mandato antes da publicação: possuir a role não é prova de que um ato externo ocorreu. Este pacote não implementa connector IdP ou ratificação profissional nem presume essas publicações reais existentes.

Writer recebe somente EXECUTE da função pinada. A função rederiva shape/digest, trava autoridade/relação, valida tenant/ator/session/purpose político/provider/revisão/roles/grupos/ceilings e serialize por command/principal/issuer+subject. CAS e audit-before-write, head do ato e receipt/readback pertencem à mesma transação. Cinco replays concorrentes do mesmo comando devem retornar um único receipt; comando com bytes diferentes conflita. Não há retry após resultado incerto: conservar command_id e reconciliar replay com source atual.

`administrative_head` é o estado do ato de administração aprovado, **não outro identity feed**. Receipt `proof_state=validated` e `application_status=pending` comprova somente esse ato durável. act_ref/audit_ref são gerados para a transação, persistidos e lidos de volta; referências de autoridade/relação vêm da fonte atual. Nenhuma emissão local de UUID ou parser basta como receipt factual.

Revogar exige novo ato humano atual e CAS contra o mesmo principal/IdP/prestador; uma relação já retirada/expirada não impede desabilitar o acesso anteriormente registrado. Revoke preserva payload de roles/subject/issuer e não estende review ceiling; não cria principal ou grant inexistente. Grant exige relação válida/current/sem revogação. Todas as mudanças de fonte são serializadas pelos locks na função; verificações após waits preservam os ceilings originais.

## Delta sequencial obrigatório para ROOT

Nenhum hot path foi alterado neste pacote. Para completar a fatia funcional, ROOT deverá integrar, sob nova revisão dos seams:

1. Composição de binding de admin autenticado e publicação independente das autoridades/relações e da action política, com instalação/descriptor qualificados. Sem source/admin real, o efeito continua recusado.
2. Aplicação do ato validado ao feed `portal_memberships` pelo lifecycle AUTH-SL1 existente `prepare_membership`→revoke/ACK→`apply_change`→readback, preservando triggers/dependências. Staff assignment administration não é alias para provider. Não escrever diretamente nem criar segunda tabela de identity/session.
3. Publicação específica do plano provider no membership_publication_job/read-provider/native catálogo conforme contrato gateado, sem ampliar o filtro staff indiscriminadamente. Registrar application/native receipt factual antes de afirmar enabled/acesso provisionado.
4. Revogação de membership/sessão/read-grants e atualidade no retorno; state enabled não decorre de administrative receipt validado. Atualização do catálogo/policies/produção e migração ordinal pertencem a ROOT, não a este autor.

## Verificação

Unit cobre DTOs inconsistentes/staff, bindings cross-tenant, policy desconhecida, ceiling vencido, source ausente, receipt divergente e exceção sanitizada. São doubles UNIT explicitamente, sem prova de autoridade externa.

`test_provider_membership_administration_live_pg.py` instala fonte sintética em schema/roles UUID somente no PostgreSQL de teste; nenhum serviço é iniciado. Valida CAS/replay concorrente/revocation, fonte ausente/received/expired/revoked/session/provider errados, ausência de DML/SELECT direto (SQLSTATE 42501), função recusando fonte ausente e ACL/hash drift. ROOT serializa essa execução e preserva resultados. Receipt/admin sintético não é principal ou provisionamento real. Contrato e backend ainda exigem gate independente de autor distinto antes de integração.
