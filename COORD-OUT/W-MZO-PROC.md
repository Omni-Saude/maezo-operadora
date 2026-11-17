# COORD-OUT — W-MZO-PROC (onda 1 do Funil de Vendas)

Canal janela → coordenador (CD-20). Só acréscimo. Cita ids e caminhos; não copia conteúdo do pacote.
Épicos: E-SF-08 → E-SF-12 (empilhados), E-SF-09, E-SF-RT-01. Base: `main@8a397e9`.

## Q-W-MZO-PROC-01 — caminhos fora da tabela de posse (EXECUTION-LOG §4), seguidos como PROVISÓRIO-aguarda-coordenador

Nenhum é de outra janela; todos são consequência mecânica de arquivo novo (registro, allowlist, contagem pinada) ou módulo novo sem caminho nomeado.

| Épico | Caminho | Motivo |
|---|---|---|
| E-SF-08 | `spec/aprovacoes-de-regras.yaml`, `spec/processes/dmn/orphans-allowlist.yaml` | DMN nova `funnel_deadline_catalog.dmn` (DRAFT, pendente no registro; órfã até haver consumidor) |
| E-SF-08 | `README.md` (contagem de DMN), `tests/unit/platform/test_engine_deploy.py`, `test_engine_deploy_cli.py`, `test_validation_phi_completeness.py`, `test_validation_vocabulario_saida.py`, `test_validation_perspective.py` | contagens pinadas da árvore (70→71 DMN; corpus) |
| E-SF-08 | `tests/unit/gateway/test_vendor_suppression.py` (arquivo existente) | pinava o defeito "dias úteis sem feriado"; troca por pin equivalente com feriado |
| E-SF-08 | `docs/processes/contracts/SP-OP-SUPP-001.md` | documento do relógio SUPP passa a citar feriados nacionais |
| E-SF-09 | `src/maezo/platform/saida_a_pedido/` | "módulo novo de E-SF-09"; `src/maezo/domain/` recusado pela catraca ADR-0037 |
| E-SF-09 | `README.md`, `AGENTS.md`, `tests/unit/docs/test_contract_bpmn_dmn_citations.py` | contagem de contratos 17→18 (contrato novo `SP-OP-ARREPENDIMENTO-001`) |
| E-SF-RT-01 | `src/maezo/platform/validation/retention_signals.py` | cerca local; nenhum caminho `src/` da PROC serve |

Opções: (a) aceitar como está (recomendado); (b) mover para caminho que o coordenador nomear; (c) retirar a peça e deixar o filho parado.

## Q-W-MZO-PROC-02 — GT-4 (ADD-A9-CONSENT §6), lado de saída, depende da janela CHAN

E-SF-12 prova o lado do store (oposição `registered` suprime; store ausente não entrega; `beneficiario` sem suporte resolve desconhecido). O OutboundGate não existe no head. O job `src/maezo/platform/lembretes/` (CHAN; desligado por padrão) não consulta oposição de beneficiário, que não é registrável em SP-OP-SUPP-001.
Opções: (a) CHAN faz o job recusar a rodada enquanto não houver oposição executável para beneficiário (recomendado; cabe em E-SF-13); (b) decisão de dono de que o opt-out por palavra-chave é o canal de oposição do beneficiário (fronteira DPO; não decidimos).

## Q-W-MZO-PROC-03 — filhos parados por campo novo no sucessor do C6-4 (CA-01; casa AMH)

- E-SF-08 / EP-REG-23b e TB-E-13-b: projeção de prazo em `journey.get` — o snapshot de jornada do C6-4 não tem campo de prazo/ETA. Só as cercas entram.
- E-SF-09 / EP-REG-19a item (4): estorno "concluído" exige portador do segundo recibo (ack Tasy) na ação financeira.
- E-SF-09 / EP-REG-27b: protocolo, digest do bloco art. 15 e comprovante pelo fio comercial.
Recomendação: registrar como demanda à W-AMH (proposta de sucessor), sem bloquear os PRs desta janela.

## ACHADOS (fora do escopo; nenhum PR aberto)

- ACHADO `spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn:27,97` — documentação ainda diz "dias úteis sem feriados" depois de E-SF-08 (caminho da GATE).
- ACHADO `src/maezo/platform/migrations/versions/0021_vendor_suppression_records.py:15,75` (comentário) — mesmo texto "sem feriados".
- ACHADO `spec/policies/autonomy/action-approvals.yaml:308,311` e `docs/reviews/mzo-040-approval-packet.md:54` — âncora obsoleta `cancel.py:731/737`; no head, `cancel.py:823-832`.
- ACHADO `tests/unit/gateway/test_audit_anchor.py:809` — falha em contêiner que roda como root (pin exige processo sem privilégio); preexistente no `main`.
- ACHADO autoria git — os commits saem com a identidade do ambiente (endereço noreply genérico); não há identidade alternativa configurada aqui.

## Estado dos PRs

PRONTO maezo-operadora #1271 head c267f6338cb34567e2ca868b300a2e4d631095a1 checks verdes (integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skip) — E-SF-08; revisão independente PASS. Próximo da janela, depois do merge: E-SF-12 (empilhado), depois E-SF-09 e E-SF-RT-01 (prontos localmente, revisão PASS).

pronto: maezo-operadora #1277, head c8486be5a986548217c64846d94e0b8124c3e6cb, checks verdes (integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skip) — E-SF-12; revisão independente PASS. #1271 (E-SF-08) mesclado em main 1d784aa.

pronto: maezo-operadora #1277, head abe603975ebe7f573f1c5a1808b0f3fa5abeb8f0, checks verdes (após Update branch da mescladora sobre main 9f71d41; integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skip).

pronto: maezo-operadora #1282, head 39e49fbf864cb4dc576ea4d20e1568fabbc5a47c, checks verdes (integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skip) — E-SF-09; revisão independente PASS. Pendente só flip-path-review-gate (deploy/Dockerfile exige code owner humano). Ramo 10 commits atrás da main, aguardando Update branch da mescladora.
CAMINHO pyproject.toml, deploy/Dockerfile, tests/unit/ci/test_spec_packaging_fence.py — E-SF-09: force-include + COPY + subtree esperado de spec/policies/saida-a-pedido, exigidos pela cerca de empacotamento (PROVISÓRIO-aguarda-coordenador).

pronto: maezo-operadora #1282, head c989c8d64ea2e7416e5991795e0bb4e116aa8ca8, checks verdes (após 2 merges da main; integração lida do junit: 5 shards, 1403 testes, 0 falhas, 0 erros, 7 skip; flip-path-review-gate vermelho por deploy/Dockerfile, exceção aceita) — repassado à mescladora pelo coordenador às ~07:18Z.

pronto: maezo-operadora #1290, head b69c390783d37fb7f3c3eeb51c989bd4a523898d, checks verdes (integração lida do junit: 5 shards, 1403 testes, 0 falhas, 0 erros, 7 skip) — E-SF-RT-01; revisão independente PASS.

## Relatório final — W-MZO-PROC

| Épico | Estado | PR | Revisor (cego, Opus) |
|---|---|---|---|
| E-SF-08 | mesclado (main 1d784aa) | #1271 | PASS após FAIL (pins de contagem, noqa inerte) |
| E-SF-12 | mesclado (main 2b421e5), empilhado sobre 08 | #1277 | PASS após FAIL (só metadado de commit) |
| E-SF-09 | mesclado (main d9e2c56) | #1282 | PASS após FAIL (2 guardas de TB-E-17-a; protocolo RN 561) |
| E-SF-RT-01 | PR aberto, pronto | #1290 | PASS após 4 rodadas (OS-04, pin A9, esquema literal, exclusão de templates) |

- Não verificado localmente: shards de integração (sem Docker; lidos do junit de cada PR) e `tests/unit/{ci,deploy,dev}` completos (helm ausente, clone raso, root; todas as falhas também na main).
- Portões fechados por construção (testes afirmam): catálogo de prazos e políticas todos DRAFT, sem ETA nem prazo exibido (REG-23, REG-27, TB-13/16); sem promessa numérica de arrependimento (REG-03/Q3); art. 15 não publicável; linha OS-08 ausente (RT-J3 a); `contract_termination` só por pessoa (PEP real); seam de supressão não servido no daemon; `beneficiario` sem suporte em SUPP-001; `ret.*` sem emissor (A9) e templates ret.* sem aprovação.
- Parados por campo novo no sucessor do C6-4 (CD-26): projeção de prazo em journey.get (EP-REG-23b, TB-E-13-b); 2º recibo de estorno (EP-REG-19a item 4); protocolo/comprovante pelo fio (EP-REG-27b).
- Sem filho executável (CD-03): promoção de SP-OP-SUPP-001 (E-SF-12r).
- Deriva principal: GAP-C6 já resolvido por `platform/calendario`; `service.py` do plano = `runtime/worker_runtime/service.py`; `tipo_titular` = `categoria_sujeito`; âncora `cancel.py:731` obsoleta; pacote de E-SF-09 fora de `domain/` (catraca ADR-0037); E-SF-13 passou a declarar templates ret.*.
- Caminhos fora da tabela, aceitos (CD-24, CD-46): registro de aprovações, allowlist de órfãos, pins de contagem, `platform/saida_a_pedido/`, `platform/validation/retention_signals.py`, `pyproject.toml`/`deploy/Dockerfile`/cerca de empacotamento.
- Pendência humana: flip-path-review-gate do #1282 (deploy/Dockerfile) sem aprovação de code owner; aceito pela mescladora.
- Custo de CI: #1271 1 push; #1277 1 push (+1 Update da mescladora); #1282 4 pushes (1 correção de empacotamento, 2 merges da main a pedido); #1290 1 push.
- Ledger/DL pedidos (entrega em lote pela coordenação): DL para o catálogo de prazos e calendário com feriados no SUPP (E-SF-08); DL para a política de saída a pedido (E-SF-09); linhas de ledger de E-SF-08, 12, 09 e RT-01.

DONE 776b6ac linhas=76
