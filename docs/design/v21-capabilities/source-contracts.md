# W0 — pedidos de contrato de fonte e qualificação v2.1

Este é um dossiê **PROPOSED** para decisão dos donos e revisão independente. O
plano admitido autoriza preparar a engenharia; não publica contratos de fornecedor,
ratifica finalidade/política, identifica um fornecedor por inferência ou admite
um efeito comercial. O registro verificável está em
[source-readiness.json](source-readiness.json). Os campos abaixo são requisitos
do contrato a obter, não schemas Avro/OpenAPI editáveis nem endpoints existentes.

Base de implementação: `e7b14522a4f70242504d2b152a57ee5269ee66e2`, branch
`codex/v21-capability-execution`. Checkout de destino:
`/Users/familia/.codex/worktrees/v21-capability-execution/maezo-operadora`.
Bundle normativo preservado no ROOT:
`/Users/familia/code/maezo-operadora/docs/audits/BPMN-CA-2026-10/v2-agent-wiring/`.
Não foram enviados pedidos a pessoas, fornecedores ou outra frota.

## Evidência reproduzida e fontes disponíveis

Em 2026-10-04, a leitura dos objetos publicados e o checker
`v2-capabilities/scripts/check_amh_pinned_objects.py` passaram: 15/15 digests de
artefatos/fixtures, manifest, tamanho e blob; o gate local do pin também passou.
O pin atual tem SHA-256
`b7bb815ee29a34b703b9bdbeb36294da49420444614302934f69427ae2b37b25`.
Artefatos: commit AMH `09a0a282e69f49aa9c6944b25afb35eee65fcc9c`.
Manifest publicado: commit `17972442b26aa31a304d5374b2493b260881c1d3`,
SHA-256 `946266fb9ab10d27c01768e78fb3b673cba3db71680f652b10ccd0798065b126`,
9441 bytes, blob `76fbc9bd030395e1c1b796fe4821fc60556a2d35`.
Identidade publicada: `amh-maezo-boundary` v1.0.0, `BACKWARD`, evidence ID
`XRG2-AMH-DEV-GHA-30991849241`. IDs/publicação Glue do pin são proveniência
histórica; não houve consulta cloud nem aceitação operacional atual.

Objetos foram lidos em `/Users/familia/code/amh/amh-data-platform`, HEAD observado
`8644efc9d72ea823639dcc8306823ab6da66240f`. O checkout
`/Users/familia/code/amh-data-platform`, HEAD `3441d20d145bba7c7035bb5404fbaa5490b25e8f`,
não contém os dois commits pinados. Nenhum HEAD AMH foi promovido a publicação.

| Fonte/superfície | Fato atual | Limite que condiciona a integração |
|---|---|---|
| AMH subject-context | Quatro GETs publicados; `ClinicalContextPort`, adapter e executor implementados | Zero consumidores de grafo e zero injeções `amh_runtime=` nas raízes de `src/maezo`; API ao vivo não qualificada |
| AMH identidade/consent | Sujeito opaco AMH e contrato de consent/revisão existem | Prospect/vínculo de matrícula e nova finalidade comercial não publicados no pin |
| Catálogo, oferta e aceite comercial | Nenhuma autoridade de produto/termos/aceite publicada identificada no escopo inspecionado | Owner funcional e provider real precisam ser designados; catálogo populacional ou nativo de tarefas não é catálogo comercial |
| Matrícula/direito-base | Nenhum contrato de mutation/recibo administrativo publicado identificado | Coverage GET, membership do portal, `care.enroll` e `OwnerEnrollmentClient` não emitem direito-base |
| Agenda/entrega | Nenhum provider contratado de hold/confirm/reschedule/cancel/fulfillment identificado | Slot disponível não é reserva; ETA ou aviso preparado não é entrega |
| Casos | Intake AUTH, staff cases, native authority/currentness e receipts implementados | Nova espécie/audience/role comercial e confirmação S4 precisam de contrato do caso; execução real ainda não qualificada |
| Comunicação | Outbox/inbox, armazenamento e publicação protegida existem | `CommunicationPublicationSource` exige fonte qualificada; receipt `inbox_available` não prova entrega por WhatsApp nem serviço realizado |
| AMH eventos/outcomes | Mapping puro, codec dark e inbox durável existem | Não há `consumer.py` no pacote AMH; manifest atual não declara framing; publisher/broker e transação de negócio vivos não comprovados |
| AMH população | API de três GETs publicada, `PopulationFeaturePort` declarado | Adapter concreto da porta e injeção estão ausentes; client legado de André não é essa porta |

Evidências do código-base: `gateway/amh.py:56-113,245-318,369-375`;
`gateway/tool_registry.py:802-810`; `adapters/amh/subject_context.py:158,334-398`;
`ports/population_features.py:107-195`; `gateway/seams/population.py:1-16,39-60`;
`gateway/staff_cases/composition.py:1-45`;
`gateway/communications/admission.py:1-5,56-84`;
`portal/contracts/communications.py:15-27`; `gateway/d7_external_owner.py:1-5,65`.
Paths de código são relativos a `src/maezo/` no SHA-base. O JSON guarda os hashes
dos arquivos inspecionados. Existência de código é separada de aceitação real.

## Contrato comum a obter de cada autoridade

O envelope interno proposto conserva exatamente a lista do plano por capacidades:

`schema_version,operation_name,tenant_ref,legal_entity_ref,journey_ref,correlation_ref,causation_ref,idempotency_key,expected_business_revision,source_authority_ref,policy_revision,data_classification; domain-specific request/result are closed and authoritative`

W0 não escolhe valor runtime de `schema_version`, não publica domínio para
`DeclaredCaseKind`, `DeclaredCaseStatus` ou `DeclaredMilestoneKind` e não cria
novo token de finalidade. Request/result são fechados; output-only é proibido no
request. Campos opcionais ausentes preservam ausência, nunca false/zero fabricado.

| Item de decisão | Evidência exigida do owner/provider |
|---|---|
| Autoridade e contrato | Organização/source owner designado, contrato publicado e versão/digest; operações realmente fornecidas; poder de emitir cada fato, recibo e compensação |
| Identidade e isolamento | Tenant + legal entity + workload/principal + domínio/objeto/jornada; refs opacas; origem/vendor/product/instância; vínculo/aliases por autoridade própria |
| Finalidade/base | Finalidade específica e escopo de campos ratificados; referência do ato de governança aplicável; consent/current revision quando exigidos, ou decisão formal de não aplicabilidade |
| Atualidade | Revisão de negócio, instante do fato e validade; política de snapshot/idade/skew publicada pelo owner; origem do relógio; invalidação/revogação durante dispatch e retorno |
| Retenção/erasure | Owner de cada dado e artefato, prazo/base aprovados, invalidation de projeção/checkpoint/cache/outbox e prova de conclusão; retenção legal com decisão documentada |
| Efeito/recibo | Comando autorizado, evidência de aceite distinta de recibo de mutation, correlação, revisão esperada, digest do request, idempotência e resultado confirmado/pendente/incerto |
| Recuperação | Consulta autoritativa por comando/idempotency key; duplicate/conflict; timeout antes/depois do commit; replay/redelivery; compensação pelo owner sem apagar evidência |
| Transporte/versão | Wire e framing publicados pelo fornecedor; origem fixa e auth de servidor, sem destino do browser/agente; quotas/paging; migração e matriz reader/writer |
| Aceitação | Adapter real + injeção na raiz + consumidor explícito + gate por principal/task/operação + reply/resume autenticado; verifier independente e prova da interface real |

Referências presentes não bastam: o gateway verifica emissor, escopo, versão,
currentness e ligação ao request/objeto. Não há default de TTL/retention/purpose,
fornecedor ou credencial neste dossiê. Dados de saúde autorrelatados mantêm a
classificação sensível; não entram em seleção de risco comercial.

## Pedidos CR-F1..CR-F6

Os IDs preservam o crosswalk admitido. Não renomeiam AMH-V2-01..06 nem atribuem
toda fonte comercial à AMH. Cada publicação é do dono da fronteira efetivamente
atravessada. Os papéis abaixo têm **designação nominal pendente**.

| Pedido | Donos funcionais a designar | Decisão e entregável concreto |
|---|---|---|
| CR-F1 / OP01 | Registro/adesão; AMH identity/consent steward somente na fronteira AMH; DPO/legal; security | Designar fonte não clínica para prospect/contexto administrativo; publicar linking/alias ao sujeito AMH se necessário, access decision/revisão, campos mínimos, finalidade/base, freshness e erasure. AMH-V2-01/04 apenas no escopo necessário |
| CR-F2 / OP02 | Produto/comercial/legal da operadora; steward do catálogo/availability provider; DPO | Designar catálogo real; publicar produto/termos/oferta/versionamento/expiry/withdrawal, disponibilidade autoritativa, critérios explicáveis com allowlist não clínica ratificada. AMH-V2-02 é núcleo; 04 se fonte protegida; 06 só analytics posterior aprovado |
| CR-F3 / OP03/04 | Customer acceptance/legal; autoridade de matrícula/contrato da operadora; registration/identity steward; AMH steward se outcome cruza AMH | Publicar autoridade de aceite de termos exatos e sua prova; designar emissor administrativo e publicar matrícula/direito-base, request/status/revisão/issuer receipt e vinculação de prospect. AMH-V2-01/02; 05 somente no wire AMH necessário |
| CR-F4 / OP05/06 | Agenda/delivery/provider; network/care owner; AMH steward/DPO se resultado clínico usa AMH | Publicar transação hold/confirm/reschedule/cancel, revisão/expiry e reconciliação; publicar fato realizado/resultado/no-show/disruption com source fact e instante. AMH-V2-03; 04 para contexto protegido; 05 se evento cruza AMH |
| CR-F5 / OP07/08/11 | Owner de casos por domínio/CX; authority de requester; native/staff case owner; AMH steward/DBA/consumer quando necessário | Publicar kind/status/audience/role e tradução de estados comerciais, caso/protocolo/relógio não resetável, revisão e receipt; S4 verifica resolução da fonte + manifestação atual do cliente autorizado. OP07 exclusivo para confirmação S4; não OP03. AMH-V2-02/04/05 somente nas respectivas fronteiras |
| CR-F6 / OP09/10 | Dono da comunicação/canal/recipient preference; owner do fato de domínio; DPO/security; AMH publisher/consumer steward se wire AMH | Publicar classes/canal/audience/capping com notice obrigatório, preferências/revogação, estados prepared/sent/delivered/unknown e receipts próprios; marcos factuais separados de previsão. AMH-V2-04/05; não AMH-V2-06 |

### Questões que cada owner deve resolver com bytes e evidência

- **CR-F1:** quem emite a identidade administrativa do prospect, como comprova
  autoridade do requester e como liga/reconcilia o vínculo AMH sem CPF/MPI no core?
  Qual campo é necessário à operação e sob qual finalidade publicada? Contexto
  clínico permanece bloqueado para comercial enquanto esse gate estiver aberto.
- **CR-F2:** quem responde por cada produto/termo/preço/availability e por sua
  revisão? Como retira oferta apresentada e detecta dados vencidos? Quais critérios
  determinísticos foram ratificados para DMN CIB, sem feature de saúde/proxy?
- **CR-F3:** qual autoridade registra manifestação sobre `offer_ref`, versão e
  termos exatos; qual entidade emite vínculo/direito-base? Qual receipt comprova
  cada ato separadamente e permite resolver timeout sem segunda matrícula?
- **CR-F4:** qual fonte possui transação de agenda e qual possui o fato de entrega?
  Como verifica hold expirado, conflitos concorrentes e committed-but-timeout?
  Qual comando de compensação é permitido e por quem? Acesso ao lake não responde
  a essas questões transacionais.
- **CR-F5:** qual contrato publica `DeclaredCaseKind`/status e confirmação de
  resolução? Como exige mesmo tenant/caso, revisão esperada, requester atual,
  receipt da fonte e evidência da manifestação? Desacordo, timeout, fechado sem
  confirmação e confirmado precisam de mapeamento publicado distinto; silêncio
  não resolve. NIP apenas com solicitação formal; RECURSO apenas glosa payer.
- **CR-F6:** o que o receipt comprova em cada canal e como confirma entrega?
  Qual fonte certifica o marco e revisa/corrige informação? Como revalida recipient,
  consent/currentness e autoridade até retorno sem enviar duas mensagens no replay?

Essas perguntas são instruções para o pacote de decisão, não mensagens enviadas.
Toda designação/ratificação/publicação permanece nula até evidência real.

## Admissão do adapter, composição e prova operacional

1. O dono publica o contrato/semântica/versão da fonte e resolve os itens acima;
   decisões protegidas de governança têm ato específico, com bytes/escopo/signer.
   DTO interno proposto é adaptado ao wire legítimo, sem copiar schema AMH.
2. Adapter usa auth de servidor/origem fixa, respostas validadas e tipos fechados.
   Não segue redirect, fallback FHIR/lake/Tasy/HAPI, propósito desconhecido ou
   request plantando output de autoridade. Fonte ausente retorna indisponível.
3. `ClinicalContextPort` exige consumidor real, `AmhRuntime` injetado na raiz,
   principal/tenant/legal entity/purpose vinculados e fonte atual de consent.
   `gate(...).allow` em modo sombra não libera esse endpoint: executor requer
   `REASON_APPROVED`. Audit-before-dispatch e revision floor durável permanecem.
   Lists exigem paging completo quando a decisão depende de completude; coverage
   isolado não possui `generated_at` obrigatório nem prova própria de consent.
4. Cada OP possui consumidor no grafo, handler/registry, seam gated e DI na raiz,
   autoridade verificável, transação/receipt e retorno autenticado ao mesmo
   principal/jornada/caso. Smoke com fake ou fixture serve a teste de mecanismo;
   não qualifica provider nem encerra a jornada de Compras.
5. Verificador independente reproduz API/provider/DB reais e CIB real quando
   afetado: wrong tenant/legal entity/principal/task/purpose, revogado/stale,
   campo/classe desconhecida, request replay/conflict, concorrência, timeout
   incerto, return durante revogação, paging e correlação. Prova também clientes
   antigos, matrícula separada de AUTH, S4 via OP07 e reuso Compras/Suporte.
6. Gate de source/schema não substitui gate de segurança, autorização de novo
   escopo do agente, conduta, CIB/hard actions ou produção. Binding e efeito ficam
   fechados até todos os gates específicos; wrappers e flags default off não
   constituem aprovação. Reviewer final não participou da autoria/reparo/integração.

### Matriz reader/writer e XRG

| Reader | Writer | Prova exigida |
|---|---|---|
| Atual | Atual | Pin e fixtures existentes + regressão do consumidor; reprodução de bytes não prova runtime |
| Novo | Antigo | `BACKWARD` estrutural e consumer/provider tests com semântica antiga, null/default/enum/replay |
| Antigo | Novo | Prova específica de aceitação ou recusa/quarentena segura; `BACKWARD` não assegura esta direção |
| Novo | Novo | Contrato publicado, authority/purpose/framing/outcomes + interface real + reconciliação |

Payloads Avro atuais usam strings em campos de workflow/purpose/outcome; isso
não publica nova semântica comercial. O enum `SourceProduct` atual contém somente
`tasy_hospital` e `tasy_healthcare_plan`. Novo enum, field, framing ou significado
exige avaliação dos dois lados; mudança incompatível exige major/tópico/migração,
drain/aliases e dual-read/publish admitidos, com janela ADR-0037 aplicável.

AMH publica seus schemas/manifest e nova evidência XRG-2; MAEZO depois verifica
objetos imutáveis/publicação e novo XRG-3 independente. Provider não-AMH publica
seu contrato; XRG-AMH aplica quando a projeção cruza a fronteira AMH. Nenhum ajuste
manual de lock/digest, fallback de versão ou downgrade silencioso. Rollback fecha
novas admissões, preserva pins/aliases/recibos e reconcilia pending/uncertain;
compensação é ato do dono do domínio, não remoção técnica do efeito confirmado.

## População, supressão e medição W0

O contrato AMH publicado fixa **k mínimo 10**; `min_cell_size` só pode aumentar
o piso. `AggregateResult` mantém células agrupadas, dois pisos k e snapshot de
consent; `suppressed=true` com `metrics=None` é ausência protegida, não zero ou
célula medida vazia. Dimensão individual é recusada. O adapter/consumidor deve
validar todas essas relações contra o contrato publicado e preservar supressão
na saída, comparação e denominadores.

`CohortAggregate` legado de André é flat, tem default k=1 e métricas numéricas;
não pode ser alias direto de `PopulationFeaturePort`. O wrapper exige política
canônica ratificada; `population-egress-v1.yaml` está DRAFT, `k_min:null` e
`allowed_metrics:null`. Não elevar esse DRAFT a ratificação nem usar analytics como
marketing. AW5 requer adapter/projeção explícita compatível, consumidores reais,
owner/benefício/controles e qualificação independente; é separado do núcleo
Compras/Suporte.

Baseline e ROI continuam **UNMEASURED**. Protocolo barato, sem query ao lake:

1. CX/product define uma janela representativa e coortes, versões de start/end,
   entrega verificada, duplicate/correction e maturidade. Ops-observability
   inventaria emissores e lineage; owner da fonte e DPO/security aprovam export
   agregado minimizado e retenção.
2. Solicitar export agregado autorizado com ambiente, versão, janela, coorte,
   counts/denominadores, missingness, maturidade, latência utilizável, abandono,
   retrabalho, contato repetido e entrega real. Qualquer métrica indisponível
   permanece ausente. AMH, se usada, conserva k>=10/suppression/snapshot.
3. Reconciliar counters com receipts/outcomes, duplicatas e correções; separar
   synthetic/staging/production. Comparar baseline/piloto de mesmo escopo,
   denominador/horizonte/endpoint/coorte; conversão bruta não prova causalidade.
4. Sem export, registrar UNMEASURED e propor eventos mínimos ao owner sem os
   instalar. Sem CPF/matrícula/MPI, nome/telefone/email, diagnóstico, transcript,
   prompt/payload/body, proxy de risco ou credencial. Journey/receipt IDs não são
   labels Prometheus; refs opacas e metadados mantêm controles de proteção.

AMH-V2-06 pertence a população/medição, não à comunicação F6. Ausência de baseline
não bloqueia pacotes independentes, nem autoriza afirmar ROI quantitativo positivo.

## Checklist histórico e disposição reviewable

`HEAD:docs/reports/predeploy-findings.json` foi lido pelo objeto Git no SHA-base:
**127 entradas**, sem restaurar o delete deliberado do ROOT. O número 82 no AGENTS
é histórico. Findings são evidência histórica, não defeitos atuais automaticamente.
Aplicar ao componente afetado: `phone-hash-keyless-brute-forceable-reidentification`
(identidade/log/event), `lucas-marina-glosa-a2a-origination-never-wired`
(consumidor/composição sem caller) e
`bridges-missing-readinessprobe-kafka-consumer-rollout-gap`
(consumer pronto somente após conexão/assinatura/rebalance). Find IDs/refutações
permanecem preservados; não marcar todos os 127 como ativos ou resolvidos.

Decisão disponível ao owner: designar autoridade real e aprovar/publicar o contrato
de cada pedido sobre os bytes revisados. Pendências concretas estão no JSON; cada
uma bloqueia somente o efeito/fonte afetado. Este pacote permite revisar contratos
e implementar mecanismos internos deny-only admitidos; não declara Compras,
Suporte ou provider entregues e não instala permissões/flags/infraestrutura.
