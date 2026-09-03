# Contrato — SP-OP-LGPD-DSR-001 (Direitos do Titular — LGPD)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 1 · **BPMN:** `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn`
**Gatilho regulatorio:** LGPD (Lei 13.709/2018) art. 18 (direitos do titular), art. 19, II (15 dias), art. 11 (dados de saude = sensiveis). Retencao legal de prontuario (Lei 13.787/2018/CFM — **DRAFT/verify**) pode impedir eliminacao.

## Papel LGPD (controlador/operador) — DRAFT/verify (PERSP-LGPD-ROLE)

**PAYER — papel de controlador (LGPD art. 5, VI) INFERIDO do comportamento do processo; nenhum
artefato desta cadeia (este contrato, o BPMN, a DMN `lgpd_dsr_routing` ou o worker `lgpd.py`)
declara explicitamente `controlador`/`operador` (art. 5, VI/VII).** A operadora executa a
retificacao/eliminacao aprovada (`ST_ExecutarRequisicao`, `spec/processes/bpmn/SP-OP-LGPD-DSR-001_
Direitos_do_Titular.bpmn:270`), decide e envia a resposta ao titular (`ST_EnviarResposta`,
`bpmn:277`) e e a titular do prazo legal de 15 dias do art. 19, II (`ESP_SlaGlobal`,
`bpmn:307-311`) — comportamento funcional de controladora (define finalidade e meios do
tratamento), nao de mera operadora agindo por conta de terceiro. **Esta e uma INFERENCIA A
PARTIR DO COMPORTAMENTO, nunca uma declaracao ratificada** — a titularidade do prazo do art. 19,
II depende dela: permanece `DRAFT/verify pendente do DPO` (WP-GOVERNANCA-DPO) antes de fundamentar
qualquer posicao regulatoria sobre papel LGPD.

## Invariantes

- Nenhum dado sensivel sai sem revisao humana (`UT_RevisaoDpo` — DPO/juridico-privacidade).
- Negativa fundamentada (ex.: retencao legal) e decisao humana com `fundamentacao_legal` obrigatoria.
- Verificacao de identidade ANTES de qualquer compilacao de dados (anti engenharia social).
- GAP-LGPD-6 (dangling-catch fix): `titular_pseudo_id` ausente/vazio em `operadora.lgpd.verify_identity`
  levanta `Error_LgpdIdentidade` (`ERR_DSR_IDENTITY_UNVERIFIED`), capturado por
  `BE_IdentidadeInverificavel` (boundary em `ST_VerificarIdentidade`) — guard TECNICO (impossibilidade
  MECANICA de verificar), NUNCA acusacao automatica de fraude (L0 hard `fraud_accusation`). Publica
  `lgpd_dsr.completed` (desfecho=`identidade_inverificavel`) e termina em `End_IdentidadeInverificavel`
  (fail-safe, nao adverso) — visivel a DPO/juridico-privacidade para revisao manual.
- SLA legal de 15 dias conta do INICIO da instancia (event subprocess `ESP_SlaGlobal`), nao da tarefa.
- FAIL-CLOSED (GAP-LGPD-4): `GW_DecisaoDsr` so avanca para envio/execucao com um dos tres valores
  EXPLICITOS de `decisao_dsr` (`APROVAR_ENVIO` | `EXECUTAR_E_ENVIAR` | `NEGAR_FUNDAMENTADO`); ausente/
  em branco/desconhecido NUNCA libera dados por omissao — roteia a `End_ErrDecisaoInvalida`
  (`ERR_DSR_DECISION_INVALID`), um incidente tecnico, nao uma decisao.
- HITL-estrutural (GAP-LGPD-3, ADR-0005): `GW_GuardFundamentacao` (avaliado pelo ENGINE, nao so por
  validacao de formulario no Tasklist) barra `NEGAR_FUNDAMENTADO` sem `fundamentacao_legal` antes de
  `ST_EnviarResposta` — `End_ErrFundamentacaoAusente` (`ERR_DSR_FUNDAMENTACAO_AUSENTE`).

## Business key (idempotencia)

```
DSR-{tenant_id}-{titular_pseudo_id}-{tipo_requisicao}-{data_solicitacao_iso}
```

Re-pedido do mesmo tipo no mesmo dia retorna a instancia ativa.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant |
| `titular_pseudo_id` | string | sim | Pseudonimo do titular (ADR-0006) |
| `canal` | string | sim | `whatsapp` \| `portal` \| `email` |
| `tipo_requisicao` | string | sim | `confirmacao_acesso` \| `correcao` \| `eliminacao` \| `portabilidade` \| `info_compartilhamento` \| `revogacao_consentimento` |
| `detalhes_requisicao` | string | sim | Texto da requisicao (pseudonimizado) |
| `data_solicitacao_iso` | string | sim | Data (YYYY-MM-DD) — compoe a business key |
| `envolve_dados_saude` | boolean | sim | Pre-resolvido por worker (escopo da requisicao) |

## Variaveis intermediarias/saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `identidade_confirmada` | boolean | Saida de `operadora.lgpd.verify_identity` |
| `decisao_dsr` | string | `APROVAR_ENVIO` \| `EXECUTAR_E_ENVIAR` \| `NEGAR_FUNDAMENTADO` (User Task humana) |
| `fundamentacao_legal` | string | Obrigatoria se `NEGAR_FUNDAMENTADO` |

## Topicos

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.lgpd_dsr.received` | produz | apos start |
| Kafka | `agents.events.lgpd_dsr.sla_breached` | produz | P15D sem conclusao (event subprocess) |
| Kafka | `agents.events.lgpd_dsr.completed` | produz | fim (payload.desfecho = `atendida` \| `negada_fundamentada` \| `expirada_identidade` \| `identidade_inverificavel`) |
| External task | `operadora.events.publish` | consome | publicador generico |
| External task | `operadora.lgpd.verify_identity` | consome | verificacao de identidade |
| External task | `operadora.lgpd.request_additional_proof` | consome | pedir prova adicional |
| External task | `operadora.lgpd.compile_data_package` | consome | compila pacote/minuta conforme `fluxo` |
| External task | `operadora.lgpd.execute_request` | consome | executa retificacao/eliminacao aprovada |
| External task | `operadora.lgpd.send_response` | consome | envia resposta (ou negativa fundamentada) |
| External task | `operadora.lgpd.notify_sla_risk` | consome | alertas de SLA (interno e legal) |
| Message BPMN | `msg.lgpd.proof_received` | recebe | prova de identidade chegou |

## DMN referenciada

### `lgpd_dsr_routing` (FIRST — DRAFT)
in: `tipo_requisicao: string`, `envolve_dados_saude: boolean`
out: `fluxo: string` (`EXPORTACAO` | `RETIFICACAO` | `ELIMINACAO_AVALIACAO` | `INFORMATIVO`), `grupo_revisor: string` (`dpo` | `juridico-privacidade`), `sla_resposta: string (ISO)`, `sla_alerta: string (ISO)`
Catch-all: tipo desconhecido -> `juridico-privacidade`.

## Papeis humanos

| Grupo | Tarefa |
|---|---|
| `dpo` | `UT_RevisaoDpo` (requisicoes sem dados de saude) |
| `juridico-privacidade` | `UT_RevisaoDpo` (dados de saude, eliminacao, tipos desconhecidos) + alertas de breach |

## SLAs

| Timer | Valor | Tipo | Fonte |
|---|---|---|---|
| Resposta total | P15D do inicio da instancia | event subprocess nao-interruptivo -> evento breach + alerta juridico (caso segue aberto) | LGPD art. 19, II |
| Alerta interno | `${roteamento_dsr.sla_alerta}` (P7D) na User Task | nao-interruptivo -> notify_sla_risk | politica interna — DRAFT |
| Prova de identidade | P10D | event gateway -> fim `expirada_identidade` | politica interna — **DRAFT/verify juridico** |

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_DSR_IDENTITY_UNVERIFIED` | `BE_IdentidadeInverificavel` (boundary em `ST_VerificarIdentidade`, GAP-LGPD-6) | `titular_pseudo_id` ausente/vazio — impossibilidade MECANICA de verificar (NUNCA acusacao automatica — L0 hard `fraud_accusation`). Publica `lgpd_dsr.completed` (desfecho=`identidade_inverificavel`) e termina em `End_IdentidadeInverificavel` (fail-safe, nao adverso); da visibilidade a DPO/juridico-privacidade para revisao manual. Tratamento NUANCED de fraude evidente de identidade permanece pendente para a promocao a FINAL |
| `ERR_DSR_DECISION_INVALID` | `End_ErrDecisaoInvalida` (default de `GW_DecisaoDsr`, GAP-LGPD-4) | `decisao_dsr` ausente/em branco/desconhecida NUNCA libera dados por omissao — incidente tecnico/operacional (fail-closed), NAO uma decisao adversa; exige reabrir `UT_RevisaoDpo` ou corrigir o dado |
| `ERR_DSR_FUNDAMENTACAO_AUSENTE` | `End_ErrFundamentacaoAusente` (default de `GW_GuardFundamentacao`, GAP-LGPD-3) | `NEGAR_FUNDAMENTADO` sem `fundamentacao_legal` nao e enviado — guard HITL-estrutural avaliado pelo engine (ADR-0005); exige reabrir a revisao e preencher a fundamentacao |

## Pendencias para promocao a FINAL

DI; validacao juridica dos prazos internos; matriz de bases legais de retencao por tipo
de dado; fluxo de revogacao de consentimento com efeito imediato (art. 18 §2 — avaliar
caminho dedicado); interacao com SP-OP-CANCEL-001.
