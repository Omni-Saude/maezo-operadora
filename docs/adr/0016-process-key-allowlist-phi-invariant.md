# ADR-0016: Allowlist de process_key + invariante de pseudonimizacao no ToolRegistry

**Status:** Accepted
**Data:** 2026-06-13
**Area:** Seguranca

## Contexto

Phase 0 identificou dois riscos de segurança catalogados como "Phase 1 SEC flags" no
`phase0-report.md`:

1. **process_key irrestrita**: `start_compliance_process` (MCP CIB Seven) aceitava qualquer
   `process_key` arbitrária. Um agente comprometido ou um prompt-injection poderia iniciar um
   processo de governança desconhecido/forjado no engine. ADR-0005/0008 restringem _ações_ por
   nível de autonomia, mas não restringem os _parâmetros_ de uma ação já permitida.

2. **PHI cru saindo do ToolRegistry para agentes de Zona Geral**: a ADR-0006 estabelece que
   agentes da Zona Geral não recebem PHI cru, mas a garantia dependia de disciplina dos
   implementadores de cada tool, sem enforcement estrutural no caminho de saída.

A PR #19 implementou ambos os controles em `src/maezo/tools/`. Este ADR formaliza as decisões
de arquitetura.

**Tensões resolvidas:**

- **PEP + allowlist de parâmetros, ou só PEP?** O PEP avalia a _ação_ (`start_compliance_process`)
  contra a matriz de autonomia. Avaliar _parâmetros_ dentro do PEP acoplaria a lógica de
  negócio de `process_key` à infraestrutura de autonomia. A decisão é manter a separação:
  PEP avalia a ação; a allowlist (uma classe dedicada, `ProcessAllowlist`) valida o parâmetro
  dentro do handler da tool. O `ToolRegistry` audita a recusa estrutural.

- **Allowlist config-driven ou congelada?** Config-driven flexível demais: um overlay de tenant
  poderia sancionar uma key inexistente. Congelada demais: impede tenant de adicionar processes
  próprios futuramente. Decisão: conjunto default congelado em código (`DEFAULT_ALLOWED_PROCESS_KEYS`)
  que todo tenant herda; config YAML por tenant só pode _adicionar_ dentro do universo
  `KNOWN_PROCESS_KEYS`. Remover um item do default é impossível por config — exige PR.

## Decisao

### Allowlist de process_key (`src/maezo/tools/process_allowlist.py`)

- `KNOWN_PROCESS_KEYS` (frozenset em código): universo de keys que a plataforma conhece na
  Phase 0-1 (`SP-OP-ESCALATION-001`, `SP-OP-LGPD-DSR-001`, `SP-OP-AUTH-001`).
- `DEFAULT_ALLOWED_PROCESS_KEYS` (frozenset em código): subconjunto que todo tenant pode
  iniciar sem config extra. Igual a `KNOWN_PROCESS_KEYS` na Phase 0-1.
- `ProcessAllowlist.ensure_allowed(process_key)`: fail-closed — levanta
  `ProcessKeyNotAllowedError` (subclasse de `PermissionError`) se a key não satisfazer:
  (a) formato `SP-OP-<DOMINIO>-<NNN>` (regex em código, defesa contra homoglifos/encoding), e
  (b) pertencer ao conjunto sancionado do tenant.
- Config YAML opcional por tenant (`load_allowlist`) pode adicionar keys dentro de
  `KNOWN_PROCESS_KEYS`; tentar adicionar key desconhecida levanta `ProcessAllowlistConfigError`
  imediatamente na carga.
- O handler `start_compliance_process` (MCP CIB Seven) chama `ensure_allowed` antes de qualquer
  chamada ao engine. O `ToolRegistry` (`src/maezo/tools/registry.py`) captura `PermissionError`
  do handler, audita a recusa como `TOOL:deny:<tipo>:<mensagem>` (ADR-0007), e re-levanta —
  garantindo trilha de auditoria mesmo quando o PEP deu ALLOW na ação.

### Invariante de pseudonimizacao no ToolRegistry (`src/maezo/tools/registry.py`)

- O `ToolRegistry` é o **único caminho de saída** do resultado de qualquer tool para o agente/LLM.
- `ToolDefinition.phi_fields` declara quais chaves do resultado contêm PHI.
- `ToolRegistry.invoke(..., consumer_zone=...)`: se `phi_fields` não-vazio e `consumer_zone`
  for `"general"` (default fail-closed), o resultado é pseudonimizado via `PhiZoneGateway`
  (ADR-0006) antes de retornar ao chamador.
- Quando `PhiZoneGateway` não é injetado e a tool declara `phi_fields`, o registry **recusa-se
  a entregar** o resultado a um consumidor `general` — nunca passa PHI cru silenciosamente.
- Isso transforma ADR-0006 de um contrato arquitetural em uma invariante estrutural: nenhum
  implementador de tool pode "esquecer" de pseudonimizar — o registry aplica o controle.

## Consequencias

**Positivas:**
- Prompt-injection que tente forçar `start_compliance_process` com key arbitrária é bloqueado
  estruturalmente, mesmo se o PEP der ALLOW na ação.
- A invariante de PHI no `ToolRegistry` fecha o gap entre o contrato ADR-0006 e a implementação:
  a propriedade é verificável por código, não por disciplina.
- A auditoria de recusas estruturais da tool (além das recusas do PEP) garante trilha completa
  de toda tentativa negada (ADR-0007).
- Adicionar um novo processo de governança exige PR (alteração de `KNOWN_PROCESS_KEYS`) —
  revisão humana obrigatória.

**Negativas (aceitas):**
- Heurística de formato `SP-OP-*` acopla a allowlist ao esquema de nomenclatura atual; uma
  mudança de convenção exige atualização do regex em código.
- `DEFAULT_ALLOWED_PROCESS_KEYS == KNOWN_PROCESS_KEYS` na Phase 0-1 torna a distinção
  puramente latente; ela ganha valor quando novos processes forem adicionados ao universo mas
  não ao default de todos os tenants.

## Supersedes
ADR-0006 (complementa — não supersede; ADR-0006 estabelece o modelo de zonas PHI; este ADR
formaliza onde a invariante é enforced no caminho de saída do ToolRegistry).
