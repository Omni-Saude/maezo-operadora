# ADR-0017: Enforcement de rede do egress da Zona PHI (CIDRs IP-fixados, fail-closed)

**Status:** Accepted
**Data:** 2026-06-13
**Area:** LGPD/Seguranca

## Contexto

A ADR-0006 estabeleceu o modelo de duas zonas (Geral / PHI-Financeira) e prometeu, para a Zona
PHI, uma **garantia de rede** e nao apenas de prompt: _"NetworkPolicy K8s impede egress fora da
allowlist (garantia de rede, nao de prompt)."_ A ADR-0016 fechou o gap do **caminho de saida de
tools** (pseudonimizacao estrutural no `ToolRegistry`). Faltava operacionalizar a metade de
**rede** da ADR-0006: como o chart `maezo-tenant` (Wave-D, ja MERGED em main) realmente impede,
no plano de rede, que um pod da Zona PHI alcance um endpoint nao aprovado.

**A licao aprendida (revisao pegou no flagra).** Uma revisao do chart identificou que a
NetworkPolicy de egress da Zona PHI, conforme escrita inicialmente, continha uma regra
`ipBlock.cidr: 0.0.0.0/0`. Em `NetworkPolicy` puro do Kubernetes isso **nao restringe nada**:
`0.0.0.0/0` permite egress para a **internet inteira** naquela porta — inclusive o LLM cloud
geral. A "garantia de rede" da ADR-0006 era, na pratica, um **no-op**: a politica PHI parecia
restritiva mas era equivalente a "permitir tudo". O controle precisava existir de fato, e o chart
precisava **se recusar a renderizar** uma politica PHI que nao fosse uma restricao real.

**A tensao de honestidade (limite tecnico que NAO pode ser exagerado).** `NetworkPolicy` puro do
Kubernetes e **baseado em IP/CIDR**. O seletor `ipBlock` **nao tem campo `fqdn:`** e
**nao consegue expressar uma allowlist por hostname/FQDN**. Endpoints reais (ex.: um endpoint
contratado cujo IP roda atras de um NLB, ou um IP que rotaciona) nao podem ser allowlistados
"por nome" com `NetworkPolicy` simples. Existem dois niveis distintos de afirmacao, e este ADR
faz questao de **nao confundi-los**:

- O que o chart **entrega hoje**: pinagem de **CIDR concreto** por endpoint BR-resident, e a
  **proibicao de `0.0.0.0/0`** (fail-closed). Isso e a metade IP-fixada da garantia.
- O que o chart **NAO entrega** e e item de **FUTURO documentado**: enforcement genuino por
  **FQDN/hostname**. Isso exige um **egress proxy** pelo qual os pods sao forcados a passar, ou
  uma **politica FQDN do Cilium** (`toFQDNs`). Nenhum dos dois esta implementado.

Em nenhum lugar este ADR (ou o chart) deve afirmar enforcement de rede por FQDN/hostname.
Afirmamos apenas CIDRs IP-fixados + recusa de `0.0.0.0/0`.

### Estado de implementacao (precisao — main HEAD `a2dabda`)

Este ADR documenta um controle de **infraestrutura de rede** que e independente do estado dos
processos de negocio. Para evitar exagero sobre o que esta vivo:

- **MERGED em main:** o body de SP-OP-CONTAS-001 (glosa; PR #30), os agentes Marina/Gustavo/Lucas,
  a tenant factory da Wave-D (este chart `maezo-tenant`), o credential vault + a fiacao
  load-bearing do console, o worker `analyze_request` + as output-vars do `WorkerHarness`, as 6
  contract sheets + topics + os ports CNAB/glosa, e o merge engine W0.4.
- **NAO merged (em PRs abertas):** os 5 bodies SP-OP RECURSO/NIP/ANS-SUBMIT/CANCEL/REEMBOLSO
  estao **autorados e in-flight na PR #38 (em CI)** — nao estao em main.
- **Start desabilitado:** a allowlist de process-start W0.1 (PR #32) esta **aberta e gated por
  humano**. Portanto **nenhum processo da Phase 2 esta start-enabled em main** — nem mesmo o
  CONTAS-001 ja merged. Este controle de egress de rede vale para qualquer agente provisionado
  independentemente disso; e um guardrail de plataforma, nao de processo.

## Decisao

Operacionalizamos a metade de **rede** da ADR-0006 no chart `maezo-tenant`
(`deploy/helm/maezo-tenant/templates/networkpolicy.yaml` + helpers em `_helpers.tpl`). A
NetworkPolicy e renderizada **por agente** sobre um baseline `default-deny-egress` + `allow-dns`.

### 1. Egress PHI fixado a CIDRs IP-concretos, por endpoint BR-resident (FIX 1)

- Cada agente da Zona PHI (`securityZone: phi` — base: rafael, marina) recebe uma NetworkPolicy
  `agent-<name>-egress` cujo egress externo abre **somente** os endpoints BR-resident aprovados
  (`networkPolicy.phiZone.brResidentEndpoints[]`), cada um como um `ipBlock.cidr` **concreto** +
  porta TCP. **Nao existe regra de egress para o LLM cloud geral** nessa politica.
- O hop para o gateway pseudonimizador (PEP) e coberto separadamente por `allow-intra-namespace`
  (item 3 abaixo), nao pela regra de egress externo.
- Se nenhum endpoint BR-resident estiver configurado, a lista de egress externo fica **vazia
  (deny)** — postura fail-closed correta; nao se relaxa.

### 2. Fail-close do chart em CIDR PHI ausente ou `0.0.0.0/0` (FIX 1 — fail-closed)

- O helper `maezo-tenant.requirePhiCidr` valida **cada** endpoint PHI em tempo de
  `helm template` e **aborta a renderizacao** (`fail`) se o `cidr` estiver **ausente/vazio** ou
  for **`0.0.0.0/0` / `::/0`**. A mensagem explica que `ipBlock` nao allowlista por hostname e
  que um CIDR BR-resident concreto e mandatorio.
- Como o `fail` ocorre no `helm template`, ele e exercido em CI por dois lados: o gate
  `validate-helm` renderiza os values commitados (CIDRs PHI concretos -> render OK), e o gate
  `quality` (`make test`) roda os testes NEGATIVOS de `tests/unit/platform/test_provision_tenant.py`
  que renderizam um endpoint PHI sem `cidr` / com `0.0.0.0/0` e **confirmam o abort** — pegando a
  politica PHI no-op **antes de qualquer apply**.
- A mesma disciplina (`maezo-tenant.requireGeneralCidr`) se aplica aos endpoints da Zona Geral
  (`generalZone.llmEndpoints[].cidr`, `mskEndpoint.cidr`): a Zona Geral so ve dado
  pseudonimizado, mas `0.0.0.0/0` ainda nao e uma allowlist e tambem e rejeitado.

### 3. Egress intra-namespace escopado por label do gateway — sem relay PHI->geral (FIX 2)

- A NetworkPolicy `allow-intra-namespace` permite egress **apenas** para os componentes
  gateway/shared-infra, selecionados por `app.kubernetes.io/component`
  (`networkPolicy.intraNamespaceEgressComponents`, default `[gateway]`) — **nao** por
  `podSelector: {}`.
- Motivo: um egress intra-namespace irrestrito deixaria um pod PHI alcancar o **pod do agente da
  Zona Geral** (que detem a regra de egress para o LLM cloud), criando um relay
  `pod-PHI -> pod-geral -> cloud` que anularia a ADR-0006. Agentes podem alcancar o gateway (o
  PEP pseudonimizador) e infra genuinamente compartilhada; egress agente <-> agente-par
  permanece **negado** pelo default-deny. Nao se adiciona componentes `agent-*` a essa lista.

### 4. `securityZone` cross-validado contra a `effectiveDefinition` merged (FIX 3)

- O helper `maezo-tenant.validateAgents` **nao confia** em `agents[].securityZone` literalmente.
  Quando o agente carrega a `effectiveDefinition` montada (o conteudo merged L0+overlay que
  `provision_tenant.py` injeta via o merge engine W0.4), o chart parseia seu `security_zone` e
  **falha** se ele divergir de `securityZone`.
- Consequencia: um values file editado a mao que rebaixe um agente base-PHI (ex.: rafael) para
  `general` **nao** consegue renderizar silenciosamente uma politica geral para um agente PHI —
  aborta. A Agent Definition merged e a fonte da verdade; a zona do values e defesa-em-profundidade
  que **precisa coincidir** com ela.

### 5. FQDN/hostname — explicitamente FUTURO, nao entregue

- O chart **nao** faz, e este ADR **nao** afirma, enforcement de rede por FQDN/hostname. A
  pinagem de CIDR cobre endpoints com IP/NLB estavel; para allowlist real por nome de host
  (IPs que rotacionam) o caminho e: **egress proxy obrigatorio** ou **politica FQDN do Cilium
  (`toFQDNs`)**. Item de roadmap; ate la, o valor de `cidr` e ambiente-especifico e deve ser
  re-pinado por VPC (ver `deploy/helm/README.md`).

## Consequencias

**Positivas:**

- A "garantia de rede" da ADR-0006 deixa de ser um no-op: para a Zona PHI o egress externo agora
  e um conjunto **finito e revisado** de CIDRs BR-resident, e a alternativa "permitir tudo"
  (`0.0.0.0/0`) e estruturalmente **impossivel** de embarcar — o chart aborta.
- O controle e **verificavel por CI** (fail no `helm template`), nao por disciplina de quem
  escreve o values; o erro que a revisao pegou a mao agora e pego automaticamente antes do apply.
- O relay `PHI -> agente-geral -> cloud` (FIX 2) e fechado no plano de rede, complementando o
  fechamento do caminho de tool da ADR-0016 no plano de aplicacao.
- Rebaixar um agente PHI para `general` por edicao de values nao passa silenciosamente: o
  cross-check contra a definicao merged (FIX 3) aborta o render.
- Vale para qualquer agente provisionado independentemente do estado dos processos de negocio
  (Phase 2 ainda nao start-enabled em main) — e um guardrail de plataforma.

**Negativas (aceitas):**

- **Nao ha enforcement por FQDN/hostname.** A pinagem de CIDR so vale enquanto o IP/NLB do
  endpoint for estavel; para hosts cujos IPs rotacionam, a allowlist real exige egress proxy ou
  Cilium FQDN policy — item de FUTURO ainda nao implementado. Esta e a limitacao honesta do
  controle: ele entrega a metade IP-fixada, nao a metade FQDN.
- Os valores de `cidr` sao **ambiente-especificos** e precisam ser re-pinados por VPC; um erro de
  pinagem (CIDR largo demais) nao e pego pelo chart — apenas `0.0.0.0/0`/`::/0` e ausencia sao.
- Adicionar um endpoint PHI exige revisao de compliance e alteracao de values (atrito
  intencional).
- O cross-check de zona (FIX 3) so dispara quando a `effectiveDefinition` esta montada; um values
  autorado a mao sem provisioning nao tem com o que cruzar (tratado como "nada a verificar").

## Supersedes

ADR-0006 (complementa — nao supersede). A ADR-0006 estabelece o modelo de duas zonas e promete a
garantia de rede; este ADR operacionaliza essa garantia no chart `maezo-tenant`, entregando a
metade de **CIDR IP-fixado + fail-close em `0.0.0.0/0`** e registrando o enforcement por FQDN como
item de FUTURO. Relaciona-se com ADR-0016 (que fecha o caminho de saida de tools no plano de
aplicacao; este fecha o egress no plano de rede) e ADR-0004 (per-tenant Agent Definitions / merge
engine, fonte do `security_zone` cross-validado).
