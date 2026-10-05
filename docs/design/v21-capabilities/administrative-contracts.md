# Contratos administrativos v2.1 — proposta W0

**Status: PROPOSED.** Base admitida: `e7b14522a4f70242504d2b152a57ee5269ee66e2`. Autoria no checkout isolado `codex/v21-capability-execution`; os bundles v2/v2.1 preservados continuam somente leitura. Este pacote torna contratos e decisões revisáveis. Não publica API de fornecedor, ratifica política/ADR, habilita ingressos ou qualifica produção.

O contrato legível acompanha [contracts.json](contracts.json), que conserva os onze request/result maps e o envelope de `plan.json.internal_operations`. [source-contracts.md](source-contracts.md) e [source-readiness.json](source-readiness.json) contêm a análise complementar do steward de fonte. As referências CA abaixo são IDs de rastreabilidade; nenhum XML, conteúdo clínico ou wire AMH foi copiado.

## Interface mínima compartilhada

O envelope fechado contém somente `schema_version`, `operation_name`, `tenant_ref`, `legal_entity_ref`, `journey_ref`, `correlation_ref`, `causation_ref`, `idempotency_key`, `expected_business_revision`, `source_authority_ref`, `policy_revision` e `data_classification`. A versão candidata de engenharia é `v21-capabilities.proposed.v1`; o nome identifica a proposta interna, sem declarar publicação externa. Requests/results permanecem fechados e distintos: campos desconhecidos e campos de saída na entrada são recusados; opcional significa ausência, sem booleano/zero fabricado.

Identidade, jornada, caso, contrato, matrícula, autorização, reserva e processo mantêm referências separadas. Prospect pode ter referência local de jornada; Maezo não cria `portable_subject_ref`, MPI nem vínculo AMH. O compositor exato de idempotência permanece pendente de ratificação W0; a proposta exige tenant/operação/objeto/revisão e hashing seguro, preservando o compositor AUTH existente.

| OP | Interface proposta | Consumidor/uso | Autoridade e gate |
|---|---|---|---|
| OP01 `access.resolve` | `ContextAccessIntent` → `ContextAccessResult` | `resolve_context` | CR-F1; purpose, source authority, tenant and current consent evidence |
| OP02 `offer.compose` | `OfferOptionsIntent` → `VersionedOfferOptions` | `present_options` | CR-F2; catalogue/version/availability, nonclinical commercial feature allowlist, expiry and explanatory evidence |
| OP03 `acceptance.record` | `AcceptanceCommand` → `AcceptanceEvidenceReceipt` | `collect_acceptance` | CR-F3; offer_ref+version, terms evidence, authenticated customer/delegation, legal authority |
| OP04 `enrollment.request` | `EnrollmentIntent` → `EnrollmentAuthorityReceipt` | `request_enrollment` | CR-F3; contract authority, prospect-to-subject linking and issuer receipt; source emits actual right-base |
| OP05 `reservation.command` | `ReservationCommand` → `ProviderReservationReceipt` | `request_reservation` | CR-F4; provider revision, atomic reservation/hold expiry, accepted offer, legal effect scope |
| OP06 `fulfillment.observe` | `FulfillmentObservation` → `VerifiedFulfillmentFact` | `track_delivery` | CR-F4; authoritative source/time/revision, fact versus ETA, no financial/clinical authority inferred |
| OP07 `case.open_or_update` | `ResolutionCaseIntent` → `ResolutionCaseStatus` | `open_case` | CR-F5; problem identity, requester scope, intake protocol when applicable, source case and non-resettable clock |
| OP08 `external_wait.settle` | `ExternalWaitIntent` → `ExternalWaitOutcome` | `await_authority/resume_authority` | CR-F5; correlation/version, expected producer, mutually exclusive completed/timeout, domain authority |
| OP09 `notice.prepare_or_send` | `NoticeIntent` → `DeliveryEvidenceReceipt` | `communicate` | CR-F6; mandatory/facultative class, authorized content/recipient, confirmed live channel and receipt semantics |
| OP10 `milestone.publish` | `VerifiedMilestoneIntent` → `MilestonePublicationReceipt` | `observe_milestone` | CR-F6; source decision/fact, revision, tenant, recipient, fact versus prediction; receipt not Kafka offset |
| OP11 `feedback.record` | `CustomerFeedbackIntent` → `FeedbackObservationReceipt` | `collect_feedback` | CR-F5; declared instrument/cohort/scale, no silence-as-resolution, no survey blocking money |

`DeclaredCaseKind`, `DeclaredCaseStatus` e `DeclaredMilestoneKind` não têm membros publicados neste pacote. O conjunto inicial vazio implica recusa no binding. Os resultados de planejamento de S4 não se tornam enums do DTO. Nenhum request/result recebe campos adicionais.

AW1 constrói o núcleo candidato de OP01/03/07/08/09/10. Os demais DTOs podem ser validados na mesma biblioteca, sem obrigar um provider/server por operação. Suporte compartilha OP01/07/08/09/10/11; a lista genérica W3 que continha OP03 é supersedida pelo v2.1. Uma contratação surgida em Suporte exige passagem explícita para JR1 admitida.

## Responsabilidades, canal, tarefas e conduta

Helena permanece na entrada do número único, executando triagem em cada mensagem. Saúde e risco psicossocial mantêm sua prioridade; saúde vence quando aparece junto de pedido de pessoa porque esse escalonamento também chega a uma pessoa com maior prioridade. Pedido humano interrompe a jornada administrativa em qualquer agente. A passagem ao Lucas usa referências administrativas mínimas tipadas e evidência da mensagem atual de origem server-side; não leva texto clínico livre. Um objeto de handoff construído não prova passagem atendida nem encaminhamento humano.

Lucas mantém o contrato atual `member-billing`. A proposta acrescenta o módulo administrativo separado para Compras/Suporte, com estado fechado e outputs proibidos ao caller. Nenhuma intenção administrativa entra no `assess` de cobrança nem reutiliza sua DMN como elegibilidade comercial. O fluxo de cobrança e sua retomada humana conservam os contratos atuais.

### Matriz candidata de channel/task/tool/action

**Todas as linhas são PROPOSED, sem binding admitido.** O único tool interno candidato é `gateway.capabilities.execute`, alvo de catálogo `src/maezo/gateway/capabilities/service.py::CapabilityService.execute`, com status `CANDIDATE_NOT_REGISTERED`. A composição usa o mesmo dispatcher e ports compartilhados, com `sources` e `admissions` server-side restritos à tarefa e à operação. Esta proposta não cria MCP/server, daemon ou HTTP por jornada. As actions administrativas abaixo são identificadores técnicos candidatos; nenhum nível de autonomia foi escolhido, nenhum grant/policy/registro foi publicado ou ratificado.

`WA` significa o turno do número único WhatsApp existente, após a triagem atual e a admissão AW0 do canal. Para Lucas, WA inclui o handoff tipado server-side recebido pelo módulo administrativo. `turno Helena` não é tarefa A2A nova: `accepted_task_types` continua vazio e o `task_ref` do turno autenticado ainda precisa de contrato/admissão explícitos. Portal/A2A exige admissão específica adicional; a matriz não abre esse canal por inferência. Finalidades na tabela são requisitos para referências publicadas pelo owner, sem novos enums de purpose.

| Agente | Task candidata | OP | Mode v2.1 | Canal | Purpose a publicar | Guard específico | Action candidata |
|---|---|---|---|---|---|---|---|
| helena | `turno Helena (sem task A2A nova)` | OP01 | `READ_REQUEST` | WA | identidade/roteamento mínimo da mensagem | CR-F1; somente contexto mínimo de ingresso; sem oferta/aceite/matrícula/agenda | `administrative_access_resolve` |
| helena | `turno Helena (sem task A2A nova)` | OP07 | `REQUEST` | WA | pedido humano explícito da mensagem | CR-F5; só intake de pedido humano; prioridade clínica e gates atuais preservados | `administrative_case_open_or_update` |
| lucas | `journey.compras.step` | OP01 | `READ_REQUEST` | WA | identidade/contexto administrativo mínimo | CR-F1; tenant/finalidade/consent/source atuais; mínimo sem PHI | `administrative_access_resolve` |
| lucas | `journey.compras.step` | OP02 | `REQUEST` | WA | opções comerciais não clínicas JR1 | CR-F2; catálogo/versão/validade/disponibilidade; sem proxy clínico | `administrative_offer_compose` |
| lucas | `journey.compras.step` | OP03 | `REQUEST` | WA | decisão sobre oferta/termos exatos JR1 | CR-F3; cliente/delegação atuais, oferta/termos e receipt; sem S4 | `administrative_acceptance_record` |
| lucas | `journey.compras.step` | OP04 | `REQUEST` | WA | matrícula/direito-base administrativo JR1 | CR-F3; issuer/linkage/receipt; matrícula separada de AUTH | `administrative_enrollment_request` |
| lucas | `journey.compras.step` | OP05 | `REQUEST` | WA | reserva do compromisso aceito JR1 | CR-F4; provider/revisão/atomicidade/receipt; hold não é confirmed | `administrative_reservation_command` |
| lucas | `journey.compras.step` | OP06 | `READ_REQUEST` | WA | fato administrativo de realização JR1 | CR-F4; fonte/revisão/tempo; recusar clinical_result_context_ref | `administrative_fulfillment_observe` |
| lucas | `journey.compras.step` | OP07 | `REQUEST` | WA | intake/update do caso e requester | CR-F5; membership/role/audience/caso/revisão/receipt; S4 só OP07 | `administrative_case_open_or_update` |
| lucas | `journey.compras.step` | OP08 | `OBSERVE_SERVICE_RESULT` | WA | observação de espera/retorno do caso | CR-F5; produtor/correlação/currentness; fonte liquida, timeout técnico incerto | `administrative_external_wait_observe` |
| lucas | `journey.compras.step` | OP09 | `REQUEST` | WA | aviso permitido ao destinatário | CR-F6; conteúdo/recipient/sender/canal/factory/dedupe/receipt | `administrative_notice_request` |
| lucas | `journey.compras.step` | OP10 | `OBSERVE_SERVICE_RESULT` | WA | observação de marco para audience | CR-F6; fonte/outbox/inbox/audience/receipt; agente não publica fato | `administrative_milestone_observe` |
| lucas | `journey.suporte.step` | OP01 | `READ_REQUEST` | WA | identidade/contexto administrativo mínimo | CR-F1; tenant/finalidade/consent/source atuais; mínimo sem PHI | `administrative_access_resolve` |
| lucas | `journey.suporte.step` | OP07 | `REQUEST` | WA | intake/update do caso e requester | CR-F5; membership/role/audience/caso/revisão/receipt; S4 só OP07 | `administrative_case_open_or_update` |
| lucas | `journey.suporte.step` | OP08 | `OBSERVE_SERVICE_RESULT` | WA | observação de espera/retorno do caso | CR-F5; produtor/correlação/currentness; fonte liquida, timeout técnico incerto | `administrative_external_wait_observe` |
| lucas | `journey.suporte.step` | OP09 | `REQUEST` | WA | aviso permitido ao destinatário | CR-F6; conteúdo/recipient/sender/canal/factory/dedupe/receipt | `administrative_notice_request` |
| lucas | `journey.suporte.step` | OP10 | `OBSERVE_SERVICE_RESULT` | WA | observação de marco para audience | CR-F6; fonte/outbox/inbox/audience/receipt; agente não publica fato | `administrative_milestone_observe` |
| lucas | `journey.suporte.step` | OP11 | `REQUEST` | WA | feedback opcional do caso JR2 | CR-F5; instrumento/respondent/evidência; sem bloquear dinheiro/entrega | `administrative_feedback_record` |

O grant do tool sozinho nunca admite uma OP. Cada linha exige AW0 do dono de agente/conduta/canal/task/tool/action, contrato CR-F afetado, AW-SPEC/AW-CALL/AW-AUTH/AW-FACT/AW-RETURN e allowlist efetiva por principal/task/OP. A política enforcing deve verificar tenant/legal entity/jornada, envelope/request exatos, finalidade, source/contract/policy revisions, requester/consent/freshness/erasure atuais e teto, audit antes da fonte e receipt/currentness antes do retorno. Fonte ausente e action desconhecida continuam recusadas. `OBSERVE_SERVICE_RESULT` em OP08/10 não dá ao agente autoridade de liquidar espera ou publicar fatos.

A matriz completa e os field sources de `AdmissionBinding` estão em `contracts.json.administrative_operation_binding_matrix`. `principal_ref`, `task_ref`, `purpose_ref`, `autonomy_action` e as demais referências vêm da composição autenticada; não do caller/modelo. `AdmissionBinding` ainda não possui campo de canal: o verifier qualificado deverá amarrá-lo à tarefa/autorização/currentness verificadas. Construir o DTO candidato não prova essa qualificação. As tools atuais `mcp-dmn.evaluate`, `mcp-whatsapp.send_message` e `mcp-cibseven.start_process` e suas actions permanecem limitadas aos mecanismos e domínios publicados; nenhum deles é alias genérico das onze OPs. Nenhum superconjunto de especialista é concedido.

### Shape interno candidato, separado do wire de fornecedor

Os arquivos atuais `src/maezo/agents/lucas/administrative/handoff.py`, `state.py` e `graph.py` contêm candidatos, ausentes das raízes de produção. O shape desta proposta corresponde a eles; não declara um wire A2A/canal publicado:

| Shape | Campos fechados | Origem e verificação requerida |
|---|---|---|
| `AdministrativeHandoff` (`v21-administrative-handoff.proposed.v1`) | `schema_version`, `task_type`, `tenant_ref`, `journey_ref`, `message_ref` | Composto no adapter autenticado após triagem atual; `task_type` limitado aos dois candidatos Lucas. `message_ref` opaco com shape `hk1_` + 64 hex; nunca wamid/texto/telefone raw. |
| `AdministrativeInput` | `envelope`, `payload`, `handoff`, `current_message_ref`, `health_priority`, `human_requested` | `envelope` mantém os 12 campos; `payload` é um único request fechado. Handoff, referência corrente e flags booleanas vêm do contexto confiável; validar igualdade de mensagem/tenant/jornada por turno. |
| `AdmissionBinding` | `principal_ref`, `task_ref`, `tenant_ref`, `legal_entity_ref`, `purpose_ref`, `operation_name`, `schema_version`, `contract_revision`, `source_authority_ref`, `policy_revision`, `data_classification`, `autonomy_action`, `security_zone` | Resolvido server-side em `admission.py`; qualified `AuthorityVerifier`, task/Card assinada quando A2A, publicação de fonte/política, registro chamável e audit continuam obrigatórios. |

Os seis campos de `AdministrativeInput` são composição técnica interna; não acrescentam campo ao envelope/request de negócio. `technical_status`, `outcome`, business facts e receipts são outputs proibidos ao caller. Flags de saúde/humano e IDs opacos não concedem autoridade. O consumer deve ter as admissions do mesmo `task_ref`; a allowlist JR1 é OP01–OP10, JR2 é OP01/07/08/09/10/11. S4 não recebe OP03. Todo wire de canal ainda exige contrato admitido, e A2A reutiliza seu envelope assinado existente quando aplicável.

### Literais administrativos propostos para decisão do dono

**Os textos a seguir são PROPOSED_NOT_APPROVED.** IDs são rótulos documentais, sem novos estados/enums de negócio. As condições são requisitos de veracidade a revisar, não regras implantadas. A tradução de estado/caso e o texto precisam do gate W0 do owner; toda saída ainda exige CR-F6 com conteúdo autorizado (`authorized_content_ref`), destinatário, sender/canal/factory qualificados, currentness e dedupe. Nenhum literal cria oferta, preço, provider, prazo, direito ou receipt. Prepared, pending, unknown, issued, held/confirmed, delivered e resolução do caso permanecem fatos distintos.

| Proposta | Literal exato | Condição de uso candidata |
|---|---|---|
| `ADM-PASSAGE-COMPRAS` | “Seu pedido de compra passou ao Lucas, aqui na conversa.” | Handoff JR1 da mensagem atual admitido e recebido/acknowledged pelo consumer; sem override saúde/humano. Objeto criado sozinho não basta. |
| `ADM-PASSAGE-SUPORTE` | “Seu pedido de suporte passou ao Lucas, aqui na conversa.” | Mesmo gate, consumer JR2. Nenhum efeito de negócio implícito. |
| `ADM-PASSAGE-UNAVAILABLE` | “Não foi possível continuar este pedido administrativo nesta conversa. Ainda não há confirmação de resultado.” | Módulo/handoff/fonte indisponível; sem anúncio de passagem ou sucesso. Resposta atual de saúde/humano/fora do canal prevalece quando aplicável. |
| `ADM-PREPARED` | “O aviso foi preparado. A entrega ainda não foi confirmada.” | Registro real de preparação do aviso; separado do DTO de status de entrega, sem receipt delivered. |
| `ADM-NOTICE-PENDING` | “O envio deste aviso está pendente. A entrega ainda não foi confirmada.” | OP09 `pending` verificado; mesma notice/recipient/revisão. |
| `ADM-NOTICE-ATTEMPTED` | “Houve uma tentativa de envio deste aviso. A entrega ainda não foi confirmada.” | OP09 `attempted` com evidência de tentativa; sem receipt delivered. |
| `ADM-UNKNOWN` | “Ainda não há confirmação do resultado deste pedido.” | Timeout técnico/resultado incerto ou OP09 `unknown`; reconciliação conserva identidade do comando. |
| `ADM-ENROLLMENT-PENDING` | “O pedido de matrícula está pendente de confirmação pela fonte responsável.” | OP04 `pending` da mesma matrícula; nenhuma emissão inferida. |
| `ADM-ENROLLMENT-ISSUED` | “A fonte responsável confirmou a emissão da sua matrícula.” | OP04 `issued` + enrollment_ref e issuer_receipt atuais; sem autorização clínica inferida. |
| `ADM-RESERVATION-HELD` | “A fonte responsável confirmou uma reserva temporária. A confirmação definitiva ainda não foi registrada.” | OP05 `held` + receipt atual e hold não vencido; nunca booked por mera disponibilidade. |
| `ADM-RESERVATION-PENDING` | “O pedido de reserva está pendente de confirmação pela fonte responsável.” | OP05 `pending`; sem held/confirmed inferido. |
| `ADM-BOOKED` | “A fonte responsável confirmou sua reserva.” | OP05 `confirmed` + booking_ref/provider_receipt atuais; hold não basta. |
| `ADM-NOTICE-DELIVERED` | “A entrega deste aviso foi confirmada pelo canal.” | OP09 `delivered` + receipt do destinatário; só este aviso, sem afirmar entrega da compra/cuidado. |
| `ADM-CASE-REGISTERED` | “Seu caso foi registrado.” | OP07 receipt/case_ref e estado publicado com sentido registered; protocolo só se real/verificado. |
| `ADM-CASE-RESOLUTION-QUERY` | “A fonte responsável informou que seu caso foi resolvido. Você confirma que o problema foi resolvido?” | Receipt de resolução da fonte no mesmo caso e tradução publicada; colher manifestação atual do cliente/delegado. |
| `ADM-CASE-CONFIRMATION-RECORDED` | “Sua confirmação de resolução foi registrada.” | OP07 registrou manifestação atual no mesmo caso/revisão; tradução confirmada pelo owner. Sem OP03. |
| `ADM-CASE-DISAGREEMENT-RECORDED` | “Seu relato de que o problema continua foi registrado no mesmo caso.” | OP07 registrou discordância atual no mesmo caso/revisão; sem fechamento inferido. |
| `ADM-CASE-CONFIRMATION-PENDING` | “Ainda não há uma confirmação sua de que o problema foi resolvido.” | Fonte atual confirma falta de manifestação; espera/timer apenas se admitidos. Silêncio não resolve. |
| `ADM-CASE-CONTRACTING-HANDOFF` | “Seu pedido de contratação passou para o fluxo de compra. O caso de suporte mantém seu registro.” | Passagem explícita JR2 → JR1 admitida/acknowledged e conservação do caso/protocolo; nenhuma contratação dentro de JR2. |

Os literais de saúde/humano já existentes permanecem os mesmos bytes: `RESPOSTA_ESCALONAMENTO_URGENTE`, `RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL`, `RESPOSTA_HANDOFF_RECUSADA`, `RESPOSTA_HANDOFF_JA_ABERTO`, `RESPOSTA_FALHA_TECNICA_START` e `RESPOSTA_SEM_ENCAMINHAMENTO` em `src/maezo/agents/helena/graph.py`. Suas referências e SHA256 de literal estão no JSON; não se presume assinatura nova. A seleção preserva motivo/severidade e o fato real do start no turno (`novo`, `ja_ativo`, falha/não tentado), sem anunciar humano por intenção. Saúde/risco mantém Helena e seus gates; pedido humano restante interrompe administração e usa o intake/escalonamento admitido existente. Nenhuma orientação clínica nova é proposta.

## Fonte, dono, gate e readiness por pacote

Os donos abaixo são papéis funcionais a confirmar; nenhum nome/assinatura é presumido. Cada requerimento aplica-se somente à fonte/fronteira realmente atravessada, sem transformar AMH em proprietário obrigatório de catálogo/agenda externos.

| Contrato | Fonte/dono requerido | Readiness observado na base | Gate ainda pendente |
|---|---|---|---|
| CR-F1 | AMH identity/consent steward for AMH identity/context; operator or provider owner for administrative/prospect identity; DPO/legal; security | `PARTIAL_IMPLEMENTED_LIVE_UNVERIFIED` | prospect identity/linkage contract when needed; published nonclinical commercial purpose and minimum fields; current consent/revocation/freshness/erasure authority; actual consumer plus injected qualified source |
| CR-F2 | operator product/commercial/legal owner; catalogue/availability provider steward; DPO | `COMMERCIAL_AUTHORITY_ABSENT_FROM_PIN;POPULATION_PORT_PENDING` | authoritative catalogue/products/terms/version/expiry; nonclinical commercial feature allowlist; operator-ratified deterministic rules via CIB DMN when used; availability source qualification |
| CR-F3 | customer or current authorized representative for acceptance; operator registration/enrollment issuer; legal; AMH identity steward only for AMH linkage; DPO | `BUSINESS_WRITES_ABSENT_FROM_PIN;LIVE_OUTCOME_PUBLISHER_UNVERIFIED` | explicit customer acceptance and terms-evidence semantics; current customer/delegate authority; administrative enrollment mutation/issuer receipt/linkage contract; issuer/source runtime qualification |
| CR-F4 | agenda/reservation/delivery provider owner; care/network owner for governed obligation; AMH steward only for an AMH crossing; DPO | `READ_ONLY_COVERAGE_PRESENT;ENROLLMENT_AGENDA_ABSENT_FROM_PIN` | availability/atomic hold/reservation lifecycle contract; provider revision and hold expiry/currentness; reschedule/cancel legal scope; authoritative reservation/performance/result receipts |
| CR-F5 | operator case/intake/staff owner; appropriate native human authority; AMH contract steward only for an AMH work-item crossing; DBA; security/DPO | `FOUNDATION_PRESENT;BROKER_CONSUMER_ABSENT_FROM_AMH_ADAPTER_PACKAGE` | DeclaredCaseKind/DeclaredCaseStatus membership and role/audience publication; case/protocol/clock/source lifecycle contract; S4 confirmation intent plus published outcome translation; native source/receipt/return qualification |
| CR-F6 | domain fact producer; communication/channel/sender owner; recipient preference/custody owner; AMH contract steward only for an AMH outcome crossing; DPO/security | `ENVELOPE_PRESENT;NEW_DOMAIN_SEMANTICS_UNVERIFIED` | mandatory/facultative notice policy and permitted content; qualified live sender/channel/factory; recipient authority/preferences/revocation/erasure; milestone/receipt semantics plus declared event catalogue |

O pin AMH atual não publica autoridade comercial, matrícula ou agenda. Maezo não edita schema/digest/lock para passar o gate. Source owner publica a extensão quando a fronteira é dele; quando ela atravessa AMH, novo processo XRG qualifica e fixa a publicação. Reading coverage, `care.enroll`, membership de portal e enrollment de bootstrap engine não emitem matrícula/direito-base. Slot disponível não é hold e preparar aviso não é entregar.

Cliente/representante atual decide aceite sobre oferta e termos exatos; a fonte administrativa emite vínculo/direito-base; AUTH/Rafael e médico auditor conservam sua autoridade assistencial. Receipt HTTP/offset, texto livre ou resumo LLM não são assinatura, matrícula, reserva, entrega ou resolução. Fonte ausente permanece indisponível; fixture de teste não vira produto.

## S4: confirmação de resolução sem oferta

Depois de verificar o receipt de resolução da fonte, Lucas coleta manifestação atual do cliente ou representante autorizado. OP07 recebe `problem_ref`, `origin_case_or_journey_ref` apontando o mesmo caso autoritativo, membro publicado de `DeclaredCaseKind`, `requester_authority_ref` atual e `evidence_refs` verificáveis do receipt de resolução e da manifestação. `formal_regulatory_request_ref` fica ausente sem pedido formal real; revisão esperada pertence ao envelope.

Não adicionar `action`, confirmação booleana ou outro campo ao DTO. A fonte verifica tenant, mesmo caso, revisão, requester atual e evidências; devolve `case_ref`, protocolo aplicável, estado publicado, `authority_receipt_ref` e `business_revision`. Só então o estado durável e o reply ao mesmo principal avançam. Confirmado, desacordo, timeout e fechamento sem confirmação são classes distintas a traduzir pelo owner CR-F5, sem publicar enum por conta própria. Silêncio não prova resolução e S4 não chama OP03 nem produz `AcceptanceEvidenceReceipt`.

## Reconciliação ADR-0062 e DL-0053

Em `docs/adr/0062-roteamento-de-conversa-por-agente.md:3` o status continua `Proposto`; linhas 7–11 declaram DL-0053 aguardando assinatura. `docs/plans/lucas-numero-unico.md:38` também mantém o rascunho de assinatura. Em contraste, `docs/decisions-log.md:16` registra a decisão de Filipe de 01/10/2026, limitada ao roteamento de cobrança, e ativação em dev pelo PR #596. O commit `bee291fea498becf14c0f2a2ad646dcba612d35e` materializa esse registro. `docs/adr/README.md:74` continua `Proposto`.

Essa evidência permite registrar a divergência e preparar um ato do dono para reconciliar status, proveniência e alcance. Não permite ao agente promover ADR a Accepted ou ampliar a assinatura de cobrança a Compras/Suporte. O YAML atual tem `accepted_task_types: []` e handoff WhatsApp condicional; a fonte inspecionada é simulada e `CONTINUA_LUCAS_ATENDIDO=False`. Servir/configuração atual não foi qualificado por este pacote.

## Ledger dos 21 conceitos

A coluna onda é decomposição candidata de implementação; todos os conceitos permanecem sem admissão de runtime. H3/analytics adicionais não são pré-requisitos de Compras/Suporte. Os requerimentos listados são condicionais à fonte real usada, não gates globais.

| Conceito do ledger | Família | OPs compartilhadas | Lead proposto | Onda candidata | Gates por fonte afetada |
|---|---|---|---|---|---|
| `Process_CA_H2_ComunicarCliente` | F6 | OP09/OP10 | case_owner_or_shared_service | W1 | CR-F6 |
| `Process_CA_H2_SelecionarCanal` | F6 | OP09/OP10 | case_owner_or_shared_service | W1 | CR-F6 |
| `Process_CA_H1_IdentidadeConsentimento` | F1 | OP01 | helena/authorized_case_owner | W1 | CR-F1 |
| `Process_CA_H3_ConhecerCliente` | F1 | OP01/OP11 | authorized_case_owner | W5 | CR-F1/CR-F5 |
| `Process_CA_H3_EnriquecerCliente` | F1 | OP01/OP11 | authorized_case_owner | W5 | CR-F1/CR-F5 |
| `Process_CA_H3_ProximaMelhorAcao` | F2 | OP01/OP02/OP11 | lucas | W5 | CR-F1/CR-F2/CR-F5 |
| `Process_CA_COMPRA` | F3 | OP01/OP02/OP03/OP04/OP05/OP06/OP07/OP08/OP09/OP10 | lucas | W2 | CR-F1/CR-F2/CR-F3/CR-F4/CR-F5/CR-F6 |
| `Process_CA_COMPRA_C1_EntenderRecomendar` | F2 | OP01/OP02/OP05/OP09 | helena/lucas | W2 | CR-F1/CR-F2/CR-F4/CR-F6 |
| `Process_CA_COMPRA_C2_DecidirContratar` | F3 | OP02/OP03/OP09 | lucas | W2 | CR-F2/CR-F3/CR-F6 |
| `Process_CA_COMPRA_C3_HabilitarDireito` | F3 | OP01/OP03/OP04/OP07/OP08/OP09 | lucas | W2 | CR-F1/CR-F3/CR-F5/CR-F6 |
| `Process_CA_COMPRA_C4_ManterDireito` | F4 | OP05/OP06/OP07/OP08/OP09 | lucas | W2 | CR-F4/CR-F5/CR-F6 |
| `Process_CA_UTILIZACAO` | F4 | OP05/OP06/OP07/OP08/OP09/OP10/OP11 | lucas | W4 | CR-F4/CR-F5/CR-F6 |
| `Process_CA_UTILIZACAO_U1_Preparar` | F4 | OP05/OP06/OP09 | lucas | W4 | CR-F4/CR-F6 |
| `Process_CA_UTILIZACAO_U2_Receber` | F4 | OP06/OP07/OP08/OP09 | lucas | W4 | CR-F4/CR-F5/CR-F6 |
| `Process_CA_UTILIZACAO_U3_Acompanhar` | F4 | OP06/OP09 | lucas | W4 | CR-F4/CR-F6 |
| `Process_CA_UTILIZACAO_U4_LiquidarAvaliar` | F4 | OP06/OP09/OP10/OP11 | lucas | W4 | CR-F4/CR-F5/CR-F6 |
| `Process_CA_SUPORTE` | F5 | OP01/OP07/OP08/OP09/OP10/OP11 | lucas | W3 | CR-F1/CR-F5/CR-F6 |
| `Process_CA_SUPORTE_S1_OuvirRegistrar` | F5 | OP01/OP07/OP09 | helena/lucas | W3 | CR-F1/CR-F5/CR-F6 |
| `Process_CA_SUPORTE_S2_DiagnosticarRotear` | F5 | OP07/OP08 | helena/lucas | W3 | CR-F5 |
| `Process_CA_SUPORTE_S3_ResolverEncaminhar` | F5 | OP07/OP08/OP09 | lucas | W3 | CR-F5/CR-F6 |
| `Process_CA_SUPORTE_S4_ConfirmarEncerrar` | F5 | OP07/OP09/OP10/OP11 | lucas | W3 | CR-F5/CR-F6 |

## Dependências e trabalho independente

| Onda | Dependência/gate concreto | Readiness e trabalho permitido |
|---|---|---|
| W0 | owner/source publication and ratification remain pending | `PROPOSED_CONTRACTS_REVIEWABLE`; contracts, role boundaries, evidence and baseline protocol |
| W1 | affected W0 source/effect contract gates; fresh independent mechanism/security review; real registration/composition/CI before binding | `CANDIDATE_MECHANISMS_ONLY`; closed DTO validation, deny-only candidate admission, common state/receipt/wait mechanisms with no production binding |
| W2 | W1 qualified mechanism; Helena/Lucas conduct and task/channel/tool/action approval; CR-F2 catalogue; CR-F3 acceptance/enrollment; CR-F4 agenda/fulfillment; CR-F1/5/6 only for actual source crossing | `BLOCKED_FOR_COMPLETE_JOURNEY`; independent candidate graph construction and negative tests without supplier simulation as product |
| W3 | W1 shared contracts/mechanisms qualified; AW0 support role/task/ingress ratification; CR-F5 case membership/S4 confirmation publication; CR-F6 notice/return qualification | `BLOCKED_FOR_COMPLETE_JOURNEY`; same-core support candidate and tests; no OP03 confirmation |
| W4 | qualified W1/W3 core; CR-F4/5/6 affected delivery/care/financial contracts; U4 token witness or equivalent sound proof; existing protected domain authorities | `BLOCKED_PER_DOMAIN`; domain contract preparation and static safety checks |
| W5 | W2/W3 measured reuse; qualified aggregate source/purpose; benefit/evaluation/privacy controls; owner promotion approval | `DEFERRED_AFTER_CORE`; approved aggregate measurement preparation only; baseline remains UNMEASURED |

Baseline usa export agregado autorizado, escolhido pelo owner, sem logs PHI nem consulta direta ao lake. Ausência de export mantém `UNMEASURED` e ROI `NOT_DEMONSTRATED`; não bloqueia globalmente a engenharia independente. Nenhum prazo regulatório, regra comercial, tentativas/limite, retenção ou preço foi escolhido nesta proposta.

## Gates e limites de implementação

Admission exige operação registrada/composição chamável, principal/task/canal declarados, allowlist por OP, tenant/legal entity/finalidade/revisão/consent/currentness/teto, fonte publicada atual, política efetivamente enforcing e audit antes de leitura/efeito. Sombra global não concede bindings novos. Gates são AW-SPEC, AW-CALL, AW-AUTH, AW-FACT, AW-RETURN, AW-REGRESSION, AW-REUSE e AW-OPS.

Os contratos permitem autoria candidata reversível de validação fechada, admissão que recusa chamadas não admitidas e mecanismos comuns de estado/receipt/espera. Esse código não lê fonte, emite efeito ou qualifica jornada antes dos gates W0 afetados. Receipt válido precisa prova da autoridade/objeto/tenant/revisão atuais; idempotência/inbox/outbox se unem transacionalmente ao estado/evidência, e efeito pending/uncertain reconcilia sem nova matrícula/reserva/aviso/pagamento.

Estado de negócio permanece separado de `conversa_agente_ativo`; CAS e isolamento no saver escolhido precisam ser reproduzidos, sem assumir proteção pelo `checkpoint_ns` de grafo raiz. Retorno valida evento/fonte/receipt/revisão antes de retomar o mesmo tenant/jornada/caso/principal. `agent_resume`/`LucasRetomada` só são reutilizados dentro dos comandos atuais ou extensão admitida; direct completion não é fallback.

ADR-0001 mantém LangGraph para jornada e CIB para governança/DMN. GP1/GP2 são candidatos condicionais. Engine-side `CibSevenDmnTransport` somente com regra ratificada. Nenhum start de ADEQUACAO em agente; NIP só pedido formal ANS; RECURSO só glosa payer. Survey/feedback é opcional e assíncrono, sem bloquear pagamento/entrega. U4 precisa witness de token ou prova equivalente de fluxo sound antes da adoção.

Permissões hard `clinical_decision`, `authorization_denial`, `nip_manter_negativa`, `fraud_accusation` e `contract_termination` ficam humanas. Não há dado clínico/proxy para seleção de risco comercial, CDC cru, write Tasy/HAPI, consulta lake, clone clínico/MPI, SDK LLM fora de `runtime.inference` ou credencial fora do gateway.

## Verificação e custódia

O checklist de findings foi recuperado do objeto Git `e7b14522a4f70242504d2b152a57ee5269ee66e2:docs/reports/predeploy-findings.json` sem restaurar o delete do ROOT. Há 127 rows no objeto; a contagem histórica 82 em AGENTS não é inventário vivo. [contracts.json](contracts.json) fixa SHA256 e IDs históricos relevantes para não repetir overclaim de cancel/start, allowlist incompleta, seam decorativo/não ligado, PHI raw em notifications/retomada e pseudonymizer sem injeção. Esses registros não são alegações de defeitos atuais: o verifier reproduz as superfícies afetadas.

A verificação estrutural local compara os onze request/result maps e envelope com os bytes admitidos, garante unicidade/cobertura dos 21 IDs e a regra S4 OP07. Ela verifica integridade documental, sem substituir review independente, engine/source integration, conduta humana ou publicação fornecedor. O fluxo continua author → verifier distinto → repair terceiro → delta pelo reviewer original → integração → CI aplicável → dois gates finais frescos.

Proveniência principal: `v2-capabilities/plan.json` (`internal_operations`, `family_contract_requests`, `waves`, `reuse_exceptions`), `COMPATIBILITY-AND-CONTRACTS.md:19-29,31-99,101-137`; `v2-agent-wiring/agent-wiring.json` (`operations`, `concept_agent_mapping`, `administrative_task_contracts`, `scope_extension_gate`, `composition_overrides`); `INTEGRATION-PLAN-V2.1.md:65-80,94-111`; ADR-0001; YAMLs de Helena/Lucas; L0-core e _hard_frozen; SP-OP-ESCALATION-001:99-160,174-197 e SP-OP-AUTH-001 (interfaces/intake e autoridade clínica).
