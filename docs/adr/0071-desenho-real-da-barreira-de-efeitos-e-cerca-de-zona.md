# ADR-0071: Desenho real da barreira de efeitos (fábrica de seams + effect_pep) e cerca de zona na construção

**Status:** Proposed — decisão do dono **C1** tomada em 31/10/2026 (R-020 / DEC-11; sub-decisão (i): "Remove FHIR
access" do Gustavo). Ratificação formal pendente: artefato `docs/compliance/human-windows/R-020-dec-adr0016-22.md`,
papel DPO. Nenhum agente assina (ADR-0007).
**Data:** 2026-10-31
**Área:** Segurança | PHI/LGPD | Runtime de agentes
**Supersede PARCIALMENTE:** ADR-0016 §"Invariante de pseudonimizacao no ToolRegistry" e a frase sobre o
`ToolRegistry` auditar a recusa da allowlist; ADR-0022 §Decisão 1 e §Decisão 4. O restante das duas ADRs continua
valendo (ver §Supersedes). Os arquivos delas NÃO são reescritos (`docs/adr/README.md:8`): cada uma recebe um adendo
de status datado que aponta para cá.
**Achados:** F-007 (F-ADR-03), PEND-213 — `docs/audits/FORENSIC-COMPLETENESS-2026-10/`.

## Contexto

ADR-0016 e ADR-0022 (Accepted) descrevem uma barreira que não existe no código:

- Um `ToolRegistry` em `src/maezo/tools/registry.py`, "único caminho de saída" do resultado de qualquer tool, com
  `ToolDefinition(name, action, phi_fields, handler)`. Esse arquivo e essa classe não existem.
- Um `PhiZoneGateway` que pseudonimiza os campos `phi_fields` do resultado para consumidor `general` e recusa
  entregar se não estiver injetado. Não existe.
- Um `runtime/tool_wiring.py` (`build_tool_invoker`, `RegistryToolInvoker`) que constrói os servers MCP e chama
  `server.register_tools(registry)`. Não existe; a citação simbólica da ADR-0022 a ele é o único item de "rot
  divulgado" de símbolo do gate R-089 (`scripts/ci/check_doc_symbol_citations.py`, `_DISCLOSED_ROT`).
- Os métodos `register_tools` dos servers MCP (FHIR, WhatsApp, memória) existiam, mas nenhum código os chamava.
  A ADR-0022 §4 os declarava "load-bearing — intocáveis".

O que existe, medido na `main` de 31/10/2026:

1. **Uma fábrica de seams como autoridade única.** `src/maezo/gateway/tool_registry.py` é o único construtor
   sancionado de seams de efeito. As cinco raízes de composição listadas no docstring do módulo o chamam por
   `src/maezo/gateway/tool_registry.py::build_agent_seams`, `src/maezo/gateway/tool_registry.py::build_agent_fhir_seam`
   e `src/maezo/gateway/tool_registry.py::build_worker_seam_context`. Todo seam devolvido é um
   `src/maezo/gateway/seams/_base.py::GatedSeam`. As chaves de efeito estão em
   `src/maezo/gateway/seams/_base.py::EFFECT_SEAM_KEYS`. O check de readiness
   `src/maezo/gateway/tool_registry.py::effect_seams_gated` (com
   `src/maezo/gateway/tool_registry.py::gated_seam_violations`) fica vermelho se um seam não estiver embrulhado.
2. **Um PEP de efeitos.** Cada método de seam chama `src/maezo/gateway/seams/_base.py::gate`. Essa função decide
   por `src/maezo/gateway/effect_pep.py::decide_effect` e `src/maezo/gateway/effect_pep.py::decide`, numa escada
   L-0 a L-5, sobre o catálogo FECHADO de operações `src/maezo/gateway/effect_classes.py::OPERATIONS`. O principal
   fica preso no `src/maezo/gateway/seams/_base.py::SeamContext` desde a construção.
3. **A allowlist de processos.** `src/maezo/tools/process_allowlist.py::ProcessAllowlist` existe, com o universo
   `src/maezo/tools/process_allowlist.py::KNOWN_PROCESS_KEYS`. A recusa auditada fica em
   `src/maezo/tools/mcp_cibseven/transport.py::start_process_idempotent`, não num `ToolRegistry`.
4. **Guardas `*_NOT_HUMAN`.** Elas são a garantia real dos itens `hard` (AGENTS.md regra 7) e nenhuma ADR as cita.
   Exemplos: `src/maezo/tools/workers/base.py::ERR_DENIAL_NOT_HUMAN`,
   `src/maezo/tools/workers/fraude.py::ERR_FRAUD_ACCUSATION_NOT_HUMAN` e
   `src/maezo/tools/workers/programa.py::ERR_PROGRAM_DISCHARGE_NOT_HUMAN`. No BPMN, o erro
   `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn::Error_NipNegativaNotHuman` é um exemplo.
5. **Scrubbers de egresso.** Os logs passam por `src/maezo/gateway/log_scrubber.py::LogScrubber` e
   `src/maezo/gateway/log_scrubber.py::PhiLogRedactor`. A chave de mensagem Kafka passa por
   `src/maezo/platform/privacy/key_scrubber.py::egress_message_key`. A inferência PHI sem provedor da Zona PHI
   levanta `src/maezo/runtime/inference/errors.py::PhiZoneRoutingError`.

**A lacuna real.** O `SeamContext.phi_zone` só bloqueava em pontos isolados: a leitura clínica AMH recusa, a cada
chamada, principal fora da Zona PHI (`src/maezo/gateway/amh.py::AmhSubjectContextExecutor`, `SCOPE_NOT_SUPPORTED`).
O seam FHIR não tinha checagem nenhuma: nada impedia que um agente da Zona Geral recebesse um leitor FHIR que
devolve PHI. Era o caso concreto do Gustavo (`security_zone: general`): o mapa
`src/maezo/gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT` lhe dava o leitor cru `read_patient`.

**Alternativas consideradas.**
- **C2:** construir um `PhiZoneGateway` real, que pseudonimiza o resultado em volta dos seams. Rejeitada. Duplica
  os scrubbers e cria uma segunda autoridade sobre efeitos ao lado da fábrica (risco D-02 de
  `docs/plans/maezo-completion-program.md`). Só se justificaria se agentes da Zona Geral precisassem de dado clínico.
- **C3:** só adendos, sem ADR nova. Rejeitada porque a lacuna de zona continuaria sem dono.
- Um registro único que invoca e media resultados ("Candidate C") foi rejeitado no desenho da onda 1
  (`docs/design/wave1-effect-chokepoint.md`), porque reescreveria todo nó de grafo.

## Decisão

Documentamos o desenho real como a barreira canônica e acrescentamos UMA checagem: a cerca de zona na construção.

### Componentes canônicos

| Papel | Símbolo |
|---|---|
| Autoridade única de construção de seams | `src/maezo/gateway/tool_registry.py::build_agent_seams` (e os demais `build_*` do módulo) |
| Decisão por chamada (PEP de efeitos) | `src/maezo/gateway/effect_pep.py::decide` |
| Catálogo fechado de operações | `src/maezo/gateway/effect_classes.py::OPERATIONS` / `src/maezo/gateway/effect_classes.py::OperationSpec` |
| Allowlist de processos | `src/maezo/tools/process_allowlist.py::ProcessAllowlist` |
| Guardas de item `hard` | constantes e literais `ERR_*_NOT_HUMAN` nos workers + `bpmn:error` |
| Cerca de zona (nova) | `src/maezo/gateway/tool_registry.py::ZoneFenceError` + `src/maezo/gateway/effect_classes.py::OperationSpec.returns_phi` |
| Scrubbers de egresso | `src/maezo/gateway/log_scrubber.py::LogScrubber`, `src/maezo/platform/privacy/key_scrubber.py::egress_message_key` |

Não existe, e não será construído, um `ToolRegistry` que media resultados, um `PhiZoneGateway` ou um
`register_tools(registry)`. Os três `register_tools` sem chamador foram removidos dos servers MCP. A identidade de
tool vive no catálogo de operações e na lista `tools:` de cada `agent.yaml`, conferidos pelo
`scripts/ci/check_effect_chokepoint_fence.py`.

### Invariantes

- **I-1 (construção única):** todo efeito de agente ou worker (as chaves de `EFFECT_SEAM_KEYS`) é um `GatedSeam`
  construído só por `src/maezo/gateway/tool_registry.py`. Um seam que falha ao construir deixa vermelho o check de
  readiness `effect_seams_gated`.
- **I-2 (principal fixo, só subtrai):** toda chamada gated passa por `effect_pep.decide`, com o principal preso na
  construção e nunca tirado de argumento de chamada (A-8). O chokepoint e a cerca de zona só podem tirar
  permissão, nunca dar.
- **I-3 (início de processo):** todo início de processo passa por `start_process_idempotent` mais
  `ProcessAllowlist`.
- **I-4 (itens hard):** cada item de `spec/policies/autonomy/_hard_frozen.yaml` tem uma guarda `ERR_*_NOT_HUMAN`
  referenciada em código pelo worker e ancorada no BPMN do processo. Duas formas de âncora são aceitas: um
  `bpmn:error errorCode` ou, para guarda que devolve registro `blocked_by_guard`, a propriedade `enforcedBy`. A
  guarda vale com o PEP em `shadow` ou em `enforce`.
- **I-5 (zona):** nenhum seam FHIR (todas as operações `fhir.*` devolvem PHI) é construído para um principal fora
  da Zona PHI. A leitura clínica AMH (`clinical_context`) continua recusada na EXECUÇÃO, não na construção, por
  `src/maezo/gateway/amh.py::AmhSubjectContextExecutor` (ver Resíduos). Toda operação
  `fhir.*` declara `returns_phi=True`. `build_fhir_seam`, e por ele `build_agent_fhir_seam` e `build_agent_seams`,
  recusa com `ZoneFenceError` quando `SeamContext.phi_zone` não é `phi`. A zona vem do `security_zone` do
  `agent.yaml`, nunca do chamador. Uma operação fora do catálogo também é recusada. Inferência PHI sem provedor da
  Zona PHI continua levantando `PhiZoneRoutingError`, de forma independente.
- **I-6 (egresso):** PHI só sai pelos seams de egresso declarados. Logs passam pelos scrubbers, a chave Kafka por
  `egress_message_key` e os bytes de documento por UMA rota de documento PHI (ligada ao item A do DEC, ainda
  aberto).
- **I-7 (fronteira):** `agents/` não importa transportes nem outros agentes (ADR-0067; catraca
  `scripts/ci/check_fronteira_agentes.py`).

### Onde o PEP está em shadow

As 17 classes de `spec/policies/autonomy/action-approvals.yaml` declaram `enforcement: shadow`, inclusive
`leitura_phi_clinica`. Isso quer dizer que o PEP registra o que negaria, mas não bloqueia. Esta ADR não decide
quando passar de shadow para enforce: isso é a sub-decisão (ii), R-065 / F-053, em aberto. O que esta ADR garante
vale nos dois modos:

- a cerca de zona (I-5) atua na CONSTRUÇÃO e não depende do modo do PEP;
- os itens `hard` são protegidos pelas guardas `ERR_*_NOT_HUMAN` (I-4), que recusam o efeito adverso sem decisão
  humana qualquer que seja o modo do PEP.

### Gustavo (sub-decisão (i))

O Gustavo é da Zona Geral e deixou de ler FHIR. Ele saiu de `_FHIR_ADAPTER_BY_AGENT`, o `agent.yaml` não declara
mais `mcp-fhir.read_patient` nem `read_phi_data`, e o `build(config)` do grafo recusa uma dependência `fhir`. O nó
`gather` da jornada J2 (NIP) registra uma nota de lacuna e o dossiê segue só com os fatos já resolvidos do processo.

### Cercas

| Invariante | Cerca |
|---|---|
| I-1, I-2 | `scripts/ci/check_effect_chokepoint_fence.py` §8.1–8.5 |
| I-3 | `scripts/ci/check_start_process_fence.py` |
| I-4 | `tests/unit/gateway/test_hard_items_guard_map.py` (nova) |
| I-5 | `tests/unit/gateway/test_phi_zone_seam_fence.py` (nova: cruza mapa × `security_zone` × `returns_phi`) |
| I-6 (parcial) | `scripts/ci/check_phi_scrub_prereqs.py` |
| I-7 | `scripts/ci/check_fronteira_agentes.py` |
| Símbolos fantasmas e citações desta ADR | `tests/unit/docs/test_adr0071_barreira_real.py` (nova; reusa o gate R-089) |

## Consequências

**Positivas:**
- A ADR descreve o que o código faz, e as citações desta ADR são verificadas pelo mesmo gate R-089.
- A lacuna de zona fica fechada por construção, num único lugar, só subtraindo permissão e sem uma segunda autoridade.
- Os itens `hard` passam a ter uma cerca que amarra a lista congelada às guardas reais.

**Negativas (aceitas):**
- O dossiê NIP do Gustavo perde o enriquecimento FHIR opcional. Ele já era best-effort e não decidia rota.
- Se a definição de um agente da Zona PHI não carregar, `build_agent_seam_context` cai para a Zona Geral e a
  construção do seam FHIR desse agente agora FALHA (readiness vermelho). Antes, o seam era construído e negado no
  L-1. Isso é fail-closed e foi aceito.
- **Resíduos declarados, fora deste ato:**
  - a cerca de construção cobre os construtores FHIR. `build_amh_context` (seam `clinical_context`, sem chamador em
    produção hoje) não é cercado na construção, mas o executor recusa principal fora da Zona PHI em toda chamada
    (`src/maezo/gateway/amh.py::AmhSubjectContextExecutor`). `population` (agregados k-anônimos, não PHI) não é
    cercado. As leituras clínicas `amh.*` não estão marcadas `returns_phi`;
  - o Gustavo ainda chama `generate(phi=True)` na inferência. O roteamento de zona da inferência (segunda metade
    de I-5) continua sendo o mecanismo independente;
  - I-6 só fica completo com a decisão do item A (rota única de documento PHI);
  - o item `_DISCLOSED_ROT` da ADR-0022 (`build_tool_invoker`) NÃO foi removido. A citação continua no corpo
    Accepted da ADR-0022, que não pode ser reescrito sem um ato do dono (ver o adendo nela). Remover a entrada sem
    remover a citação quebraria o próprio gate (auto-auditoria de entrada obsoleta).

## Supersedes

PARCIAL:
- **ADR-0016:** a seção "Invariante de pseudonimizacao no ToolRegistry" inteira e a frase "O `ToolRegistry` …
  audita a recusa …" da seção da allowlist. Continuam valendo a decisão da allowlist de `process_key` (conjunto
  congelado, só adição por tenant dentro do universo conhecido, `ensure_allowed` fail-closed) e as consequências
  dela.
- **ADR-0022:** a Decisão 1 (costura `register_tools` em `build_tool_invoker`) e a Decisão 4 (`register_tools`
  "load-bearing"). Continuam valendo as Decisões 2, 3 e 5 (sem Deployments MCP fora do processo, DoD negativo de
  `deployment-mcp-*.yaml`, backends upstream por cliente HTTP).
