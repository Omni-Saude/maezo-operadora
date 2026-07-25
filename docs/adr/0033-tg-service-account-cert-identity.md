# ADR-0033: Identidade de servico do agente — emissao e verificacao do certificado/service-account (metade cert do T-G)

**Status:** Proposed — NAO VINCULANTE · **Data:** 2026-07-25 · **Area:** Seguranca / Auditoria

> **Enquadramento (leia antes do resto).** Este ADR e produzido para ACELERAR a decisao humana de
> seguranca/infra, nao para substitui-la. Ele **NAO decide** — estrutura o espaco de decisao
> (drivers, opcoes, trade-offs, perguntas abertas) e propoe uma recomendacao explicitamente
> **nao vinculante**, pendente ratificacao do arquiteto de seguranca/infra (gatekeeper). O BUILD da
> metade certificado deste T-G fica, adicionalmente, **bloqueado externamente** por provisionamento
> de vault/KMS/CA — nenhum modulo terraform de KMS/PKI existe hoje neste repo
> (`deploy/terraform/modules/` = `aurora-postgres/ecr/eks-cluster/github-oidc/observability/secrets`,
> nenhum `kms`/`acm`/`pca`). Nenhum codigo de emissao/verificacao de certificado deve ser escrito
> antes deste ADR (ou uma sucessora) ser `Accepted` **e** o vault/KMS correspondente estar
> provisionado.
>
> Este ADR cobre APENAS a metade certificado/service-account do T-G (ADR-0007 `:10`, "service
> account + certificado"). A metade **Agent-Card-assinado** ja esta especificada e construida na
> branch `t2.4-a2a-w1-card-signing` (ainda nao mergeada em `main`) —
> `src/maezo/a2a/{card,signing,registry}.py`: `AgentCard` frozen + `signing_payload()` +
> `CardSigner` (HMAC-SHA256) + `A2ARegistry(verifier=...)`; ver
> `docs/design/A2A-dispatcher-card-signing.md` §3.3/§4/§7.1, que ja identifica esta lacuna como
> "DESIGN-GAP" e pede exatamente este ADR antes de qualquer codigo de emissao de certificado.

## Contexto

### O que existe hoje

1. **Agent Card assinado (W1, branch `t2.4-a2a-w1-card-signing`)** — liga criptograficamente
   `agent_id`+`version`+`tenant`+`security_zone`+capabilities via HMAC-SHA256 sobre
   `signing_payload()` (`card.py`), verificado por `CardSigner.verify`/`require_valid`
   (`signing.py:79-107`) antes de admissao no `A2ARegistry.register` (`registry.py:59-68` —
   `require_valid` chamado ANTES da insercao, fail-closed quando um `verifier` e injetado). A chave
   HMAC chega por injecao (`assembly.card_signer_from_key`/`card_signing_key_from_env`,
   `assembly.py:65-80`), espelhando o seam de `gateway.pseudonymizer` — mas a POPULACAO real da
   chave em vault/KMS e dependencia externa bloqueada (design doc §6.2). Isto resolve "Agent Card
   assinado" de ADR-0007:10; NAO resolve "service account + certificado".
2. **`AUDIT_AGENT_ID` — uma STRING PLANA, sem lastro criptografico** —
   `src/maezo/tools/workers/harness.py:111`: `AUDIT_AGENT_ID: str = "operadora-worker"`. E a
   identidade auditada de toda completude de worker (`harness.py:1151,1218`): qualquer processo
   capaz de chamar o harness carimba esse mesmo literal — nada verifica que o chamador e de fato o
   worker daemon operado pela operadora. O mesmo padrao de string-plana-por-agente aparece em
   `AgentDecisionProvenance.agent_id` (`src/maezo/tools/mcp_cibseven/transport.py:464-502` — campo
   `str` puro, sem verificacao) e nos 10 grafos de agente que instanciam
   `AgentDecisionProvenance(agent_id="andre", ...)` / `agent_id="rafael"` / etc. (e.g.
   `andre/graph.py:902`, `rafael/graph.py:479`, `helena/graph.py:659`) — cada um um literal
   auto-declarado, nao verificado.
3. **ADR-0007:10** pede "Identidade de servico por agente+tenant (service account + certificado;
   Agent Card assinado)" numa unica clausula, sem elaborar mecanismo de emissao, granularidade ou
   chokepoint de verificacao. ADR-0032:67-73 ja sinalizou isto como um DESIGN-GAP proprio,
   explicitamente fora do escopo daquela correcao de status ("a metade service-account/cert de T-G
   e um DESIGN-GAP em si mesma ... precisa de ADR dedicado, fora de escopo aqui").
4. **Infra disponivel hoje (k8s + AWS), nenhuma PKI/identidade de workload:** ServiceAccount POR
   AGENTE ja existe via IRSA (`deploy/helm/maezo-tenant/templates/deployment-agent-runtime.yaml:8`,
   "per-agent ServiceAccount (IRSA role scoped to that agent — no shared cloud identity)"), mas e
   identidade de NUVEM (AWS IAM) para acessar recursos AWS, nao identidade de aplicacao para
   mTLS/auditoria. Secrets chegam via External Secrets Operator + AWS Secrets Manager, autenticado
   por IRSA (`externalsecret.yaml:2,11` — `SecretStore` cujo `provider` e AWS Secrets Manager) —
   este e o unico seam de "vault" que existe hoje neste repo. Nao ha service mesh (Istio/Linkerd),
   nem SPIFFE/SPIRE, nem modulo terraform de KMS/PKI dedicado.

### O que falta

Uma identidade de runtime VERIFICAVEL por processo agente/worker — certificado mTLS de cliente ou
token de service-account assinado — de modo que o `agent_id` da cadeia de auditoria (ADR-0007) seja
NAO-REPUDIAVEL (criptograficamente amarrado a quem de fato executou), nao apenas uma string
auto-declarada. Hoje qualquer processo com acesso de rede ao harness pode se apresentar como
`AUDIT_AGENT_ID`/qualquer `agent_id` de `AgentDecisionProvenance` sem ser desafiado.

## Decision drivers

- **Nao-repudio (ADR-0007)** — a razao de existir deste ADR; sem identidade verificavel, o "quem"
  na tupla `(agent_id, agent_version, tenant, tool, input_hash, decision_basis, ...)` e
  auto-declarado, nao provado.
- **Fronteiras de confianca da Zona PHI (ADR-0006)** — duas zonas de seguranca (PHI/nao-PHI); a
  identidade de servico deve poder amarrar qual zona um chamador esta autorizado a operar.
- **Requisitos de auditoria regulatoria de saude / LGPD** — a cadeia de auditoria de ADR-0007
  alimenta resposta a ANS/justica; uma identidade repudiavel enfraquece a cadeia inteira, nao so o
  T-G.
- **Viabilidade operacional (k8s/vault/KMS disponiveis, mas NENHUMA PKI/workload-identity hoje)** —
  a opcao escolhida precisa ser operavel com a infra que este time ja roda (EKS + ESO + AWS Secrets
  Manager/IRSA), nao pressupor um control-plane que ainda nao existe.
- **Rotacao** — chave/cert comprometido ou vencido precisa de caminho de revogacao/renovacao sem
  downtime coordenado manual.

## Opcoes (espaco de decisao — nenhuma e a decisao final)

### Opcao A — SPIFFE/SPIRE workload identity + mTLS

- **Emissao:** SPIRE Server atesta a identidade do workload (k8s ServiceAccount + node attestation)
  e emite um SVID (X.509 ou JWT) de curta duracao via SPIRE Agent (DaemonSet) ao lado de cada pod.
- **Granularidade:** por SPIFFE ID, tipicamente
  `spiffe://<trust-domain>/ns/<tenant-ns>/sa/<agent>` — naturalmente per-agent+per-tenant (o
  namespace ja e por tenant, ADR-0004).
- **Rotacao:** automatica, TTL curto (minutos-horas), sem intervencao humana pos-bootstrap.
- **Chokepoint de verificacao:** na BORDA de rede (sidecar/mTLS terminator) — verificacao ANTES do
  processo da aplicacao ver a requisicao; o audit-emit chokepoint receberia a identidade ja
  verificada via contexto de conexao/header.
- **Trade-off:** o mecanismo mais forte para N servicos se autenticando entre si, mas exige
  implantar e operar um NOVO control-plane (SPIRE Server + Agents) que este cluster nao tem hoje
  — nenhum service mesh, nenhum SPIRE. Custo de operacao alto para o ganho marginal frente a um
  runtime que hoje e "harness por agente falando com Postgres/Kafka/CIB Seven", nao um mesh
  peer-to-peer.

### Opcao B — Vault-issued short-lived service-account JWT por agente+tenant (RECOMENDADA)

- **Emissao:** um emissor ("vault", aqui dado o que ja existe: AWS Secrets Manager/KMS + ESO, ou um
  HashiCorp Vault real se introduzido) assina um JWT de curta duracao por processo agente na
  inicializacao, com claims `(agent_id, tenant, agent_version)`; renovado periodicamente via um
  sidecar/init-container que reautentica via IRSA (identidade de nuvem ja existente) e chama a API
  de emissao.
- **Granularidade:** claims explicitos — cobre `agent_id` E `tenant` E `agent_version` no proprio
  token; `agent_version` e exatamente o dado que falta em `AUDIT_AGENT_ID` hoje.
- **Rotacao:** curta (minutos-horas), automatica, sem novo control-plane de rede — so uma chamada
  de reemissao que se pareceria com o que `card_signing_key_from_env` ja faz hoje para a chave
  HMAC.
- **Chokepoint de verificacao:** IN-PROCESS, no ponto de audit-emit
  (`emit_once`/`start_process_idempotent`/construcao de `AgentDecisionProvenance`) — o token e
  verificado (assinatura + expiracao + claims) exatamente onde `AuditRecord.agent_id` e hoje
  preenchido por uma string crua; substitui "confie na string" por "o `agent_id` do claim de um JWT
  verificado".
- **Trade-off:** reaproveita a infra JA EXISTENTE (ESO/Secrets Manager/IRSA, mesmo padrao do seam
  `card_signer_from_key`) sem exigir um novo control-plane de rede; mas a verificacao IN-PROCESS
  (nao na borda) exige uma biblioteca de verificacao de JWT correta e deixa uma janela de confianca
  entre "token emitido" e "token verificado" que um sidecar mTLS eliminaria estruturalmente. Nao
  cobre transporte (nao e mTLS) — TLS de transporte continua sendo preocupacao separada (ja citado
  como "mTLS interno" em ADR-0003:11).

### Opcao C — Certificado estatico por agente de uma CA interna (service-account cert)

- **Emissao:** uma CA interna (privada — AWS Private CA/ACM PCA, ou self-managed) emite UM
  certificado X.509 por `(agent_id, tenant)` na hora do deploy (Helm cria o secret via
  ExternalSecret, mesmo padrao de `externalsecret.yaml`); o certificado e montado no pod.
- **Granularidade:** por agente+tenant, fixado na Subject/SAN do certificado; `agent_version` NAO
  cabe naturalmente (reemitir a cada bump de versao de Agent Definition e operacionalmente caro).
- **Rotacao:** manual ou semi-automatica (cron de renovacao antes da expiracao) — sem
  short-lived-token/SVID automatico; e o modelo mais proximo do texto literal de ADR-0007:10
  ("service account + certificado"), mas o mais fraco em rotacao.
- **Chokepoint de verificacao:** pode ser na borda (mTLS terminator) OU no processo (client-cert
  presente na conexao, verificado no worker/agent-runtime); mais simples de implementar que A, mas
  herda o mesmo atrito operacional de rotacao manual que ja morde este repo em outro lugar
  (ADR-0029 cita rotacao/chave assinada como fonte de atrito operacional para o audit_chain).
- **Trade-off:** menor esforco de infra nova (nenhum control-plane, so uma CA + ExternalSecret),
  mas rotacao fraca e granularidade que nao cobre `agent_version` sem reemissao cara — o requisito
  "sob-qual-versao" de ADR-0007 fica mal servido.

### Tabela-resumo

| Opcao | Emissao | Granularidade | Rotacao | Chokepoint de verificacao | Amarra a AUDIT_AGENT_ID/Provenance | Trade-off principal |
|---|---|---|---|---|---|---|
| A — SPIFFE/SPIRE + mTLS | SPIRE Server (node+SA attestation) | per-agent+per-tenant (SPIFFE ID) | Automatica, curta (min-h) | Borda de rede (sidecar/mTLS terminator) | Identidade ja verificada chega via contexto de conexao | Mais forte; exige NOVO control-plane (nenhum mesh hoje) |
| B — Vault-issued short-lived JWT (RECOMENDADA) | Reemissao periodica via API assinadora (IRSA-autenticada) | per-agent+per-tenant+per-agent_version (claims) | Curta, automatica, sem novo control-plane de rede | In-process, no ponto de audit-emit (`emit_once`/`AgentDecisionProvenance`) | Direta — claim vira `agent_id`/`agent_version` verificados | Reaproveita infra existente; janela de confianca in-process, nao cobre transporte |
| C — Cert estatico por agente (CA interna) | CA interna (ACM PCA ou self-managed) na hora do deploy | per-agent+per-tenant (Subject/SAN); NAO cobre agent_version | Fraca (manual/cron) | Borda OU processo | Parcial — cert amarra agent_id/tenant, nao version | Menor esforco novo; rotacao fraca, version mal servida |

## Perguntas abertas que o HUMANO deve decidir

1. **Autoridade de emissao** — quem/o que assina: uma CA/Vault interna nova, ou reaproveitar AWS
   Private CA (ACM PCA) / AWS Secrets Manager+KMS ja em uso? Introduzir SPIRE Server e uma decisao
   de infra maior que este ADR nao pode tomar sozinho.
2. **Granularidade** — por agente? por agente+tenant (minimo, dado ADR-0004)? por
   agente+tenant+agent_version (a unica opcao que satisfaz literalmente "sob-qual-versao" de
   ADR-0007 sem reemissao cara)?
3. **Chokepoint de verificacao** — na borda de rede (mTLS terminator/sidecar) vs in-process (no
   handler) vs no proprio ponto de audit-emit (`emit_once`/`start_process_idempotent`/construcao de
   `AgentDecisionProvenance`)? Isto determina se um sidecar novo e necessario.
4. **`agent_version` amarra no certificado/token?** — se sim, qual o custo de reemissao a cada bump
   de versao de Agent Definition (qual a frequencia esperada)?
5. **Cadencia de rotacao** — minutos/horas (JWT curto) vs dias/semanas (cert com cron de renovacao)
   vs anual (cert estatico manual)? Em grande parte, funcao da resposta a pergunta 1.

## Recomendacao (NAO VINCULANTE)

Para uma operadora k8s-deployed, com AWS Secrets Manager/KMS+ESO ja disponivel e SEM service mesh
hoje, a **Opcao B (Vault-issued short-lived JWT por agente+tenant, com `agent_version` como claim)**
e a que melhor se encaixa: reaproveita o padrao de injecao JA EXISTENTE
(`card_signer_from_key`/`card_signing_key_from_env`, `assembly.py:65-80`, e o precedente irmao
`gateway.pseudonymizer`) sem exigir um control-plane de rede novo (Opcao A), e cobre
`agent_version` — que a Opcao C estruturalmente nao cobre sem reemissao cara. O chokepoint de
verificacao recomendado e IN-PROCESS, no ponto onde `AgentDecisionProvenance`/`AuditRecord.agent_id`
e hoje preenchido pela string crua (`build_start_audit_record`, `transport.py:514`) — substituindo
"confie na string" por "verifique o claim do JWT".

### Caminho de migracao (interim -> cert-backed, SEM big-bang)

1. **Hoje -> interim:** `AUDIT_AGENT_ID = "operadora-worker"` continua funcionando exatamente como
   esta hoje — nenhuma mudanca e necessaria para a cadeia de auditoria continuar operando.
2. **Quando este ADR (ou sucessora) for `Accepted` E o vault/KMS estiver provisionado (externo,
   bloqueado):** introduzir um provider de identidade de servico (nome ilustrativo:
   `ServiceIdentityProvider`) que resolve um JWT assinado do ambiente/injecao (mesmo padrao de
   `card_signing_key_from_env`), com fallback EXPLICITO e auditavel para a string literal quando o
   provider nao esta configurado (dev/staging sem vault) — nunca cair silenciosamente para uma
   identidade nao verificada em producao.
3. **Ponto de corte:** `AgentDecisionProvenance`/o caminho de `AUDIT_AGENT_ID` ganham um
   campo/flag (`identity_verified: bool`, ilustrativo) preenchido pelo provider — permitindo que um
   gate de producao EXIJA `identity_verified is True` (fail-closed, mesmo padrao ja usado em
   ADR-0031/GAP-LGPD-6 para `identidade_verificada`) sem quebrar ambientes que ainda nao tem o
   vault provisionado, DESDE que esse ambiente seja explicitamente marcado como nao-producao.
4. Nenhum destes passos exige reescrever a cadeia de auditoria (Postgres append-only + `emit_once`,
   ADR-0027) — apenas troca a FONTE do `agent_id` de uma constante para um claim verificado.

## Consequencias

**Positivas (uma vez ratificado):**
- A metade certificado do T-G se torna construivel assim que este ADR for `Accepted` e o vault/KMS
  correspondente for provisionado (dependencia externa, fora do controle desta ADR).
- `AUDIT_AGENT_ID`/`AgentDecisionProvenance` ganham um caminho incremental para nao-repudio real,
  sem exigir uma reescrita da cadeia de auditoria existente (ADR-0007/ADR-0027).
- Fecha o DESIGN-GAP apontado por ADR-0032:67-73 e pela design doc `A2A-dispatcher-card-signing.md`
  §4/§7.1.

**Negativas (aceitas):**
- Nenhuma implementacao acontece aqui — isto e apenas o espaco de decisao; a construcao real fica
  bloqueada ate (a) ratificacao humana e (b) provisionamento externo de vault/KMS/CA (nenhum modulo
  terraform de KMS/PKI existe hoje).
- A recomendacao (Opcao B) e uma posicao arquitetural desta sessao, nao uma decisao — o arquiteto
  de seguranca/infra pode escolher A ou C por razoes que este documento nao pode prever (ex.: um
  mandato corporativo de adotar SPIFFE/SPIRE ja em outro sistema, ou uma exigencia regulatoria por
  certificado estatico auditavel).

## O que isto desbloqueia / relacao com T-F e W1

- **T-F (auditoria de delegacao A2A)** — roda por cima de `emit_once` (W4, per
  `A2A-dispatcher-card-signing.md` §3.2/§5) e e ORTOGONAL a este ADR: T-F audita a delegacao como
  efeito; este ADR endurece QUEM esta fazendo a chamada. Nao ha dependencia de bloqueio entre os
  dois.
- **Metade Agent-Card-assinado do T-G (W1)** — ja construida (`t2.4-a2a-w1-card-signing`, ainda nao
  mergeada), NAO depende deste ADR: `AgentCard`/`CardSigner`/`A2ARegistry` continuam funcionando
  como estao. Este ADR cobre a metade complementar (identidade de PROCESSO/servico, nao identidade
  de CARD/capacidade).
- **Consumo futuro:** uma vez ratificado + vault/KMS provisionado, a metade cert deste T-G PODE
  alimentar o mesmo `signing_payload`/`verifier` gate do W1 (ex.: o processo assina seu proprio
  Agent Card usando a identidade de servico deste ADR, em vez de uma chave HMAC compartilhada
  injetada externamente) — mas isto e uma otimizacao futura, nao um requisito desta ADR.

## Convencao seguida (amends, nao supersede)

Per `docs/adr/README.md:8` ("ADR aceito so muda por novo ADR com `Supersedes`") e o precedente de
ADR-0027 (emenda a clausula Kafka de ADR-0007 via ADR NOVO, sem editar o arquivo de ADR-0007 —
"Amends ADR-0007 (transport clause only; does not supersede it)") e ADR-0032 (emenda a alegacao de
status de ADR-0015 via ADR NOVO, mesma convencao): este ADR EMENDA a clausula `:10` de ADR-0007
("service account + certificado") ELABORANDO o mecanismo de emissao/verificacao que aquela clausula
nunca especificou — sem editar `docs/adr/0007-agent-identity-audit-non-repudiation.md`. Nao
supersede ADR-0007 nem nenhuma outra ADR.

## Supersedes

Amends ADR-0007 (elabora a clausula `:10` "service account + certificado" — mecanismo de emissao e
chokepoint de verificacao, previamente nao especificados). Nao supersede. Nao edita ADR-0015,
ADR-0032 nem nenhuma outra ADR. Complementa (nao substitui) a metade Agent-Card-assinado de T-G ja
especificada em `docs/design/A2A-dispatcher-card-signing.md` e construida em
`t2.4-a2a-w1-card-signing`.
