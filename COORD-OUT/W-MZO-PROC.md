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
