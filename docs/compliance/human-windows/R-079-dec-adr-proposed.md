# R-079 — Aceite do lote de ADRs Proposed (DEC-27): registro da decisão e pacote de revisão

**Natureza:** REGISTRO DE DECISÃO DO DONO + PACOTE DE REVISÃO. Segue a regra de ouro desta árvore: o agente informa e registra; não decide nem assina (ADR-0007). O bloco do §5 fica em branco para o dono. Nenhum agente preenche nome, data ou assinatura.

**Origem:**
- Item R-079 do roadmap da auditoria forense @f57f0821 (decisão humana; dono: owner). Achados F-034 e F-052; pendência PEND-205.
- **DEC-27** na issue #1084. Resposta do dono no comentário de 2026-10-31 (https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6545662766): **opção (a)**, "ratificar o lote com revisão independente por ADR" e promover a Accepted.
- Item dependente: R-072 (errata em ADRs Accepted), preparado no mesmo PR (§4).

## 1. O que foi feito

| ADR | Status antes | Status agora | Registro |
|---|---|---|---|
| 0040 | Proposed | Accepted, com emenda anunciada a D6-bis | `docs/adr/0040-perspectiva-operadora-contas-recurso.md`, seção "Decisao de aceite" |
| 0041 | Proposto — DRAFT/verify | Accepted | `docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md`, idem |
| 0042 | Proposed — DRAFT/verify | Accepted; valor do piso e allowlist seguem abertos | `docs/adr/0042-k-anonimato-egresso-agregados-lago.md`, idem |
| 0045 | Proposed — DRAFT/verify | Accepted | `docs/adr/0045-fail-notify-canonico-start-process.md`, idem |
| 0048 | Proposto — DRAFT/verify | Accepted | `docs/adr/0048-driver-idempotency-reancorada-dedup-wamid.md`, idem |
| 0062 | Proposto | Accepted | `docs/adr/0062-roteamento-de-conversa-por-agente.md`, idem |

- Em cada ADR mudou só a linha (ou o parágrafo) de Status, com o mesmo número de linhas. O registro datado foi acrescentado ao final e cita o Status anterior. Nada mais foi reescrito.
- O índice `docs/adr/README.md` foi atualizado.
- A cerca `tests/unit/docs/test_adr_amendments.py` (direção 1) agora exige que o Status da ADR-0041 cite DEC-27, #1084 e a data. As oito ADRs reconciliadas continuam byte-idênticas (direção 2 intacta).

**ADR-0041 entrou no aceite.** A DEC-27 nomeia 0040/0042/0045/0048/0062. A pergunta da DEC-27 cita também "correções de Accepted só no ADR-0041 (Proposed)", e o R-079 pede ratificar ou emendar esse ponto. Por isso a ADR-0041 entrou no mesmo aceite, com a mesma revisão. O dono confirmou a inclusão no mesmo comentário de 2026-10-31 da #1084 (https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6545662766): "a ADR-0041 entra no mesmo aceite (sim)".

## 2. Revisão independente, por ADR (resumo; detalhe com `arquivo:linha` em cada ADR)

O revisor leu o texto de cada ADR contra `main` = `2be4515f`. Ele não é autor de nenhuma delas. A revisão independente por ADR exigida pela DEC-27 é o gate cego publicado no PR #1137 (https://github.com/Omni-Saude/maezo-operadora/pull/1137#issuecomment-6545468678).

| ADR | O código faz o que a ADR diz? | Divergências (follow-ups) | Em aberto |
|---|---|---|---|
| 0040 | Sim: D1, D3–D7 conferidos | âncora `harness.py:546-561` derivou (hoje `src/maezo/tools/workers/harness.py:579`); nota de proveniência e plano de merge são de 2026-09; menções "ADR-0040, Proposed" em contratos/BPMN/DMN/`src/`; M-4 segue com a perna PAGAR automática | OQ-1..OQ-15, OQ-R2; **emenda de D6-bis** (classificação balanceada) |
| 0041 | Sim: a direção 4 da cerca re-deriva os fatos e passa | §1(d) e §1.3 superados pela ADR-0048; âncoras medidas em `71dd4da` derivaram | ponteiro in-loco (dono) |
| 0042 | Mecanismo sim; política não decidida | texto diz "não mergeado"; critério 6 falha por 2 comentários; dois mecanismos (piso do Andre e política R-117) | valor de `k`, allowlist, momento vs WB.4 |
| 0045 | Sim: módulo `src/maezo/runtime/start_outcome.py`, cerca em teste, raise tipado no A2A | texto diz "não mergeado"; as opções das Partes 3–5 foram escolhidas na implementação | limiar do `MaezoAgentCrashLoop` |
| 0048 | Sim: migração `0010`, registry, chave HMAC keyed | há mais consumidores do registry do que o texto lista | — |
| 0062 | Sim: migração `0017`, CAS, função pura, flag default `False` | domínio de motivos maior que o texto; DL-0053 já registrado | purga (DPO); riscos aceitos |

### Nota sobre a ADR-0040 e a classificação de pagamentos

D6-bis diz: "Toda ordem originada em CONTAS ou RECURSO atravessa uma User Task humana". O dono aprovou depois (2026-10-31, #1084, comentário de 2026-10-31) uma classificação balanceada para SP-OP-PAGTO-001: lastro conferido dentro de PAGTO contra o histórico da origem, e a ordem de baixo valor com checagens objetivas verdes sai sem toque humano. Isso muda aquela consequência. A ADR-0040 foi aceita **como está**, com nota datada de que **uma ADR de emenda virá**. A emenda não foi escrita neste PR. Até ela, o código segue D6-bis (`spec/processes/dmn/pagto_admissibility.dmn:54-60`).

## 3. O que este aceite NÃO faz

- Não escolhe nenhum valor de política: o piso `k` da ADR-0042 continua 1 (`src/maezo/agents/andre/graph.py:345`); a política de retry/alerta da ADR-0045 não foi revista.
- Não liga nenhuma flag (`MAEZO_ROTEADOR_LUCAS` continua com default `False`).
- Não valida nenhuma citação de RN nem nome TISS (continuam `DRAFT/verify`).
- Não ratifica `spec/policies/autonomy/action-approvals.yaml` (MZO-040).
- Não muda código, BPMN nem DMN.

## 4. R-072 — erratas em ADRs Accepted

**Mecanismo.** A regra 2 de `docs/adr/README.md`, a `## Convencao seguida` da ADR-0032 e a da ADR-0041 (agora Accepted) dizem que uma ADR Accepted só muda por ADR nova. A cerca da ADR-0041 trava os bytes de oito delas. Por isso **nenhum adendo foi escrito dentro de ADR Accepted**. As erratas factuais ainda sem dono foram reunidas na **ADR-0072 (`Proposed`)**, que precisa de aceite do dono.

| ADR | Errata | Onde está |
|---|---|---|
| 0005, 0006, 0008, 0012, 0015 (`topic_registry`), 0024, 0025, 0032 | já corrigidas | ADR-0041 (aceita neste ato); 0024 também pela ADR-0048 |
| 0013 cláusula 3 | simulador Tasy | ADR-0037 (supersede parcial) |
| 0013 cláusula 5 | tópicos `agents.events.whatsapp.*` nunca declarados | ADR-0072 §1 |
| 0014 | caminhos reais em `deploy/observability/` | ADR-0072 §2 |
| 0015 | `A2ARegistry`, não `AgentCardRegistry` | ADR-0072 §3 |
| 0017 | `provision_tenant.py` e o teste negativo citado não existem | ADR-0072 §4 |

**Status pt/en (F-052).** As linhas do índice das seis ADRs aceitas agora dizem "Accepted", como o cabeçalho de cada uma. As ADR-0047 e 0061 continuam "Proposto" no índice e no arquivo, sem divergência.

**DoD do R-072.** O repro F-052 passa a sair 0. O repro F-114 confere a existência de arquivos e símbolos na árvore. Uma errata documental não o deixa verde. Esse DoD não fecha só com documento.

## 5. Bloco do dono (preenchimento exclusivo do dono; agentes não preenchem)

- **Confirmo o aceite registrado acima (DEC-27):** [  ]
- **ADR-0041 incluída no aceite:** [ sim / não ]
- **ADR-0072 (errata factual):** [ aceitar / pedir mudança ]
- **Ponteiro in-loco nas ADRs Accepted corrigidas (Consequências da ADR-0041):** [ não / sim, por ADR nova ]
- **Decisor (dono):** [nome]   **Data:** [  ]   **Assinatura:** [  ]
