# Recuperação documental e divulgação do trem R6

**Corte:** 2026-09-08 · **base imutável:** `fe91b912811db34a24583fe00805ab9ab945af99` ·
**tip do trem original:** `2db4bd7d` · **candidato recebido:**
`e9689e7dacc0417d5c7fe10d6c520f8df66d33cf` · **estado:** implemented — unverified

Este documento corrige a superfície de evidência do trem sem reclassificar sua aprovação. O
veredito histórico de `VERIFY-TRAIN1.md` continua uma aprovação estática com **ENGINE-PENDING**.
O pacote A/B posterior foi aprovado localmente por outro autor; isso não aprova o trem, não substitui
verificação independente e não constitui prova de engine. Este pacote altera somente documentação,
um comentário de workflow e o ledger; o runtime permanece igual ao candidato recebido.

## 1. Fontes congeladas e regra de leitura

Os relatórios antigos são evidência histórica, não estado corrente. Contagens abaixo foram derivadas
diretamente dos SHAs com `git diff`, e ownership foi calculado pelo parser do próprio repositório
(`parse_codeowners` + `owners_for_path`), cuja regra é **última correspondência vence**.

| Fonte | SHA-256 |
|---|---|
| `PLANS.md` no candidato | `d33f941b1308ed75e340a0a14ae6262096908d74fe1d4df5d90c947b1264fe07` |
| `docs/plan.md` local, Wave 1 | `53215026ed494a928f062627db87aa71ade1cc27bf257be257c957510ec4a6bf` |
| `docs/prompts/execute-maezo-completion-plan.md` local | `eb1dc11018b497e9e96677cdc64602adb8d9490d71a66640f3650ba1539644ac` |
| `REPORT-SECURITY-RECOVERY.md` | `a903570edcb54e2303651f47246753b0ea706f6890bc88767b8c86be29251ee1` |
| histórico `VERIFY-TRAIN1.md` | `9e9b78ff82bacbe00d00d2335ab8b56644677907686950ca8a315256a02343af` |
| histórico `REPORT-INTEGRATION-TRAIN1.md` | `7fd6bb2a7bc4bc3db228b063f157c6cb7faad570e2c9f177695041345649d9f1` |
| snapshot `OWNER-DECISIONS-REGISTER.csv` | `f063856296e80bbb1e44c7e003761805185a755efa78cc291e0ee749ce63c7b7` |
| snapshot `UNLOCK-LEDGER.yaml` | `a401610472c75ed33fd65515d8e23d48aefc3980bced10233469266cb00884ea` |
| ADR-0007 / ADR-0027 / ADR-0033 | `8c4f4c67189d0619ca28170c3accadb75d34afc487a7cf00814dafdbd66d4da7` / `de8cd77894e946cbb73cee7c41c4d0cac0d54577884887d73b44001336625ed0` / `c5a67cb7b2b7d434dfbef41510f234a2b332b397abd89838addf7c5cde9b25bf` |
| `docs/decisions-log.md` | `9d394cde9b940fb2d73de374a7e48fef0dc94f7246fea1a6abdef8a4795bd3c3` |
| `docs/reports/predeploy-findings.json` | `7a99ffb007c66aa93115a81fff4089a386ddd6c55ba6c3a0ef5023f91e106751` |

Os arquivos locais de auditoria ficam em
`docs/audits/maezo-deep-audit/remediation/completion-r6-assessment/` e
`completion-register-reconciliation/snapshots/` no checkout principal. Eles não são instruções de
execução e não foram copiados para esta branch.

## 2. Censo completo dos nove componentes originais

`git diff --shortstat fe91b912..2db4bd7d` retorna **225 arquivos, 8.858 inserções e 1.164
remoções**. A lista de ancestrais e merges abaixo preserva todos os nove componentes; R199 é o sexto
componente do mesmo trem, não um pacote independente posterior.

| # | Componente | Tip do componente | Merge no trem | Situação deste documento |
|---:|---|---|---|---|
| 1 | R114 | `4424748a` | `def7587f` | histórico preservado; verificação corrente pendente |
| 2 | LGPD/topologia | `d781d629` | `2c18af8c` | histórico preservado; A/B posterior separado |
| 3 | evals/jornadas | `58a9af28` | `5f3f8ea0` | histórico preservado; ID novo desambiguado |
| 4 | finanças | `bc8a6db7` | `a4b76d14` | histórico preservado |
| 5 | tooling/fences | `2f158154` | `88d8f936` | histórico preservado; ID novo desambiguado |
| 6 | R199 | `91cdca1d` | `cce24b4a` | componente do trem; sucessor R199 continua rastreável |
| 7 | pequenas decisões | `37d392e7` | `cfc54269` | histórico preservado |
| 8 | docs sweep | `ec96c492` | `bb8ef7a7` | histórico preservado; ID novo desambiguado |
| 9 | webhook/WAMID | `8f0e5f47` | `2db4bd7d` | histórico preservado |

O cabeçalho antigo de cinco componentes/174 arquivos e referências posteriores a oito componentes
ficam classificados como **históricos e obsoletos**. O censo executável do tip é nove componentes e
225 caminhos. Isso corrige a descrição; não reescreve os relatórios antigos.

## 3. Pacote A/B posterior, separado do trem original

`git diff --name-only 2db4bd7d..e9689e7d` retorna exatamente **9 caminhos**:

1. `docs/evidence-ledger.md`
2. `src/maezo/gateway/log_scrubber.py`
3. `src/maezo/platform/observability.py`
4. `src/maezo/platform/webhooks/whatsapp/app.py`
5. `src/maezo/tools/workers/lgpd.py`
6. `tests/integration/processes/test_lgpd_execution_refusal.py`
7. `tests/unit/gateway/test_error_log_scrubber.py`
8. `tests/unit/platform/webhooks/whatsapp/test_error_log_privacy.py`
9. `tests/unit/tools/workers/test_lgpd_refusal_privacy.py`

Por sobreposição, `fe91b912..e9689e7d` tem **230** caminhos, e não 225+9. A aprovação local de A/B
não aprova estes nove componentes como conjunto e não fecha `ENGINE-PENDING`.

## 4. CODEOWNERS recalculado

Para os mesmos 225 caminhos do trem original:

- regras da base `fe91b912` (33 regras): **23 caminhos CODEOWNED**;
- regras do tip `2db4bd7d` (34 regras): **24 caminhos CODEOWNED**;
- aplicar base/tip aos 230 caminhos do candidato mantém **23/24**, pois os nove caminhos A/B não
  acrescentam caminho coberto.

Lista pelas regras do tip, com última regra efetiva:

| Caminho | Regra | Owners listados |
|---|---|---|
| `.github/CODEOWNERS` | `/.github/CODEOWNERS` (L237) | `@rodaquino-OMNI @Omni-Saude/security-team` |
| `.github/workflows/ci.yml` | `/.github/workflows/` (L249) | `@rodaquino-OMNI @Omni-Saude/security-team` |
| `Makefile` | `/Makefile` (L287) | `@rodaquino-OMNI @Omni-Saude/security-team` |
| `deploy/helm/maezo-tenant/templates/_helpers.tpl` | `/deploy/` (L324) | `@rodaquino-OMNI @Omni-Saude/security-team` |
| `deploy/helm/maezo-tenant/templates/deployment-bridge-consent-revocation.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/helm/maezo-tenant/templates/deployment-bridge-netchange.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/helm/maezo-tenant/templates/deployment-bridge.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/helm/maezo-tenant/templates/serviceaccount.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/helm/maezo-tenant/values-amh.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/helm/maezo-tenant/values-staging.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/helm/maezo-tenant/values.yaml` | `/deploy/` (L324) | mesmos owners |
| `deploy/observability/alert-rules.yml` | `/deploy/` (L324) | mesmos owners |
| `deploy/observability/dashboards/worker-runtime.json` | `/deploy/` (L324) | mesmos owners |
| `docs/adr/0040-perspectiva-operadora-contas-recurso.md` | `/docs/adr/` (L65) | `@rodaquino-OMNI` |
| `scripts/ci/check_bpmn_error_allowlist.py` | `/scripts/ci/` (L288) | `@rodaquino-OMNI @Omni-Saude/security-team` |
| `scripts/ci/check_chart_env_reconciliation.py` | `/scripts/ci/` (L288) | mesmos owners |
| `scripts/ci/check_deviation_expiry.py` | `/scripts/ci/` (L288) | mesmos owners |
| `scripts/ci/check_effect_chokepoint_fence.py` | `/scripts/ci/` (L288) | mesmos owners |
| `scripts/ci/check_evidence_ledger.py` | `/scripts/ci/` (L288) | mesmos owners |
| `scripts/ci/check_flip_path_review.py` | `/scripts/ci/` (L288) | mesmos owners |
| `scripts/ci/check_helm_entrypoints.py` | `/scripts/ci/` (L288) | mesmos owners |
| `scripts/ci/check_plans_counts.py` | `/scripts/ci/` (L288) | mesmos owners |
| `spec/policies/autonomy/action-approvals.yaml` | regra exata do arquivo (L128) | `@rodaquino-OMNI @Omni-Saude/security-team` |
| `spec/policies/phi/phi-dispositions-migration.yaml` | `/spec/policies/phi/` (L166) | `@rodaquino-OMNI @Omni-Saude/security-team` |

Esta é uma lista de ownership, não uma lista de aprovações. Nenhuma revisão foi inferida dos owners.

## 5. R-198: duas identidades diferentes

A decisão de dono R-198 escolheu principals de auditoria distintos. O que o componente entregou foi
uma **ServiceAccount Kubernetes distinta** para o template da ponte de rede. ADR-0033 declara que
ServiceAccount/IRSA é identidade de nuvem e não a identidade verificável de aplicação que alimenta
`AuditRecord.agent_id`; a ADR segue **Proposed — não vinculante**. ADR-0007 exige identidade de
serviço verificável e ADR-0027 altera somente o transporte durável da auditoria para Postgres.

A árvore candidata também mostra:

- nenhum módulo `src/maezo/platform/integrations/network_change_bridge`;
- `networkChangeBridge.enabled: false` no default, sem override nos overlays relevantes;
- o template Helm existe, mas permanece **DORMANT**.

Portanto, a segregação de ServiceAccount está entregue, enquanto
`NETBRIDGE-SERVICE-PRINCIPAL-IDENTITY` permanece pendente: antes de habilitar a ponte, deve existir
um daemon real e um principal de aplicação distinto, emitido e verificado no chokepoint de audit.
Nada neste pacote entrega ou simula esses dois itens.

## 6. Integridade do ledger e IDs

A comparação escape-aware da tabela produz:

| Corte | Linhas de dados | IDs únicos | Grupos duplicados | Excedentes por duplicação |
|---|---:|---:|---:|---:|
| `main@fe91b912` | 370 | 283 | 42 | 87 |
| candidato após este append | 409 | 322 | 42 | 87 |

As **501 linhas físicas** do ledger de `main@fe91b912` permanecem byte-idênticas e na mesma ordem.
O ledger já continha duas linhas `AF-06` em main; elas não foram alteradas. Três linhas criadas no
trem e ainda inexistentes em main receberam ID líder único, sem perda do texto original:

| ID líder antigo | ID líder novo | Vínculo preservado |
|---|---|---|
| `11.5` de 2026-09-06 | `R6-JOURNEY-EVAL-EXPANSION` | Evidence registra alias `11.5` |
| `AF-06` de 2026-09-06 | `R6-TOOLING-COUNTS-FENCE` | Evidence registra alias `AF-06` |
| `PERSP-REEMBOLSO-BINDING` de 2026-09-06 | `R6-REEMBOLSO-BINDING-DOC` | Evidence registra o alias completo |

Referências do próprio trem foram propagadas em `docs/review-queue.md`. Duplicações históricas já
presentes em main continuam divulgadas; este pacote não reescreve o passado.

## 7. R-051 e governança observada

O snapshot anterior tinha **31** erros CODEOWNERS. Depois de o dono autorizar e executar a concessão
de acesso, `repo-teams-after.json` mostra `security-team` com `permission=push`, e
`codeowners-errors-after.json` mostra zero erros. O rerun `34131714827`, tentativa 2, terminou
`success` com os jobs `branch-protection-check` e `codeowners-errors-check` verdes no SHA `fe91b912`.
Esses fatos provam resolvibilidade e acesso do token do time.

`security-team.json` mostra `members_count=1`. Logo, **R-051 continua pendente quanto à segunda
pessoa humana**. O comentário do workflow foi corrigido para não usar `errors=0` como prova de
staffing. A ruleset ativa `20775655` exige quatro checks, mas registra
`required_approving_review_count=0` e `require_code_owner_review=false`; a política documental de
owner-review continua sendo requisito separado.

Hashes dos snapshots: `codeowners-errors.json` `15c479...f7b1`;
`codeowners-errors-after.json` `debf84...61f8`; `security-team.json` `21507e...4dd`;
`repo-teams-after.json` `d940d4...ee1`; `governance-rerun-34131714827.json`
`355150...4a9`.

## 8. Superfície humana e limites do pacote

Os runbooks continuam descrevendo Tasklist como a interface disponível neste candidato. A mudança
Tasklist→portal pertence a ADR-0049/DL-0048, pacote separado ainda a aterrissar e verificar. A
referência prospectiva adicionada aos runbooks não duplica a decisão nem afirma que o portal existe.

Permanecem necessários para fechar o trem: verificação independente renovada, execução real-engine
das suítes aplicáveis e reconciliação dos pacotes posteriores no tip final. Nenhuma aprovação de
SME, A/B/C, assinatura, mudança de cerca, política, contrato, ADR, chart ou runtime ocorreu aqui.
