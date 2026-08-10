# Review-before-launch — extensão #89–#221

> Complementa [`docs/Tarefas_Pendentes.md §1.5`](../Tarefas_Pendentes.md#15--revisão-de-código-pré-lançamento-lista-review-before-launch)
> e a lista original de 2026-06-14 (#65–#88), preservada em
> [`docs/reports/autonomous-completion-report.md`](autonomous-completion-report.md) §5. Gerado
> 2026-08-10, base `main`@`091abd8`.

## Resultado

- **63 de 122** PRs mergeados no intervalo #89–#221 tocam invariante verificado (§ Seção 3 do
  relatório de 2026-06-14) ou caminho CODEOWNERS — são **review-relevant** para o go-live.
- **11 números de PR** no intervalo **nunca foram mergeados** (fechados sem merge ou nunca
  existiram): #145, #150, #152, #154, #161, #163, #164, #175, #182, #183, #184.
- Os **59 PRs restantes** não são review-relevant (só docs, só teste, ou dependências —
  `dependabot`/`chore(deps)`/`chore(ci)`).

## Método

1. `gh pr list --state merged` no intervalo #89–#221, título + arquivos tocados
   (`gh pr view N --json files`).
2. Classificação como *review-relevant* se o PR toca:
   - superfícies CODEOWNERS: `src/maezo/policies/`, `spec/policies/` (todos os subdirs),
     `spec/processes/dmn/` (candidatos de ratificação e tabelas vivas), `docs/adr/`,
     `src/maezo/tools/process_allowlist.py` e allowlists relacionadas,
     `config/artifact_signoff.yaml`; **ou**
   - categorias de trabalho review-relevant além de CODEOWNERS: `src/maezo/gateway/` (auditoria,
     PHI, gates de execução de ação), `src/maezo/platform/` (lifecycle, privacy, erasure,
     retenção, migrations), `src/maezo/tools/workers/` (caminhos de dinheiro — pagto/reembolso —,
     tetos de auth, tratamento de PHI), `spec/processes/bpmn` (diagramas de processo).
3. **Spot-check independente**: 5 linhas sorteadas da tabela abaixo (#93, #132, #148, #171, #209)
   re-verificadas via `gh pr view N --json title,files` nesta sessão — título e arquivos batem
   exatamente com a tabela em **5/5**. Os 2 números de exemplo do critério "nunca mergeado"
   (#175, #183) também foram re-verificados via `gh pr view N --json state` — ambos `CLOSED`
   (não `MERGED`) — **2/2**.

## Tabela completa (ordem ascendente)

| PR | Título | Superfícies de invariante tocadas |
|---|---|---|
| #91 | feat(audit): emit_once idempotent emission + audit_emit_dedup migration (T-A) [T1.10] | src/maezo/gateway; src/maezo/platform |
| #92 | fix(dmn-transport): decode Json-typed DMN result variables [T1.5] | src/maezo/tools/workers |
| #93 | fix(inadimplencia): implement anti-dupla-terminação query (GAP-INAD-1) + missing dossier/sla workers [T3.1] | src/maezo/tools/workers |
| #96 | feat(audit): worker-daemon fail-closed sink + readiness gate; activate GAP-INAD-1 engine seam + CANCEL-001 handoff (T-D) [T1.10] | src/maezo/gateway; src/maezo/tools/workers |
| #97 | feat(audit): harness emit-before-complete + PHI-safe decision_basis + purity arch-test (T-C) [T1.10] | src/maezo/tools/workers |
| #98 | feat(audit): provenance+sink fence at start_process_idempotent chokepoint (T-C2) [T1.10] | src/maezo/platform |
| #101 | feat(audit): wave integration — live-validated fail-closed audit-emit unit [T1.10] | src/maezo/gateway; src/maezo/platform; src/maezo/tools/workers |
| #102 | fix(dmn-transport): type list/dict as Json in _to_camunda_vars [T1.5] | src/maezo/tools/workers |
| #103 | feat(workers): ADR-0030 Tier-0 boundary-proof gate + production bpmn_error_allowlist [T3.1] | src/maezo/tools/workers |
| #109 | feat(ans-submit): AnsGatewayTransport triple — remove fabricated ANS protocol [T2.6] | src/maezo/tools/workers |
| #110 | fix(recurso): parse valor_glosa_aceito monetary String before <=0 guard [t3.1-a2] | src/maezo/tools/workers |
| #111 | fix(dmn-input): coerce-or-drop fraude numeric scoring inputs + drop tuss_codes footgun [t1.5] | src/maezo/tools/workers |
| #113 | fix(lgpd): fail-closed DSR identity gate + GAP-LGPD-6 raise + #55 R-B worker [t2.8] | src/maezo/tools/workers |
| #123 | fix(recurso): auditor ACEITAR_GLOSA desistência channel + stale donor-shape test adaptation [t3.1] | src/maezo/tools/workers |
| #125 | feat(lgpd): #55 R-F send_response + R-G notify_sla_risk handlers; delete dead AssessRequestWorker [t2.8] | src/maezo/tools/workers |
| #126 | feat(programa): stratify_risk fail-closed worker — un-stall every consented enrollment [t2.5] | src/maezo/tools/workers |
| #128 | feat(recurso): batch-2 — 5 missing-topic workers + modeled ERR_RECURSO_INVALID_GLOSA boundary [t3.1] | src/maezo/tools/workers |
| #129 | feat(reembolso): family reconciliation — 3 workers built, 2 dead topics deleted [t2.5] | src/maezo/tools/workers |
| #130 | feat(cred): family reconciliation — 3 workers built (register_credenciamento clerical per BPMN) [t2.5] | src/maezo/tools/workers |
| #131 | feat(audit): T-E audited refusal — PHI-safe audit event on every guard/denial refusal [t1.10] | src/maezo/tools/workers |
| #132 | feat(ans-submit): real XSD/TISS validation seam, fail-closed — two-regime test model [t2.6-2] | src/maezo/tools/workers |
| #133 | feat(p2b): round-2 sweep — pagto/adequacao/fraude workers + escalation cleanup [t2.5] | src/maezo/tools/workers |
| #135 | feat(bpmn): cancel+auth pended publish tasks — contract obligations emitted [t3.1] | spec/processes/bpmn |
| #136 | feat(nip): notify_deadline_risk worker + 4 dead-topic deletions [t2.5] | src/maezo/tools/workers |
| #138 | feat(bpmn): recurso pended publish task — contract obligation emitted [t3.1] | spec/processes/bpmn |
| #139 | feat(bpmn): reembolso.pended + inadimplencia.notified publish tasks + regression tests [t3.1] | spec/processes/bpmn |
| #140 | feat(bpmn): nip deadline_risk alerts converted to generic publisher [t3.1] | spec/processes/bpmn; src/maezo/tools/workers |
| #143 | chore(bpmn): normalize CONTAS-001 + PROGRAMA-001 diagram layout (semantic layer proven identical) | spec/processes/bpmn |
| #148 | feat(bridge): T2.6-7 NIP/cron->SUBMIT triggers via fenced start [t2.6-7] | src/maezo/platform |
| #153 | fix(guards): close bare-truthiness input + truthy-approval-flag classes across 15 adverse guards [t3.1] | src/maezo/tools/workers |
| #157 | feat(bridge): EB-3 wiring + EB-4 CONTAS→RECURSO→FRAUDE live + T3.3 A3 negative-cert [t2.6-eb3][t2.6-eb4][t3.3] | src/maezo/platform; src/maezo/tools/workers |
| #159 | fix(t3.4): remediation wave — erasure honesty, A2A fail-closed + T-F terminal audit, schema/redaction/fence hygiene [t3.4][t3.4-f3][t3.4-f2][t3.4-f4f5f1] | src/maezo/platform; src/maezo/tools/workers |
| #165 | feat(t4): platform completion — checkpoint persistence, bridge armed + CONTAS→FRAUDE live, Kafka producer leg, pytest 9 [t4-checkpoint][t4-bridge-arming][t4-kafka-producer][t4-pytest9] | spec/processes/bpmn; src/maezo/platform; src/maezo/tools/workers |
| #166 | fix(t5): completion wave — DSN prod fix, cred/ANS/nip worker correctness, DL-0033/0034, L2 descope, deploy footgun [t4b-dispatch][t5-workers-f1][t5-workers-f2][t5-l2][t5-deploy-hygiene] | src/maezo/platform; src/maezo/tools/workers |
| #167 | fix(phi): key the pseudonymizer with HMAC-SHA256, fail-closed in prod [t6-hmac] | src/maezo/gateway; src/maezo/platform |
| #170 | T8 escalation notify boundary | src/maezo/tools/workers |
| #171 | fix(t8/t9): audit-fix wave — escalation notify fail-closed, keyed PHI conversation id, NIP handoff armed, BK parity, token metering | spec/processes/bpmn; src/maezo/gateway; src/maezo/platform; src/maezo/tools/workers |
| #177 | Tier-2 wave A: token-metering correlation ids, ADR ratification, retention-matrix fail-closed seam | spec/policies; src/maezo/platform |
| #178 | Tier-2 wave B: notification delivery integrity + real A2A dossier delegation | src/maezo/platform; src/maezo/tools/workers |
| #181 | Item 9 wave-2: pagto bucket-1 (12/12) + ADR-0030 guard migration (nip/programa) | src/maezo/tools/workers |
| #185 | Item 9 waves 3-7: 31 strict-xfail flips live-proven + gatekept src/spec fixes (dossier A2A, Class-C, event wiring, money plumbing, auth model) | spec/processes/bpmn; src/maezo/tools/workers |
| #192 | MZO-050-prep: registry de tópicos passa a admitir os nomes AMH pinados (4 segmentos + hífen) | src/maezo/platform |
| #193 | chore(governance): registra o programa AMH-compat no PLANS.md (§0.6) + corrige comentário obsoleto em audit.py | src/maezo/gateway |
| #196 | Item-9 auth Class-A: 6 xfails re-expressed engine-side + 2 live-proven defects fixed + GAP-AUTH-4 recorded | spec/processes/bpmn; src/maezo/tools/workers |
| #197 | Ans submit nack assemble | spec/processes/bpmn; src/maezo/tools/workers |
| #198 | Auth auto criteria gate | spec/processes/bpmn; spec/processes/dmn; src/maezo/tools/workers |
| #199 | Cred substituicao fact | src/maezo/tools/workers |
| #200 | Adequacao fact preservation | spec/processes/bpmn; src/maezo/tools/workers |
| #204 | RN 259: tabela adequacao_gap corrigida como SHADOW candidate + telemetria de divergência (dark build) | spec/processes/dmn; src/maezo/tools/workers |
| #205 | reembolso: honestidade de proveniência no cálculo (fix MAJOR M-2 — audit-trail + fail-closed) | src/maezo/tools/workers |
| #207 | Varredura de referências obsoletas (DL-0042/GAP-AUTH-4) + backfill de 3 linhas do evidence-ledger | src/maezo/tools/workers |
| #208 | auth: criterios_nao_cobertos passa a ser ENFORCED — ratificar contratual sem fonte de rede não abre auto-aprovação (fix M-3) | spec/processes/dmn; src/maezo/tools/workers |
| #209 | pagto: guard de dinheiro fail-closed + lower bound no CeilingResolver (fix BLOCKER B-1 — classe zero-value-payment) | src/maezo/tools/workers |
| #210 | PHI em business keys: fix B-2 (guard dual-form) + remediação DL-0043 leg(c) atrás de flag ratificável (dark build) | spec/policies; src/maezo/platform; src/maezo/tools/workers |
| #211 | MZO-040: ActionExecutionGateway em SHADOW no chokepoint universal — aprovações Médica/ANS/Security viram DADO (dark build) | spec/policies; src/maezo/gateway; src/maezo/tools/workers |
| #212 | Idempotência de start de processo: fecha a classe do pagamento duplicado (fix BLOCKER B-3 + M-8 + M-9) | src/maezo/gateway; src/maezo/platform; src/maezo/tools/workers |
| #213 | MZO-060: inbox durável AMH (migração 0007 + repositório + packet DBA) — dark build, DBA revisa em vez de autorar | spec/policies; src/maezo/platform |
| #214 | Bundle de minors da auditoria: guard de centavos AUTH unificado, competência ANS corrigida, strip de business key, consent_revision negativo, citações | src/maezo/gateway; src/maezo/tools/workers |
| #215 | adequacao M-1: fail-safe fecha o ponto cego de tipo_carater em branco/desconhecido (leg de runtime do achado do W3) | spec/processes/dmn; src/maezo/tools/workers |
| #216 | ADR-0029 dark build: esqueleto de erasure LGPD por camada + plano de revisão do DPO (fail-closed, inerte) | spec/policies; src/maezo/platform |
| #217 | Shadows DMN M-4/5/6/7: candidatos ratificáveis para as 4 tabelas irmãs com a mesma classe de inversão do adequacao_gap | spec/processes/dmn |
| #218 | TISS-XSD: seam de pin de schema ratificável (dark build, inerte) — o validador lxml já existe; o gate é a ratificação | spec/policies; src/maezo/tools/workers |
| #221 | Binding de digest de ratificação para os 6 candidatos DMN: tabela_viva {path, sha256} — uma ratificação nunca sobrevive em silêncio à edição da tabela que revisou | spec/processes/dmn; src/maezo/tools/workers |

## Notas

- #219 (close-out) e #220 (docstring cleanup) não constam na tabela por serem **docs-only**
  (evidence-ledger backfill / docstring) — não tocam invariante nem CODEOWNERS na classificação
  acima; ambos ficam listados no ledger em `docs/evidence-ledger.md`.
- Os 11 números nunca mergeados foram fechados sem merge (confirmado `state: CLOSED` via
  `gh pr view`) — não representam trabalho perdido, apenas numeração de PR consumida e descartada
  (rebase/superseded/duplicata).
