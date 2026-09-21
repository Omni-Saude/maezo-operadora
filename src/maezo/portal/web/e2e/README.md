# Portal e2e — prova sintética de browser (WP-J1-10)

Este pacote é o plano de e2e do portal (`src/maezo/portal/web/e2e/**`), distinto dos testes de
comportamento em `src/**.test.tsx` (jsdom, sem navegador). Ele dirige um Chromium REAL contra o
bundle REAL construído (`npm run build`) e o BFF FastAPI REAL (`maezo.portal.api.create_app`), com
suas rotas de produção de login/callback/sessão, cookies de transação e cerca de uso único do
`code`.

A ÚNICA peça sintética é a fonte de identidade: `maezo.portal.api.local_auth.StubAuthenticator`
(modalidade `local-test`, jamais construível sob `create_production_app`) responde a um código
fixo, e `support/portal-target.ts` responde pelo salto `oauth2/authorize` — nenhum IdP e nenhuma
rede é contactada; qualquer origem de terceiros é abortada e registrada, o que é como "sem
analytics de browser" (ADR-0049 D3) é provado, não presumido.

## O que é provado aqui

- Login → callback → sessão (`audience=staff`) com o bundle e o BFF reais (D4/A06, fatia local).
- D3: nada em `localStorage`/`sessionStorage`/IndexedDB/service-worker, `document.cookie`
  ilegível, URL final sem query, nenhuma requisição de terceiros.
- D10 (parte automatizada): varredura axe-core (wcag2a/2aa/21a/21aa/22aa) nas telas com e sem
  sessão, operação por teclado (Tab/Enter) e foco visível.

## O que NÃO é provado aqui (e não deve ser simulado)

- O trecho com efeito no engine (intake → claim → APROVAR → outcome no fixture seguro): exige os
  materiais de custódia fornecidos pela ROOT, a mesma postura `root_fixture` dos suites de engine
  reais. `uv run python scripts/dev/run_portal_journey.py --leg journey` RECUSA sem eles (exit 3) —
  nunca degrada silenciosamente, nunca fabrica resultado de engine.
- VoiceOver humano (D10): passo humano, permanece pendente e é sinalizado no relatório; nenhuma
  varredura automática o substitui.

## Uso

```bash
npm run build                        # bundle real
uv run python scripts/dev/run_portal_journey.py --artifacts /tmp/portal-journey
npm run verify:e2e                   # typecheck do plano + playwright
```

Evidência: `journey-evidence.json` (snapshots de storage, resultados axe, requisições observadas,
digests SHA-256), `playwright-report.json`, `playwright-html/`, traces em falha.
