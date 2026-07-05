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

Texto integral e racional estendido: `agent-platform-adrs.md` no projeto de arquitetura
(serie AP-001..AP-012 mapeia 1:1 para 0001..0012 desta serie).
