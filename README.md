<h1 align="center">🏥 MAEZO Operadora</h1>

<p align="center">
  <strong>Plataforma agents-first para operadoras de saúde — v0.2.0 alpha-dev</strong><br/>
  <em>Agentes de IA nomeados operam a operadora; BPMN/DMN (CIB Seven) é a espinha dorsal de governança — auditoria, SLAs regulatórios (ANS) e escalonamento humano garantidos por arquitetura, não por prompt.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/version-0.2.0-blue" alt="v0.2.0">
  <img src="https://img.shields.io/badge/status-alpha--dev-yellow" alt="Alpha Dev">
  <a href="https://github.com/Omni-Saude/maezo-operadora/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/tests-passing_(CI)-brightgreen" alt="tests passing (CI)"></a>
  <img src="https://img.shields.io/badge/coverage-10K_LOC-blue" alt="~10K LOC">
  <img src="https://img.shields.io/badge/lint-0_issues-brightgreen" alt="Lint: 0 issues">
  <img src="https://img.shields.io/badge/mypy-strict_clean-brightgreen" alt="Mypy: strict clean">
  <img src="https://img.shields.io/badge/milestones-15/15_self--certified-orange" alt="15/15 self-certified (unverified)">
  <img src="https://img.shields.io/badge/licença-Proprietary-lightgrey" alt="Licença Proprietary">
</p>

---

## O que é este repositório

Plataforma **agents-first** completa para operadoras de saúde (payer). Reconstrói do zero o MVP `Maezo-Healthcare-Plan` (v1) a partir da especificação canônica em `docs/`, entregando qualidade de produção.

| Componente | Quantidade | Descrição |
|-----------|-----------|-----------|
| Workers BPMN | 16 | External tasks para 15 processos SP-OP |
| Agentes AI | 10 | Helena, Rafael, Marina, Gustavo, Lucas, Carolina, Fernando, Valentina, Beatriz, André |
| MCP Servers | 6 | CIB Seven, DMN, FHIR, WhatsApp, Memory, Process Allowlist |
| Testes | CI workflow | Unitários cobrindo workers, gateway, agents, platform, tools (veja CI para count atual) |
| Código fonte | ~10K LOC | Arquivos Python em `src/maezo/` (veja a árvore para count atual) |
| Documentação | ~205K palavras | 16 contratos SP-OP, 38 ADRs (34 Accepted + 4 Proposed), 45 DLs, 8 runbooks |

### Princípios inegociáveis

1. **Nenhuma decisão clínica ou negativa de cobertura por agente.** Negativa = médico auditor, via User Task BPMN.
2. **LGPD por construção.** PHI pseudonimizado; dado integral só em infra aprovada com egress restrito.
3. **Multi-tenant absoluto.** Instância por cliente; zero contaminação.
4. **Toda decisão tem trilha.** Quem, com base em quê, aprovado por quem.
5. **Regra determinística mora em DMN, não no LLM.**

---

## Métricas reais (v0.2.0-alpha)

```
make test   → all passed (veja CI workflow para count atual)
make lint   → All checks passed! (121 files)
make type   → Success: no issues found (mypy --strict; veja CI para output atual)
```

| Métrica | Valor |
|---------|-------|
| Testes unitários | CI workflow (dynamically updated) |
| Arquivos fonte (`src/`) | veja a árvore `src/` (count estático removido — rot) |
| Arquivos de teste (`tests/`) | veja a árvore `tests/` (count estático removido — rot) |
| Linhas de código (src) | ~10K |
| Workers implementados | 16 (15 processos + registry) |
| MCP servers | 6 |
| Agentes AI | 10 (2 com graph.py completo) |
| ADRs | 38 (34 Accepted, 4 Proposed) |
| DLs (Decisions Log) | 45 |
| Contratos SP-OP | 16 |
| Runbooks | 8 |

---

## Início rápido

```bash
# Pré-requisitos: Python 3.12, Docker, make
make setup             # uv sync --extra dev
make dev-stack         # postgres+pgvector, cibseven, hapi-fhir, kafka
cp .env.example .env   # preencher chaves
make test              # testes unitários (veja CI para count)
```

---

## Estrutura do projeto

```
src/maezo/
├── agents/          # 10 agentes (Helena, Rafael, Marina, ...)
├── a2a/             # Agent2Agent registry, anti-loop, card
├── gateway/         # PEP, pseudonymizer, audit chain, credential vault, custody
├── platform/        # Validation CLI, topic registry, erasure, retention, observability
├── runtime/         # LangGraph harness, checkpointer, inference
└── tools/
    ├── workers/     # 16 workers BPMN (auth, contas, fraude, nip, ...)
    ├── mcp_cibseven/
    ├── mcp_dmn/
    ├── mcp_fhir/
    ├── mcp_whatsapp/
    └── mcp_memory/
```

---

## Milestones concluídos

| Milestone | Status | Descrição |
|-----------|--------|-----------|
| M0 — Foundation | ✅ | Scaffold, CI/CD, dev-stack, tooling |
| M1 — ADR Ratification | ✅ | 24 ADRs promovidos para Accepted |
| M2 — BPMN/DMN | ✅ | 16 BPMN + 53 DMN regenerados |
| M3 — Core Runtime | ✅ | LangGraph harness, CIB Seven MCP, Alembic |
| M4 — Gateway & Security | ✅ | PEP, pseudonymizer, audit chain, credential vault, custody |
| M5 — Agent Framework | ✅ | MCP servers, A2A, agent contracts |
| M6 — Foundation Processes | ✅ | ESCALATION, AUTH, LGPD-DSR + Helena, Rafael |
| M7 — Core Compliance | ✅ | CONTAS, RECURSO, NIP, ANS-SUBMIT, CANCEL, REEMBOLSO |
| M8 — Advanced Processes | ✅ | INADIMPLENCIA, CRED, ADEQUACAO, FRAUDE, PROGRAMA, PAGTO |
| M9 — Cross-Process | ✅ | Handoffs, notification bridge, topic registry, validation CLI |
| M10 — Multi-Tenancy | ✅ | Tenant isolation, Helm chart, Terraform modules |
| M11 — Observability | ✅ | Prometheus metrics, OTEL tracing, dashboards |
| M12 — PHI & LGPD | ✅ | Egress enforcement, log scrubbing, erasure, retention |
| M13 — Production Hardening | ✅ | CI review, version bump, security scanning |
| M14 — Docs & Handoff | ✅ | README, runbooks, handoff artifacts |

---

## CI/CD

| Pipeline | Status | Jobs |
|----------|--------|------|
| **CI** (`ci.yml`) | Active | lint/type/unit (see CI workflow for count), terraform validate, helm lint, security scanning |
| **CD** (`cd.yml`) | Ready | Build & push ECR, deploy staging (two-phase), smoke tests, production promotion (manual approval) |
| **Security** (`security.yml`) | Partial | gitleaks (secrets) Active · CodeQL Disabled (Advanced Security unavailable) · dependency review Disabled (Advanced Security unavailable) |

---

## Documentação

| Documento | Conteúdo |
|-----------|----------|
| [docs/architecture/overview.md](docs/architecture/overview.md) | Visão arquitetural completa |
| [docs/adr/](docs/adr/) | 38 ADRs (0001–0038; 34 Accepted, 4 Proposed) |
| [docs/processes/catalog.md](docs/processes/catalog.md) | Catálogo de 15 processos SP-OP |
| [docs/processes/contracts/](docs/processes/contracts/) | 16 contratos — ~1000 regras de negócio |
| [docs/decisions-log.md](docs/decisions-log.md) | 45 decisões operacionais |
| [docs/runbooks/](docs/runbooks/) | 8 runbooks operacionais |
| [AGENTS.md](AGENTS.md) | Guia para desenvolvimento assistido por IA |
| [PLANS.md](PLANS.md) | Plano de implementação greenfield |
| [PROJECT.md](PROJECT.md) | Arquitetura em 30s |

---

## Licença

**Proprietary.** Uso interno Americas Health (AMH); sem licença de redistribuição.
