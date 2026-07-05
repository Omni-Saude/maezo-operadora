# Phase 2 Report — "A operadora decide pelo humano onde nega" (ciclo de receita + calendário ANS)

**Status:** Phase 2 **COMPLETA** — as 6 quádruplas SP-OP em main e provadas no engine CIB Seven real (#38), agentes Marina/Gustavo/Lucas, plataforma Wave D, e o **closeout fechado**: D9 merge-engine fiado no harness (#42), D10 catálogo de métricas + emissão (#43), Wave E cross-process seam + sweep no-denial consolidado fail-closed (#44) — todos verdes vs engine real. **DoD D1–D10 MET** (D6 = allowlist #32 permanece humano-gated por design). DoD de staging **bloqueado por acesso AWS** ([issue #16](https://github.com/Omni-Saude/Maezo-Healthcare-Plan/issues/16))
**Data:** 2026-06-13 · **Commit:** `56d1138` · **Plano:** `docs/reports/phase2-plan.md`

## O que SHIPPED em main (evidência = CI) vs em voo

**MERGEADO em main (`1bd2e9a`):**

| Capacidade | Evidência |
|---|---|
| **As 6 quádruplas SP-OP** — CONTAS (pathfinder, #30) + RECURSO/NIP/ANS-SUBMIT/CANCEL/REEMBOLSO (#38): BPMN + DMN + workers + suítes de integração vs engine real | **job 'integration tests (real engine)' verde 18m54s no #38**; cada negativa-like com terminal humano-gated + guard `ERR_*_NOT_HUMAN`; no-denial estrutural por body (DMN sem coluna adversa; catch-all→humano); validate-artifacts limpo |
| **3 agentes** Marina (PHI), Gustavo + Lucas (general) (PRs #33, #39) | agent.yaml válido; grafos clonados de Rafael; `false_denial_rate ==0` asserido; golden datasets gateiam grafo/prompt (ADR-0009); 43 unit + evals verdes por agente; sem import cruzado entre agentes |
| **Wave D — tenant factory por-agente** (PR #35): Helm `maezo-tenant` (Deployment/SA/NetworkPolicy/ConfigMap por agente) + `scripts/provision_tenant.py` | `helm template`/dry-run verdes; IRSA por agente; label `maezo.io/phi-zone` ligada à NetworkPolicy; gustavo/lucas `enabled:false` até merge dos agent.yaml |
| **Cofre de credenciais load-bearing** (PRs #27, #36): `gateway/credential_vault.py` + fiação em `platform/console/app.py` | `complete_task` obtém a credencial de assinatura da `HumanCredentialPartition` ANTES de formalizar a negativa; falha do cofre → 403 (sem material vazado); proveniência da credencial entra no `decision_basis` da auditoria (ADR-0005 §7 + ADR-0007); 90 unit/arch verdes |
| **Worker real `operadora.auth.analyze_request`** (PR #37): fecha o gap de produção da `ST_PrepararDossie` + **WorkerHarness output-vars** | dossiê do Rafael IN-PROCESS reusando `RafaelGraph` (nunca duplica); harness completa a task EXATAMENTE UMA VEZ a partir das variáveis retornadas (sem double-complete); `decisao_cobertura` sempre None (L0 hard); degradação → dossiê mínimo + rota humana |
| **6 contract sheets + test-specs + registro de tópicos** (PR #28); **ports CNAB 240/400 + enums glosa** (PR #29) | no-denial review PASS; 88 testes de port verdes contra fixtures próprios (nenhum import do reference, ADR-0011); todo conteúdo regulatório DRAFT (review-queue) |
| **W0.4 — motor de merge Agent-Definition L0–L3** (PR #31): `platform/tenancy/agent_def_merge.py` | overlay só RESTRINGE (tools/autonomy_actions subconjunto; zona só endurece; ações hard inrebaixáveis; `version_hash` SHA-256 idêntico ao harness); testes do overlay verdes |
| **Validador BPMN endurecido** (PR #34): child-ordering generalizado para sequência XSD + DI | gate `validate-artifacts` agora pega ordenação em todo flow element |

**ABERTO por design (humano-gated):**

- **W0.1 — allowlist de process_key** (**PR #32, ABERTO, humano-gated CODEOWNERS**): adiciona os 6 keys Phase 2 a `KNOWN_PROCESS_KEYS`. **Consequência crítica: nenhum processo Phase 2 está start-enabled em main — nem o CONTAS já mergeado.** Em `1bd2e9a`, `KNOWN_PROCESS_KEYS` contém apenas os 3 keys de Phase 0/1; iniciar qualquer SP-OP Phase 2 falha fail-closed até #32 mergear (gate humano por design, ADR-0016).

## O valor da VERIFICAÇÃO ADVERSARIAL (cada achado passou nos testes verdes do builder)

A doutrina de revisão por painel de três lentes (engine-deployable / no-denial-sound / security) pegou seis defeitos que os **testes verdes do próprio autor não pegaram** — porque eram defeitos de *garantia*, não de comportamento no caminho feliz:

1. **BREACH CRÍTICO de rede PHI (`0.0.0.0/0`)** — a NetworkPolicy de egress PHI permitia a internet inteira (no-op de isolamento). FIX 1: cada endpoint exige um CIDR concreto BR-resident e o render **ABORTA** se faltar ou for `0.0.0.0/0`/`::/0` (`_helpers.tpl:requirePhiCidr`/`requireGeneralCidr`).
2. **Vazamento de credencial cross-tenant** — um médico-auditor do tenant A obtinha a credencial de assinatura de B. Guard de isolamento de tenant adicionado ANTES de qualquer lookup (`credential_vault.py:267`); recusa auditada, zero material alcançado.
3. **Bug de double-complete no worker** — handler completava a external task e o harness completava de novo (o 2º falha fora do lock). Contrato refeito: o handler RETORNA variáveis e o harness completa **EXATAMENTE UMA VEZ** (`workers/harness.py`); handlers nunca tocam `transport.complete`.
4. **Dois routers de agente fail-open** — valor de DMN desconhecido prosseguia em vez de escalar. Gustavo e Lucas agora usam **allowlist FECHADA**: qualquer valor fora dela → rota humana conservadora (fail-safe fechado); `dmn_indisponivel` → humano, nunca auto.
5. **Inconsistências de hint em CANCEL** — 2 `<bpmn:incoming>` divergentes corrigidos pós-review (PR #38).
6. (+ achados de Wave C herdados, já fechados e mutation-proven: value-leak por vacuous-pass; `is_human` não-pinado.)

Defesa-em-profundidade adicional do mesmo painel: **FIX 3** — o factory de tenant faz cross-validation da `security_zone` do `values.yaml` contra a `security_zone` da Agent Definition mergeada; um values editado à mão que rebaixe um agente PHI para `general` **aborta o render** em vez de produzir uma policy mis-zonada (`_helpers.tpl:validateAgents`).

### O que SÓ o engine real pegou (4 fixes de runtime ENGINE-16004 antes do merge de #38)

A revisão adversarial (estática) e o `validate-artifacts` passaram, mas a primeira execução de integração de #38 falhou no engine real com "User Task X não apareceu". O fix de IDs-XML-duplicados (#40, classe ENGINE-22004) **desmascarou** uma família de incidentes `ENGINE-16004 "Cannot resolve identifier"` no agendamento dos boundary-timers das User Tasks humanas — invisível à análise estática, ao `--collect-only` (sem engine local) e à revisão. Quatro causas distintas, mesma superfície de falha, cada uma corrigida espelhando os bodies que JÁ passavam (CONTAS/CANCEL):

1. **ANS-SUBMIT** — timers `timeDate` apontando para datas absolutas PASSADAS do `ans_calendar` → o timer interruptivo disparava no instante da criação e cancelava a UT. Fix: novo `ans_sla.dmn` (durações ISO-8601 relativas) + `BRT_AnsSla resultVariable="sla"` antes da UT + `timeDuration`.
2. **NIP** — uma DMN (`nip_routing`/`nip_sla`) consumia o OUTPUT de outra DMN (`classificacao`) como identificador bare → ENGINE-16004 em `BRT_Roteamento`, antes de `sla` ser setado. Fix: promover os campos do result-map a variáveis de topo via `camunda:inputOutput outputParameters`.
3. **RECURSO** — `sla` setado em só 1 de 3 caminhos para a UT + `desfecho_humano`/`loop_counter` referenciados sem inicialização (o engine lança até dentro de `==null`/ternário). Fix: nó de convergência SLA + init das vars via `outputParameter` no 1º task de cada caminho.
4. **REEMBOLSO** — `sla` bypassado no caminho admissibility-direct. Fix: relocar `BRT_SlaAnalise` para o ponto de convergência antes de `ST_PrepararDossie` (espelha CONTAS).

**Lição (agora em `ci_notes` do HANDOFF):** todo identificador JUEL que um boundary-timer/gateway referencia precisa estar SETADO em todo caminho que chega nele — a DMN de SLA roda como `businessRuleTask resultVariable="sla"` no nó de convergência ANTES da User Task humana. A garantia estrutural no-denial (ADR-0018) foi preservada nos 4: só roteamento + inicialização de variável mudaram; nenhuma DMN ganhou saída adversa. Revisão adversarial pós-fix: `all_sla_defined: true`, `all_identifiers_resolvable: true`; CI de engine real verde (18m54s).

## Disciplina de pathfinder

**CONTAS-001 provou o padrão BPMN/DMN no engine REAL antes dos clones.** O pathfinder (PR #30) materializou a quádrupla completa — incluindo um desvio documentado inline (`hitPolicy="FIRST"` em catch-alls, porque `UNIQUE` colide em runtime) — e só então os outros 5 corpos clonaram contra seus contratos congelados. Resultado: zero retrabalho de shape nos 5 (PR #38), todos engine-deployable de primeira.

## A OUTAGE de meio de sessão + recuperação limpa (zero trabalho perdido)

Houve uma interrupção do swarm a meio da execução. A recuperação foi limpa graças à doutrina single-source-of-state: HANDOFF.yaml + as branches por-workstream sobreviveram, cada workstream era arquivo-disjunto (um dono por path, §7.1), e o ponto de sincronização era a contract sheet (§4-bis-F). **Zero trabalho perdido** — os workstreams retomaram contra o mesmo estado.

## DRAFT / review-queue

Todo conteúdo regulatório/clínico das 6 quádruplas e ports é **DRAFT** (`docs/review-queue.md`, seção "Phase 2"): 6 contract sheets + as 14 DMN dos corpos (SLAs em dias-úteis→ISO, RN 305/424/388/593/259), `glosa.py` (26 GlosaReasonCode + mapa recorrível) e os offsets CNAB (validar vs arquivos reais do banco). Nada DRAFT vai a tenant vivo; `hard:true` permanece CI-enforced. Candidate-groups em TODOS os contratos são PROPOSTOS — confirmar contra a taxonomia da operadora antes de congelar.

## AWS-blocked (issue #16) vs CI-verificado-agora

**CI-verificado agora:** todas as quádruplas BPMN/DMN/contrato/test-spec; invariantes no-denial vs engine real (consultando history do engine); 3 agentes + grafos + golden evals; delegações A2A; ports reautorados; merge engine L0–L3; Helm templating + `provision_tenant.py` (render/dry-run); separação de credenciais.
**AWS-blocked (não bloqueia o acima):** `terraform apply` em staging; creds Tasy Oracle (CDC real; simulador cobre CI); WABA token (Gustavo/Lucas); LLM keys + endpoint PHI-zone BR (bloqueia Marina, que é PHI); conectividade real ANS/banco; OIDC/Cognito do console; sign-off humano dos itens DRAFT.

## Riscos atualizados + o que REMAINS

1. **Wave E não feita** — suíte de invariantes cross-process (CONTAS→RECURSO; NIP→ANS-SUBMIT handoff; varredura no-denial de TODAS as DMNs em conjunto) ainda por escrever; cada processo tem invariante própria verde, mas a cross-process não.
2. **Deltas de observabilidade (WD.3)** pendentes — métrica de desfecho de submissão periódica (ANS-SUBMIT), countdown de prazo NIP (deadline-risk), métrica de job de calendário; `runtime/metrics.py` ainda não estendido.
3. **W0.1 allowlist (#32) humano-gated desabilita o start de TODO processo Phase 2 em main** — incluindo CONTAS já mergeado. É o gate humano por design (ADR-0016/CODEOWNERS); precisa do PR humano para qualquer start Phase 2.
4. **Cofre não fiado no runtime VIVO do agente** — o primitivo + a fiação no lado humano (console) estão merged e load-bearing; falta entregar a `AgentCredentialView` (e só ela) na construção do runtime do agente.
5. **WorkerHarness output-vars não adotado por todos os workers** — novo no harness e usado por `auth_analyze`/`contas`; os workers legados de Phase 0/1 ainda no contrato antigo (return None/lista). Migração incremental.
6. **Egress FQDN não existe** — a enforcement é só por CIDR IP-pinned. NetworkPolicy `ipBlock` não expressa allowlist por hostname; os próprios helpers documentam que FQDN real exige egress proxy / Cilium FQDN policy. **Não há enforcement de rede por FQDN/hostname em lugar nenhum.**
7. **Merge engine é skeleton** — `agent_def_merge.py` é puro e testado mas **não plugado** em `runtime/harness.py` (prereq real do provisionamento ≤1 dia).
8. **Prazos ANS NIP são HARD** com exposição a sanção (RN 388, dias-úteis→ISO) — confirmar com regulatório/jurídico antes de qualquer timer em produção.
9. **Versão do engine** — ADRs citam 2.1.3; imagem pública 2.1.0 (DL-0006). Revalidar no upgrade.

## ADRs novos a registrar

Dois ADRs documentam decisões já materializadas em código nesta fase (a série terminava em ADR-0016). Linhas adicionadas em `docs/adr/README.md`:

- `| 0017 | Enforcement de rede do egress da Zona PHI (CIDRs IP-fixados, fail-closed; render aborta em 0.0.0.0/0; FQDN via egress proxy/Cilium é FUTURO, não entregue) | Accepted |`
- `| 0018 | Padrão estrutural no-denial: replicação vinculante de 5 partes para todo SP-OP negativa-like | Accepted |`

## Demo (roteiro)

1. `make validate-artifacts` — verde nas 6 quádruplas (gate de shape engine-deployable).
2. Invariante no-denial CONTAS vs engine real: `tests/integration/processes/test_sp_op_contas_001.py` — varre `glosa_triage` e prova via history do engine que nenhum caminho automatizado aceita glosa.
3. Cofre load-bearing: tentar `complete_task` como agente/cross-tenant → 403 auditado, zero material; humano médico-auditor do mesmo tenant → assina e formaliza.
4. Worker do dossiê: `operadora.auth.analyze_request` move `ST_PrepararDossie` → `UT_AnaliseMedicoAuditor` com `decisao_cobertura=None`.
5. `helm template deploy/helm/maezo-tenant -f values-amh.yaml` — render por-agente com NetworkPolicy de zona; provar que um endpoint sem CIDR (ou `0.0.0.0/0`) ABORTA o render.
6. (MERGED, #38) os 5 corpos restantes (RECURSO/NIP/ANS-SUBMIT/CANCEL/REEMBOLSO) rodam a mesma prova de invariante vs engine real — verde 18m54s em CI.

Staging = mesmo roteiro após `terraform apply` (bloqueado, issue #16).

## Estado do orquestrador

Estado autoritativo vivo em `docs/handoffs/HANDOFF.yaml` (atualizado para `last_green_commit: 1bd2e9a`; todos os PRs #28–#41 incl. #38 em `phase2_merged`). Closeout em andamento (ramos próprios): **WS-E1** Wave E cross-process + sweep no-denial consolidado fail-closed; **WS-E2** D9 fiar merge-engine no harness + hash; **WS-E3** D10 estender `runtime/metrics.py` + emissão nos workers. Gaps de runtime-wiring (consumidor do handoff cross-process; assembly de produção do DelegationDispatcher) registrados em `phase2_known_runtime_gaps` — não são DoD Phase 2 (overlap AWS-blocked), endereçados em Phase 3/deploy. #32 (allowlist) permanece humano-gated.
