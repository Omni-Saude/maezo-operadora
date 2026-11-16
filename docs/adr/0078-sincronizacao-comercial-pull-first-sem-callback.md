# ADR-0078: Sincronização comercial pull-first — nenhum callback comercial de entrada; eventos só server-side via CR-F6, por gatilho

**Status:** Proposto — redigido por AGENTE (E-SF-03, filho EP-CORE-11b do plano SALES-FUNNEL-DECISIONS-PLAN-2026-11, registro CORE-11). Um agente não aceita ADR e nenhum campo de assinatura foi preenchido (ADR-0007). `/docs/adr/` é CODEOWNERS.
**Data:** 2026-11-16
**Área:** Integração / Comercial / Superfície de rotas

## Contexto

O registro CORE-11 do plano de decisões do funil de vendas (`working/DEC-CORE.md`, seção "CORE-11 — OMNI-GAP-08: eventos/webhooks vs polling para estados assíncronos portal–Maezo", classe **DECIDED-autonomous**) responde a esta pergunta:

> "para v1, como a superfície buyer aprende que um estado assíncrono mudou (efeito `uncertain`, matrícula, caso de suporte, cobertura) — puxando (pull receipt-first) ou recebendo (push/webhook/evento)? E qual é o gatilho objetivo para evoluir?"

Âncora da pergunta, citada no registro (OMNI §7:196): "integração é command/response síncrono; não há design de event bus entre portal e Maezo. Sync de estados assíncronos depende de polling via `operation.get`/`journey.get`."

Opções consideradas no registro, transcritas:
- **A** — "Pull receipt-first disciplinado: receipts síncronos; `operation.get` para pendentes/incertos; `journey.get` na (re)entrada e ao voltar a aba visível; servidor (AMH `JourneySource`) faz leitura corrente da fonte; nenhum canal de eventos portal–Maezo."
- **B** — "Webhooks Maezo → BFF da superfície."
- **C** — "Push ao navegador (SSE/WebSocket) a partir do BFF, alimentado por polling server-side."
- **D** — "Reusar o tópico AMH `amh.maezo.work-items.v1` para eventos comerciais."
- **E** — "Barramento de eventos novo (marcos de jornada AMH → Maezo, família CR-F6 `OP10 milestone.publish`) consumido server-side."

Contrato congelado C6-4 (`tests/candidate_artifacts/commercial/contract-c6-4.json`, só leitura):
- `x-workflow.callbacks` (`:11082`): "Existing mounted qualified PSP notification ingress feeds the same source query and atomic work acknowledgement. Manual/source handoff completes by concrete polling in work.reconcile and runner; no new external callback endpoint/schema is introduced. Any future source callback requires its own qualification and cannot bypass authenticated source readback."
- `x-workflow.query_before_effect` (`:11080`): "[...] Uncertain paths never blindly resend."
- `x-customer-journey.privacy` (`:11120`): "[...] Fetch current admitted source state on re-entry and recover pending commands with original operation.get/query, never redispatch writes."

Fatos do Maezo conferidos no head deste ADR:
- `config/integrations/amh/contracts.lock.json` pina três tópicos: `amh.maezo.work-items.v1`, `amh.maezo.consent.v1` e `maezo.amh.outcomes.v1`. Nenhum é comercial.
- O consumidor AMH não existe: `src/maezo/platform/integrations/amh_inbox.py` recusa construção sem ratificação DBA; `src/maezo/ports/work_items.py` não nomeia broker nem tópico.
- As únicas rotas comerciais montadas por `src/maezo/portal/api/app.py` são `POST /api/v1/sales/commercial` e `GET /api/v1/sales/public/catalog` (`src/maezo/portal/api/commercial.py`).

## Decisão

### 1. Opção A para v1 (pull-first)
Adotamos a **opção A** do registro CORE-11, com **E** como alvo de evolução, só server-side. O desenho, transcrito do registro:

1. "Escritas devolvem receipt síncrono (`pending|uncertain|completed` — OMNI §2:103)."
2. "Para pendentes/incertos: `operation.get` **automático e limitado** na superfície (na entrada, ao voltar a aba visível, e com recuo progressivo enquanto a tela está aberta) + o botão manual existente; **nunca** redispatch de escrita (C64:11120)."
3. "`journey.get` na (re)entrada de `/acompanhamento` e ao recuperar visibilidade; a frescura vem da leitura corrente da fonte no servidor (C64:11112), não de cache do navegador."
4. "Server-side: conclusão de handoffs por `work.reconcile`/runner (pull — C64:11082); webhooks de PSP continuam **apenas dica** que dispara readback [o registro cita aqui a linha 1 do módulo de webhooks de PSP da AMH, `amh-data-platform@12c06c6`]."
5. "**Proibições v1:** nenhum endpoint de callback novo na Maezo ou no BFF; nenhum SSE/WebSocket; nenhum uso de `amh.maezo.work-items.v1` para estado comercial."

Os itens 2 e 3 são da superfície buyer (EP-CORE-11a, outro repositório). No Maezo, este ADR fixa o item 5: **nenhuma rota de entrada para notificação comercial externa é montada**. A lista fechada de rotas comerciais é `POST /api/v1/sales/commercial` e o GET público do catálogo.

### 2. Gatilhos para evoluir para E
Transcritos do registro CORE-11, sem acréscimo ("todos objetivos, sem métrica inventada"):

- **G-a** — "DEC-18 ratificar prazos e o dono querer o **gatilho proativo** do tracker (IDE §5 regra 2) → exige eventos de marco **server-side** (CR-F6 `OP10 milestone.publish`), consumidos pela Maezo (MZO-050b + `amh_inbox` ratificado) e entregues ao comprador por canal outbound (G17 / CR-F6), **nunca** por webhook ao navegador".
- **G-b** — "a observabilidade operacional já existente do gateway (rate limit — `M@fc:src/maezo/gateway/rate_limit.py`; não analytics de funil, A9 intocado) registrar recusas por limite atribuíveis a `operation.get`/`journey.get` da superfície".
- **G-c** — "surgir uma fonte cujo resultado só exista como notificação (sem readback autenticado) — então a qualificação própria exigida por C64:11082 é aberta antes de qualquer código."

Nenhum gatilho dispensa a qualificação própria nem o readback autenticado do `x-workflow.callbacks`.

### 3. Guarda executável
`tests/unit/portal/test_commercial_callback_fence.py::test_no_inbound_commercial_callback_route_is_mounted` lê as rotas que o `create_app` real monta. Falha, listando a rota, quando aparece sob `/api/v1/sales` qualquer rota fora da lista fechada, ou quando surge rota `callback`/`webhook` além do retorno OIDC do login staff.

## Consequências

**Positivas:**
- Nenhum ingress público novo com autoridade a provar. O readback autenticado continua sendo a única fonte de estado comercial.
- A opção A não cria nada que E precise desfazer: E é aditivo e server-side (registro CORE-11, "Reversibilidade").
- O v1 funciona com pull: o atraso desta decisão não bloqueia nada.

**Negativas (aceitas):**
- A superfície consulta por `operation.get`/`journey.get` em vez de receber aviso. O custo dessa consulta é o que o gatilho G-b observa.
- Notificação proativa de status fica fora do v1. Se o dono a tornar requisito de GO, E vira pré-requisito junto com G17 (registro CORE-11, "O que mudaria a resposta").

## Supersedes
—
