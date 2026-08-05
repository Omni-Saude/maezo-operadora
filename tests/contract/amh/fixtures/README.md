# Fixtures do contrato canônico AMH×Maezo v1 (AMH-030)

Fixtures de conformidade dos três schemas Avro canônicos (ADR-042 §2). São o material que o
XRG-3 usa nos contract tests de consumidor do Maezo, e que o CI AMH usa para validar o
produtor. Publicação é ato humano do XRG-2 — nada aqui está publicado.

**Regra inviolável: fixtures NUNCA contêm PHI nem ID real.** Todo uuid, hex de HMAC, raiz de
CNPJ, referência e timestamp é SINTÉTICO (fabricado à mão). Os hex de
`protected_source_record_ref`/`beneficiary_ref` são padrões repetidos (`3f…`, `5a…`) — não são
HMACs reais; a tupla-chave dos HMACs é GATED e não é exercida aqui. O
`contract_manifest_digest` das fixtures é o placeholder sintético `ff…` (64 hex): o digest
real só nasce na publicação (XRG-2). Invariante de wire: nenhum `mpi_id`, `source_patient_id`,
CPF ou CNS (claro ou hash) aparece em campo algum.

`payload_hash` é internamente consistente: sha256 da forma canônica JSON do `payload`
(UTF-8, chaves ordenadas, separadores compactos), conforme declarado no
`contract-manifest.yaml`.

## Papel de cada fixture

| Arquivo | Schema | Papel |
|---|---|---|
| `work_item.authorization_review.json` | `amh_maezo_work_item.avsc` | VÁLIDA — work item de revisão de autorização originado do PEDIDO hospitalar (`source_product=tasy_hospital`, matriz ADR-042 §3); `beneficiary_ref` null (fora de contexto healthcare_plan); `due_at` presente. |
| `work_item.eligibility_check.json` | `amh_maezo_work_item.avsc` | VÁLIDA — verificação de elegibilidade no contexto payer (`source_product=tasy_healthcare_plan`, `amh_tenant=omni`); `beneficiary_ref` presente; `due_at`/`context_refs` vazios (opcionais). |
| `work_item.bad-source-product.invalid.json` | `amh_maezo_work_item.avsc` | INVÁLIDA — `source_product: "tasy"`, fora do vocabulário fechado; o enum Avro reprova (fail-closed no wire). |
| `consent.granted.json` | `amh_maezo_consent.avsc` | VÁLIDA — grant `{purpose=analytics, scope=analytics}`, `consent_revision=1`; `amh_mpi_ref` presente (exemplo de opcional autorizado). |
| `consent.revoked.json` | `amh_maezo_consent.avsc` | VÁLIDA — revoke do MESMO `{subject, purpose, scope}`, `consent_revision=2`: demonstra a monotonicidade da revisão (last-revision-wins; revogação p95 ≤ 60s). |
| `consent.missing-consent-decision-ref.invalid.json` | `amh_maezo_consent.avsc` | INVÁLIDA — campo obrigatório `consent_decision_ref` ausente; reprova na validação de schema. |
| `outcome.revision-1.json` | `maezo_amh_outcome.avsc` | VÁLIDA — `authorization_decision` revisão 1 (`returned_for_information`, `decided_by_kind=automation`); mesma `correlation_id` do work item de autorização. |
| `outcome.revision-2.json` | `maezo_amh_outcome.avsc` | VÁLIDA — revisão 2 CONSECUTIVA da mesma tupla `{maezo_tenant, workflow_business_ref, outcome_type}` (`approved`, `decided_by_kind=human`; `causation_id` = event_id da revisão 1): demonstra o revision-ordering e a guarda de business-revision (XRD-10). |
| `outcome.non-integer-revision.invalid.json` | `maezo_amh_outcome.avsc` | INVÁLIDA — `outcome_revision` como string em campo `long`; reprova na validação de tipo. |

Nos outcomes (`outcome.revision-1.json`/`outcome.revision-2.json`), a troca de
`amh_tenant`/`legal_entity`/`portable_subject_ref` em relação ao work item correlacionado é
INTENCIONAL: o bloco `source_*` do outcome carrega a proveniência PAYER da transação
adjudicada (ADR-042 §3), e a mesma pessoa tem OUTRO `portable_subject_ref` na PJ payer
(ADR-041 §6/XRD-05); a ligação com o work item é por `correlation_id`, não por eco de
proveniência.

A fixture `outcome.non-integer-revision.invalid.json` mantém o `payload_hash` da forma válida — inócuo: ela reprova por tipo antes de qualquer verificação de hash.

Toda fixture VÁLIDA deve passar `fastavro.validation.validate` contra seu schema; toda
`*.invalid.json` deve REPROVAR. Isso é verificado na AMH-030 e vira gate de CI (T4/AMH-040).

## Decisões de vocabulário registradas (enum Avro × string)

- **`source_product` = enum Avro fechado** (`tasy_hospital` | `tasy_healthcare_plan`): o
  vocabulário é congelado por ADR (ADR-042:186, verbatim: "Nenhum outro valor de
  `source_product` existe."; a formulação "nenhum valor novo jamais sem decisão de contrato
  versionada" é do doc dos avsc, não do ADR); o enum torna o wire fail-closed por construção.
- **`event_type`, `purpose_of_use`, `data_classification`, `workflow_type`, `priority`,
  `decision`, `outcome_type`, `outcome_status`, `decided_by_kind` = string + vocabulário no
  `doc`**: vocabulários governados fora do wire (DDL `mpi.consent_log`,
  classification-policy) ou extensíveis em minor de contrato; um enum Avro exigiria major a
  cada acréscimo e quebraria consumidores antigos sob BACKWARD. A validação de valor é
  fail-closed em contract test (T4), não no deserializador.

## Fixtures de exemplo das APIs OpenAPI

Os payloads de exemplo de `subject-context.openapi.yaml` e `population-features.openapi.yaml`
vivem inline nos próprios yaml (`example:`/`examples:`), 100% sintéticos, sob as mesmas
regras deste diretório.
