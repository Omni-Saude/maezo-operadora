# COORD-OUT — W-MZO-CHAN (onda 1 do Funil de Vendas)

Arquivo só de coordenação (ramo `sf-w1/chan/coord`, nunca vira PR). Só acréscimos.

## Q-W-MZO-CHAN-01 — 2026-11-16 — E-SF-17 precisa de 8 linhas fora da posse CHAN

Contexto: o agente novo `spec/agents/comercial/` faz cercas de frota existentes (que contam ou listam agentes um a um) exigirem uma linha cada. Os 8 arquivos estão fora da linha CHAN da tabela de posse (EXECUTION-LOG §4):

1. `src/maezo/runtime/turn_telemetry.py`: `_DESFECHO_VOCAB["comercial"]` (só declaração; nada é emitido; nenhuma medição A9).
2. `tests/evals/test_trigger_coverage.py`: `_AGENTS_ENFORCED` + `comercial`.
3. `tests/unit/agents/test_error_field_no_raw_exception.py`: `_GRAFOS_SEM_EVALUATE_DMN` + `comercial`.
4. `tests/unit/agents/test_terminal_nodes_emit_desfecho.py`: exceção do erro de start (o comercial não inicia processo).
5. `tests/unit/gateway/test_pep_policy_unification.py`: contagem de agent.yaml 11 → 12 (arquivo existente em `tests/unit/gateway/`, onde só arquivo novo é compartilhado).
6. `tests/unit/platform/test_agent_yaml_tools_wired.py`: 3 entradas de classificação de tool.
7. `tests/unit/platform/test_validation_agent_def.py`: postura WhatsApp do comercial.
8. `tests/unit/spec/test_carencia_check_t1_interlock.py`: marcador `agents/comercial/` no docstring.

Nenhum muda semântica de produção; todos registram o agente novo. Total +30/−5.

Opções:
- (A) Autorizar os 8 no mesmo PR de E-SF-17 (sem eles o CI do PR fica vermelho).
- (B) Entregar as 8 linhas a outra janela/trilha que as aplique antes do E-SF-17.
- (C) E-SF-17 sem o agente `spec/agents/comercial/` nesta onda (só G-10 documental e a cerca da passagem), deixando o agente para depois.

Recomendação: (A). PROVISÓRIO-aguarda-coordenador: o E-SF-17 fica pronto em ramo local, sem push, até a resposta; nenhum desses 8 arquivos é editado antes dela.

## Estado (2026-11-16 ~21:10Z)

- E-SF-14: PR Omni-Saude/maezo-operadora#1269 aberto; revisão independente PASS (head aeb4c5b); unit verde; integração em curso; `flip-path-review-gate` aguarda APPROVED de code owner de `/deploy/` (ato humano).
- E-SF-18: revisão independente PASS (ramo local); aguarda a vez (um PR aberto por janela).
- E-SF-17: implementado; revisão independente em curso; depende de Q-W-MZO-CHAN-01.
- E-SF-13: em implementação.

PRONTO maezo-operadora #1269 head aeb4c5b02cc133a873695c907f680919ba3ce1c3 checks verdes (integração lida do junit: cancel-contas 136/0 falhas, cred-ans 129/0, recurso-nip 130/0, reembolso 56/0, restante 818/0) — exceto `flip-path-review-gate`, que aguarda APPROVED de code owner de `/deploy/` (ato humano; nenhum push resolve). Revisão independente PASS no mesmo head.
