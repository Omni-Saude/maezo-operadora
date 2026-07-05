# ADR-0019: amh-data-platform como lake-of-record analitico; Maezo consome agregados pseudonimizados, nunca duplica fact tables clinicas

**Status:** Proposed
**Data:** 2026-06-13
**Area:** Dados / LGPD / Analytics
**Regulado:** parcial — parametros de supressao (k-anonymity/small-cell), escopo de consent e os
grants LF-Tag cross-tenant sao **DRAFT — requires human review (DPO/jurídico/regulatório/atuarial)
before any deploy**.

## Contexto

A Phase 3 introduz processos e agentes que precisam de sinal **populacional/atuarial** — gestao de
saude populacional, identificacao de care-gaps, sinistralidade por coorte, priorizacao de programas
de cronicos, e o sinal de base para SP-OP-FRAUDE-001 (deteccao de padrao anomalo, nunca acusacao
automatizada — ver ADR-0018/ADR-0020). Esse sinal e, por natureza, **agregado** (sobre coortes de
beneficiarios), nao um stream de fatos clinicos paciente-a-paciente.

A ADR-0013 ja estabeleceu o eixo **operacional/transacional**: o `amh-data-platform` e a plataforma
de dados de registro; consumimos seus topicos CDC Tasy (`cdc.amh.tasy.*`) como contrato de
integracao e **nunca duplicamos** o pipeline CDC nem as credenciais Oracle. O que faltava decidir e
o eixo **analitico**: o `amh-data-platform` tambem materializa um **lake/feature-store** (zonas
bronze/silver/gold, feature tables atuariais e clinicas, governado por AWS Lake Formation per os ADRs
do proprio amh-data-platform). A tentacao seria, para servir os agentes de Phase 3, **copiar** essas
fact tables clinicas paciente-a-paciente para dentro do Maezo (um data mart proprio). Isso seria o
mesmo erro que a ADR-0013 evitou no eixo operacional, agora no eixo analitico: duplicaria PHI, criaria
uma segunda copia de registro a reconciliar para erasure LGPD, e fragmentaria a governanca.

Tres opcoes foram consideradas:

1. **Copiar fact tables clinicas para um data mart Maezo** (rejeitada). Maxima flexibilidade de
   query, mas duplica PHI clinico paciente-a-paciente fora do perimetro de governanca do
   amh-data-platform; multiplica a superficie de erasure LGPD (cada copia precisa ser apagada
   independentemente); diverge do principio consume-not-duplicate da ADR-0013.

2. **Query federada ad-hoc direto nas tabelas gold** (rejeitada). Sem copia, mas sem contrato:
   acoplaria o Maezo ao schema interno do lake, exporia granularidade paciente-a-paciente sem gate
   de consent/supressao, e tornaria toda query um vetor de re-identificacao nao auditado.

3. **Consumir apenas agregados pseudonimizados via um client read-only contratado, com gate de
   consent e supressao de celula pequena** (escolhida). O amh-data-platform permanece o
   lake-of-record analitico; o Maezo le um conjunto **finito, versionado e suprimido** de
   features populacionais/atuariais, nunca a fact table clinica crua.

A tensao central e LGPD: minimizacao e finalidade. Sinal populacional para gestao de saude tem base
legal distinta de cobertura/atendimento individual; misturar os dois num data mart copiado embaralha
finalidades e dificulta o atendimento do direito ao esquecimento (ADR-0002).

## Decisao

1. **O `amh-data-platform` e o lake-of-record analitico. O Maezo CONSOME, nunca duplica fact tables
   clinicas paciente-a-paciente.** Estende a ADR-0013 do eixo operacional (CDC) para o eixo
   analitico (lake/feature-store): em nenhum dos dois eixos o Maezo e producer ou detentor de copia
   de registro de PHI clinico. Nao existe data mart clinico paciente-a-paciente neste repo.

2. **`PopulationFeatureClient` — client read-only de agregados.** O unico caminho de leitura do lake
   analitico e um client read-only que expoe **features agregadas** (populacional, atuarial,
   care-gap por coorte), nunca linhas clinicas individuais. O client:
   - le exclusivamente das views/feature tables **gold agregadas** expostas pelo amh-data-platform
     para este fim (contrato de saida do lake, nao schema interno);
   - retorna metricas por coorte/segmento, jamais um `fhir_patient_id` resolvivel nem um registro
     clinico cru;
   - pina a **versao do snapshot do feature-store** consumido (reprodutibilidade — base para o
     freeze de evidencia de ADR-0020);
   - e read-only por construcao: nenhuma credencial de escrita no lake e provisionada ao Maezo
     (espelha o TASY-write-DROP da ADR-0013 — consumimos, nunca escrevemos).

3. **`ConsentGate` com `scope=operational_analytics`.** Toda leitura via `PopulationFeatureClient`
   passa por um gate de consent que so libera a feature se o escopo `operational_analytics`
   (analytics operacional / gestao de saude populacional) estiver satisfeito para a coorte. O gate
   e fail-closed: ausencia/ambiguidade de base legal **nega a leitura**, nunca libera por default.
   O escopo `operational_analytics` e distinto do escopo de cobertura/atendimento individual — a
   separacao de finalidade LGPD e estrutural, nao convencional.

4. **k-anonimato / supressao de celula pequena como parametro de compliance.** Toda feature agregada
   servida obedece a um piso de k-anonimato (`k` minimo por celula) e supressao de small-cell: uma
   coorte com contagem abaixo do piso e **suprimida** (nao retornada / mascarada), evitando
   re-identificacao por celula pequena. O valor de `k` e a politica de supressao sao um **parametro
   de compliance versionado** — **DRAFT — requires human review (DPO/jurídico/regulatório) before
   any deploy**; o codigo le o piso de config, nunca hardcoda um `k` baixo.

5. **Reconciliacao de erasure LGPD.** O direito ao esquecimento (ADR-0002, cascata por
   `fhir_patient_id`) interage com o lake assim:
   - o Maezo **nao detem** a fact table clinica; o erasure paciente-a-paciente no lake e
     responsabilidade do amh-data-platform (lake-of-record), acionado pelo Maezo via o contrato de
     erasure do amh-data-platform;
   - o que o Maezo apaga em cascata e o **surrogate de ligacao** `mpi_id <-> fhir_patient_id` (o
     mapeamento que permitiria re-ligar um agregado a um individuo) — derruba-se essa ponte;
   - **agregados anonimos sobrevivem** ao erasure: uma vez suprimida a celula pequena e dropado o
     surrogate, a metrica populacional nao e mais dado pessoal e nao precisa (nao deve, por
     finalidade atuarial/regulatoria) ser destruida. A fronteira "o que sobrevive ao erasure" e
     **DRAFT — requires human review (DPO/jurídico)**.

6. **Isolamento cross-tenant via LF-Tag tenant-scoped grants (desenho agora; enforcement
   AWS-blocked).** O isolamento entre tenants no lake e expresso por **grants Lake Formation
   tag-based (LF-Tags) escopados por tenant**: cada feature table/coluna carrega uma LF-Tag de
   tenant, e o principal de leitura do Maezo so recebe grant para a(s) tag(s) do seu tenant. Isso e
   **desenhado agora** como contrato; o **enforcement real e AWS-blocked** (depende de
   provisionamento de Lake Formation/IAM pelo time amh-data-platform — analogo ao BLOCKED das
   credenciais Oracle na ADR-0013). Honestidade: este ADR **nao afirma** isolamento cross-tenant
   enforced hoje; afirma o **desenho** do grant tenant-scoped e o registra como item gated por
   desbloqueio AWS. Ate la, dev/CI usa o feature-store simulado (contrato vivo, espelhando o padrao
   simulador-como-contrato da ADR-0013).

## Consequencias

**Positivas:**
- Zero duplicacao de PHI clinico no eixo analitico: o principio consume-not-duplicate da ADR-0013 se
  estende ao lake; nao ha segunda copia de registro a reconciliar para erasure.
- Minimizacao LGPD por construcao: agentes de Phase 3 veem sinal **agregado e suprimido**, nunca
  linha clinica crua; o `ConsentGate` separa a finalidade `operational_analytics` da finalidade de
  cobertura individual.
- Re-identificacao por celula pequena e estruturalmente mitigada (k-anonimato/small-cell como
  parametro versionado, fail-closed).
- Erasure LGPD reconciliavel: dropar o surrogate `mpi_id<->fhir_patient_id` desliga a ponte
  re-identificadora; agregados anonimos sobrevivem com base legal atuarial/regulatoria clara.
- Read-only por construcao (sem credencial de escrita no lake) espelha o TASY-write-DROP: o Maezo
  nao pode corromper o lake-of-record.

**Negativas (aceitas):**
- Dependencia do contrato de saida (gold agregada) do amh-data-platform: novas features exigem
  coordenacao upstream, nao uma query ad-hoc.
- Isolamento cross-tenant via LF-Tag e **desenho, nao enforcement** ate o desbloqueio AWS; ate la a
  garantia real e o feature-store simulado + o grant read-only escopado — nao se deve exagerar o
  enforcement vivo.
- Supressao de small-cell reduz a granularidade disponivel para coortes raras (trade-off explicito:
  privacidade > resolucao em celulas pequenas); `k` alto demais cega analises legitimas de coortes
  pequenas — calibracao e decisao de governanca pendente de sign-off.
- A fronteira "agregado anonimo sobrevive ao erasure" depende de sign-off jurídico/DPO; ate o
  sign-off, tratamos a fronteira como DRAFT e nao a automatizamos como destruicao nem como retencao.

## Supersedes

— (estende a ADR-0013 do eixo operacional/CDC para o eixo analitico/lake; relaciona-se com a
ADR-0002 (erasure em 3 camadas + cascata por `fhir_patient_id` — aqui adiciona o drop do surrogate
`mpi_id<->fhir_patient_id`), a ADR-0006 (zonas PHI / pseudonimizacao — o agregado servido e
pseudonimizado e suprimido na origem) e a ADR-0020 (a custody chain congela o snapshot de
feature-store pinado por este client). Nao substitui nenhuma.)
