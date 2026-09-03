# ADR-0015: Runtime de delegacao A2A: Agent Card registry, envelope e anti-loop estrutural

**Status:** Accepted
**Data:** 2026-06-13
**Area:** Comunicacao

## Contexto

ADR-0003 escolheu A2A v1.0 (Linux Foundation) para delegação dirigida agente→agente e enunciou
as quatro guardas anti-loop em alto nível (cadeia acíclica, `max_hops=3`, budget, `task_id`
idempotente). Não especificou as estruturas de dados, o contrato de PHI no envelope, como os
handlers são injetados, ou o esquema de fatos Kafka.

A PR #17 implementou o runtime completo em `src/maezo/a2a/`. Este ADR formaliza as decisões
concretas feitas nessa implementação.

**Tensões resolvidas:**

1. **Agent Card como cópia ou derivação?** Manter um Agent Card separado cria drift em relação
   à Agent Definition (`agent.yaml`). A alternativa é derivar o Card diretamente do
   `AgentDefinition` do harness (fonte da verdade de `agent_id`, `security_zone`, `version`).

2. **Guardas anti-loop por convenção ou por construção?** Convenção (comentários, docs) não é
   enforcement. Guardas estruturais — levantam exceção antes de construir um envelope inválido —
   são enforcement real.

3. **PHI no envelope?** O envelope trafega entre agentes possivelmente em zonas distintas
   (ADR-0006). Carregar PHI cru no `payload` o exporia a qualquer consumidor da cadeia.

4. **Idempotência em memória ou durável?** Uma store durável (Redis/Postgres) é necessária para
   tolerância a falhas de pod, mas é wiring Phase 1+. A decisão é tornar a interface idempotente
   desde o dia 1 (contrato garantido), com backing in-memory no Phase 0 e store durável depois.

## Decisao

### Agent Card registry (`src/maezo/a2a/registry.py`)

- O `AgentCard` é **derivado** da `AgentDefinition` pelo harness via `AgentCard.from_definition(...)`.
  `agent_id`, `security_zone` e `version` vêm sempre do harness — nunca de config avulsa.
- O registry é **tenant-scoped**: chave `(tenant, agent_id)`. Lookup sem tenant é recusado em
  código. Zero cross-contamination por construção (ADR-0004).
- Capabilities, skills e `accepted_task_types` vêm da seção `a2a:` do `agent.yaml` do agente
  destino. `accepted_task_types` vazio = aceita qualquer task_type (compatibilidade Phase 0).
- O dispatcher rejeita delegações cujo `task_type` não conste no `accepted_task_types` do Card.

### Envelope de delegação (`src/maezo/a2a/delegation.py`)

- `DelegationEnvelope` é imutável (`frozen=True`). Construção via `root()` (primeiro hop) e
  `extend()` (sub-delegação); qualquer modificação direta de atributos é impossível.
- **Guardas estruturais — enforced na construção, não em runtime separado:**
  - Guard 1 (acíclica): `extend()` rejeita `target` já presente em `delegation_chain` →
    `CyclicDelegationError`.
  - Guard 2 (max_hops): `extend()` rejeita cadeia que excederia `max_hops=3` →
    `MaxHopsExceededError`.
  - Guard 3 (budget): `Budget.charge()` decrementa e rejeita quando esgotado →
    `BudgetExhaustedError`. O budget é imutável; `charge()` devolve um novo `Budget`.
  - Guard 4 (idempotência): implementada no dispatcher (ver abaixo).
- **PHI**: `payload_ref` deve ser uma referência FHIR/pseudonimizada (ex.: `Patient/abc` no
  servidor FHIR do tenant), nunca PHI cru. `__post_init__` aplica heurística defensiva:
  strings que parecem CPF (11 dígitos) ou CNPJ (14 dígitos) são rejeitadas com `DelegationError`.
- `deadline` deve ser timezone-aware (UTC); `expired()` é verificado pelo dispatcher antes de
  rotear.

### Dispatcher (`src/maezo/a2a/dispatcher.py`)

- `DelegationDispatcher.delegate(envelope)` é o **único ponto de entrada** para toda delegação.
  Nunca levanta exceção para o chamador: rejeições são `DelegationResult` estruturado.
- **Guard 4 (idempotência)**: se `task_id` já foi visto, devolve o resultado anterior
  (`idempotent_replay=True`) sem re-executar o handler. Um `asyncio.Lock` por `task_id` garante
  que execuções concorrentes do mesmo `task_id` não disparam duas execuções.
- **Dependências injetadas**: `AgentCardRegistry`, mapa `agent_id → AgentHandler`, `AuditLog`
  (ADR-0007) e `FactProducer`. Handlers reais (grafos LangGraph) são injetados pelo harness no
  Phase 1; nos testes, `FakeAgentHandler` é injetado.
- **Ordenamento de execução**: (1) idempotência → (2) validação de contrato (Card, task_type,
  handler, deadline) → (3) auditoria pre-efeito (ADR-0007) → (4) fato `requested` no Kafka →
  (5) roteamento ao handler → (6) fato `completed` ou `rejected`.

### Fatos Kafka (`src/maezo/a2a/facts.py`)

- Três tópicos declarados em `config/topic_registry.yaml` (validados pelo `validate-artifacts`):
  - `agents.events.delegation.requested`
  - `agents.events.delegation.completed`
  - `agents.events.delegation.rejected`
- O fato **nunca carrega PHI**: apenas `task_id`, `task_type`, `tenant`, `origin`, `target`,
  `delegation_chain`, `ts` e (em rejeição) `reason`. `payload` jamais é emitido.
- Particionamento por `tenant` (chave da mensagem Kafka).

### Primeiro caso de uso

- `helena → rafael` é a primeira aresta de delegação: Helena (triagem) delega análise de
  autorização ao Rafael (operações de autorização). `task_type: authorization.analyze`.

### Wiring futuro (Phase 1+)

- Store de idempotência durável (Redis/Postgres) substitui o dict in-memory do dispatcher.
- Injeção de handlers reais pelo harness (grafos LangGraph por tenant).

## Consequencias

**Positivas:**
- Anti-loop é uma propriedade estrutural do tipo, não uma checagem em runtime separada;
  impossível construir um envelope inválido silenciosamente.
- Auditoria por delegação (ADR-0007) garante não-repúdio de toda cadeia desde o primeiro hop.
- Fatos Kafka desacoplam consumidores reativos (observabilidade, compliance) do fluxo síncrono.
- Handler resolution injetável torna o dispatcher testável sem grafos de agente reais.
- Envelope imutável elimina mutações acidentais entre hops.

**Negativas (aceitas):**
- Store de idempotência in-memory não sobrevive a reinício de pod; `task_id` re-entregue
  após reinício pode re-executar. Mitigação: store durável é wiring Phase 1 obrigatório antes
  de produção.
- A heurística de PHI no `payload_ref` (CPF/CNPJ por contagem de dígitos) é defesa
  superficial; a garantia real é o contrato de tipo (`payload_ref` como referência FHIR) e
  a validação no PHI zone gateway (ADR-0006).

## Supersedes
ADR-0003 (parcial — ADR-0003 continua válido para a escolha de protocolo A2A v1.0 + Kafka;
este ADR refina e formaliza as decisões de implementação do runtime que ADR-0003 deixou em aberto).
