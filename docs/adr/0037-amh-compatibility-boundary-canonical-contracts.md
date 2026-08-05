# ADR-0037: Fronteira de compatibilidade AMH — contratos canônicos AMH-owned; supersede PARCIAL do ADR-0013 (MZO-000)

**Status:** Accepted — ratificado por Rodrigo (repo owner) 2026-08-03, autoridade humana do stop point §5 do execution prompt; registrado como DL-0040. A aceitação fecha a METADE MAEZO do gate XRG-1 — o gate XRG-1 só fecha por completo com o ADR companheiro (AMH-000) aceito no repo amh-data-platform. Os gates operacionais §5 downstream (DPO/Legal em valores de identidade/consent, Security em designs, stewards em inventário de fonte, aprovações Médica/ANS de MZO-040) permanecem exigíveis nas waves de implementação. · **Data:** 2026-08-03 · **Area:** Integracao / Dados / Fronteira de plataforma

> **Gate humano — override explícito da prática de ratificação autônoma deste repo.** Existe precedente
> de flip Proposed→Accepted pelo orquestrador sob autoridade autônoma vigente (ADR-0032; DL-0036).
> PARA ESTE ADR essa prática NÃO se aplica: `docs/prompts/EXECUTE_AMH_COMPATIBILITY_PLAN.md` §5 lista
> "accepting/superseding an ADR or changing capability/source ownership" como stop point de aprovação
> humana obrigatório — e este ADR faz exatamente as duas coisas (supersede parcial do ADR-0013 +
> fixação de propriedade de capacidade XRD-01). A aceitação deste ADR constitui a metade Maezo do gate
> XRG-1 do plano de compatibilidade e exige os aprovadores nomeados na linha de Status acima; nenhum
> agente, orquestrador ou gatekeeper automatizado pode mover este Status.

> **Ratificação registrada (2026-08-03):** o gate humano acima foi CUMPRIDO — ratificação explícita do dono do repositório na sessão de orquestração de 2026-08-03, exercendo os papéis de aprovação nomeados; paperwork executado por agente sob instrução direta (DL-0040). Nenhum conteúdo decisório do ADR foi alterado nesta ratificação.

Owner: boundary-adr-author (MZO-000, Wave 0). Escopo: docs-only — nenhuma edição de `src/`, `spec/`,
`tests/`, `deploy/` ou evidence-ledger. Planos citados: `docs/prompts/AMH_COMPATIBILITY_TECHNICAL_PLAN.md`
(plano Maezo) e `/Users/familia/code/amh/amh-data-platform/docs/Prompts/MAEZO_COMPATIBILITY_TECHNICAL_PLAN.md`
(plano companheiro AMH) — artefatos de planejamento locais, não arquivos versionados (no Maezo,
`docs/prompts/` é gitignored — `.gitignore:43`); as citações de linha valem para os snapshots que
eles declaram.

## Contexto

### 1. A incompatibilidade de contrato (evidência forense)

O ADR-0013 (Accepted, 2026-06-12) fixa como contrato de integração Tasy os tópicos raw-CDC
`cdc.amh.tasy.pessoa_fisica` / `cdc.amh.tasy.convenio_paciente` / `cdc.amh.tasy.autorizacao_convenio`,
descritos como projeções de views FHIR-oriented (`VW_FHIR_PATIENT`/`VW_FHIR_COVERAGE`/`VW_FHIR_CLAIM_AUTH`).
O relatório forense cross-repo (`docs/reports/amh-integration-forensics-summary.md`, 2026-08-02,
working-tree) verificou que o conector AMH real publica outra coisa: prefixo `cdc.austa_clinicas.tasy`,
capturando **tabelas físicas** Tasy (tópicos na forma `cdc.austa_clinicas.tasy.tasy.pessoa_fisica`),
não as views que o ADR-0013 assume (forensics `:100-106`). Ou seja: o contrato que o Maezo consome
não existe como shipped, e o que a AMH shippa não é o que o Maezo espera — "AMH contract
compatibility: Critical gap — 3/10" na avaliação forense. Ambos os planos técnicos (Maezo §3.1,
tabela "ADR-0013 currently directs Maezo to consume raw AMH CDC"; plano AMH companheiro) chegam à
mesma conclusão: o ADR-0013 conflita com a fronteira autoritativa de sistema-de-registro e precisa
ser superseded antes de qualquer implementação de consumer.

### 2. A governança de dois planos e os gates XRG

A relação-alvo escolhida (plano Maezo §2.2) é uma **ponte de contrato governada com planos separados
de dados e de ação**: Tasy Hospital e Tasy Healthcare Plan permanecem sistemas de transação; a
**AMH Data Platform** é o sistema de registro governado, autoridade canônica de identidade/consent,
dona de FHIR/lake, publicadora de schemas e backbone de integração; o **Maezo Operadora** é o sistema
de ação regulado do payer (BPMN/DMN, execução de agentes, limites de autonomia, revisão humana,
estado de workflow, evidência de decisão). Três gates cross-repo governam a execução (plano §6.3):

- **XRG-1 (ownership):** ADRs cross-repo aceitos congelam propriedade de capacidade, matriz de
  fonte, semântica de identidade/tenant, topologia CIB e o rename do componente AMH.
- **XRG-2 (publicação):** steward de contrato AMH publica UM manifest (commit SHA AMH, SHA-256 do
  manifest, IDs de versão Glue, relatório de compatibilidade, fixtures, classificação de segurança,
  registro de aprovação); o CI de compatibilidade deve ser bloqueante.
- **XRG-3 (pin do consumidor):** steward Maezo verifica XRG-2 independentemente, escreve apenas o
  arquivo de pin exato, roda contract tests de consumidor contra os fixtures publicados e registra o
  evidence ID. Antes de XRG-3, nenhum agente Maezo implementa mapping de consumer que assuma campo
  não-publicado.

### 3. Estado corrente dos gates (recon Wave-0, 2026-08-03)

Verificado por reconhecimento direto em 2026-08-03:

- **XRG-1 ABERTO nos dois lados.** Este ADR (Proposed) é a metade Maezo; o repo AMH
  (`/Users/familia/code/amh/amh-data-platform`, `main` @ `6348cb0`) NÃO tem ADR de ownership
  correspondente (índice termina em ADR-041).
- **XRG-2 ABERTO.** No repo AMH não existem `schemas/avro/integration/`, `schemas/openapi/`,
  `schemas/contracts/`, nenhum contract manifest e nenhum evidence ID publicado.
- **XRG-3 ABERTO.** O Maezo não tem `config/integrations/` (nenhum pin escrito — correto: escrever o
  pin antes de XRG-2 seria fabricação).
- **Drift AMH-side desde o snapshot do plano:** o `|| true` do step de CI de compatibilidade Avro
  foi REMOVIDO (commit AMH `8a3a061`, 2026-08-03); o gate agora é bloqueante para PRs que tocam
  `schemas/avro/**/*.avsc` — a invocação antiga, além de non-blocking, nunca tinha de fato comparado
  schemas. A citação do plano (`schemas-validate.yml:64-70` com `|| true`) está, portanto, STALE, e a
  premissa do work package AMH-040 (tornar o gate bloqueante) está PARCIALMENTE pré-satisfeita — com
  duas ressalvas de escopo: só caminhos avro são gated, e ainda não existe step de publicação de
  version-ID Glue nem de manifest.

### 4. As cinco cláusulas do ADR-0013 e a análise de conflito

O ADR-0013 decide: (1) consumir os tópicos raw-CDC do amh-data-platform como O contrato de
integração Tasy, com versões de schema pinadas pelo consumidor em fixtures de teste; (2) `aiokafka`;
encoding dev = JSON (envelope Debezium), prod = Avro+Glue "não implementado aqui"; (3) o simulador
Tasy como contrato vivo permanente do envelope CDC; (4) credenciais Oracle AMH a serem injetadas no
cofre do Maezo quando provisionadas; (5) tópicos DLQ `cdc.amh.tasy.dlq` / `cdc.amh.tasy.fhir-sync.dlq`.

Conflitos: a cláusula 1 acopla o payer core a tabelas físicas de um ERP específico via um contrato
que a AMH não shippa (§1 acima) e que a própria AMH (ADR-040 do amh-data-platform) já decidiu
substituir por um produtor canônico interno; a cláusula 2 institucionaliza um wire dev (JSON
Debezium) diferente do wire de produção (Avro+Glue), exatamente a classe de divergência que um
contrato publicado + pin elimina; a cláusula 3 faz do simulador local a autoridade do contrato —
autoridade que pertence à AMH (publicadora) e não a um harness de dev do consumidor; a cláusula 4
traria credencial de fonte primária para dentro do Maezo, violando a fronteira de não-responsabilidade
(plano §2.4); a cláusula 5 ancora o tratamento de falha em DLQs do caminho raw-CDC que deixa de
existir. Os PRINCÍPIOS do ADR-0013 — consume-not-duplicate, TASY write DROP, nenhuma infra CDC neste
repo — permanecem corretos e são re-ancorados por este ADR (ver "Relação com ADRs existentes").

## Decisao

Ratificamos, pelo lado Maezo, as doze decisões XRD-01..XRD-12 do registro de decisões do plano
técnico (§5), todas na opção **C** selecionada. Cada cláusula está escopada ao que o MAEZO decide;
decisões de propriedade AMH são endossadas, não decididas aqui.

1. **XRD-01 — Propriedade de capacidade.** Operamos a ponte de contrato governada com planos
   separados: AMH é o data plane governado (registro, identidade/consent, FHIR/lake, publicação de
   schemas); Maezo é o action plane regulado do payer. Maezo consome work items e mudanças de consent
   ERP-neutros publicados pela AMH e publica outcomes transacionais auditáveis; nunca consome tabelas
   Tasy nem envelopes Debezium.

2. **XRD-02 — Rename do componente AMH "maezo".** Decisão AMH que o Maezo ENDOSSA (não decide): o
   componente `applications/maezo` do repo AMH (endpoints BPM 501, coordenador no-op) passa a
   `amh-interop-coordinator`, com alias de uma release. O Maezo Operadora é o único produto payer
   com esse nome; o endosso elimina a ambiguidade operacional.

3. **XRD-03 — Autoridade de fonte.** Adotamos como insumo vinculante a matriz de autoridade de fonte
   AMH (plano §5.1): Tasy Hospital e Tasy Healthcare Plan permanecem produtos distintos em
   proveniência; o Maezo usa exclusivamente projeções canônicas AMH (elegibilidade point-in-time,
   autorização como fatos request/decisão distintos, encontro/diagnóstico/prescrição como contexto
   read-only mínimo) e nunca recomputa nada de tabela crua. A publicação de contrato permanece
   bloqueada até o inventário Philips módulo-a-módulo aprovado pelos data stewards AMH+OMNI+Hospital.

4. **XRD-04 — Propriedade de contrato.** A AMH é a única dona e editora dos artefatos canônicos do
   catálogo abaixo. O Maezo NUNCA copia schemas editáveis; registra apenas pins imutáveis em
   `config/integrations/amh/contracts.lock.json` (mecanismo XRG-3): commit AMH, digest SHA-256 do
   manifest, cada schema/version-ID Glue, digests OpenAPI e major de tópico. Digest divergente,
   schema ausente, tópico errado ou versão rebaixada falham fail-closed antes de merge/deploy de
   adapter.

5. **XRD-05 — Identidade.** O sujeito no payer core é o `portable_subject_ref` mintado pela AMH,
   opaco e estável dentro de `{amh_tenant, legal_entity}`; só a AMH o mapeia para registro de fonte,
   beneficiário, MPI per-tenant e identificadores FHIR. Nenhum ID cru de paciente/beneficiário/MPI
   entra no domínio Maezo; merges viram aliases AMH, sem rewrite destrutivo de referências de
   processo. (Cláusula gated por DPO/Legal — **portão DESCARREGADO em 2026-08-05**: aprovação de
   Lucas, Diretor Jurídico e de Compliance, e de Rodrigo, CEO e dono do repositório, declarada pelo
   dono na sessão de orquestração; registrada em **DL-0042** e implementada por **MZO-020**. O
   descarregamento vale SÓ para esta cláusula: as aprovações Médica/ANS/Security do MZO-040
   permanecem ABERTAS.)

6. **XRD-06 — FHIR.** Só a AMH escreve no HAPI. O Maezo lê contexto clínico exclusivamente via um
   `ClinicalContextPort` read-only sobre a API subject-context; na célula AMH não recebe credencial
   de escrita HAPI e o HAPI in-cluster opcional do ADR-0021 permanece desabilitado.

7. **XRD-07 — Lake/população.** O acesso populacional/atuarial usa a API population-features
   (purpose-bound, consent-filtered, k-suprimida) implementando o `PopulationFeaturePort`; sem acesso
   direto Athena/Gold e sem credencial Gold irrestrita na célula AMH. Até a API ser publicada, vale o
   mecanismo gold-view do ADR-0019 (ver Relação com ADRs existentes).

8. **XRD-08 — Topologia de processo.** Células CIB hospital (AMH) e payer (Maezo) isoladas: engine,
   banco, credenciais, process keys e worker topics separados; namespace de processo `maezo-payer/*`.
   Nenhum compartilhamento de engine database ou de chaves de processo com a célula hospitalar.

9. **XRD-09 — Enforcement de ação externa.** Re-introduzimos o chokepoint por chamada: todo
   read/tool/efeito externo passa por um `ActionExecutionGateway` inevitável e fail-closed (política +
   consent + teto de autonomia + audit-before-effect + regras de revisão humana), ADICIONAL ao
   enforcement estrutural BPMN/HITL do ADR-0018 — nunca substituto. Ação/política desconhecida,
   outage de consent ou de audit negam. Esta cláusula é o veículo de ratificação que a precondição de
   revisita do ADR-0034 exige, e só produz efeito com a aceitação deste ADR + evidência MZO-040
   entregue (ver Relação com ADRs existentes).

10. **XRD-10 — Confiabilidade de entrega.** O wire é Avro + Glue Schema Registry com compatibilidade
    `BACKWARD`; o consumo usa inbox durável com deduplicação `{contract_manifest_digest, event_id}` +
    guard de business-revision; a publicação de outcomes usa outbox transacional na MESMA transação
    da transição de estado/evidência (padrão Postgres dos ADR-0024/0027); inválidos vão a quarentena
    sem perder a linha de origem; offsets Kafka nunca representam conclusão de negócio.

11. **XRD-11 — Deployment.** O data plane AMH roda em ECS; o Maezo permanece imagem OCI + Helm numa
    célula EKS isolada dentro da AMH — digests imutáveis, IRSA/workload identity, MSK IAM/SASL, mTLS
    para APIs e telemetria, secrets de store gerenciado, default-deny de rede com egress estreito —
    preservando a portabilidade Kubernetes do produto fora da AMH.

12. **XRD-12 — Segurança/prontidão.** Prontidão AMH exige evidência viva fail-closed: os gates
    XRG-1..3 + suites cross-repo executadas contra MSK/Glue/CIB/APIs REAIS, com auditoria explícita
    de skips (um skip de teste live obrigatório falha o gate) e verificador independente que não
    implementou o caminho revisado. Este ADR não faz nenhuma claim de prontidão.

### Catálogo canônico (AMH-owned; caminhos = contratos a criar pela AMH, não implementação)

**Seis artefatos** (edição SOMENTE no repo AMH):

1. `schemas/avro/integration/maezo/v1/amh_maezo_work_item.avsc`
2. `schemas/avro/integration/maezo/v1/amh_maezo_consent.avsc`
3. `schemas/avro/integration/maezo/v1/maezo_amh_outcome.avsc`
4. `schemas/openapi/maezo/v1/subject-context.openapi.yaml`
5. `schemas/openapi/maezo/v1/population-features.openapi.yaml`
6. `schemas/contracts/maezo/v1/contract-manifest.yaml`

**Três tópicos:** `amh.maezo.work-items.v1` (AMH→Maezo), `amh.maezo.consent.v1` (AMH→Maezo),
`maezo.amh.outcomes.v1` (Maezo→AMH; schema ainda assim AMH-owned).

**Três tópicos de quarentena** (padrão `<tópico>.quarantine.v1`): `amh.maezo.work-items.v1.quarantine.v1`,
`amh.maezo.consent.v1.quarantine.v1`, `maezo.amh.outcomes.v1.quarantine.v1` — metadados opacos,
payload cifrado/restrito, sem PHI.

**Baseline congelado de campos do envelope comum** (publicado como baseline; qualquer mudança é uma
decisão de contrato VERSIONADA e AMH-owned, nunca um ajuste local Maezo): `event_id`, `event_type`,
`canonical_schema_version`, `occurred_at`, `ingested_at`, `source_vendor`, `source_product`
(`tasy_hospital` | `tasy_healthcare_plan`), `source_instance`, `source_tenant`, `source_entity`,
`protected_source_record_ref`, `source_position` (`kind`, `value`, referência de transação opcional),
`amh_tenant`, `legal_entity`, `portable_subject_ref`, `amh_mpi_ref` (opcional), `beneficiary_ref`
(opcional), `correlation_id`, `causation_id`, `idempotency_key`, `consent_decision_ref`,
`purpose_of_use`, `data_classification`, `trace_id`, `producer_version`, `contract_manifest_digest`,
`payload_hash`, `replay_count`. Este ADR não inventa nenhum campo de wire além deste baseline.

### Proibições imutáveis (invariantes da fronteira)

1. Nenhum consumo de tópico raw `cdc.*` no caminho de compatibilidade AMH.
2. Nenhum tipo Tasy/Debezium/AWS/Glue/HAPI/AMH no domínio core do payer (só em adapters/deployment).
3. Nenhuma credencial Tasy/Oracle, de escrita HAPI, Athena ou Gold-irrestrita no deployment AMH do
   Maezo.
4. Nenhuma cópia de schema editável neste repo — só o pin imutável (`contracts.lock.json`).
5. Nenhum PHI nem ID cru de fonte em keys, logs, traces, métricas ou metadados de DLQ/quarentena —
   referências opacas apenas.
6. Business keys CIB opacas na forma `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`;
   process-definition keys começam com `maezo-payer-`.

## Relacao com ADRs existentes

**ADR-0013 — SUPERSEDE PARCIAL.** Morrem (substituídas pelas cláusulas acima):

- **Cláusula 1** (raw-CDC como contrato de integração): o contrato passa a ser o catálogo canônico
  AMH-owned + manifest + pin (XRD-01/03/04).
- **Cláusula 2, apenas o wire dev-JSON**: o wire é Avro+Glue pinado em dev e prod (XRD-10); a escolha
  de biblioteca `aiokafka` é detalhe de implementação, não contrato, e não é afetada.
- **Cláusula 3** (simulador como contrato vivo): o simulador Tasy é DEMOVIDO a harness local de dev —
  nunca mais autoridade de contrato; autoridade é o manifest + fixtures publicados pela AMH.
- **Cláusula 4** (credenciais Oracle no cofre Maezo): credenciais Oracle AMH NUNCA vêm para o Maezo,
  em nenhum cenário de desbloqueio (proibição imutável 3).
- **Cláusula 5** (DLQs `cdc.amh.tasy.dlq` / `cdc.amh.tasy.fhir-sync.dlq`): substituídas pelos tópicos
  `*.quarantine.v1` + inbox durável (XRD-10). Os tópicos `agents.events.whatsapp.*` listados na mesma
  cláusula não fazem parte do contrato Tasy e NÃO são tocados por este ADR.

SOBREVIVEM e continuam ancorando ADR-0019/0021/0014/0020 e as 7 cláusulas TASY-write-DROP (que
citam ADR-0013 em formatos variados) dos contratos de processo em `docs/processes/contracts/`:
**consume-not-duplicate** (o Maezo nunca é producer nem detentor de cópia de registro), **TASY write
DROP** (nenhuma escrita em sistema de fonte; um comando de fonte, se um dia existir, é adapter
AMH-owned com approval próprio, fora deste repo) e **nenhuma infra CDC neste repo**. Onde esses
documentos citam "ADR-0013" como âncora do princípio, a âncora passa a ser "ADR-0013-princípios via
ADR-0037" — o princípio é o mesmo; só o mecanismo de transporte/contrato mudou.

**ADR-0034 — AMENDS (não supersede).** O ADR-0034 ratificou que `PEP.evaluate` tem zero chamadores
de runtime e fixou a precondição única de revisita: um chokepoint de dispatch por-call só volta com
"uma nova ADR ratificando a re-introducao". A cláusula XRD-09 deste ADR É essa ratificação — efetiva
somente com (a) aceitação humana deste ADR e (b) evidência MZO-040 entregue (gateway inevitável +
fence estático + testes audit-before-effect). Até lá, o enforcement estrutural do ADR-0018 permanece
a única garantia, exatamente como o ADR-0034 registra.

**ADR-0019 — nota (não supersede).** O eixo analítico continua válido. Quando a API
population-features for publicada (XRG-2), o acesso populacional migra do mecanismo gold-view do
`PopulationFeatureClient` para a API (mesma semântica: consent-gate, k-supressão, snapshot pinado);
até lá, o mecanismo do ADR-0019 permanece. Na célula AMH o Maezo não recebe credencial
Athena/Gold-irrestrita (XRD-07).

**ADR-0021 — nota (não supersede).** O StatefulSet HAPI opcional do §1-§2 permanece como opção PORTÁVEL
para deployments não-AMH (cliente com FHIR service próprio); na célula AMH fica desabilitado e o
Maezo não recebe credencial de escrita HAPI nem de lake (XRD-06/07). A cláusula §5 (MSK amh-owned,
consume-not-duplicate, zero provisionamento de tópico/ACL aqui) segue integralmente válida e passa a
apontar para os tópicos canônicos deste ADR.

**ADR-0022 — nota (não supersede).** O registro in-process de tools permanece. Quando MZO-040
aterrissar, o server `mcp_fhir` (hoje cliente HAPI direto) DEVE rotear pelo `ActionExecutionGateway`
— o gateway torna-se a fronteira inevitável de efeito externo, complementando o `ToolRegistry` do
ADR-0016/0022, sem reabrir framing out-of-process.

**ADR-0023 — nota (não supersede).** A lane de CI "fhir-sync simulator smoke (ADR-0013 gap #33)"
(não-required, ADR-0023 §3) perde a razão de ser junto com o mecanismo simulador-como-contrato; sua
aposentadoria segue processo próprio de CI (mudança de workflow + registro), NÃO é executada por este
ADR.

## Consequencias

**Positivas:**
- O conflito de contrato documentado (raw-CDC inexistente vs shipped) deixa de ser fundação de
  qualquer implementação: consumer mapping só nasce de contrato publicado + pin verificado (XRG-2/3),
  eliminando a classe inteira de drift simulador-vs-realidade.
- Fronteira de responsabilidade explícita e testável: portabilidade do core (zero tipos
  AWS/AMH/Tasy/HAPI), mínima superfície de credencial na célula AMH, PHI fora de keys/logs/métricas
  por construção.
- Os princípios já provados do ADR-0013 (consume-not-duplicate, TASY write DROP, zero CDC aqui)
  sobrevivem re-ancorados, sem re-litigação — ADR-0019/0021 e os 7 contratos de processo permanecem
  coerentes.
- O chokepoint por chamada (XRD-09) ganha o veículo de ratificação que o ADR-0034 exigia, com
  condição de efetividade objetiva (aceitação + MZO-040), não um flip silencioso.

**Negativas (aceitas):**
- Fardo dual-platform intencional (ECS data plane + célula EKS Maezo): duas superfícies operacionais,
  aceitas para evitar dois rewrites (XRD-11).
- Janelas de dual-publish/dual-read de no mínimo 30 dias para todo major de contrato, estendíveis até
  reconciliação zero + ensaio de rollback.
- PRs docs-only deste programa pagam o CI completo, incluindo a lane real-engine — o `ci.yml`
  dispara em todo `pull_request` sem path filter (custo aceito; registrado no ADR-0023
  §Consequencias). Nota de drift verificada em 2026-08-03: a proteção server-side de main que o
  ADR-0023 prescreve (opção 1, decisão WS-6 do usuário) NÃO está aplicada no remoto neste momento
  (protection 404, rulesets vazios) — restaurá-la é decisão do dono do repo, fora do escopo deste
  ADR.
- O caminho crítico de 12–16 semanas é dominado por APROVAÇÃO (humana e cross-repo), não por
  paralelismo de código; variância esperada vem de aprovadores e ambientes reais, não de engenharia.
- Trabalho bloqueado até a aceitação (XRG-1) e gates seguintes — mapa compacto de gating por work
  package: **MZO-010**→XRG-2 (evidence ID AMH assinado; fecha XRG-3) · **MZO-020**→XRG-1 + DPO/Legal
  (identidade) · **MZO-030**→XRG-1 (nenhum campo de wire antes de XRG-3) · **MZO-040**→aceitação
  deste ADR + aprovações Médica/ANS/Security · **MZO-050**→XRG-3 (+MZO-010/020/030) ·
  **MZO-060**→MZO-020/030/050 + revisão DBA · **MZO-070**→MZO-040/050/060 + contrato de consent AMH e
  regras de disposição DPO/médica · **MZO-080**→XRG-3 (+MZO-010/030/040) + AMH-060/070/090/100 ·
  **MZO-090**→XRG-3 + schema de outcome AMH (+MZO-010/030/040/060) · **MZO-100**→XRG-1 + aprovação
  de topologia SRE · **MZO-110**→design SRE/Security aprovado + identidades provisionadas pela AMH ·
  **MZO-120**→MZO-060/090 + protocolo AMH-110/120 + aprovação DPO de replay ·
  **MZO-130**→MZO-040/060/090 + aprovação de retenção/legal-hold · **MZO-140**→MZO-050/060/090/110/120 ·
  **MZO-150**→MZO-100/110 + CTO/SRE (apply de infra human-gated) · **MZO-160**→MZO-050..150 +
  serviços AMH correspondentes · **MZO-170**→MZO-160 + AMH-150/160/170 + go/no-go ·
  **MZO-180**→DPO/Legal/médico-regulatório (human-gated; só a política pode paralelizar).

## Supersedes

ADR-0013 (parcial — cláusulas 1, 2 [wire dev-JSON], 3, 4, 5; princípios consume-not-duplicate e
TASY-write-DROP preservados e re-ancorados). Amends ADR-0034 (re-introdução do chokepoint por
chamada, condicionada à aceitação + MZO-040). Não supersede ADR-0019/0021/0022/0023.
