# ADR-0021: Dependencias in-cluster (CIB Seven, HAPI-FHIR) via StatefulSet — NAO operator; MSK continua amh-owned

**Status:** Accepted (2026-07-05)
**Data:** 2026-06-14
**Area:** Infra / Deploy / Seguranca

## Contexto

O agent-runtime fala com duas dependencias stateful por DNS de Service fixo: CIB Seven (motor de
governanca, ADR-0001) em `http://cibseven:8080/engine-rest` e HAPI-FHIR (servidor FHIR R4) em
`http://hapi-fhir:8080/fhir` (ver `deployment-agent-runtime.yaml`, env `CIBSEVEN_BASE_URL` /
`FHIR_BASE_URL`, e `deployment-fhir-sync.yaml`). Ate aqui essas duas dependencias eram **externas** ao
chart — providas fora do cluster e referenciadas pelos URLs acima. Em ambientes onde nao ha instancia
externa gerenciada (staging, demonstracao, um tenant que prefere rodar tudo no proprio cluster) e util
ter a opcao de subir CIB Seven e HAPI-FHIR **dentro do cluster**, sob os mesmos nomes de Service, sem
mudar uma linha do agent-runtime.

Tres tensoes precisam ser resolvidas:

1. **Como rodar uma dependencia stateful no Kubernetes?** Deployment (sem identidade estavel), operator
   (CRD + controller proprio), ou StatefulSet (identidade de rede estavel, DNS por pod, ordering).

2. **Onde colocar o HAPI-FHIR no modelo de duas zonas (ADR-0006)?** O HAPI-FHIR guarda **PHI clinico
   bruto** (recursos FHIR de pacientes reais). O CIB Seven guarda estado de **governanca/processo**, nao
   PHI clinico bruto.

3. **Banco de dados.** Tanto CIB Seven quanto HAPI-FHIR precisam de **um banco proprio**, separado do
   checkpointer do agente (Aurora). A tentacao seria provisionar um segundo Postgres in-cluster por
   dependencia — mais um StatefulSet stateful, mais um ponto de backup/restore/PITR a operar.

4. **Topicos/ACLs do MSK.** O MSK Serverless (e os topicos CDC do Tasy) sao **propriedade do
   amh-data-platform** — consumimos, nao duplicamos (ADR-0013). Um chart de tenant que crie topicos/ACLs
   seria duplicacao de propriedade e quebraria a fronteira consume-not-duplicate.

## Decisao

1. **StatefulSet (NAO operator) como estrategia de HA in-cluster.** CIB Seven e HAPI-FHIR sao
   renderizados como **StatefulSet + Service ClusterIP + Service headless** cada
   (`statefulset-cibseven.yaml`, `statefulset-hapi-fhir.yaml`). StatefulSet da identidade de rede
   estavel e DNS por pod (via o Service headless) sem o peso operacional de adotar/operar um operator e
   seu CRD. O Service ClusterIP reusa **exatamente** o nome que o agent-runtime ja resolve (`cibseven`,
   `hapi-fhir`), entao ligar o in-cluster nao muda o agent-runtime.

2. **Ambos GATED OFF por padrao; o render default fica IDENTICO.** As flags
   `.Values.cibseven.inCluster.enabled` e `.Values.fhir.inCluster.enabled` sao **`false` por padrao**.
   Com elas OFF, os dois templates renderizam **nada**, e o chart default continua apontando o
   agent-runtime para os URLs externos (`deployment-agent-runtime.yaml` permanece **inalterado**). Ligar
   o in-cluster e uma decisao explicita do operador, por ambiente; o default e sempre o caminho externo
   no-op.

3. **HAPI-FHIR guarda PHI bruto -> colocado na Zona PHI (ADR-0006).** O pod do HAPI-FHIR carrega
   `maezo.io/phi-zone: "phi"`, espelhando o `fhir-sync` (que ja toca PHI integral Tasy <-> FHIR). O CIB
   Seven, que ve estado de governanca/processo e nao PHI clinico bruto, fica na **Zona Geral**
   (`maezo.io/phi-zone: "general"`). As NetworkPolicies por-agente ja isolam o egress PHI; colocar o
   store FHIR na zona PHI mantem o PHI bruto dentro dessa fronteira.

4. **Banco PROPRIO de cada dependencia via URL EXTERNA configuravel — NAO um segundo Postgres no
   chart.** Cada StatefulSet aponta para um banco **externo** configuravel
   (`.Values.cibseven.inCluster.db.url` / `.Values.fhir.inCluster.db.url`, ou um secret via
   `existingSecret`), injetado como `CIBSEVEN_DATABASE_URL` / `SPRING_DATASOURCE_URL`. O chart **NAO
   provisiona um segundo Postgres**: prover esse banco (RDS/Aurora separado, ou um Postgres operado a
   parte) e responsabilidade do operador. Isso mantem o chart sem mais um workload stateful de banco a
   operar e separa o banco da dependencia do checkpointer do agente.

5. **MSK e amh-owned — este chart NAO provisiona topicos/ACLs.** Consistente com ADR-0013
   (consume-not-duplicate), **nenhum** Job/recurso de provisionamento de topico ou ACL de MSK e
   autorado por este chart. Os topicos CDC do Tasy e o MSK Serverless pertencem ao amh-data-platform;
   o agent-runtime apenas **consome** via `KAFKA_BOOTSTRAP_SERVERS` (secret `maezo-kafka-config`, ja
   sincronizado do Secrets Manager do amh-data-platform).

6. **Fora de escopo: rodar de fato.** Subir as instancias, aplicar creds reais de banco e qualquer
   apply de nuvem/AWS estao **fora de escopo** deste ADR e do chart (so template/autoria, gated OFF).
   O render default e um no-op verificavel por `helm template` / `helm lint --strict`.

## Consequencias

**Positivas:**
- Ligar o in-cluster nao toca o agent-runtime: os Services reusam os nomes `cibseven` / `hapi-fhir`,
  entao `CIBSEVEN_BASE_URL` / `FHIR_BASE_URL` continuam validos sem mudanca.
- Sem peso de operator: StatefulSet entrega identidade/DNS estavel sem adotar e operar um CRD/controller.
- Render default inalterado e gated-off: o default continua o caminho externo, no-op, verificavel.
- PHI bruto do HAPI-FHIR fica na Zona PHI (ADR-0006), sob as NetworkPolicies de isolamento ja existentes.
- Sem segundo Postgres no chart: cada dependencia usa banco externo configuravel; um store stateful a
  menos para o chart operar.
- Fronteira amh-owned respeitada: nenhum topico/ACL de MSK e criado aqui (ADR-0013).

**Negativas (aceitas):**
- O operador precisa prover o banco externo de cada dependencia (URL/secret) antes de ligar o in-cluster;
  sem isso o StatefulSet sobe sem datasource valido. Aceito: prover banco e responsabilidade de infra,
  fora de escopo do chart.
- StatefulSet nao traz a automacao de failover/backup de um operator dedicado; HA real do CIB Seven /
  HAPI-FHIR depende do banco externo e de runbooks operacionais, nao do chart.
- Imagens/tags default das dependencias sao placeholders sensatos; o operador deve pinar tag/registry
  apropriados por ambiente antes de ligar.

## Supersedes

— (estende ADR-0001 (CIB Seven como backbone de governanca) e ADR-0006 (zonas PHI: HAPI-FHIR na Zona
PHI), e respeita ADR-0013 (consume-not-duplicate: MSK/topicos amh-owned, nao provisionados aqui).)
