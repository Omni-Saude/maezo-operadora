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

## Q-W-MZO-CHAN-02 — 2026-11-16 — E-SF-13: migração 0036 e um teste fora da posse

1. A migração 0036 (custódia cifrada do telefone de lead, G-43) cria tabela nova. O gate `tests/unit/platform/.../test_erasure_plan.py::test_the_enumeration_covers_every_table_the_migration_chain_creates` exige registrá-la em `src/maezo/platform/lifecycle/erasure_plan.py` e `spec/policies/retention/erasure-plan.template.yaml` (posse GATE; decisão DPO pendente). Também mudam os pinos de head (`src/maezo/platform/validators/vendor_plane.py` EXPECTED_HEAD 0035→0036 e testes que fixam head).
   - (A) E-SF-13 sai SEM a migração: a custódia de lead fica só como módulo + testes unitários (sem tabela); a migração 0036 e o registro no plano de eliminação viram linha entregue à GATE/coordenação (camada ≥ 49, `decisao_dpo: PENDENTE`).
   - (B) Autorizar CHAN a tocar os dois arquivos do plano de eliminação e os pinos de head no PR de E-SF-13.
   - Recomendação: (A). PROVISÓRIO-aguarda-coordenador: o commit local já está assim; migração preparada fora do repo.
2. `tests/unit/docs/test_contract_bpmn_dmn_citations.py` (fora da linha CHAN): a contagem de `.md` em `docs/processes/contracts/` passa de 17 para 18 por causa do `MESSAGE-CATALOG.md` novo, cujo caminho o plano impõe. Edição mínima (um número; a asserção dos 17 contratos `SP-OP-*` fica).
   - (A) Autorizar no PR de E-SF-13. (B) Entregar a linha a W-MZO-DOC. Recomendação: (A). PROVISÓRIO-aguarda-coordenador.

Nota de fila: CD-21 item 2 dá prioridade ao E-SF-13 na fila do maezo. Ele só abre depois do merge do #1269 (um PR aberto por janela), salvo instrução em contrário.

## Estado (2026-11-16, após reinício do container)

- E-SF-14: PR #1269, PRONTO registrado acima; aguarda APPROVED de code owner de `/deploy/` e a vez.
- E-SF-18: revisão independente PASS (be10fd82, ramo local); aguarda a vez.
- E-SF-17: revisão independente PASS (c572eddf, ramo local); aguarda Q-W-MZO-CHAN-01 e a vez.
- E-SF-13: revisão independente PASS (dbe3eb0d); CD-25 recebido e em implementação (trava explícita de oposição de beneficiário no job de lembretes); nova revisão depois. Aguarda Q-W-MZO-CHAN-02 e a vez.

## Estado (2026-11-16 ~23:30Z)

- E-SF-14: #1269 mesclado.
- E-SF-13: PR #1274 aberto (head 3ebad170, rebaseado sobre b3512b7), revisão independente PASS; sem a migração 0036 (CD-31); aguarda CI.
- E-SF-18: pronto em ramo local (rebaseado), abre depois do #1274 conforme CD-29/CD-31.
- E-SF-17: pronto em ramo local; entra com as 8 linhas autorizadas (CD-31).

PRONTO maezo-operadora #1274 head 3ebad170ee6a0edc8d64b89eb5f628dda4a45e5c checks verdes (integração lida do junit: cancel-contas 136/0 falhas, cred-ans 129/0, recurso-nip 130/0, reembolso 56/0, restante 818/0) — exceto `flip-path-review-gate`: `scripts/ci/check_whatsapp_templates.py` (novo) exige APPROVED de membro verificado de `@Omni-Saude/security-team` (o único dono usuário de `/scripts/ci/` é o autor). Revisão independente PASS no mesmo conteúdo.

E-SF-18: PR #1276 aberto 2026-11-17 ~00:08Z (revisão independente PASS), CI em curso.

pronto: maezo-operadora #1276, head 099d399da433f16931188ce6fdb81f38e3b5756e, checks verdes (integração lida do junit: cancel-contas 136/0 falhas, cred-ans 129/0, recurso-nip 130/0, reembolso 56/0, restante 818/0; flip-path-review-gate verde). Enviado à mescladora (CD-42).

- 2026-11-17 01:12Z: #1274 (E-SF-13) mesclado. Próximo: #1276 (Update branch pela mescladora), depois PR de E-SF-17.

- 2026-11-17 ~01:30Z: PR #1279 (E-SF-17) aberto por pedido do dono (head b14ba0d77e8c9c50b57156a8efae111e6ba97efe, com merge de main 9f71d41), revisão independente PASS; 8 linhas fora da posse listadas no corpo (CD-31/CD-38). #1276 segue aguardando CI na base nova.

pronto: maezo-operadora #1276, head d0f463793c4e5137e7ea9c60b52b51eac648d44c, checks verdes (base 9f71d41; integração lida do junit: cancel-contas 136/0, cred-ans 129/0, recurso-nip 130/0, reembolso 56/0, restante 818/0).

- 2026-11-17 01:57Z: #1276 (E-SF-18) mesclado (main 87bd9e48). Resta #1279 (E-SF-17): CI em curso; job Java com falha de censo de descritores (não tocado pelo diff) a rerodar uma vez; flip-path-review-gate aguarda security-team.

pronto: maezo-operadora #1279, head b14ba0d77e8c9c50b57156a8efae111e6ba97efe, checks verdes (integração lida do junit: cancel-contas 136/0, cred-ans 129/0, recurso-nip 130/0, reembolso 56/0, restante 818/0; job Java verde na rerodada única; exceto flip-path-review-gate = aprovação do security-team). Enviado à mescladora.

- 2026-11-17 ~02:55Z: #1279 aguarda #1278 e Update branch pela mescladora. Respondidas à mescladora: (a) teste Java ContinuousPossessionCustodyTest:327 (censo de descritores do processo, 90 vs 91) não depende do diff; (b) nenhum teste confere digests de contracts.json/administrative-contracts.md com source-readiness.json.

pronto: maezo-operadora #1279, head 128237cc570f046cac0208003672625ffa132949, checks verdes (após Update branch, main 2b421e5a; junit de integração: cancel-contas 136/0, cred-ans 129/0, recurso-nip 130/0, reembolso 56/0, restante 818/0; job Java verde de primeira; exceto flip-path-review-gate = aprovação do security-team). Enviado à mescladora.

## Relatório final W-MZO-CHAN — 2026-11-17 ~03:45Z
- E-SF-14: MESCLADO #1269 (b3512b7f). Revisor: FAIL→PASS (id nulo passava; corrigido). Não verificado: render em cluster real. Portões fechados: outbound/lembrete off no chart; phoneNumberId obrigatório. Deriva: âncora :924 do dashboard w7 forçou comentário compacto em values.yaml. Pedido: follow-up _GUARDED_MODULES (posse alheia).
- E-SF-13: MESCLADO #1274 (9f71d410). Revisor: FAIL→PASS (brecha wa-consent por versão, travas sem prova, replay; corrigidos). Não verificado: envio real Meta; aprovação de templates. Portões fechados: OutboundGate nega por padrão; lembrete flag off (RT-D4) + trava CD-25 BENEFICIARY_OPPOSITION_NOT_EXECUTABLE; ret.* sem aprovação. Deriva: sem migração 0036 (CD-31; custódia cifrada G-43 depende de DPO e do plano de eliminação, posse GATE). Pedido: ledger "G-43 custódia de lead pendente DPO"; DL sobre canal de oposição do beneficiário (DPO, não decidido).
- E-SF-18: MESCLADO #1276 (87bd9e48). Revisor: FAIL→PASS (varredura DMN fraca; reforçada). Não verificado: jornada vendor ponta a ponta (A10 só cercado). Portões fechados: OP16 fora da v1; nenhuma DMN ratificada. Deriva: noqa inertes S603/S405/S314 removidos.
- E-SF-17: MESCLADO #1279 (9cf06684). Revisor: FAIL→PASS (mover handoff_cobranca quebrava check_roteador_lucas; guarda CD-13 faltava; corrigidos). Não verificado: suíte unitária inteira local (só CI); Helena com flag ligada. Portões fechados: agente comercial flag off; roteador_comercial off; sem PHI, sem hard, sem envio proativo, kpis: [] (sem A9); _hard_frozen byte-idêntico; só +send_prospect_message L3. Não feitos: GTM-E-11 (G-11, ADR-0067 + baseline fora da posse), classe comunicacao_prospect (gateway fora da posse), EP-REG-G3a (RL-REG-43 jurídico). GAPs: telemetria rotularia passagem comercial como passagem_cobranca_sem_frase (corrigir antes da ativação); SC-03.D1 DPO; nome da persona e headcount (dono). Deriva: linhas contracts.json/agent_def.py deslocadas; ADR-0067 cita graph.py 11.381 linhas (base 6979); ComercialInputError no lugar de AdministrativeInputError; digests de contracts.json/administrative-contracts.md defasados em source-readiness. Pedido: DL extensão ADR-0062/DL-0053 a Compras (dono); ledger GAP-PERSONA-NAME, GAP-HEADCOUNT, GAP telemetria comercial.
- Transversal: Java ContinuousPossessionCustodyTest:327 (censo de fd 90 vs 91) intermitente, não ligado ao diff; repassado à mescladora (T-MZO-A). Nenhum .bpmn, nenhuma DMN ratificada, nenhum caminho de outra janela/programa além das 8 linhas CD-31.
DONE 9cf06684f9a3e38fbcadafc819972e588679208c linhas=7
