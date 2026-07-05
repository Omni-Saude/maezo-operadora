# CONTRIBUTING — Guia de Desenvolvimento

## Princípios (lições do repo hospitalar — ADR-0011)

1. **Árvore única.** Todo código em `src/maezo/`. Sem cópias, sem `.archive/`, sem `_old`, sem `_bkp` no main — histórico é papel do git.
2. **ADR antes de código.** Decisão arquitetural sem ADR não entra. Use `docs/adr/template.md`.
3. **Sem mock de engine em integração.** Testes `integration` rodam contra CIB Seven/Postgres reais. O repo hospitalar teve 508 testes "passando" enquanto 91 não-conformidades BPMN dormiam.
4. **Artefato é código.** BPMN, DMN, `agent.yaml` e policies passam por `make validate-artifacts` (blocker de CI).
5. **Nenhum SDK de LLM fora de `runtime/inference.py`. Nenhuma credencial fora do gateway.**

## Como adicionar um agente

1. Copie `src/maezo/agents/_template/` → `src/maezo/agents/<id>/`.
2. Preencha `agent.yaml`: zona de segurança (ADR-0006), allowlist de tools, KPIs, gestor humano, rota de escalonamento. **Todo agente tem gestor humano e rota de escalonamento — sem exceção.**
3. Implemente `graph.py:build` — nós curtos e idempotentes (checkpoint é entre nós).
4. Adicione ações novas à matriz `policies/autonomy/` via PR separado (revisão de compliance obrigatória — CODEOWNERS).
5. Crie golden dataset em `tests/evals/golden/<id>/` ANTES do go-live (mínimo definido por fase).
6. Dashboards: a "ficha de funcionário" do agente (KPIs do `agent.yaml`) é parte da entrega.

## Como adicionar uma tool (MCP server)

1. Novo pacote em `src/maezo/tools/mcp_<nome>/`. Clientes externos maduros podem ser **portados** do repo hospitalar (`shared/integrations/`) — copiar, adaptar, re-testar; nunca importar entre repos.
2. Toda tool declara: ação correspondente na matriz de autonomia, escopo de PHI dos argumentos/retorno (para o pseudonimizador) e schema tipado.
3. Registre no Tool Gateway. Agente nenhum chama a tool sem passar pelo PEP.

## Como adicionar um processo SP-OP

1. Justifique o gatilho regulatório no PR (SLA legal, HITL mandatório, auditoria, multi-ator legal). Sem gatilho → é jornada de agente (AGJ), não BPMN.
2. Modele em `src/maezo/processes/bpmn/` seguindo `SP-OP-{AREA}-{NNN}_{Titulo}.bpmn`; tópicos `{dominio}.{contexto}.{acao}`.
3. Ações L0/L1 exigem User Task com candidate group humano + timer + escalation.
4. Atualize `docs/processes/catalog.md` e `config/topic_registry.yaml`.

## Convenções

- Python 3.12, `ruff` + `mypy --strict`. Async por padrão.
- Commits: conventional commits (`feat(helena): ...`, `adr: ...`).
- PR pequeno > PR épico. Um agente/tool/processo por PR.
- Datas/prazos regulatórios: sempre em DMN ou BPMN timer — nunca hard-coded em Python.

## Fluxo de release

`main` protegido → CI completo (lint, type, unit, artifact-validation, integration, evals) → tag → imagem única `maezo-agent` + manifests por tenant. Promoção de prompt/modelo segue o mesmo fluxo de release de código (eval gate, ADR-0009).
