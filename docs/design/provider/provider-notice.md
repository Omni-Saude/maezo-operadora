# OP09 prestador — aviso protegido e acknowledgement explícito

Construção PW1-C sob ADR-0063. O núcleo `gateway/capabilities` e seus contratos OP09 são reutilizados. Esta fatia não altera contratos de comunicação staff/beneficiário existentes, workers, core compartilhado, SQL histórico, produção ROOT ou `maezo_native`. Não cria uma persona, SDK, servidor de canal externo ou identidade humana para o workload.

## Contrato e significado

`ProviderNoticeSource` implementa `CapabilitySourcePort` de `notice.prepare_or_send`, exclusivamente no binding explícito `provider-capabilities.internal.v1`. Os seis campos de `NoticeIntent` permanecem: notice_ref, notice_class, authorized_content_ref, recipient_authority_ref, confirmed_channel_ref e delivery_policy_ref. A origem de principal/task/tenant/legal_entity/purpose/policy/classification é `AdmissionBinding`, não o payload. A fonte confere o digest de envelope+request inteiro contra sua publicação independente e o papel PostgreSQL real contra o scope.

Retorno preserva `DeliveryEvidenceReceipt`: pending ou delivered, provider_delivery_receipt_ref, attempt_revision e metadata_publication_receipt_ref. Preparação grava um fato de disponibilidade de metadata, **pending**. GET, `inbox_available`, HTTP 2xx, mensagem de broker e WAMID não criam delivered. Somente acknowledgement explícito do prestador autenticado, exigido pela política e vinculado ao aviso/revisão/digest exatos, cria esse estado e um receipt da fonte.

`CapabilityOutcome.result` ou `succeeded` indicam sucesso técnico da operação, não cumprimento de obrigação. `require_provider_delivery()` rejeita pending/attempted/failed/unknown e delivered sem receipt; é um teste **necessário**, não autoridade de domínio nem currentness por si só. O consumidor deve obter o resultado pela fonte/admissão qualificada e reabrir a fonte/política antes de efeito, fechamento de obrigação, resume DocReq ou timer dependente. Nenhum desses consumidores foi religado nesta autoria. O consumidor administrativo existente somente projeta `technical_status` e outcome; nenhum caller atual da nova fonte afirma notificado ou libera obrigação.

Fonte suportada: `protected_portal_ack`. É um canal complementar concreto, sem substituição do hipermedium TISS, do aviso a beneficiário/ANS ou de manifestação profissional. Não existe `tiss_transmitido` na fonte ou no receipt novo. Suficiência desse canal para cada classe obrigatória requer política/contrato publicado pelo dono; a construção não o ratifica automaticamente. O source grant precisa provar o conteúdo autorizado, relação/recurso/finalidade e canal, inclusive concessão de leitura protegida do corpo. Não tratar disponibilização da referência como entrega ou como grant de conteúdo.

## Wire público aditivo

- `GET /api/v1/portal/provider-notices/{notice_ref}` → `ProviderNoticeSummary` v1: notice_ref, notice_revision, body_ref, content_digest e receipt. Sem texto livre, nonce, ciphertext ou chave. GET não grava acknowledgement.
- `POST /api/v1/portal/provider-notices/{notice_ref}/acknowledgements` → `ProviderNoticeReceipt` v1. Body `ProviderNoticeAcknowledgement` v1: command_id, notice_revision, content_digest. Não aceita actor, principal, provider, audience, source proof, receipt ou body.
- notice/body/command/receipt usam `ResourceRef` existente; notice_revision preserva o `OpaqueRef` de revisão do core. Antes da escrita o adapter confere a forma dos notice/body refs; revision não é transformada em ID de recurso ou inteiro arbitrário.
- Receipt: notice_ref, notice_revision, delivery_status pending/delivered, metadata_publication_receipt_ref e provider_delivery_receipt_ref opcional, obrigatório exatamente em delivered.

O router existente, `ProductRoute`, fornece parsing limitado, extra fields rejeitados, resposta sanitizada e cache-control no-store. Slot `provider_notice_service_factory` ausente → dependency_unavailable. As props dos wire antigos continuam idênticas; nenhuma rota antiga se torna caminho alternativo.

## Fonte própria e efeitos

Novo template `communications/provider-notice-schema.sql` é instalado em schema dedicado (`portal_provider_notice` como exemplo; descriptor real é independente). Não é runtime DDL. Owner NOLOGIN, authority-validator, producer e recipient são quatro papéis distintos e sem herança/elevated flags/CREATE/TEMP. Papéis aplicativos têm EXECUTE apenas nas funções necessárias; não têm SELECT/DML nas tabelas de negócio/autoridade. PUBLIC não recebe privilégios.

`ProviderNoticeInstallation` fecha tenant/environment, database/schema OIDs, roles, instalação e fonte de identidade, prazo, relação de conteúdo e seu owner, função de lock de sessão e owner/hash, cinco relações e seis funções com OID/hash exatos. Constructor não é qualificação. Cada transação reabre TLS, session_user/current_user, objeto/owner/ACL/colunas/triggers, pins e function bytes. Descriptor trocado depois da construção, PUBLIC EXECUTE, acesso direto de aplicativo, trigger desabilitado/trocado, owner conflado ou hash da identidade divergente recusam o efeito.

As relações:

| Relação | Propósito |
|---|---|
| authority | Publicação independente de scope/intent/actor/source policy/recipient/provider/body/digest/revision/receipt e ceilings. received→validated→enabled; somente enabled admite. Revoke monotônico; bytes não podem ser reescritos; deadline nunca aumenta. Nova revisão exige nova identidade de aviso/publicação. |
| notice | Journal imutável de comando original, request digest, source receipt, revision e metadata receipt. |
| acknowledgement | Ato imutável do recipient real: comando/digest, principal/session/revisão de membership, revisão/content digest, delivery receipt e instante. |
| audit | Intent/fato de fonte sem texto PHI, gravado antes da escrita do journal na mesma transação. |
| outbox | notice_available/notice_acknowledged com referências/digests/fatos; a linha outbox não prova consumo de broker nem entrega externa. |

Source validator recebe acesso somente à publicação authority. Deve verificar verdadeiramente as evidências/representação/política/publicação do canal antes de publicar enabled; possuir uma role ou preencher source_receipt_ref não prova ato externo. O pacote não implanta esse produtor externo, não registra grants em startup e não gera aprovações reais. Sua ausência restringe a preparação/ack que depende dele; código/controles/testes podem ser construídos.

`prepare_notice` trava authority e metadata do corpo protegido, serializa por tenant/environment/notice e faz replay/CAS do mesmo command/digest/revision/source. Receipt, audit e outbox estão no mesmo commit. Timeout não vira failed/delivered nem libera um retry novo: recuperar pelo comando original. Fonte revogada/expirada mantém histórico, mas recusa novo efeito/disclosure. `acknowledge_notice` deriva principal/session do lock de identidade source-owned, nunca de JSON de ator; confere provider subject binding, revisão/session corrente, notice/body/policy/currentness e journal. Ack concorrente/restart retorna o mesmo receipt; comando conflitante recusa. Nenhum `_pending` local guarda autoridade ou resultado incerto neste fluxo novo.

Corpo permanece na fronteira protegida existente. A fonte Notice recebe somente SELECT das colunas metadata necessárias e UPDATE de uma coluna metadata exclusivamente para FOR SHARE, concedidos pelo dono da fonte protegida e qualificados pelo deployment. Não recebe ciphertext/nonce/key. Nenhum GRANT cross-owner é executado por runtime ou pela instalação do novo owner. Content ingress/read continuem pelos gateways PHI/authority qualificados já existentes; não criar sessão humana para o producer obter acesso.

## Identidade, currentness e AUTH-SL1

`ProviderNoticeRecipientService` usa `HumanSessionResolver` real, audience provider, Origin/CSRF da sessão, e source recipient específico. Na mesma transação, `PostgresCommunicationAdmission._identity` e o lock source-owned revalidam os registros originais. O source retorna `NoticeRecipientObservation` **interno** com ceiling; nunca é recebido do browser nem projetado no wire. Preservam-se ceilings iniciais de sessão/membership/fonte/instalação. Após o último await de resolução da sessão, reabre-se source+identidade e confere-se o receipt/summary antes da serialização final, sem novo await depois dela. Revogação após ack commit impede disclosure e conserva o ato confirmado.

Há uma dependência concreta pendente: `portal_communication.lock_session(text)` atualmente shipped lê `public.portal_sessions/public.portal_memberships`, enquanto a identidade atual pode ser governada por AUTH-SL1. A fábrica **não** permite usar uma cópia pública como substituto silencioso. `compose_provider_notices` exige `_auth_composition` canônica instalada e confere source_ref, installation_ref, owner, database OID, tenant e ceiling contra o binding real. O owner AUTH deve publicar/instalar helper de lock que derive a sessão/membership canônica e suas fences com o mesmo retorno contratado, com OID/hash/installation proof independente. O novo owner recebe EXECUTE explícito. Qualificar somente o nome do helper antigo não satisfaz essa obrigação. Nenhuma alteração em AUTH-SL1/kernel/source schema pertence a esta autoria.

## Integração ROOT exata, após gate independente

1. Source owners instalam schema/roles/funções e grants cross-owner estreitos; entregam descriptor e recibos reais. Publicador qualificado cria authority de conteúdo/recipient/channel/policy com prova, sem valor inventado. Material/réplicas/checks devem nomear o mesmo objeto e pins.
2. Owner AUTH publica o helper source-owned descrito acima e evidencia fences/session/member atuais; ROOT qualifica os bytes e a fronteira. Sem isso, production factory recusa.
3. ROOT fornece producer/recipient engines, `CapabilityAdmission` OP09 enforcing com signed task/Card/policy/audit+source-result verifier reais e `PostgresCommunicationAdmission` do mesmo resolver/store/engine. Chama `compose_provider_notices(...)`; nenhum material/factory é selecionado pelo caller.
4. ROOT combina **sources/admissions** do runtime com os demais bindings da mesma `CapabilityService`, preservando os outros OPs, e coloca `runtime.recipient_factory` em `application.state.provider_notice_service_factory` durante lifespan. Remover slot e fechar recursos ao sair. Não substituir o service compartilhado inteiro por um OP09-only binding nem deslocar factories staff existentes.
5. Root wiring de conteúdo PHI e source resource/recipient permissions precisa funcionar para o mesmo body_ref/digest. Metadata sem leitura do corpo não qualifica canal operacional. Frontend consome protected content autorizado e submete ack explícito; o comando confirma o recebimento do conteúdo exato, não uma assinatura clínica/financeira.
6. Só após gate funcional/review/source proof, ligar consumidores CRED/RECURSO/CONTAS/DocReq com receipt guard + currentness; preservar falta de fonte e pending/unknown como estados, sem boolean notificado/tiss_transmitido/timer/release espúrio. TISS requer adapter/pin/receipt próprio e continua separado.

## Validação e limites

Unit/ASGI usam explicitamente LocalTestIdentityStore, TestOnlyJournal e IdP RSA sintético. Exercitam GET pending, ack explícito/replay, Origin/CSRF, actor extra, revisão/digest errados, revogação antes/depois de commit, reply perdido, fonte ausente, oldwire e PHI perimeter. Não provam ato/identidade/entrega de produção.

Seis testes `integration` PostgreSQL reais criam DB e roles UUID próprios num servidor loopback já fornecido por ROOT/CI, com TLS de CA explícita verificada. Não iniciam serviço/container, não editam DB compartilhado, não têm skip/xfail/fallback e não usam recibos de fixture como atos externos. Cobrem replay/concurrency, restart/readback, source expiry/revoke race, actor forgery via SQL, ACL PUBLIC/hash drift e ausência de DML direto. O helper estável de identidade e registros desse DB são **TestOnly**; não qualificam helper AUTH-SL1 de produção. Execução viva dos testes PG e CIB pertence ao ROOT, não foi realizada pela autoria.

Construção/validação focal não equivalem a PW1 funcional, engine acceptance ou ativação. Pendem provas de source publication, helper/instalação canônica, corpo/recipient read de produção, root wiring e atos reais do recipient. Todos os requisitos permanecem em escopo específico; nenhuma pendência externa impede terminar o software autorizado e seus controles.


## Addendum — reparo terceiro PW1C-SEC-F01/F02

Qualifier impõe ACL exata sem GRANT OPTION nas cinco tabelas, schema e seis funções próprios. Validator conserva somente SELECT/INSERT/UPDATE de authority; notice/ack/audit/outbox ficam exclusivamente no owner. Aplicativos mantêm apenas EXECUTE por função no papel declarado (prepare producer; acknowledge/inspect recipient; helpers/guards owner-only). Column ACL não owner, PUBLIC, terceiro, cross-role ou privilégios destrutivos recusam. Schema dos três atores possui apenas USAGE, sem delegação. Nada amplia privilégios canônicos ou inventa perfil LOGIN não previsto; owner NOLOGIN e restrições existentes permanecem.

Statement TRUNCATE guarda as cinco relações usando immutable_history já existente, sem nova função/auth helper. Qualificação confere row guards e truncate guards habilitados, função/OID/tipo, ausência de WHEN/args e contagem completa de triggers; apagar/reinserir identidade não é mecanismo de revisão. Histórico permanece mesmo se DBA der TRUNCATE indevidamente ao validator.

Digest de ProviderNoticeInstallation usa serialização JSON estável própria de metadata, com OIDs integer preservados e allow_nan=false. A causa do constructor ProfileError foi separada do wire human-envelope/operation: esse perfil canônico continua recusando números e ack conserva seu hash original. Não foram transformados OIDs em strings nem modificados o canonicalizer compartilhado, helper de identidade, factory ou instalação externa.

RED do constructor real AsyncEngine sem conexão e RED de quatro matrizes adversas no qualifier UNIT foram preservados. UNIT não é prova PostgreSQL; testes PG próprios acrescentam direct TRUNCATE, preservação dos IDs/receipts, destructive/table/column/schema/function grant drift. Execução e gates vivos seguem ROOT. Ausentes continuam producer de publicação/mandato/canal, leitura protegida concreta e wiring de source/identidade canonical admitido; este reparo mecânico não os fabrica nem qualifica efeitos produtivos.
