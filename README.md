<h1 align="center">🏥 MAEZO Operadora</h1>

<p align="center">
  <strong>Plataforma agents-first para operadoras de saúde — greenfield v2</strong><br/>
  <em>Reconstrução a partir da especificação canônica em docs/. Agentes de IA nomeados operam a operadora; BPMN/DMN (CIB Seven) é a espinha dorsal de governança — auditoria, SLAs regulatórios (ANS) e escalonamento humano garantidos por arquitetura, não por prompt.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/status-greenfield--day0-lightgrey" alt="Greenfield Day 0">
  <img src="https://img.shields.io/badge/spec-205K_palavras-success" alt="205K palavras de especificação">
  <img src="https://img.shields.io/badge/ADRs-24-informational" alt="24 ADRs">
  <img src="https://img.shields.io/badge/contratos-16_processos-blueviolet" alt="16 contratos SP-OP">
  <img src="https://img.shields.io/badge/regras-~1000-orange" alt="~1000 regras de negócio">
  <img src="https://img.shields.io/badge/licença-Proprietary-lightgrey" alt="Licença Proprietary">
</p>

---

## O que é este repositório

Este é o **greenfield v2** da plataforma Maezo. Diferente do repo `Maezo-Healthcare-Plan` (que foi um MVP construído por AI swarms e atingiu 30-40% de cobertura funcional dos contratos), este repo começa com a **especificação completa** como guia canônico e reconstrói a plataforma do zero com qualidade de produção.

### O que trouxemos do repo anterior

| Trouxemos | Por quê |
|-----------|---------|
| `docs/` integral (~205K palavras) | **A especificação canônica.** 16 contratos SP-OP, 15 test-specs, 24 ADRs, 34 DLs, 8 runbooks, 2 auditorias, relatórios |
| `spec/processes/bpmn/` (16 .bpmn) | Base estrutural 85% correta — acelera a regeneração |
| `spec/processes/dmn/` (53 .dmn) | Estrutura de inputs/outputs alinhada com contratos |
| `spec/policies/autonomy/` | Matriz L0-L3 — dado regulatório, não código |
| `spec/agents/*/agent.yaml` | Contratos de 10 agentes — escopo e permissões |
| `deploy/` (Dockerfile, Helm, Terraform) | Infraestrutura como código — base comprovada |
| `.github/` (CI/CD, Dependabot) | Pipeline de qualidade — union-green, security scanning |
| `Makefile`, `pyproject.toml`, `.env.example` | Tooling de desenvolvimento |
| `README.md`, `PROJECT.md`, `AGENTS.md`, `CONTRIBUTING.md` | Documentação de projeto |

### O que NÃO trouxemos (será reconstruído)

`src/maezo/runtime/`, `gateway/`, `a2a/`, `tools/mcp_*/`, `tools/workers/`, `platform/`, `agents/*/graph.py`, `agents/*/prompts/`, `tests/`. Todo o código de implementação será reconstruído contra a especificação v2.

### O que está em `docs/handoffs/` (histórico)

Os arquivos em `docs/handoffs/` são artefatos do programa predeploy do repo anterior. Foram mantidos como **referência histórica** — documentam o estado final do MVP (21 PRs merged, 82 findings, decisões do usuário). Não são parte ativa da especificação v2.

---

## Princípios inegociáveis (do v1, mantidos no v2)

1. **Nenhuma decisão clínica ou negativa de cobertura por agente.** Negativa = médico auditor, via User Task BPMN.
2. **LGPD por construção.** PHI pseudonimizado; dado integral só em infra aprovada com egress restrito.
3. **Multi-tenant absoluto.** Instância por cliente; zero contaminação.
4. **Toda decisão tem trilha.** Quem, com base em quê, aprovado por quem.
5. **Regra determinística mora em DMN, não no LLM.**

---

## Início rápido

```bash
# Pré-requisitos: Python 3.12, Docker, make
make setup             # pip install -e ".[dev]"
make dev-stack         # postgres+pgvector, cibseven, hapi-fhir, kafka
cp .env.example .env   # preencher chaves
```

---

## Documentação

| Documento | Conteúdo |
|-----------|----------|
| [docs/architecture/overview.md](docs/architecture/overview.md) | Visão arquitetural completa |
| [docs/adr/](docs/adr/) | 24 ADRs (0001–0024) |
| [docs/processes/catalog.md](docs/processes/catalog.md) | Catálogo de 15 processos SP-OP |
| [docs/processes/contracts/](docs/processes/contracts/) | 16 contratos — ~1000 regras de negócio |
| [docs/processes/test-specs/](docs/processes/test-specs/) | 15 especificações de teste |
| [docs/decisions-log.md](docs/decisions-log.md) | 34 decisões operacionais |
| [docs/runbooks/](docs/runbooks/) | 8 runbooks operacionais |
| [AGENTS.md](AGENTS.md) | Guia para desenvolvimento assistido por IA |
| [PLANS.md](PLANS.md) | Plano de implementação greenfield |

---

## Licença

**Proprietary.** Uso interno Americas Health (AMH); sem licença de redistribuição.
