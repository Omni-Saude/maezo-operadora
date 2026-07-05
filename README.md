<h1 align="center">🏥 MAEZO Operadora</h1>

<p align="center">
  <strong>Plataforma agents-first para operadoras de saúde — v1.0.0 production-ready</strong><br/>
  <em>Agentes de IA nomeados operam a operadora; BPMN/DMN (CIB Seven) é a espinha dorsal de governança — auditoria, SLAs regulatórios (ANS) e escalonamento humano garantidos por arquitetura, não por prompt.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/version-1.0.0-success" alt="v1.0.0">
  <img src="https://img.shields.io/badge/status-production--ready-brightgreen" alt="Production Ready">
  <img src="https://img.shields.io/badge/tests-553_passing-brightgreen" alt="553 tests passing">
  <img src="https://img.shields.io/badge/coverage-10K_LOC-blue" alt="~10K LOC">
  <img src="https://img.shields.io/badge/lint-0_issues-brightgreen" alt="Lint: 0 issues">
  <img src="https://img.shields.io/badge/mypy-strict_clean-brightgreen" alt="Mypy: strict clean">
  <img src="https://img.shields.io/badge/milestones-15/15_complete-brightgreen" alt="15/15 milestones">
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
| Testes | **553** | Unitários cobrindo workers, gateway, agents, platform, tools |
| Código fonte | ~10K LOC | 62 arquivos Python em `src/maezo/` |
| Documentação | ~205K palavras | 16 contratos SP-OP, 24 ADRs, 34 DLs, 8 runbooks |

### Princípios inegociáveis

1. **Nenhuma decisão clínica ou negativa de cobertura por agente.** Negativa = médico auditor, via User Task BPMN.
2. **LGPD por construção.** PHI pseudonimizado; dado integral só em infra aprovada com egress restrito.
3. **Multi-tenant absoluto.** Instância por cliente; zero contaminação.
4. **Toda decisão tem trilha.** Quem, com base em quê, aprovado por quem.
5. **Regra determinística mora em DMN, não no LLM.**

---

## Métricas reais (v1.0.0)

```
make test   → 553 passed, 1 warning in 2.45s
make lint   → All checks passed! (121 files)
make type   → Success: no issues found in 62 source files (mypy --strict)
```

| Métrica | Valor |
|---------|-------|
| Testes unitários | 553 |
| Arquivos fonte (`src/`) | 62 (.py) |
| Arquivos de teste (`tests/`) | 59 (.py) |
| Linhas de código (src) | ~10K |
| Workers implementados | 16 (15 processos + registry) |
| MCP servers | 6 |
| Agentes AI | 10 (2 com graph.py completo) |
| ADRs | 24 (todos Accepted) |
| DLs (Decisions Log) | 34 |
| Contratos SP-OP | 16 |
| Runbooks | 8 |

---

## Início rápido

```bash
# Pré-requisitos: Python 3.12, Docker, make
make setup             # uv sync --extra dev
make dev-stack         # postgres+pgvector, cibseven, hapi-fhir, kafka
cp .env.example .env   # preencher chaves
make test              # 553 testes unitários
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
| **CI** (`ci.yml`) | Active | lint/type/unit (553 tests), terraform validate, helm lint, integration (real engine), security scanning |
| **CD** (`cd.yml`) | Ready | Build & push ECR, deploy staging (two-phase), smoke tests, production promotion (manual approval) |
| **Security** (`security.yml`) | Active | CodeQL (Python), gitleaks (secrets), dependency review |

---

## Documentação

| Documento | Conteúdo |
|-----------|----------|
| [docs/architecture/overview.md](docs/architecture/overview.md) | Visão arquitetural completa |
| [docs/adr/](docs/adr/) | 24 ADRs (0001–0024) |
| [docs/processes/catalog.md](docs/processes/catalog.md) | Catálogo de 15 processos SP-OP |
| [docs/processes/contracts/](docs/processes/contracts/) | 16 contratos — ~1000 regras de negócio |
| [docs/decisions-log.md](docs/decisions-log.md) | 34 decisões operacionais |
| [docs/runbooks/](docs/runbooks/) | 8 runbooks operacionais |
| [AGENTS.md](AGENTS.md) | Guia para desenvolvimento assistido por IA |
| [PLANS.md](PLANS.md) | Plano de implementação greenfield |
| [PROJECT.md](PROJECT.md) | Arquitetura em 30s |

---

## Licença

**Proprietary.** Uso interno Americas Health (AMH); sem licença de redistribuição.
