# V2.1 — contrato técnico de admissão estrita

Estado: **PROPOSED / INERT TECHNICAL CANDIDATE**. Base de autoria:
`e7b14522a4f70242504d2b152a57ee5269ee66e2`. Checkout de implementação:
`/Users/familia/.codex/worktrees/v21-capability-execution/maezo-operadora`.
O bundle admitido permanece em
`/Users/familia/code/maezo-operadora/docs/audits/BPMN-CA-2026-10/`.
Esta autoria não é revisão independente, publicação de fornecedor, assinatura
humana, admissão de novos bindings ou qualificação operacional.

## Fonte normativa e rastreabilidade

- `v2-agent-wiring/INTEGRATION-PLAN-V2.1.md`, seções 3–5: gateway efetivo por
  agente/tarefa/operação, injeção de fonte, estado e retorno; AW1 condicionado a W0.
- `v2-capabilities/COMPATIBILITY-AND-CONTRACTS.md`: envelope interno de 12 campos,
  requests/results fechados OP01–OP11 e recusas técnicas; refs não provam autoridade.
- `v2-capabilities/INTEGRATION-PLAN-V2.md`, fronteira AMH, segurança e ondas W0/W1:
  pin publicado não autoriza comércio, matrícula ou agenda; sem propósito fabricado.
- ADR-0006, ADR-0008/0034, ADR-0016, ADR-0025 e ADR-0037/XRD-05–10:
  zonas PHI, hard humano, allowlist e autonomia canônica, finalidade e receipt.
- `SP-OP-AUTH-001.md`, invariante L0 e admissão do intake: representação atual,
  revisão/revogação e autoridade de fonte; AUTH não é intake comercial.
- `SP-OP-ESCALATION-001.md`, origem/retomada: resolução e comunicação não
  substituem decisão clínica ou prova de entrega.
- DL-0005/0010/0012/0013/0018/0030/0040–0045/0048–0050: preservar hard,
  pseudonimização, audit, falha técnica visível, gates humanos e ausência de fallback
  para direct completion. DL-0045 admite construção dark para produzir evidência;
  não autoriza o agente a preencher aprovações.

`docs/reports/predeploy-findings.json` foi lido por `git show HEAD:...` no objeto
autoritativo, sem restaurar seu delete no ROOT. Há **127 registros** nesse objeto,
incluindo disposições/refutações históricas; não 82 findings atuais afirmados.
Checklist aplicável ao candidate:

| Findings históricos | Invariante deste pacote |
|---|---|
| `lucas-cancel-start-overclaim`, `helena-autonomy-actions-incomplete-triage-scheduling`, `gustavo-autonomy-actions-includes-never-exercised-L1-actions` | Binding exato e PEP canônico por tenant; nenhuma ampliação por texto/declaração; unknown e L1 recusados. |
| `matricula-raw-to-general-zone-notifications-topic`, `inad-cancel-matricula-phi-egress-unscrubbed`, `phone-hash-keyless-brute-forceable-reidentification` | Envelope/DTO de refs; audit somente binding e digests; nenhuma matrícula, telefone ou business key nova composta aqui. |
| `resume-driver-error-dlq-paths-unredacted-phi-echo-inbound-sibling-hardened`, `fhir-sync-validation-error-logs-raw-pydantic-input-value-phi` | Refusal limitado a enum técnico; nenhuma exception/provider payload impressa, logada ou devolvida. |
| `set-pseudonymizer-never-wired-layer2-freetext-log-scrub-inert-all-runtimes` | Este pacote não depende de log scrub para ocultar payloads: não produz logs nem inclui payload no audit intent. Não declara pseudonimizador live. |
| `recurso-marina-a2a-decorative`, `inad-fernando-a2a-unreachable`, demais `*-origination-never-wired` | Código candidate e UNIT não são wiring completo; nenhuma factory/registry/root de produção alterada. |

Esses registros motivam controles; não são alegações de vulnerabilidade atual nem
findings encerrados por este pacote.

## Interface entre admissão e dispatcher

`src/maezo/gateway/capabilities/admission.py` fornece `CapabilityAdmission`.
Sua composição recebe `AdmissionBinding` **servidor**, um `PEP` do tenant e portas
`AuthorityVerifier`/`AdmissionAuditPort` qualificadas. Ausência implica recusa.
Nenhuma porta operacional, credencial, endpoint, policy override ou binding default
é distribuído. O catálogo e o dispatcher continuam fora dos roots de produção.

O binding contém principal, tarefa, tenant/legal entity, propósito, operação,
schema/contrato/policy revision, source authority, classificação, ação canônica e
security zone. O envelope do caller conserva exatamente os campos planejados;
não ganha principal, purpose, booleano de autorização ou output fields.
Construir o binding ou uma evidência Python não admite o produto.

Sequência obrigatória do dispatcher:

1. Parse estrito de envelope/request, membership publicado e operação conhecida.
2. `authorize(envelope, request)`: escopo exato; PEP canônico do tenant retorna a
   identidade `Decision.ALLOW`; verifier prova source contract publicado, policy
   ratificada/enforcing, agente/Card e tarefa assinados, permissões da operação,
   finalidade, sujeito/representação, consent/revocation/erasure aplicáveis e
   revisão de negócio. Audit durável devolve receipt SHA-256 antes de qualquer IO
   do adapter de domínio. Leitura de evidência de autorização pela porta é o IO
   de controle; não é leitura do contexto/efeito de negócio solicitado.
3. `revalidate(lease, phase="before_source")`: rechecagem vigente depois do audit,
   request digest inalterado e lease de uso único, inclusive chamadas concorrentes.
4. Adapter contratado executa uma vez; parse fechado do resultado. Timeout/erro
   mantém resultado incerto na fonte durável e nunca causa retry automático.
5. `verify_result(lease, result)`: receipt autoritativo da fonte sobre bytes exatos,
   escopo, revisão, currentness e validade. Resultado clínico em `general` é recusado.
6. `revalidate(lease, phase="before_disclosure")`: a porta recebe também a
   evidência do resultado; prova currentness específica de source receipt/result,
   ator e política. A observação deve ligar o digest exato do resultado; expiry de
   evidência inicial e do resultado é conferido novamente antes do retorno.
7. `release(lease)` em `finally`: invalida apenas a invocação local e elimina refs
   transitórias. Não libera dedup, reserva, outbox ou efeito pendente/incerto da
   fonte e não produz fato de compensação ou conclusão.

Erros tipados conservam `UNKNOWN_OPERATION`, `CONTRACT_MISMATCH`,
`SOURCE_UNAVAILABLE`, `AUTHORITY_UNPROVEN`, `PURPOSE_DENIED`, `STALE_REVISION` e
`AUDIT_UNAVAILABLE`. Nenhum outage/unknown vira aprovação. Instâncias construídas
com `model_construct` das evidências são revalidadas, incluindo timestamps com
timezone e Literals `allow`/`enforcing`; shadow ou bool não autorizam.

Lease é identidade do objeto original e guard de invocação; **não** é token externo,
idempotência de domínio, transação distribuída ou substituto de fonte durável. O
adapter deve conferir o grant e expected revision atomicamente na própria operação.
Um guard local não elimina a janela entre a última leitura e o efeito remoto. A
qualificação futura deve provar que revogação/concorrência nessa janela é recusada
pela fonte, com receipts e reconciliação — UNIT não prova esse requisito.

## Obrigações da qualificação W0/fornecedor

| Porta/evidência | Prova exigida antes de um root registrar o binding | Owner |
|---|---|---|
| Binding/Card/task/policy | Allowlist exata por agente/tarefa/operação, assinaturas/digests vigentes, ação/classes e regras correspondentes ratificadas; ceiling e DMN quando aplicáveis; nunca ALLOW derivado só do PEP | Security/compliance e owner de domínio |
| Source contract e purpose | Publicação real da fonte, reader/writer compatíveis, tenant/legal entity/operação/finalidade exatos; membership fechado e papel do solicitante publicados | Provider/steward e Legal/DPO |
| Currentness | Verificação efetiva de revisão, actor/delegation, source fact/receipt, consent revocation, alias e erasure; observação atual com validade da fonte, sem TTL default inventado | Fonte e identity/consent owner |
| Source result | Receipt assinado/verificável com objeto/revisão exatos e estados pending/confirmed/uncertain separados; delivery não é metadata ACK | Owner transacional do domínio |
| Audit | Commit durável, chain/receipt e disponibilidade antes do dispatch; falha recusa; hash de refs não é autorização | Audit owner e Security |
| Idempotência/outbox/inbox | Chave admitida por contrato, CAS e outbox com estado/evidência na mesma TX, dedup/replay/concurrency/recovery na fonte; sem raw PHI em chaves | Source owner e DBA |

O pin AMH permanece intacto. Nem `analytics`, coverage GET, `care.enroll`, portal
membership, Engine OwnerEnrollmentClient ou slot observado fornecem autoridade
comercial/matrícula/reserva. Sem raw CDC, direct lake/Tasy/HAPI write, MPI clone,
clinical proxy para oferta, SDK LLM, credenciais ou start ADEQUACAO neste pacote.
Os cinco itens hard conservam seus mecanismos humanos existentes; não existe
adaptador de human command ou direct completion novo aqui.

## Evidência de autoria e readiness

Write set exclusivo: `admission.py`,
`tests/unit/gateway/capabilities/test_admission.py`, este documento.
Testes focalizados: **50 UNIT** passando; ruff check/format e mypy do módulo passam.
Cobertura inclui sequência audit→source→receipt→currentness, scope mismatch,
unknown/L1/hard, shadow/junk/booleans, expiries, revocation após audit e após source,
digest/request mutation, forged lease e concorrência single-use, source receipt
de outros bytes e clinical context em Zona Geral.

Readiness: shape/mecânica candidate exercitadas; gate independente da implementação
**PENDING**. Providers, source/contract publications, IAM operacional, human policy
ratification, engine, root wiring, pin delta e jornada completa **não qualificados**.
Este documento não altera assinaturas do bundle nem transforma UNIT em aprovação.
