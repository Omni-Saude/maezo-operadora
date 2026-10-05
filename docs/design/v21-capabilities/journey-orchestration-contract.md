# W2/W3: contrato candidato de orquestração completa

**PROPOSED — interface técnica para revisão independente; defaults desabilitados.**
Base de leitura: `a62c05008f4d2e13af83cee7f6a61e913b51dcab`, no checkout
`/Users/familia/.codex/worktrees/v21-capability-execution/maezo-operadora`.
O bundle admitido continua no ROOT absoluto; seus bytes não são alterados.
O JSON irmão contém proveniência, mapas exatos, coreografia, ports, write sets e
critérios executáveis de verificação. Nenhum estado ou enum de negócio é publicado
por esta proposta. Publicação de fonte, conduta, permissões e ativação permanecem
gates por efeito.

Delta documental de terceiro para **DJ-F01/DJ-F02/DJ-F03**, após REVISE do gate
`durability-journey-contract-verifier.json`. Os inputs originais permanecem nos
snapshots `initial-frozen-*`; os quatro documentos reparados aguardam delta do
reviewer original. Esta proposta não admite código nem fecha gate humano/fonte.

## O que o driver acrescenta

O código atual compila `receive → execute → END` para uma OP por turno. Não
persiste encadeamento, contrato aceito, espera, comando incerto, inbox ou reply
pendente. O lote proposto implementa **uma máquina de orquestração compartilhada
para a jornada inteira**, usada por JR1/Compras e JR2/Suporte com configurações,
fontes e authorities distintas. O driver escolhe o próximo cursor a partir do
journal e da transição verificada da fonte; um caller não escolhe OP, próximo nó,
estado, receipt ou `Command` de resume do LangGraph.

Os cursores C1/C2/C3/C4 e S1/S2/S3/S4 identificam posições técnicas rastreáveis aos
conceitos CA. Eles não substituem `case_status`, matrícula, reserva, realização,
negativa ou decisão humana. O mapa de transições pertence ao contrato governado
do domínio: o driver verifica o mapa, a origem, o predecessor, a revisão e os
sucessores permitidos, sem avaliar elegibilidade ou criar regra em Python/prompt.
Regras determinísticas ratificadas continuam no CIB/DMN quando aplicáveis.

## Interfaces internas e fronteiras

| Símbolo proposto | Entrada confiável | Saída/efeito limitado |
|---|---|---|
| `JourneyBinding` | Task, tenant, legal entity, versão da topologia e referências de contratos, provenientes da composição | Scope fixo de uma instância; defaults desabilitados; não amplia YAML/Card/allowlist |
| `CurrentJourneyTurn` | Handoff administrativo validado, mensagem pseudonimizada atual, referência de manifestação autenticada, observação de saúde/humano, revisão local esperada | Intenção atual; sem payload clínico, OP, output, próximo cursor ou receipt plantado pelo caller |
| `JourneyPreparationPort.prepare` | Binding, snapshot durável, intenção/observação corrente e predecessor verificado | `PreparedJourneyAction`: próxima ação técnica permitida, envelope e DTO exatos quando houver OP, comprovação da preparação e transição source-owned |
| `JourneyTransitionAuthorityPort.verify` | Binding, cursor/predecessor, ação preparada e fatos/receipts atuais | `VerifiedJourneyContinuation` qualificada pela fonte, ou recusa técnica existente; sucessor pertence à topologia publicada |
| `JourneyDriver.accept_turn` | `CurrentJourneyTurn` autenticado pelo ingress confiável | Leitura/CAS do journal, preparação, execução da ação admitida ou espera/interrupção; não retorna sucesso de negócio inventado |
| `JourneyDriver.accept_observation` | `VerifiedInboxObservation` da interface única de durabilidade | Commit inbox+referências de fatos+continuação/outbox; ack de transporte somente depois do commit |
| `JourneyDriver.resume` | Referência de observação já durável, binding e revisão local esperada | Retomada do mesmo caso/journey mediante currentness novo; nenhuma conclusão direta de human task |
| `JourneyDriver.recover` | Handles retornados pelo journal sob scope atual | Reconciliação de comandos possivelmente aplicados e drenagem de outbox; nunca resend automático de efeito incerto |
| `ExistingDomainHandoffPort` | Roteamento de domínio verificado, caso e material mínimo sob autoridade própria | Target/handler existente admitido, tarefa/Card assinados quando A2A, ack/proveniência e retorno contratado; ack não prova resolução |
| `QualifiedReplyPort` | `JournalBinding`, `OutboxSnapshot`, `VerifiedAuthority` e `VerifiedCurrentness` existentes; conteúdo/destinatário/canal resolvidos no target qualificado | Evidências distintas `OutboxAckEvidence` de transporte e `DeliveryEvidenceReceipt` OP09, verificadas separadamente; ausência continua desconhecida |

`PreparedJourneyAction` é composição interna fechada, não uma nova API de
fornecedor. Suas alternativas são passo OP, passagem a domínio existente, espera
por observação, solicitação de manifestação atual ou verificação de terminal da
fonte. A preparação não autoriza a ação. Todo envelope/DTO é reparsed, e cada OP
passa pela `CapabilityService` e pela admission estrita correspondente à tarefa,
principal, tenant, purpose e operação. Ausência de port, publicação ou gate fecha
a ação dependente; não resulta em fallback de fonte, aprovação ou terminal.

O JSON fecha os campos de `PreparedJourneyAction` e
`VerifiedJourneyContinuation`, com schema candidato, task/tenant/legal entity,
journey/topologia, cursor técnico, predecessor/evidências e autoridade da
preparação/transição. O verifier deve provar essas referências, não ecoá-las.
Somente a alternativa de passo OP contém envelope/DTO; a passagem a domínio
existente carrega binding/material/return próprios, e a espera carrega produtor,
correlação e deadline da fonte. Sucessor deve pertencer à topologia da task e
provir de transição source-owned atual. São tipos internos candidatos, sem enum
de negócio ou wire de fornecedor novo. `QualifiedSourceRecoveryPort` consulta o
receipt do comando original sob authority independente de leitura; não execute
como fallback de query ausente.

`QualifiedReplyPort` usa exatamente os records declarados; não existe alias
genérico de outbox. O snapshot deve vir do commit reconhecido de
`begin_outbox_delivery`, sob binding/revisão local exatos, estado
`DELIVERY_FENCED`, claim/worker/fence/delivery_ref correntes e lease operacional
ainda válida. Tipo/snapshot construído não prova esse commit. Vincular
command/outbox/causation/dedupe e target/payload refs/digest imutáveis ao mesmo
tenant/legal entity/journey/principal/task. O target registrado resolve conteúdo,
recipient/canal e janela sob autoridade própria; nada disso é caller wire.

Authority/currentness existentes provêm da admissão OP09/source qualificada,
vinculadas ao request/binding/authorization originais; após cada await revalidar
a mesma lease privada, fence e currentness antes de transporte/disclosure. Lease
vencida, worker antigo, target/payload divergente ou revogação recusa envio e
preserva fence/incerteza. O resultado fechado é
`tuple[OutboxAckEvidence | None, DeliveryEvidenceReceipt | None]` ou recusa técnica
existente. `record_outbox_ack` só aceita a primeira evidência realmente autenticada;
ACK_RECORDED não implica entrega. A segunda exige DTO OP09 e prova independente
da fonte; campos presentes, offset/HTTP ACK ou tipo de objeto não são recibo.
Tuple sem qualquer evidência verificada permanece inconclusiva/incerta, nunca
ACK/completion. Receipt real de delivery sem ACK de transporte conserva o fato
da fonte e não fabrica um ACK para o outbox.

## Revisões e dados

O envelope permanece exatamente com seus doze campos e versão candidata
`v21-capabilities.proposed.v1`. `expected_business_revision` continua **Ref/string
opaca**, como em `models.py` da base. As revisões dos results (`business_revision`,
`settlement_revision`, `provider_revision`, etc.) são referências da fonte. O
`journal_revision` é contador inteiro local, exclusivo da interface de
durabilidade. É proibido converter, comparar numericamente ou incrementar Ref da
fonte, ou usar o contador do journal como revisão esperada da próxima OP.

O planner qualificado fornece o próximo envelope, sua revisão esperada, source
authority, policy, correlação, causa e chave idempotente literal. Um resultado de
OP não vira automaticamente input de outra: o port verifica o objeto, a versão,
a finalidade e a relação semântica de cada referência. Exemplos: o vínculo entre
`acceptance_ref` e `accepted_commitment_ref` vem da fonte; `offer_ref` não vira
booking; `portable_subject_ref` continua AMH-minted; coverage não matricula.

Persistir apenas referências mínimas, digests, estados técnicos do journal,
receipts autoritativos e versões governadas; sem mensagens, sintomas, diagnósticos,
telefone, CPF, tokens, URLs assinadas, perfil universal ou proxy de risco comercial.
A tradução de `DeclaredCaseKind`, `DeclaredCaseStatus` e `DeclaredMilestoneKind`
continua vazia sem publicação/verificação do owner. Não escolher nova finalidade
AMH, regra, deadline, prazo regulatório ou status a partir de nomes da topologia.

## JR1: necessidade até entrega ou recuperação

| Cursor técnico | OP/port | Continuação verificada |
|---|---|---|
| C1 necessidade/contexto mínimo | Manifestação autenticada; OP01 `access.resolve` | Contexto permitido por finalidade; alternativa mínima somente se o contrato da fonte permitir. Denied/unavailable não libera contexto nem perfil |
| C1 opções | OP02 `offer.compose` | Catálogo, preferências não clínicas, disponibilidade e versão atuais. `usable`, `unavailable`, `review_required` permanecem distintos; sem opção válida encaminha recovery/caso contratado |
| C1 apresentar | OP09; OP10 quando o catálogo governado exigir marco | Conteúdo da oferta/termos versionados, destinatário/canal reais; preparar/enviar/entregar separados. Esperar manifestação atual, sem usar silêncio como aceite |
| C2 decisão | OP03 `acceptance.record` | Cliente/delegado atual, `offer_ref`, versão e termos exatos. Source receipt `accepted` ou `declined`; falta de receipt/timeout fica incerta, nunca matrícula por intenção do agente |
| C3 decisão de rota | `JourneyTransitionAuthorityPort` | Rota administrativa de matrícula, reserva ou pedido assistencial AUTH existente sob contrato próprio; não inferir ramo clínico nem direito-base do aceite |
| C3 emissão administrativa | OP04 `enrollment.request` quando a rota exige matrícula | `pending`, `issued`, `refused`, `review_required` da fonte; issued exige evidência do issuer verificada. Pendência usa OP08; refusal/review seguem recovery/autoridade própria |
| C3 reserva | OP05 `reservation.command` quando a rota exige agenda | Hold/confirm/reschedule/cancel apenas por comando autorizado e revisão do provider. `held` não é `confirmed`; slot disponível não é hold. Timeout passa a reconciliação da mesma chave |
| C3 AUTH quando legítimo | Port para ingress AUTH/native existente, Rafael/médico somente em seus atos | Identidade/correlation/receipt próprio, pendência/espera e retorno do contrato SP-OP-AUTH-001. Nunca alias de matrícula, direct completion ou start ADEQUACAO |
| C3 espera e retorno | OP08 `external_wait.settle`; inbox de fonte/humano | Produtor, mesmo objeto, correlação, revisões e currentness verificados; timer BPMN somente se obrigação governada. Tempo técnico decorrido não significa conclusão |
| C4 realização | OP06 `fulfillment.observe` | Fato autenticado e revisão da fonte, sem `clinical_result_context_ref` no administrativo. `pending`/`no_show`/`disrupted` e realizado têm ramos próprios contratados |
| C4 manutenção/recuperação | OP05 quando mudança de reserva é autorizada; OP07/OP08 para caso e espera | Compensação/reagendamento/cancelamento somente por autoridade do domínio; sem desfazer efeito confirmado porque tópico mudou ou reply falhou |
| C4 terminal e comunicação | Fonte verifica fato contratual de entrega/recusa/intervenção; OP09/OP10 | Receipt de entrega/resolução da fonte, evidência de confirmação quando exigida e reply qualificado. Outbox/reply pendente é conservado; sem declarar jornada entregue com fonte ausente |

Nem toda compra exige simultaneamente matrícula, reserva e AUTH. O contrato
governado seleciona os ramos exigidos; o driver possui todos esses ramos e suas
esperas/recovery, sem iniciar efeitos de ramos não escolhidos. Pedido de ajuda ou
sem oferta pode passar explicitamente a JR2 admitida, conservando referências de
contrato/journey, origem e receipts. Nenhum feedback/analytics avançado bloqueia o
núcleo, e não se promete entrega com engine start ou A2A `completed`.

## JR2: caso até confirmação de resolução

| Cursor técnico | OP/port | Continuação verificada |
|---|---|---|
| S1 ouvir/registrar | Manifestação mínima autenticada; OP01 quando o scope exigir; OP07 `case.open_or_update` | Mesmo problema/caso, requester atual, kind/audience/role publicados, protocolo na primeira ação aplicável; preservar relógio não resetável |
| S1 confirmar recebimento | OP09; OP10 quando governado | Receipt do caso/protocolo antes de dizer registrado/encaminhado; conteúdo e canal qualificados |
| S2 diagnosticar/rotear | Preparação/transition authority da fonte; OP07 quando lifecycle exigir update | Domínio real e material mínimo; atendimento comum não vira NIP, RECURSO clínico ou investigação de fraude. Encaminhamento a compras é passagem explícita JR2→JR1, preservando caso/protocolo |
| S3 resolver/encaminhar | `ExistingDomainHandoffPort` ou fonte do caso | Handler/filas/roles/source/return realmente admitidos. NIP apenas formal; RECURSO somente glosa payer; permissões hard continuam humanas |
| S3 espera | OP08 + journal wait/inbox | Produtor e evento esperados, deadline source-owned opcional, timeout/pending/incident distintos; avisos não equivalem a atendimento |
| S3 resume | Evento/receipt humano ou externo atual; OP08 e OP07 conforme contrato | Mesmo caso/journey, tenant, revisão, currentness e requester; resolver destinatário/janela no retorno. Não usar direct completion como fallback |
| S4 colher confirmação | Receipt de resolução da fonte + manifestação atual autenticada | Sem oferta/aceite comercial; silêncio, desacordo, timeout e fechamento sem confirmação permanecem distintos até mapeamento publicado |
| S4 registrar confirmação | **OP07**, refinamento v2.1 | `problem_ref` da fonte; origem é o caso autoritativo existente; kind publicado; requester atual; evidências de resolução e manifestação; revisão esperada no envelope. Nenhum `action`/boolean/field novo; **nunca OP03** |
| S4 terminal/reply | Fonte devolve mesmo `case_ref`, protocolo aplicável, status publicado, receipt e revisão; OP09/OP10 | CAS journal após receipt verificado e reply do mesmo principal; replay não fecha novamente nem duplica aviso |
| Feedback opcional | OP11 em fila independente | Instrumento e respondent autorizados; recusa/silêncio/outage não mudam resolução nem bloqueiam entrega/pagamento; nenhuma acusação/decisão financeira inferida |

## Execução durável e integração serial

Usar a interface única `DurabilityJournalPort` do contrato de durabilidade irmão;
não criar uma tabela/journal por OP nem reinterpretar `conversa_agente_ativo` como
estado de negócio. O JSON declara essa dependência e seu estado de gate. A
implementação de driver aguarda a interface congelada e gate independente.

Dependência reparada candidata: `DurabilityJournalPort.proposed.v1`, JSON SHA-256
`173db7440d95f895edcd54c2b0e0c5cc91bfdaa4a01355e6e15349ca9130b21b` e Markdown
`72a1beeddae110f810f0d08a9448a396420ba2312762866713a59522c9cbb741`.
Os dezesseis métodos e dezessete records do arquivo reparado são autoridade de
assinatura; esta proposta não redefine seus args/results. A extensão técnica
PROPOSED `wait_intents: tuple[WaitDescriptor]` dos dois métodos de chegada de
resultado está declarada no DUR0 JSON e aguarda delta independente antes de código.
O gate original foi REVISE; a proposta reparada continua pendente. `JournalBinding` projeta environment/tenant/legal entity/
journey/principal/task/data-policy confiáveis; scope de operação/purpose permanece
em `AdmissionBinding`.

O driver usa `record_command`, `observe_command`, `observe_journey`, `record_wait`,
`enqueue_outbox` e `recover`. `begin_dispatch`, `record_pre_dispatch_refusal`,
`record_verified_result` e `mark_uncertain` são do gateway command boundary.
Inbox autenticada aplica `ingest_verified_observation`; scheduler confiável chama
`mark_wait_elapsed`; worker/boundary de outbox usa claim, fence de delivery, ack e
uncertainty exatos. O driver não se transforma em verifier de receipt por obter
esses methods.

`JourneySnapshot` não guarda cursor/status de negócio. Planner qualificado
reconstrói a posição técnica pela linhagem exata de predecessor/command, topologia
admitida, fonte e custódia protegida. Linhagem insuficiente impede preparação; não
reinicia C1/S1. `RecoverySnapshot.next_cursor_ref` é apenas paginação protegida do
journal, nunca sucessor de jornada. Nenhum campo novo é acrescentado ao journal.

A fronteira de comando deve observar esta ordem:

1. Preparar envelope/DTO exatos, source currentness e transição permitida; registrar
   o comando imutável/chave literal no journal. Mesmo comando retorna handle;
   mesma chave com digest diferente é conflito e não dispatch.
2. Admission verifica contrato/publicação, principal/task, policy **enforcing**,
   finalidade, atualidade e audit-before-effect. O boundary conhece o handle
   original; o caller não recebe lease reutilizável.
3. Journal `begin_dispatch` persiste o fence antes de possível source I/O. Crash a
   partir desse ponto implica possível efeito: UNCERTAIN/reconcile-only.
4. Revalidar currentness **depois do await do fence** e imediatamente antes de
   source I/O; o provider valida autoridade/revisão atomicamente no commit. A
   `revalidate(before_source)` atual é one-shot e não pode ser chamada duas vezes.
   A integração serial deve acrescentar checkpoint privado after-fence na mesma
   lease, sem novo wire ou bypass de admission.
5. Parse/atestar receipt e resultado exatos. Expor `VerifiedSourceResult` e audit
   receipt somente internamente ao command boundary. `record_verified_result`
   recebe observation, wait_intents e outbox_intents explícitos; grava head/evidence
   refs, waits declarados, refs de continuação em outbox e revisão local na TX
   descrita em `atomic_method_entities`. O driver não grava resultado sem atestação.
6. Revalidar antes de disclosure incluindo o receipt de fonte, original ceiling,
   revogação/erasure e scope. Falha de disclosure não apaga o fato confirmado: a
   resposta fica protegida/pendente, sem repetir efeito.
7. Timeout, transporte, cancelamento ou crash após fence conservam incerteza. O
   boundary consulta receipt sob autorização independente de leitura e reconcilia
   a mesma chave/digest; ausência técnica não autoriza retransmissão. Reenvio não
   existe neste contrato congelado: não há API de redispatch. Uma futura mudança
   exigiria contrato próprio, gate independente e ato do owner que provem
   não-execução/old packet/native uniqueness e admitam o comando exato. Esse
   comportamento não é ramo desta implementação.

Consulta de receipt qualificada, inclusive após pending já RESPONSE_RECORDED,
segue `VerifiedResultObservation → record_verified_result`; não fabrica event_ref.
Callback com evento real autenticado segue `VerifiedInboxObservation →
ingest_verified_observation`, inclusive primeiro callback quando o comando ainda
está DISPATCH_FENCED/UNCERTAIN. Ambos seguem `result_head_rules`: duplicata exata
unchanged; divergência/older/currentness stale sem alteração do head; troca de
revisão exige witness da fonte, sem cast/clock/offset/local CAS como ordem.

O caminho exato `direct_result_atomic_path` usa `wait_intents: tuple[WaitDescriptor]`
para novos waits e `outbox_intents: tuple[OutboxDescriptor]` para continuação.
`VerifiedJourneyContinuation` é verificada pelo port de transição da fonte e
preparada na custódia qualificada antes da chamada; sua referência/digest ocupam
`payload_ref`/`payload_sha256` de RETURN_INTENT para target já admitido. Nenhum
cursor/entity ou corpo de continuação é acrescentado ao journal. O gateway recebe
esses descriptors da composição qualificada; o driver não se torna verifier.
Custódia do corpo não está na TX local. Cada elemento da atomicidade prometida
resolve argumento fechado e entidade em `atomic_method_entities` do DUR0 JSON.

Se faltar preparação qualificada, tuples vazias registram somente evidence de
resultado, sem claim de progressão/wait/retorno. Recovery usa comando/resultado/
linhagem originais e planner atual para preparar `record_wait`/`enqueue_outbox`
separadamente com CAS e transition proof correntes. Esse caminho é declarado e
recuperável após crash; não reaplica resultado duplicado para inserir intents,
não chama source.execute e não promete atomicidade retroativa.

Atomicidade local é somente a TX tenant dos elementos explicitamente declarados.
Nenhuma TX/lock do journal atravessa source I/O, e não há promessa de exactly-once
distribuído. O outbox OP10 da fonte pertence ao commit do fato na fonte; outbox
local não o substitui. Intents de reply causados por resultado/inbox entram em
`outbox_intents` dos métodos atômicos congelados. `enqueue_outbox` isolado não
declara atomicidade retroativa com um resultado já gravado.

Os ajustes de `CapabilityService`/`CapabilityAdmission`, source transport e
composition roots são pontos compartilhados **serializados** pelo orquestrador.
O driver tem write set novo e separado do PostgreSQL journal/migration.

## Interrupção, concorrência e retorno

Saúde/humano são observações do ingress confiável da mensagem atual, nunca regras
reexecutadas pelo driver ou flags inferidas do LLM. Saúde vence; pedido humano
interrompe o administrativo. Conferir currentness antes de cada novo efeito e
antes de reply/resume. Se uma interrupção ocorrer depois do fence, conservar o
comando e reconciliar seu receipt; não apagar caso, cancelar matrícula/reserva ou
liberar a chave. Novas admissões desligadas não abandonam casos aceitos.

Troca de assunto aplica CAS na conversa separado de CAS do journal. Conservar
caso/protocolo, pending commands/waits e replies. Nova journey/handoff precisa de
ack real e link governado; retomar a antiga exige intenção e autoridade atuais.
Roots do saver continuam derivados por task+tenant+journey com o Pseudonymizer
injetado, sem confiar apenas em `checkpoint_ns` ou aceitar raw thread/config.

Inbox verifica produtor/signature, tenant/legal entity, mesmo objeto, correlação,
causa, fonte/revisão e vínculo do receipt. Evento repetido com mesmo digest é
idempotente; mesmo ID/digest diferente é conflito; stale/out-of-order é decidido
pela fonte qualificada, sem ordenar Ref opaca numericamente. Nunca ack antes de
commit durável de inbox+continuação+outbox. Evento tardio depois de revogação pode
exigir custódia/reconciliação protegida, mas não libera novo efeito ou disclosure.

Outbox possui produtor/consumer conectados, leases/fences e chave idempotente
original. Claim expirado não demonstra ausência de envio. `attempted`/`unknown`
exigem receipt/reconciliação do provider; preparação/offset HTTP/Kafka não prova
entrega. Reply perdido não apaga source commit nem vira journey completada; não
reenviar matrícula/reserva para produzir outra resposta.

## Write sets futuros e critérios de entrega

O JSON separa: driver/topologia/retorno e consumer de jornada novos; journal PG e
migration do steward; service/admission e composition roots serializados; fonte,
conduta/YAML/Card/policy sob gates próprios. A implementação preserva os consumers
de um passo existentes e usa o mesmo mecanismo para as duas journeys.

Verificar os percursos completos por ingresso candidato autenticado → preparação
→ journal → admission/audit → fonte → receipt/inbox → CAS/continuação → outbox →
reply/resume. Testes UNIT podem usar doubles explicitamente identificados; isso
não qualifica source/engine. PostgreSQL real qualifica races, crashes e atomicidade;
integração AUTH usa CIB real, sem engine mock. Providers ausentes mantêm gate de
integração bloqueado e CI qualificado, sem fixture de produto fabricada.

Gates incluem: necessidade→opções→aceite→emissão/reserva→entrega/recovery; caso→
encaminhamento→espera→resume→S4 OP07; saúde/humano/troca de assunto; dois tenants,
dois tasks e journeys concorrentes; stale/revoked/erasure; duplicata/mesma chave
divergente; crash antes/depois de fence e commit; timeout após efeito; outbox/reply
perdido; hold diferente de confirmação; matrícula separada de AUTH; S4 sem oferta,
desacordo, timeout e fechamento sem confirmação; feedback não bloqueante; dois
consumidores no mesmo código/versão; negativas/fraude/ADEQUACAO sob fronteiras
atuais. A prova precisa falhar ao remover origem, registro, port, consumer ou
retomador. Código, contrato, source acceptance, ativação e produção têm estados
separados, sem claim de jornada completa pela presença de um wrapper de steps.


# Journey effect authority amendment — R4 third repair

**Status:** Proposed, pending original independent reviewer's delta. **Date:** 2026-10-05. **Repair author:** source_runtime_readiness_steward, distinct from architect and verifier.

R3 and original frozen inputs remain immutable. This repair addresses `DUR-CONTRACT-R3-F01` and `DUR-CONTRACT-R3-F02` from the exact original review SHA256 `862f2c247175bd69d3736b93a6f4a2249c55d5f9d0c0aaf78034250e46a0128b`. It preserves original transition/action/binding propagation, explicit default-empty journey source mapping, native hard human authority and no legacy fallback.

The original `JourneyEffectAuthority` alone does not carry a previously narrowed deadline. A closed private `JourneyInvocationCheckpoint` now accompanies each irreversible source/target invocation. It contains the original immutable context, first and latest accepted transition currentness with fixed anchor and accumulated minimum deadline, and a discriminated operation or existing-domain authority snapshot. Exact closed fields and signatures are normative in `journey-orchestration-contract.json#/journey_effect_authority_amendment`. Gate controls are in the same embedded object under `required_gate_controls`.

For a capability effect, `OperationInvocationAuthority` transports the exact original existing `VerifiedAuthority`, first and latest same-original `VerifiedCurrentness`, envelope/request digests, full binding, fixed authorization/publication/ratification/task/Card/currentness pins, nondecreasing checked time and accumulated minimum deadline. It exposes no private lease. Trusted DUR3 captures this snapshot over its original lease after final awaited checks; source receives the exact checkpoint, authenticates original owner evidence and pins the upstream canonical snapshot. Valid authorization B for the same request cannot replace original A.

For an existing-domain handoff, qualified source-owner `ExistingDomainInvocationAuthorityPort` obtains original native domain/human evidence for the exact route/binding/action and authenticates its original authorization, publication, contract, protected evidence reference/digest, fixed currentness anchor and source-owned validity. That native evidence and its accumulated minimum are transported in the domain checkpoint. No commercial operation grant, transition proof, OP12 snapshot, ACK or new generic human claim fills an absent native authorization. Missing accepted native contract or authentic evidence resolution denies the dependent handoff before effect-target dispatch. Source-owned native hard boundaries remain mandatory.

`JourneyCapabilitySourcePort.execute_under_authority` accepts only the operation `invocation_checkpoint`. `ExistingDomainHandoffPort.execute` accepts only the domain `invocation_checkpoint`. Every receiving qualified source/target reparses a deep copy, verifies authentic same-original source-owner evidence through trusted root-injected typed ports and pins the exact received checkpoint; neither record construction nor structural protocol conformance grants authority. It preserves all earlier accepted minima while checking after **all internal awaits**, including verification/resolution awaits. The final synchronous BOTH check compares original transition and original operation/native pins, every narrowed ceiling and trusted time strictly before the effective minimum; any subsequent await requires that check cycle again. The source/native contract enforces these constraints atomically at actual effect/revision/idempotency commit. Starting an HTTP request is not assumed atomic effect. If the existing native interface cannot preserve original authorization identity and narrowed ceilings at its true commit boundary, it remains unqualified; no new external wire is introduced by this proposal.

Counterexample closure: original expires at t+60, upstream accepts t+5, source enters t+1 and awaits t+6. The received checkpoint retains t+5; a fresh proof through t+60 is refused and effect count stays zero. The analogous native-domain handoff uses its independently authenticated original native grant and the same irrevocable upstream deadline rule. All accepted currentness checks may shorten and never extend; original authorization/currentness anchors never change.

ROOT owns a narrow private admission snapshot API and `_durable_assert_current` synchronous final check. `_durable_before_disclosure` is nondestructive, allowing final BOTH comparison after the last await before disclosure; private lease release follows in final cleanup. Existing legacy nonjourney compatibility behavior is outside this repair. No broad admission rewrite, arbitrary callback, public bearer lease or producer-manufactured field is admitted.

Acknowledged fences and literal original keys survive later refusal/expiry; only receipt-first reconciliation follows an uncertain effect. No reset/resubmit/pre-dispatch reclassification after accepted fence. Genuine historical source facts survive later disclosure rejection.

Independent review must reproduce narrowed transition/operation deadline, A→B substitution, source/target internal await expiry/revocation, mutation/anchor substitution, domain native authority separation, positive once-only dispatch and nondestructive disclosure controls. UNIT ports remain synthetic mechanics; supplier atomicity/publication/runtime, human policy and complete operational journeys remain separate gates. This document introduces internal transport/evidence records only, not new business enums, source APIs, table entities, policies, provider wire or production flags.

Canonical four-file references deliberately use each Markdown's sibling JSON embedded amendment. Private `amendment.json` is review custody and is not an extra integration artifact.


# R5: exact driver-facing existing-domain effect boundary

**Status:** Proposed, pending original independent reviewer delta. **Repair author:** journey_gateway_author, distinct from R4 author and reviewer. This narrow third repair addresses only `DUR-CONTRACT-R4-F01` in original R4 review SHA256 `ec855a6c1a2f3ccd9cfe026edd1b120d3749707aae94bfdeac1b912744803c7f`. R4 snapshots and all R4 records, fields, minima, authority separation and source/target obligations remain unchanged.

The concrete internal `ExistingDomainEffectBoundary` exposes this exact driver-facing signature:

```python
ExistingDomainEffectBoundary(
    *,
    clock: Callable[[], datetime],
    transition_authority: JourneyEffectAuthorityPort | None = None,
    native_authority: ExistingDomainInvocationAuthorityPort | None = None,
    target: ExistingDomainHandoffPort | None = None,
)

async def execute(
    self,
    binding: JourneyBinding,
    snapshot: JourneySnapshot,
    action: PreparedDomainHandoffAction,
    *,
    effect_authority: JourneyEffectAuthority,
) -> OutboxDescriptor | CapabilityRefusalReason: ...
```

`JourneyDriver` receives `domain_handoff: ExistingDomainEffectBoundary | None = None`. It captures the already admitted original entry context and calls `boundary.execute(binding, snapshot, action, effect_authority=original_context)`. It never assembles a native grant or `JourneyInvocationCheckpoint`. `ExistingDomainHandoffPort` remains the irreversible target-only interface from R4; ROOT never installs it directly as this driver dependency.

ROOT injects independently qualified transition verifier, original native-domain verifier and exact existing target. All three qualifying dependencies default absent. Missing boundary or any dependency refuses before native-authority/target invocation. The required injected clock measures trusted operational aware time; it does not select a regulatory deadline or new authority duration. No dynamic discovery, signature adaptation, arbitrary callback or legacy target fallback qualifies a dependency.

Before any authority or target await, the boundary reparses deep copies and verifies exact original binding, journal snapshot binding, domain action/digest, `request_sha256=None`, original entry transition/topology/contract/lineage and original validity ceiling. It pins canonical originals privately, obtains original native grant only from `native_authority.authorize_original(original_context_copy)`, then verifies current transition and same-original native authority through the two qualified typed ports after all preparation awaits. Original native authorization, protected evidence reference/digest, admitted contract/publication and currentness anchors remain fixed; checked time cannot go backwards or into the future, and every accepted deadline only narrows. Transition currentness stays bound to its first accepted anchor.

After the final awaited authority/preparation step, BOTH original transition/context and native grant/evidence pins and accumulated minima are checked synchronously against trusted time. The boundary forms the existing-domain checkpoint from that authenticated evidence and immediately calls only `target.execute(binding_copy, snapshot_copy, action_copy, invocation_checkpoint=exact_domain_checkpoint)`. There is no unrelated await between final check and this call. The target retains all R4 requirements to authenticate the received original checkpoint and enforce BOTH authorities after all target-internal awaits at its own atomic irreversible native effect/fence/revision/idempotency boundary. No commercial operation grant or journal fence fills absent native authority.

Authority-dependent disclosure after the target await repeats same-original transition/native checks and final synchronous BOTH comparison. Refusal preserves genuine target facts/native uncertainty and existing original receipt-only recovery; it cannot resend the target or fabricate an ACK/outbox. Target source qualification and native human authority remain separate gates.

Exact JSON declarations and executable gate criteria are normative in `journey-orchestration-contract.json#/journey_effect_authority_amendment`, including `ports.ExistingDomainEffectBoundary`, `propagation.JourneyDriver.domain_handoff` and the four R5 controls under `required_gate_controls`. Missing dependency yields zero target calls; binding/action poisoning denies before authority calls; expiry/revocation/substitution during native authorize/currentness or transition checks yields zero effect calls; target-internal await expiry/substitution yields zero irreversible effects. Positive unchanged authentic originals transport the exact checkpoint and permit one effect. No source implementation, source/provider acceptance, human ratification, DB/engine acceptance or production activation is claimed by this contract repair.
