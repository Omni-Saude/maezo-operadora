# W1 — contrato técnico compartilhado de durabilidade

**PROPOSED, default disabled, sem grant/signoff.** Este contrato define journal,
CAS, inbox/outbox e recuperação técnica comuns a Compras e Suporte. Não publica
estado de negócio, finalidade, autoridade, recibo ou wire de fornecedor. Os mapas
fechados de registros/métodos estão em
[durability-contract.json](durability-contract.json); esse arquivo é a interface
para revisão independente antes de autoria de código.

Base desta proposta: `a62c05008f4d2e13af83cee7f6a61e913b51dcab`, no checkout
`/Users/familia/.codex/worktrees/v21-capability-execution/maezo-operadora`, branch
`codex/v21-capability-execution`. A admissão original permanece no bundle v2/v2.1
preservado do ROOT, baseline `e7b14522a4f70242504d2b152a57ee5269ee66e2`.
`administrative-contracts.md:182-184` admite mecanismos candidatos reversíveis
de estado/receipt/espera; binding/read/effect continua sujeito aos gates W0.

Reparo documental por terceiro após o gate **REVISE DJ-F01/DJ-F02/DJ-F03**.
O freeze original e os quatro snapshots permanecem imutáveis em
`docs/gates/v21-capability-execution-20261004/initial-durability-journey-freeze.json`.
Este delta explicita transições e o argumento técnico `wait_intents` antes de
código; continua **PROPOSED**, aguardando o delta do reviewer original. Não
altera envelope/DTO externo nem admite código, fonte, grant ou produção.

## Fronteiras e invariantes

- Os **12 campos do CapabilityEnvelope e os 11 request/result DTOs permanecem
  intactos**. `expected_business_revision` é `Ref`/string opaca existente;
  `business_revision`/`settlement_revision` continuam refs emitidas pela fonte.
  Somente `journal_revision` é inteiro técnico local. Não comparar refs de revisão
  lexicalmente nem derivar próxima revisão de contadores, casts ou clock local.
- A chave é exatamente `envelope.idempotency_key` supplied pelo caller admitido.
  O journal não cria compositor, concatenação ou hash novo de chave de negócio.
  Unicidade técnica cobre environment/tenant/legal entity/operation/key literal;
  o digest existente vincula envelope completo e request, inclusive journey.
  Reuso da mesma chave com outro request/journey/binding é conflito, não alias.
- `models.py::request_digest` e `admission.py::result_digest` são reutilizados
  pelo papel; não criar segunda canonicalização/hash ou recomputar identidade de
  pessoa. Digest não anonimiza dados pessoais e não concede autoridade.
- Journal registra o que o gateway fez/observou. Fonte continua dona da revisão,
  atomicidade do efeito, status, receipt e reconciliação. Checkpoint é cache técnico
  de execução; `conversa_agente_ativo` é roteamento; nenhum dos dois liquida negócio.
- Após persistir fence de dispatch, **crash, cancelamento, timeout, lease vencido,
  erro SQL ou `SOURCE_UNAVAILABLE` não autorizam reenviar**. O comando fica cercado
  para reconciliação da identidade original. Não existe API W1 reset/retry/release
  que o transforme novamente em comando novo.
- `RESPONSE_RECORDED` significa resposta da fonte verificada e registrada; ela
  pode ser pending/refused/review_required. Não significa matrícula, reserva,
  entrega ou resolução completada. Unknown nunca promove fato confirmado.
- O próximo envelope é fornecido por planner qualificado do domínio, a partir
  das fontes/contratos atuais. Driver não incrementa source refs nem escolhe novo
  purpose/case-kind/authority/receipt. S4 continua OP07; Suporte não recebe OP03.

## Registros técnicos fechados

Todos são internos, server-side e default disabled. Reparse estrito, campos extras
recusados, enums técnicos fechados, bool nunca aceito como int, instants aware
UTC, refs não vazias e digests pelo tipo existente. Handles não são bearer grants;
o port verifica binding autenticado em cada chamada. Nenhum registro é input de
browser, mensagem, LLM ou source payload sem verificação independente.

| Registro | Papel e dados mínimos |
|---|---|
| `JournalBinding` | Environment/tenant/legal entity/journey/principal/task e referência da política de dados, provenientes da composição confiável. Operation/purpose/authority permanecem em `AdmissionBinding` |
| `CommandDescriptor` | Envelope existente, request protegido por referência, digest existente, digest do AdmissionBinding esperado e predecessors locais da mesma jornada |
| `CommandHandle` | Binding, command_ref técnico local e request_sha256; identifica o comando durável sem autoridade externa |
| `CommandSnapshot` | Handle, journal_revision, estado técnico, dispatch_ref/fence_version e referências/digests do último resultado verificado; nenhum source status novo |
| `JourneySnapshot` | Binding, journal_revision e listas de command/wait/outbox refs; projeção de coordenação, sem enum de estágio/negócio |
| `DispatchEvidence` | `VerifiedAuthority`/`VerifiedCurrentness` existentes e audit receipt do gateway, ligados ao request e binding atuais; não simples campos afirmando ALLOW |
| `DispatchToken` | Handle, dispatch_ref, fence_version e revisão local do commit reconhecido; coordena uma tentativa possível, não autoriza fonte |
| `VerifiedResultObservation` | `VerifiedSourceResult` existente, result_ref protegido, result digest, observer/provenance refs e witness de ordem de revisão quando substitui head |
| `VerifiedInboxObservation` | Event/source/producer/contract refs autenticadas, digest, handle e VerifiedResultObservation; vínculo de identidade/correlação verificado antes da transação |
| `WaitDescriptor` | `ExternalWaitIntent` existente, handle e instante opcional de wakeup operacional; deadline regulatório somente pela referência autoritativa já contratada |
| `WaitSnapshot` | WaitDescriptor, estado técnico e referência da observação que motivou retomada; timeout local não vira `wait_status=timed_out` |
| `OutboxDescriptor` | Payload protegido/ref/digest, causalidade e target binding server-side já registrado; kind técnico e dedupe identity estáveis; não endereço/canal/recipient arbitrário |
| `OutboxSnapshot` | Descriptor, revisão local, estado, claim/worker/lease/fence/delivery refs e acknowledgement verificável do transporte contratado; sem inferir entrega ou compromisso |
| `OutboxAckEvidence` | Evidência autenticada do target sobre outbox/delivery/payload, verificada independentemente; ACK de transporte não é recibo de entrega OP09 |
| `PreDispatchRefusal` | Motivo técnico existente/fechado e referência de evidência antes de qualquer fence; não reclassifica falha pós-dispatch |
| `JournalCallResult` | Status técnico `recorded/unchanged/conflict/unavailable/uncertain`, snapshot específico opcional e motivo fechado sem payload/SQL/stack trace |
| `RecoverySnapshot` | Binding/revisão local e referências para reconciliação de comandos/outbox/esperas; next_cursor é paginação protegida, sem sucessor de negócio |

Request/result permanecem em custódia própria aprovada, com referência e digest
no journal. `request_ref`/`result_ref` não criam um novo object store ou autorização
de leitura. A política de dados precisa cobrir conteúdo, metadados e refs; sem
custódia/retention/erasure qualificados, escrita real e recovery permanecem negados.

## Estado do comando e CAS

O conjunto técnico é `RECORDED`, `DISPATCH_FENCED`, `UNCERTAIN`,
`RESPONSE_RECORDED`, `REFUSED_BEFORE_DISPATCH`. Não há `COMPLETED`, `ISSUED`,
`CONFIRMED`, `DELIVERED` ou `RESOLVED` no journal.

| Origem → destino | Método exato | Condição obrigatória |
|---|---|---|
| Ausente → RECORDED | `record_command` | Parse fechado; data gate; scope/binding; chave literal única; request digest; insert e revisão local na mesma transação |
| RECORDED → REFUSED_BEFORE_DISPATCH | `record_pre_dispatch_refusal` | Recusa antes de qualquer fence/I/O; não classificar falha depois de fence como pre-dispatch |
| RECORDED → DISPATCH_FENCED | `begin_dispatch` | Admission enforcing + audit durável + CAS; commit reconhecido antes da fonte |
| DISPATCH_FENCED → UNCERTAIN | `mark_uncertain` | Falha/timeout/cancelamento/crash; sem prova de resultado verificado |
| DISPATCH_FENCED/UNCERTAIN → RESPONSE_RECORDED | `record_verified_result` | Primeiro resultado direto ou consulta de receipt verificados, sobre dispatch/request originais; não exige nem fabrica event_ref |
| RESPONSE_RECORDED → RESPONSE_RECORDED | `record_verified_result` | Consulta posterior autenticada pode substituir head somente com witness da fonte; duplicata exata é unchanged |
| DISPATCH_FENCED/UNCERTAIN → RESPONSE_RECORDED | `ingest_verified_observation` | Primeiro callback com event_ref realmente autenticado, mesmo sem resposta síncrona; inbox e apply atômicos |
| RESPONSE_RECORDED → RESPONSE_RECORDED | `ingest_verified_observation` | Evento real posterior com witness, ou replay exato do inbox sem nova aplicação/efeito |

Ambos os métodos aceitam exatamente os três estados acima, conforme
`allowed_command_states` e `command_transitions` no JSON. Consulta pontual sem
evento segue `record_verified_result`; `QualifiedSourceRecoveryPort` fornece a
observação verificada ao gateway confiável sob autoridade independente atual de
leitura. Não há alias de lookup para callback, event_ref inventado ou execute de
fallback. Receipt de transporte não se transforma em `VerifiedSourceResult`.

`result_head_rules` exige scope/request/fence e prova corrente antes de qualquer
apply ou dedup. Mesma revisão com digest/receipt divergente é conflito. Revisão
mais nova só substitui o head com witness qualificado que vincule ambos os heads
ao comando original. Revisão antiga/incomparável, witness ausente ou currentness
stale/revogada recusam sem substituir head nem criar waits/outbox. Um replay de
evento já aceito exige novamente acesso/prova atuais, retorna unchanged e nunca
reaplica seu head antigo sobre um novo. Duplicata não acrescenta intents ou revisão;
preparação posterior usa explicitamente os métodos separados, sob CAS novo.

Todas as mutações exigem `expected_journal_revision` do reader autenticado.
Primeiro journal local começa em 0; insert reconhecido avança para 1; cada mudança
local aceita incrementa exatamente uma vez. `UPDATE ... WHERE revision=expected`
afetando zero rows retorna conflict. Nenhum efeito remoto ocorre a partir de um
CAS perdido. Duplicata idêntica lê o registro existente sem nova revisão/dispatch;
duplicata divergente nunca substitui bytes, binding, tenant ou identidade.

CAS cobre a unidade da jornada: command/observation/inbox/wait/outbox e a revisão
local mudam na mesma transação PostgreSQL. Um writer atrasado relê o journal e
reavalia somente o merge local. Isso não implica retry de fonte. Source revisions
são opacas; somente witness do contrato/owner determina igualdade, precedência
ou conflito. Ordem de Kafka offset, timestamp e arrival não substitui o witness.

## Port e ordem do gateway

O port candidato é `DurabilityJournalPort`; métodos e argumentos exatos estão no
JSON. Todos exigem `binding`; toda mutação exige CAS. A camada gateway executa
`begin_dispatch`, `record_verified_result` e `mark_uncertain`. Driver não aceita
claims de autoridade ou escreve resultado de fonte; usa record/observe/recover
e um serviço durável composto pelo gateway.

1. Driver valida task/OP/payload/handoff atual e prioridade saúde/humano. Com
   feature/política de dados negadas, não persiste narrativa ou request sensível.
   O service valida os DTOs fechados e registra comando/ref/digest no journal.
2. Gateway obtém admission enforcing, currentness e audit receipt atuais. Se a
   chamada é recusada antes de fence, registra recusa técnica; sem fonte, nenhum
   fato/receipt é fabricado.
3. `begin_dispatch` persiste fence e audit linkage por CAS. Commit local incerto
   bloqueia I/O até readback autenticado da mesma chave/digest esclarecer o fato.
4. Após o await do journal, gateway revalida currentness da **mesma lease e digest**
   antes da fonte. O provider também verifica autoridade/revisão atomically no
   commit do seu próprio efeito; check local não elimina race distribuída.
5. Gateway chama source adapter uma vez com envelope/request originais. Não
   mantém transação/lock PostgreSQL aberto durante rede/CIB/API. Qualquer falha
   depois de fence é uncertain; finally libera somente lease de invocação.
6. Resultado é parseado nos DTOs existentes e verificado pela autoridade da fonte.
   Journal armazena referência/digest/evidence e eventual intent local de retorno
   na mesma transação. Commit incerto impede disclosure até readback, sem chamar
   source.execute outra vez.
7. Gateway revalida source/result/currentness depois de awaits de persistência
   e antes do retorno. Revogação/erasure/health/human/current message pode bloquear
   disclosure/resume; não apaga efeito confirmado, não o transforma em failure e
   não libera replay. Retorno usa principal/tenant/journey/case atuais qualificados.

### Seam que precisa de integração serial

No SHA-base a62, `CapabilityService.execute` não recebe journal, e
`CapabilityAdmission.revalidate(before_source)` consome uma fase privada
`authorized→dispatched`; `before_disclosure` termina a lease. `verify_result`
retorna `None` e mantém `VerifiedSourceResult` privado. A integração deve expor
evidence/audit linkage somente ao boundary confiável e acrescentar revalidação
privada depois de fence e depois da persistência do resultado. **Não chamar a
fase one-shot duas vezes, modificar o wire, fingir que uma lease antiga é atual
ou aceitar async callback arbitrário como autoridade.** Esse delta tem autor,
verificador e gate próprios. Driver e journal não contornam o admission.

## Atomicidade, inbox, outbox e espera

Extensão técnica proposta explícita para **DJ-F02**, ainda sem código: os métodos
`record_verified_result` e `ingest_verified_observation` recebem
`wait_intents: tuple[WaitDescriptor]`; ausência de novos waits é tuple vazia
explícita, sem default/alias de assinatura. Os dezesseis métodos e dezessete
records são os mesmos; nenhum request/result da fonte ganha campos.

| Método | Argumentos fechados, definidos pelo JSON | Elementos da mesma TX local |
|---|---|---|
| `record_verified_result` | binding, handle, dispatch_ref, observation, outbox_intents, wait_intents, expected_journal_revision | Head/observação/result refs; criação dos WaitDescriptor declarados; OutboxDescriptor declarados; revisão local |
| `ingest_verified_observation` | binding, observation, wait_ref, outbox_intents, wait_intents, expected_journal_revision | Inbox e head/observação/result refs; novos waits declarados; wait_ref existente qualificado; outbox declarado; revisão local |

`atomic_method_entities` liga cada argumento às entidades exatas. Falha de prova,
descriptor, link, CAS ou constraint faz rollback da chamada inteira; mutação aceita
incrementa a revisão uma vez. `wait_intents` cria somente `OPEN`; descriptor já
idêntico preserva seu estado, e divergência recusa. Não reabrir wait liquidado.
`wait_ref` de inbox deve existir no início da TX, casar produtor/correlação e ter
mapeamento de settlement publicado; não pode ser criado/reaberto simultaneamente
por wait_intents. Resultado direto não liquida wait antigo por inferência.

A continuação qualificada da jornada é preparada antes da chamada na custódia
existente. Sua referência/digest entram em `OutboxDescriptor.payload_ref`/
`payload_sha256`, dentro de `outbox_intents`, com target de retorno já registrado.
A TX grava essas referências, sem corpo de continuação, cursor de negócio ou tabela
nova. A preparação do corpo na custódia é separada: não há promessa de TX conjunta
sem interface same-connection qualificada própria. Se a preparação faltar, tuple
vazia pode registrar somente o resultado; não declara progressão/espera/retorno.
Recovery relê a linhagem/source/planner correntes e usa `record_wait`/`enqueue_outbox`
como preparações CAS separadas, nunca atomicidade retroativa ou replay de resultado
duplicado para inserir intents. O comando remoto não é reenviado.

| Unidade | Garantia candidata e limite |
|---|---|
| Local PostgreSQL | Apenas elementos representados nos argumentos e em atomic_method_entities unem revisão/journal/observação/inbox/outbox/wait. Custódia prévia e record_wait/enqueue_outbox separados não pertencem a essa TX. Audit same-connection exige sink qualificado; pool/schema/roles/grants e rollback/commit uncertain precisam de prova real |
| Fonte remota | Não participa da transação local. Autoridade do domínio possui seu command idempotency, business CAS, receipt e lookup/reconciliação publicados. W1 não promete exatamente uma vez remoto |
| Source outbox | OP10 exige outbox no commit do fato de negócio da fonte. Journal local não fabrica essa atomicidade e não substitui source outbox por evento do agente |
| Inbox local | Autenticação de produtor + source contract + objeto/correlação/revisão antes do apply; journal/inbox/projeção/wait/outbox local unidos. ACK de consumo apenas depois de commit reconhecido, duplicata exata ou quarentena durável qualificada |
| Outbox local | Intent estável de coordenação/retorno; target binding existente, payload ref, digest e dedupe identity preservados. Ack de transporte não é delivery receipt nem domain completion |
| Wait local | `OPEN`, `ELAPSED`, `OBSERVATION_RECORDED` são estados técnicos. Wakeup/lease operacional não é deadline regulatório, `completed` ou negativa; producer/source outcome atual liquida o DTO OP08 |

Inbox dedup namespace: environment/tenant/legal entity/source authority/producer/
contract revision/event_ref. Mesma identidade+digest+link é duplicata; mesma
identidade com digest ou target diferente é conflito e não sobrescreve a primeira
observação. Evento forjado, estranho à allowlist, stale sem witness, cross-tenant,
cross-case/journey/principal ou com fields output plantados nunca retoma ninguém.
Quarentena reutiliza o transporte/custódia admitidos; não cria topic/canal novo.

Outbox tem estados `RECORDED`, `CLAIMED`, `DELIVERY_FENCED`, `UNCERTAIN`,
`ACK_RECORDED`. Claim/lease apenas distribui trabalho local. Claim vencido pode ser
reobtido **somente antes de qualquer DELIVERY_FENCED**, com fencing version e CAS
que rejeitem worker antigo. Após fence/crash/timeout, recovery consulta o destino
contratado pelo mesmo event/command id; não reenvia por lease ou timer. Caso o
transporte/fonte admita redelivery exata e possua dedup atômico, seu contrato e
gate específicos devem provar esse comportamento antes de acrescentar retry.
Este contrato W1 não acrescenta tal permissão/API.

`record_wait` conserva `ExternalWaitIntent.wait_ref`, expected producer,
correlation/deadline/source outcome refs existentes. `mark_wait_elapsed` registra
só wakeup operacional comprovado; ausência de resposta continua desconhecida.
Observação tardia autenticada pode registrar fonte/revisão sob contrato sem mudar
ELAPSED em negação/resolução. Tradução de `completed/timed_out/incident` pertence
ao source outcome OP08 publicado e é mutuamente consistente na revisão do domínio.
`agent_resume` e `LucasRetomada` mantêm seus comandos atuais; nenhum generic resume,
direct completion ou acesso arbitrário a checkpoint é instalado por esta proposta.

## Persistência lógica e privacidade

O JSON define entidades candidatas, índices/constraints e campos; não é DDL nem
migração aplicada. Entidades são separadas das tabelas upstream de LangGraph,
do router de conversa e dos outboxes humanos/A2A existentes. Não clonar esses
componentes globalmente nem alterar sua política TTL/lease para novas jornadas.

Guardar somente envelope/ref/digest, scoped binding, revisão/estado técnicos,
instants operacionais, evidence refs e causalidade necessários. Texto, nome,
telefone, CPF/MPI/FHIR ID, source payload, segredo/token, health narrative e stack
trace não entram no journal/outbox/log. Ref opaca/digest continua protegida;
classificação, purpose e policy vêm das fontes/gateway, não de defaults novos.

Dados/payloads protegidos permanecem na custódia qualificada com access/retention/
erasure próprios. Sem política ratificada, schema/roles/proteção e destino instalados,
o journal real permanece disabled. Retenção de dedup/fence/prova para evitar replay
e apagamento de conteúdo exigem decisão explícita do owner/DPO; não definir prazo
ou tombstone eterno. Erasure não reabre comando incerto nem recria conteúdo apagado.
Se a política exigir apagar refs, recovery/disclosure fica bloqueado; minimal fence
somente sob retenção aprovada. Logs/metrics usam códigos bounded e agregados sem
IDs de jornada/command/receipt como labels; k10/None da população permanece separado.

## Dependências, autoria e gates

| Pacote/DAG | Write set candidato exclusivo | Entrada/saída |
|---|---|---|
| DUR0 contrato | Estes dois documentos e evidence prefix `durability-contract-` | Verificador distinto reproduz tipos/maps/invariantes; freeze de hashes antes de código |
| DUR1 PostgreSQL | `gateway/capabilities/durability/{models.py,ports.py,postgres.py,schema.sql,__init__.py}` e testes próprios | DUR0 PASS; storage default disabled, sem source. Migração/publicação serial por owner, com DBA/privacy gates e prova PG real |
| DUR2 jornada | `agents/lucas/administrative/journey.py` e testes próprios; alteração de graph/state serial se necessária | DUR0 PASS; usa port congelado e planner qualificado, mesmo núcleo JR1/JR2; source absent permanece gap |
| DUR3 gateway/driver | `gateway/capabilities/service.py`, `admission.py` e testes próprios; composição/registry serial ROOT | DUR0 PASS e interfaces DUR1/2; conserva source ordering/currentness/audit/private phases, sem novo wire/source/grant |
| DUR4 verificação | Evidência exclusiva; nenhum source edit ou autoria | Reproduz PG/CAS/crash/replay/inbox/outbox/gateway/journey; findings vão a reparador distinto |
| DUR5 integração/CI | Composição, imports e migração/deltas compartilhados serializados ROOT | Gates das partes; CI aplicável, exact SHA/fingerprint e gates finais frescos independentes |

Paths de código/testes são propostas a confirmar pelo ROOT; não há autorização
de migration/deploy neste dossiê. DUR1 e DUR2 podem avançar em paralelo somente
após freeze/interface gate com writes disjuntos. DUR3 shared edits e composição
final ficam serializados. Suite PG/CIB pesada usa lane coordenada. Autor não assina
gate; reparo por terceiro e delta pelo reviewer original permanecem obrigatórios.

Gates para mecanismo: interface fechada e freeze; PG/schema/role isolamento e CAS;
audit same-transaction ou linkage qualificado; privacy/retention/erasure; source
reconciliation semantics; fonte/task/channel/tool/action enforcing; planner e
return currentness; consumidores antigos; reuso JR1/JR2; testes reais e reviewer
independente. Grants/ratificação/providers continuam vazios e flags default off.

## Verificação necessária e blockers por efeito

Testar mecanicamente a matriz: pending→consulta posterior sem event_ref;
FENCED/UNCERTAIN→primeiro callback; duplicate identical/divergent; revisão antiga,
prova stale ou witness ausente sem alteração do head; cada elemento prometido na
TX ligado a argumento/entidade declarados; tipo/fence/claim/lease/binding de reply;
CAS winner/loser; tenant/
legal entity/journey/task/principal cross-scope; extra/output fields; crash antes
record commit, depois fence, depois source commit e antes resultado, depois inbox/
outbox commit e antes ACK; cancellation/DB commit uncertain; stale worker/fence;
revogação/erasure nos awaits; source pending/unknown; forged/replayed/reordered
event; source revision opaca; wait ELAPSED sem completion; same code JR1/JR2;
saver/checkpoint isolation; input plantado; old consumers; no remote execute count
increment after fence recovery. Teste de fake é evidência do mecanismo, não provider.

| Efeito/família | Blocker que permanece |
|---|---|
| OP01 acesso | CR-F1: fonte/finalidade/identidade/freshness atual; ClinicalContextPort sem consumidor/injeção qualificados |
| OP02 oferta | CR-F2: catálogo/termos/availability/regras não clínicas reais e planner autorizado |
| OP03 aceite / OP04 matrícula | CR-F3: autoridade de cliente e issuer, recibos separados, fonte com lookup idempotente; não AUTH/portal membership |
| OP05 reserva / OP06 fulfillment | CR-F4: transação/provider/expiry/receipt e fato source-owned, reconciliação do comando original |
| OP07 caso / OP08 espera / OP11 feedback | CR-F5: case kind/status/role/audience e S4 publicados; produtor/outcome atual; timeout/silêncio não resolve |
| OP09 aviso / OP10 marco | CR-F6: conteúdo/recipient/sender/canal/receipt atuais e publisher/source outbox reais; journal ack não prova delivery |

Mecanismo durável habilita recuperação reviewable; **jornada completa continua
dependente desses gates/fornecedores**. Nada neste contrato mede ROI, publica AMH,
liga ambiente real, ratifica decisão humana, mistura jurisdição clínica/financeira
ou altera permissões hard. Os anexos de evidência demonstram checks de autoria;
review independente e aceitação runtime permanecem pendentes.


# Journey effect authority amendment — R4 third repair

**Status:** Proposed, pending original independent reviewer's delta. **Date:** 2026-10-05. **Repair author:** source_runtime_readiness_steward, distinct from architect and verifier.

R3 and original frozen inputs remain immutable. This repair addresses `DUR-CONTRACT-R3-F01` and `DUR-CONTRACT-R3-F02` from the exact original review SHA256 `862f2c247175bd69d3736b93a6f4a2249c55d5f9d0c0aaf78034250e46a0128b`. It preserves original transition/action/binding propagation, explicit default-empty journey source mapping, native hard human authority and no legacy fallback.

The original `JourneyEffectAuthority` alone does not carry a previously narrowed deadline. A closed private `JourneyInvocationCheckpoint` now accompanies each irreversible source/target invocation. It contains the original immutable context, first and latest accepted transition currentness with fixed anchor and accumulated minimum deadline, and a discriminated operation or existing-domain authority snapshot. Exact closed fields and signatures are normative in `durability-contract.json#/journey_effect_authority_amendment`. Gate controls are in the same embedded object under `required_gate_controls`.

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

Exact JSON declarations and executable gate criteria are normative in `durability-contract.json#/journey_effect_authority_amendment`, including `ports.ExistingDomainEffectBoundary`, `propagation.JourneyDriver.domain_handoff` and the four R5 controls under `required_gate_controls`. Missing dependency yields zero target calls; binding/action poisoning denies before authority calls; expiry/revocation/substitution during native authorize/currentness or transition checks yields zero effect calls; target-internal await expiry/substitution yields zero irreversible effects. Positive unchanged authentic originals transport the exact checkpoint and permit one effect. No source implementation, source/provider acceptance, human ratification, DB/engine acceptance or production activation is claimed by this contract repair.
