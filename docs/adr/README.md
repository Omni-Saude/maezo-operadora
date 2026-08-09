# ADRs — Maezo Healthcare Plan

Serie propria deste repo (0001+). O repo hospitalar `Omni-Saude/Maezo` tem serie
independente (001-020) usada apenas como referencia intelectual.

Regras:
1. Toda decisao arquitetural relevante vira ADR ANTES do codigo.
2. ADR aceito so muda por novo ADR com `Supersedes`.
3. Formato: Contexto / Decisao / Consequencias / Supersedes (usar `template.md`).

| ADR | Titulo | Status |
|---|---|---|
| 0001 | CIB Seven como backbone de governanca; LangGraph como runtime de agentes | Accepted |
| 0002 | Estado de agentes em 3 camadas (working/episodica/semantica) | Accepted |
| 0003 | A2A v1.0 para colaboracao; Kafka para fatos | Accepted |
| 0004 | Instancia por tenant + Agent Definitions federadas L0-L3 | Accepted |
| 0005 | HITL como garantia arquitetural | Accepted |
| 0006 | PHI em duas zonas; pseudonimizacao no gateway | Accepted |
| 0007 | Identidade de agente, auditoria e nao-repudio | Accepted |
| 0008 | Niveis de autonomia L0-L3 por acao | Accepted |
| 0009 | Portfolio de modelos com abstracao de provider + eval gates | Accepted |
| 0010 | Observabilidade de agentes | Accepted |
| 0011 | Repo greenfield; hospitalar como referencia; licoes estruturais | Accepted |
| 0012 | DMN como ferramenta deterministica unica | Accepted |
| 0013 | Integracao Tasy via CDC da Plataforma de Dados; simulador como contrato | Accepted |
| 0014 | Plataforma de observabilidade: AMP/AMG gerenciados vs Prometheus/Grafana in-cluster | Accepted |
| 0015 | Runtime de delegacao A2A: Agent Card registry, envelope e anti-loop estrutural | Accepted |
| 0016 | Allowlist de process_key + invariante de pseudonimizacao no ToolRegistry | Accepted |
| 0017 | Enforcement de rede do egress da Zona PHI (CIDRs IP-fixados, fail-closed; FQDN via egress proxy/Cilium e FUTURO, nao entregue) | Accepted |
| 0018 | Padrao estrutural no-denial: replicacao vinculante de 5 partes para todo SP-OP negativa-like | Accepted |
| 0019 | amh-data-platform como lake-of-record; surrogate mpi_id<->fhir_patient_id; snapshot de feature-store | Accepted |
| 0020 | Chain-of-custody como PROJECAO sobre a cadeia de auditoria ADR-0007 (nao fork) | Accepted |
| 0021 | Dependencias in-cluster (CIB Seven, HAPI-FHIR) via StatefulSet | Accepted |
| 0022 | MCP servers registram tools IN-PROCESS no boot; sem Deployments stdio/SSE out-of-process | Accepted |
| 0023 | Politica de merge — main protegido com required status checks + strict up-to-date | Accepted |
| 0024 | Idempotencia DURAVEL dos drivers inbound/resume reusa o padrao Postgres | Accepted |
| 0025 | PEP ↔ Policy Unification — autonomy-matrix YAML loading + single action vocabulary (T1.8) | Accepted |
| 0026 | Worker standardization — adapter (FunctionWorker) sobre subclassing; 16→17 modulos registram no WorkerRegistry com retry/metrics (T1.2; 17o = raw-handler `events.py`, ver amendment note no ADR) | Accepted |
| 0027 | Audit Transport — Postgres-first (amends ADR-0007's Kafka assumption) (T1.10) | Accepted |
| 0028 | Avaliacao de DMN em runtime — engine-side (CIB Seven REST), fail-closed (T1.4) | Accepted |
| 0029 | Poda de audit_chain via checkpoint assinado + re-anchor; BLOQUEADA ate ADR-0020-amend + este ADR ratificarem (T2.8) | Proposed |
| 0030 | Semantica de erro de worker vs boundary catches do BPMN — `WorkerBpmnError` modelado (opt-in por codigo gate-proven), incidente para todo o resto (T3.1) | Accepted |
| 0031 | LGPD DSR identity gate fail-closed em `identidade_verificada is True` + GAP-LGPD-6 raise + #55 R-B request_additional_proof (T2.8) | Proposed |
| 0032 | Corrige a alegacao de "runtime completo" do ADR-0015 (e do donor v1 PR #17, nao do v2); amends ADR-0015, nao supersede (T3.4) | Accepted |
| 0033 | Identidade de servico do agente — emissao/verificacao do certificado/service-account (metade cert de T-G); espaco de decisao + recomendacao nao-vinculante; amends ADR-0007, nao supersede | Proposed |
| 0034 | L2 review sampling intencionalmente fora do v2 (descope ratificado, #24); autonomia enforce-ada estruturalmente (ADR-0018), nao por PEP em runtime; amends ADR-0008 + ADR-0025, nao supersede | Accepted |
| 0035 | PHI pseudonymizer — keyed HMAC-SHA256 (fecha SHA-256 sem chave reversivel); prod fail-closed em `PHI_HMAC_KEY` ausente, dev determinista por-tenant nao-secreto; cutover limpo (sem store persistido); amends ADR-0006, nao supersede (t6-hmac) | Accepted |
| 0036 | Identidade conversa/thread/business-key com HMAC keyed (fecha reversibilidade residual do ADR-0035 no `hash_phone`/`conversation_id` que o T4b passou a persistir + no Cockpit); trava do thread-id exige marcador keyed `hk1_` (nao mais so "nao-numerico"); fold-in `LogScrubber` fail-closed; cutover limpo; estende ADR-0035 + ADR-0006, nao supersede (t9-phi-conversation-id) | Accepted |
| 0037 | Fronteira de compatibilidade AMH — contratos canonicos AMH-owned (work-items/consent/outcomes v1 + APIs subject-context/population-features + contract manifest; pin imutavel `config/integrations/amh/contracts.lock.json`); supersede PARCIAL do ADR-0013 (clausulas 1, 2 [wire dev-JSON], 3, 4, 5; principios consume-not-duplicate e TASY-write-DROP preservados e re-ancorados); amends ADR-0034 (re-introducao do chokepoint por chamada, condicionada a aceitacao + MZO-040); RATIFICADO pelo owner 2026-08-03 (DL-0040) — metade Maezo do XRG-1 FECHADA; XRG-1 completo aguarda AMH-000 (MZO-000) | Accepted |
| 0038 | Emenda a proibicao 6 do ADR-0037 — reconciliacao da FORMA das chaves CIB com a realidade implantada (business keys hifen-primeiro `{PREFIXO}-{tenant}-{ids}`; process keys `SP-OP-<DOMAIN>-<NNN>`) + correcao da auto-inconsistencia XRD-08 (`maezo-payer/*`) vs proibicao 6 (`maezo-payer-`); DUAS opcoes com recomendacao NAO VINCULANTE (1 = grandfathering da forma viva, semantica preservada; 2 = manter forma congelada + janela dual-read com alias legado); amends ADR-0037, nao supersede, e NAO emenda a proibicao 5 — a perna (c) de DL-0043 (PHI/ID cru em keys) permanece ABERTA. Redigido em DL-0044; **escolha da opcao PENDENTE DO DONO** | Proposed |

Texto integral e racional estendido: `agent-platform-adrs.md` no projeto de arquitetura
(serie AP-001..AP-012 mapeia 1:1 para 0001..0012 desta serie).
