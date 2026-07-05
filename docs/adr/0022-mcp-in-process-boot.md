# ADR-0022: MCP servers registram tools IN-PROCESS no boot — sem Deployments stdio/SSE out-of-process

**Status:** Accepted · **Data:** 2026-06-14 · **Area:** Orquestracao / Runtime

## Contexto

A plataforma expoe cinco "MCP servers" como a superficie de tools que o grafo de cada agente dirige
(ADR-0001): `mcp_dmn` (DMN deterministica — ADR-0012), `mcp_cibseven` (governanca de processos),
`mcp_fhir` (leitura de PHI R4 — ADR-0006), `mcp_memory` (memoria episodica/semantica — ADR-0002) e
`mcp_whatsapp` (outbound, WABA BLOCKED). "MCP server" e aqui um **contrato de tools** — cada classe
expoe `register_tools(registry)` que registra `ToolDefinition(name, action, phi_fields, handler)` no
`ToolRegistry`, a UNICA fronteira PEP+auditoria+scrub-PHI (ADR-0016). O Model Context Protocol admite
duas formas de habitar esse contrato:

1. **Out-of-process** — cada MCP server e um processo separado, falado por um transporte de protocolo
   (stdio ou SSE/HTTP), e o runtime do agente e um *cliente* MCP que descobre tools por handshake.
2. **In-process** — os MCP servers sao objetos Python construidos no proprio processo do agente-runtime
   e auto-registram suas tools no `ToolRegistry` durante a bring-up, sem nenhum transporte de protocolo
   entre o agente e o server.

A decisao precisava ser ratificada porque o codigo ja a tomou **de facto** nas waves de runtime-wiring
(W-R0/W-R1), e deixar a decisao implicita arriscava que alguem (re)introduzisse framing out-of-process
("servers" stdio/SSE, Deployments dedicados, sidecars MCP) por inercia de convencao MCP, fraturando a
fronteira unica de PEP+auditoria.

Tres fatos verificados sustentam a ratificacao (estado em `src/`, junho/2026):

- **`src/maezo/runtime/tool_wiring.py::build_tool_invoker`** constroi os cinco servers no processo e
  chama `server.register_tools(registry)` para cada um, contra UM `ToolRegistry(pep, audit_log,
  phi_gateway=...)`. Os "transports" injetados (`HapiFhirTransport`, `CibSevenHttpTransport`,
  `CibSevenDmnTransport`, `WabaCloudTransport`) sao **clientes HTTP para os backends upstream reais**
  (HAPI FHIR, CIB Seven REST, WABA Cloud) — NAO sao transportes de protocolo MCP entre agente e server.
- **`src/maezo/runtime/agent_runtime/service.py`** (bring-up do agente-runtime, passo R4/"KEYSTONE")
  invoca `build_tool_invoker(...)` e injeta o `RegistryToolInvoker` resultante via
  `build_agent(tool_invoker=...)`. Assim o grafo le `config["tools"].call(tool, action, payload)` e
  cada call atravessa o MESMO `pep`+`audit_log` do tenant — registro in-process e a costura viva.
- **Nenhum framing out-of-process existe** em `src/maezo/tools/`: zero `stdio_server`/`SseServerTransport`/
  `FastMCP`/`mcp.server`, zero loops `if __name__ == "__main__"`/`asyncio.run(...serve...)`. O scaffolding
  de "server framing" simplesmente nunca foi construido — nao ha codigo morto a remover.

## Decisao

1. **Os MCP servers registram suas tools IN-PROCESS no boot — a costura escolhida e
   `server.register_tools(registry)` em `build_tool_invoker`, chamado pelo `agent_runtime/service.py`
   na bring-up.** Cada agente roda no processo do agente-runtime com os cinco servers construidos e
   auto-registrados contra UM `ToolRegistry` (a fronteira PEP+auditoria+scrub-PHI, ADR-0016). Nao ha
   handshake MCP, descoberta de tools por protocolo, nem cliente MCP no caminho.

2. **NAO construimos Deployments MCP out-of-process (stdio/SSE).** Nao ha processo MCP separado por
   server, nem sidecar, nem transporte de protocolo entre o agente e os servers. "MCP server" e um
   contrato de tools auto-registrado, nao um processo de rede.

3. **DoD NEGATIVO (estrutural):** **NAO existe nenhum `deployment-mcp-*.yaml`** sob `deploy/`. Os unicos
   Deployments do chart `deploy/helm/maezo-tenant` sao `deployment-agent-runtime.yaml`,
   `deployment-gateway.yaml`, `deployment-fhir-sync.yaml` e `deployment-webhook-receiver.yaml`. Um
   `deployment-mcp-*.yaml` (ou um `grep -r 'mcp-server' deploy/` com acerto) e uma **regressao desta
   ADR** e deve ser rejeitado no review. Os MCP servers nao tem footprint de Deployment proprio porque
   vivem no processo do agente-runtime.

4. **`register_tools` e load-bearing — intocavel.** O metodo `register_tools(registry)` de cada server e
   a costura que R4/R10 dependem; esta ADR o RATIFICA, nao o altera. Remover ou renomear `register_tools`
   quebra `build_tool_invoker` e a injecao de tools do grafo.

5. **Backends upstream continuam out-of-process — via clientes HTTP, nao via MCP.** O agente fala HAPI
   FHIR, CIB Seven e WABA por HTTP (os `*Transport` injetados). A decisao "in-process" e sobre a
   *fronteira MCP/tool* (registro de tools), nao sobre os backends de negocio — esses sempre foram, e
   continuam, servicos remotos.

## Consequencias

**Positivas:**
- **Uma so fronteira PEP+auditoria+scrub-PHI.** Com registro in-process contra um `ToolRegistry`, todo
  tool-call do agente atravessa o mesmo `pep`+`audit_log` do tenant (ADR-0016/ADR-0007). Um transporte
  MCP out-of-process abriria a possibilidade de um caminho de tool que contorna a fronteira — exatamente
  o risco que a costura unica fecha por construcao.
- **Sem superficie de protocolo a proteger.** Nao ha endpoint stdio/SSE de MCP a autenticar, isolar por
  rede ou hardenizar; a unica superficie de rede do agente sao os clientes HTTP para os backends
  (governados por ADR-0017 — egress da Zona PHI).
- **Menos footprint operacional.** Zero Deployments/Pods MCP por tenant; o agente-runtime ja carrega os
  servers. O DoD negativo (`grep` sem acerto) e barato de verificar em CI/review.
- **Provenancia de auditoria coesa.** O `RegistryToolInvoker` carrega `agent_id/agent_version/tenant/
  model_id/prompt_version` por construcao (mesma fonte de verdade, sem rede), porque o registro acontece
  no processo do agente com a Agent Definition ja resolvida.

**Negativas (aceitas):**
- **Acoplamento de processo.** Os MCP servers compartilham o ciclo de vida do agente-runtime; nao podem
  ser escalados/reiniciados de forma independente. Aceitavel: sao adaptadores finos sobre backends HTTP,
  nao cargas pesadas — o que escala/reinicia de forma independente sao os backends upstream.
- **Sem interop MCP cross-language por enquanto.** Um consumidor MCP externo (outra linguagem/runtime)
  nao pode falar com estes servers por stdio/SSE; eles so existem dentro do agente-runtime Python. Se um
  dia houver necessidade de expor um server por transporte de protocolo, sera uma NOVA ADR que
  supersede(parcialmente) esta — adicionando um transporte SEM remover o registro in-process (a fronteira
  PEP+auditoria continua a unica verdade).
- **A decisao vive no codigo, nao num manifesto de deploy.** Quem procurar "onde estao os MCP servers"
  no `deploy/` nao acha um Deployment — acha-os no `agent_runtime/service.py`. Esta ADR e o ponteiro.

## Supersedes

— (RATIFICA a costura ja viva em `runtime/tool_wiring.py` + `runtime/agent_runtime/service.py`; reforca a
fronteira unica de ADR-0016 (ToolRegistry = PEP+auditoria+scrub-PHI) e ADR-0007 (provenancia); os
backends upstream out-of-process continuam governados por ADR-0017 (egress Zona PHI). Numero 0022: 0021
esta reservado por uma PR Track-S em voo; usamos o proximo livre para evitar colisao no merge.)
