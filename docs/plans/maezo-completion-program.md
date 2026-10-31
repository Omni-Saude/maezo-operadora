# Plano — Programa de Completude Maezo (clientes, vendors, providers, equipe)

**Status:** **FECHADO (D-29 GO, 2026-10-29)** — anteriormente **ATIVO** (retomada registrada 2026-10-23, D-01, dono via sessão interativa; ver §Decisões). [Nota datada 2026-10-29 — fechamento pelo dono: **D-29 GO**, decisão tomada por seleção clicada em sessão do orquestrador W8 (proveniência transcrita nos adendos: "YOU ARE AUTHORIZED TO EXECUTE IN MY NAME THE PENDING OWNER ACTS… multiple choices clickable options"); supercessão formal v3→v4 (D-11) **EFETIVADA**; o programa encerra e o backlog ROLL (35 = 22+11+2) vira fila pós-programa; registros nos adendos append-only `W8-D29-SIGNATURE-PACKAGE-v1.md` (sha256 `a3e6b875…`) e `MASTER-PLAN-ANNEX.md` (sha256 `d41b99fc…`) do bundle `docs/audits/JOURNEYS-COMPLETENESS-2026-10/` (gitignored; shas conferidos por cp+sha nesta sessão de registro); ver bloco §Decisões D-53 abaixo; os prazos de verdade externa (OP21 1º/1/2027 · fecho C2 2027-01-26 · muro 2027-02-09) seguem correndo por natureza própria, independentes do programa]. Todas as ondas despachadas conforme seus gates. **Data:** 23/10/2026 (`main` em `d21aed72`; auditoria-base `docs/audits/JOURNEYS-COMPLETENESS-2026-10/`, gate cego Tier-2/3 fechado). **Autor:** orquestrador (agente), a pedido do dono. **Este documento não aprova nada**: separa quem GERA de quem APROVA (§HDR) e todo bloqueio de aprovação/ratificação permanece com humanos nomeados.

Regra de leitura: toda afirmação de fato traz `arquivo:linha`, finding `F-*` ou PR# — base: bundle de auditoria acima (INVENTORY A/A-V4/B/C, TRACEABILITY, FINDINGS, REGULATORY-SWEEP, COMPARISON-V3-V4, VERIFICATION-TIER2). O que não foi executado está **NÃO VERIFICADO**.

Decisões do dono (2026-10-23): plano de efeitos **completa em shadow** (flips = onda ROLL pós-completude); trilha de compra = **híbrida** (autoridade comercial dentro da maezo via PR #907; portal externo `omni_sales_platform` consome contrato compartilhado congelado); este plano = documento rastreado + anexo de auditoria.

---

## §0 Protocolo de estado e retomada

Cabeçalho de estado do programa: `state: PLANO` → `ATIVO` (após D-01) → `PAUSADO` (a qualquer momento). Nada marcado `engineering` inicia sem a diretiva RESUME do dono registrada no COORDINATION.md e espelhada aqui; somente itens `record-repair` podem rodar pré-resume, e só se o dono dirigir. Regras de pausa/retomada: todo item é tamanho-PR e mergeável isolado; estado vive só em artefatos rastreados (este doc, verdict files, COORDINATION); todo gate de onda re-executável de checkout limpo; em cada re-pausa, snapshot de quiescência (lição F-M14).

**Cercas permanentes:** (1) `docs/processes/New_BPMN/*_v3.bpmn` intocáveis (WIP do orquestrador paralelo; linhagem = **v4** em `Adapted_BPMN/`, rastreado); CA_1 exige broker via COORDINATION. (2) As 2 regiões de conflito do PLANS.md (~`:1248-1252` e `:1314-6178`) jamais resolvidas (F-P07). (3) Cadeia de merge da casa: autor → revisão adversarial → reparo → delta PASS → squash pinado → byte-check 0-diff. (4) Agentes jamais preenchem blocos de aprovação/ratificação (ADR-0007).

**Prazos (verdade externa):** 1º/1/2027 escalonamento OP21 (RN 489/2022, art. 23-A (redação dada pela RN 659/2025), multa-base R$ 506.250) · 2027-01-26 fim da janela de evidências C2 · 2027-02-09 muro shadow + revisão C2 (renovado pelo dono 2026-09-20, PR fix/d7-exception-renewal-2027; F-P04 emendado GOV-0.2). O caminho crítico corre por **humanos**: ativação da security-team e pareceres jurídicos são os dois postes longos — ambos pré-estacionados na W0.

```
W0 GOV ─┬→ W1 REG (jurídico-gated; deadline)   ─┐
        ├→ W2 CORP (corpus v4; owner-gated)      ├→ W6 DISC → W8 CLOSE
        ├→ W3 BRDG (cadeia #907; security-gated) ┤
        ├→ W4 PROV (cauda N2; independente)      ┘
        ├→ W5 CLNT (plano de capacidades, shadow)
        └→ W7 HARD (contínuo)
HDR (registro de decisões humanas) gateia W1/W2/W3/W5/W6
```

Classes de esforço: **S** dispatch único · **M** multi-dispatch + 1 gate cego Tier-2 · **L** multi-onda.

## W0 GOV — governança e verdade de registro (S/M; parte pré-resume)

| ID | Item | Fecha | Esforço | Status 23/10 |
|---|---|---|---|---|
| GOV-0.1 | Protocolo de retomada (header de estado, registro no COORDINATION, re-verificação das cercas: shas das regiões de conflito, tip da main, ruleset 20775655) | F-P03 | S | aguarda D-01 |
| GOV-0.2 | **Emenda F-P04**: 2026-11-11 → renovado 2027-02-09; verificar linhas de dados do gate de expiração | F-P04 | S | **EXECUTADO** (FINDINGS emendado) |
| GOV-0.3 | IMPLEMENTATION-LOG: `MERGED` na tabela global `:12` (VW1-P0-fences `fc061633`) e na seção VW1 `:35` (P4 OP16 `26b818fb`); linha GP5–GP10 supereditada c/ ratificação 2026-10-09 | F-E07 | S | **EXECUTADO** (live + snapshot anotado) |
| GOV-0.4 | COORDINATION.md dedupe C001–C008 | F-E08 | S | **DEFERIDO**: arquivo é WIP do orquestrador paralelo — a cerca §0(1) overrides; executar só via broker (junto c/ D-30) |
| GOV-0.5 | Label "RN 501/2024" → "RN 501/2022" onde persistir | F-E11 | S | **EXECUTADO** (grep 0 hits pós-fix em artefatos próprios) |
| GOV-0.6 | Nota de proveniência: commits provider T1.7/Q2/C1 sem token `(#NNN)` | F-E13 | S | **EXECUTADO** (nota rastreada `docs/compliance/PROVIDER-COMMITS-PROVENANCE.md` — 6 commits núcleo F-E13 + 7 satélites mapeados aos PRs de aterrissagem (recorte delimitado, não exaustivo — famílias C1-checkpoint/Q2-T1.4b/T1.5/T1.11-AUTH/H5 do mesmo intervalo seguem sem nota; extensão pendente) #479/#481/#487/#491/#505/#520/#538/#540/#543, todos MERGED; ancestralidade exit=0 ×13; histórico intacto) |
| GOV-0.7 | Cadeia PR646 (PL-01…PL-05 `:1227-1231`): executar ou deferir por item | F-P05 (parte) | M | **EXECUTADO** (disposição item-a-item `docs/compliance/PR646-CHAIN-DISPOSITION.md` — PL-01..04 apontamento ao merge #646, squash `05e9e9f6` ANCESTOR exit=0, PR state MERGED 2026-10-06; PL-05(a) conferir-main exit=0, PL-05(b) DEFERIDO → teardown frota Codex, owner) |
| GOV-0.8 | **GAP-REGISTER citação pendente**: `docs/Tarefas_Pendentes.md:1068` cita `docs/audits/maezo-deep-audit/remediation/GAP-REGISTER.yaml` que não existe no checkout; espelho rastreado em `docs/compliance/GAP-REGISTER.yaml` OU re-point; grep de citações pendentes = 0 | mecânica | S+M | pendente (D-31) |
| GOV-0.9 | Disposição do papel AWS_DERIVA_ROLE_ARN (criar ou registrar ausência aceita) | mecânica | S | pendente (D-31) |
| GOV-0.10 | **Ativação da security-team** (desbloqueia W3 e todo flip): conceder WRITE a Omni-Saude/security-team; ≥1 humano ≠ rodaquino-OMNI (roster: filipecarmo81, gtaquino-automatelabs, hugohiroshi92, joaopanisi, lucasreisEvah, robsonfelix); confirmar/vetar lucasreisEvah nas linhas de privacidade; lint CODEOWNERS = 0; adicionar `flip-path-review-gate` aos required checks do ruleset 20775655 | R-051 | S (atos owner/admin) | procedimento pré-estacionado (D-14) |

Aceitação W0: log reparado confere sha-a-sha c/ `git log main` por terceiro; greps de deadline-velho e citação-pendente = 0; gate no ruleset OU deferral datado do dono.

### §GOV-0.10 Runbook de ativação da security-team (D-14 — DECIDIDO: ATIVAR AGORA)

Estado medido em 2026-10-23 (API GitHub, leitura): time `security-team` (id `18091084`) existe com permissão **pull** neste repo e membros **{rodaquino-OMNI, T01SS0}** — nenhum humano ≠ dono (T01SS0 é conta de serviço). Ruleset `main-protection` (20775655) ativo com 5 required checks, **sem** `flip-path-review-gate`. Passos (dono executa; comandos prontos — o orquestrador executa os marcados ◆ assim que o token tiver escopo `admin:org`, ou o dono cola no terminal):

1. **Adicionar ≥1 humano qualificado ao time** (roster候选: filipecarmo81, gtaquino-automatelabs, hugohiroshi92, joaopanisi, lucasreisEvah — confirmar/vetar lucasreisEvah nas linhas de privacidade):
   `gh api -X PUT teams/18091084/memberships/<login> -f role=member` ◆
2. **Elevar a permissão do time no repo para WRITE**:
   `gh api -X PUT repos/Omni-Saude/maezo-operadora/teams/security-team -f permission=push` ◆
3. **Lint CODEOWNERS = 0 erros** (o orquestrador roda e reporta; hoje a linha 294 já exige security-team p/ `.gitleaks.toml`).
4. **Adicionar `flip-path-review-gate` aos required checks do ruleset 20775655** (via UI Settings→Rulesets→main-protection, ou API `PUT /repos/.../rulesets/20775655` com o contexto acrescentado aos 5 existentes) ◆
5. **Verificação**: `gh api repos/Omni-Saude/maezo-operadora/rulesets/20775655` lista 6 contexts; `gh api teams/18091084/members` mostra ≥2 humans; branch-protection-check job relata os passos pendentes cumpridos.
6. **Efeito imediato**: o flip-gate do PR #907 (run 39872763178) passa a satisfazível — revisor security-team ≠ autor aprova; BRDG-3.1 prossegue.

R-051 herda: se nenhuma das pessoas puder atuar, registrar deferral datado do dono com caminho compensatório (revisão manual de cada flip pelo próprio dono, registrada no PR) — nunca bypass.

## W1 REG — completude regulatória (M/L; gated por jurídico)

| ID | Item | Fecha | Dep |
|---|---|---|---|
| REG-1.1 | **OP21 `legitimacy.screen`** (tela + estado bloqueante + retenção de evidência no caminho coletivo e substrato vendor; shadow; merge até **2026-12-15**) | F-M02, F-E06 | D-03, D-04 |
| REG-1.2 | Re-ancoragem RN 395→**RN 623/2024** no v4 (CA_1 `C3_EV_Expirou`; PT48H → art. 12: 5/10 dias úteis, urgência imediata; valor mais-rígido-que-norma = D-05) | F-E04 | D-05; após CORP |
| REG-1.3 | Extensão de desenho RN 623: protocolo-como-primeira-ação (art. 10), negativa a termo c/ cláusula+base (art. 14), Ouvidoria ≤7du (art. 16 §1º III), vedação de resposta genérica (art. 12 §5º), acesso registros 72h + guarda reconciliada c/ LGPD arts. 15/16 (COUNSEL-5) | F-M06, F-M11(parte) | D-06 |
| REG-1.4 | Obrigação TISS nas jornadas (ponte para RX8) — hoje 0 menções | F-M07 | — |
| REG-1.5 | Prazo reembolso 30d (Lei 9.656 **art. 12, inciso VI** — correção de registro 2026-10-27: o art. 12 termina no inciso VII, "art. 12 VIII" NÃO EXISTE; verificado no Planalto por `REG-RESIDUAL-AUDIT-v1` sha256 `5f25e8a4…`; forma correta pousada em #1057, merge `f57f0821`) + decisão LA-02 | F-M08 | D-07 |
| REG-1.6 | TISS pin → Julho/2025 (04.02.00/01.05.00). **Nota 2026-10-30 (emenda por DEC-17, posição do dono na issue #1081 de 2026-10-29 → R-036/R-068):** o alvo passa a ser a edição Setembro/2026, Componente de Comunicação **04.03.00 e 01.06.00**, pinada no worker de submissão (`tiss_schema.PINNED_TISS_SCHEMA_VERSIONS`; `MAEZO_TISS_SCHEMA_VERSION` só escolhe dentro do pin e segue sem valor por padrão). 04.03.00 reusa o pin do gateway provider; 01.06.00 vendorizado byte a byte do zip oficial (`spec/schemas/tiss/01.06.00/ans-source-pin.json`). Ressalvas: o conjunto oficial 01.06.00 não compila (inclui `tissSimpleTypesMonitoramentoV1_05_01.xsd`, ausente do zip); nenhum `report_type` tem XSD oficial mapeado; a ratificação de `spec/policies/ans/tiss-schema-pin.yaml` continua ato humano (ADR-0007, CODEOWNERS) | F-E05, F-066 | DEC-17 |
| REG-1.7 | Citação LGPD: art. 37 = registro; bases = arts. 7º/11 | F-E12 | D-08 |
| REG-1.8 | NBA reward + perfil 360: ruling art. 11 §4º ANTES de qualquer wiring | F-M09 | D-09 |
| REG-1.9 | Passo de autorização expressa ANS p/ redução de rede (art. 17 §4º) | F-M10 | — |
| REG-1.10 | LGPD restante: arts. 33/48/16 | F-M11(resto) | — |
| REG-1.11 | RN 566/2022 prazos de garantia de atendimento na agenda credenciada | F-M12 | — |
| REG-1.12 | Base LGPD do fluxo evento clínico→cliente (CA_2 M3) | F-M15 | — |
| REG-1.13 | Aquisição de textos-base: RN 528/529/489/515/557 verbatim + URLs DOU | GAPs do sweep | COUNSEL-7/8 |

Aceitação: teste negativo bloqueia venda a PJ ilegítima no CI (`gateway-platform`+`restante`); re-sweep mapeia 7+8 touchpoints; gate cego no diff; OP21 em main até 2026-12-15.
Aceitação (REG-1.1 — as 8 provas do desenho §7 pousadas, apontadas pelo nodeid): prova 1/2 = `tests/unit/gateway/test_commercial_legitimacy_enforcing.py` padrão `test_prova1_*`/`test_prova2_*` (enforcing montado direto no teste, sem bind de produção); prova 3/4 = `tests/unit/gateway/test_commercial_legitimacy_guard.py` padrão `test_prova3_*`/`test_prova4_*` (a cerca estrutural da prova 4 vive nos dois arquivos, `test_prova4_*`); prova 5 = `tests/unit/platform/test_legitimacy_screens_migration_live_pg.py` (live-PG, "prova 5" do desenho §7 no cabeçalho do módulo); prova 6 = `tests/unit/portal/test_legitimacy_screen_guard_off.py` padrão `test_prova6_*` (incl. a cerca de 8 envs `test_prova6_env_do_op21_está_registrado_na_cerca_do_programa_e_deploy_calado`); prova 7 = `tests/unit/portal/test_legitimacy_screen_endpoint.py` (`test_cerca_de_citacao_imprecisa_sobre_toda_string_da_superficie` + fechamentos do checklist de 5 critérios); prova 8 = `test_prova8_*` no arquivo de enforcing + `test_phi_camada_2_o_guard_nunca_abre_o_corpo_do_comando` no guard.

**Status 2026-10-27 (PR5/5 — REG-1.1 implementado em shadow; série OP21 fechada):** 4 PRs merged — **#1022** (`b2e31736`) substrato (migração 0030 + port + store + live-PG dark) · **#1028/#1029** (`fd37f3bb` + reparo `76c52e6a`) tela staff + flag `collective_legitimacy_screen` + cerca CI de 8 envs (#1029 = reparo de docstring) · **#1039** (`a8bff4df`) guard shadow (wrap `LegitimacyScreenGateway(CommercialGateway)` — o `isinstance` do router congelado permanece; bound set fechado de 7 operações; eventos art. 39) · **#1040** (`896bbd72`) `LegitimacyScreenEnforcing` provada SEM bind de produção (a porta da flag aceita só a shadow exata; recusa declarada renderizada pelo codec). Prazo de 2026-12-15 cumprido com folga. **Elevação pendente do dono (E-2 do ledger; decidido 2026-10-27: D-48 — adiado p/ ROLL, agregado ao discriminador de modalidade):** a recusa `legitimacy_unproven` byte-exata no wire exige vocabulário no codec C6-4 (enum de 50 tokens, contrato `d1d796b8…`) = sucessor do freeze — declarado no corpo do #1040; até lá a shadow registra/sinaliza sem alterar a venda, e o enforcing provado recusa pela forma de indisponibilidade (`unavailable`/`source_unavailable`). Emendas R-1..R-7 no registry vendor (gitignored, bundle-local) aplicadas por esta sessão, per design §8.1.

**Status 2026-10-26 (reparo de citação, per parecer §3 item 2):** forma canônica OBRIGATÓRIA em todos os materiais do controle OP21 — "RN 489/2022, art. 23-A (redação dada pela RN 659/2025)"; na tela, forma curta "RN 489/2022, art. 23-A"; "RN 659/2025, art. 23-A" é IMPRECISA (a RN 659/2025 apenas alterou a RN 489/2022, onde o artigo está). Varredura em origin/main (1ee44e91): nenhum material VIVO rastreado carrega a forma imprecisa (o controle ainda não está implementado — REG-1.1 pendente); o registro GP7 com "RN 557/659" vive em `docs/audits/VENDOR-XP-2026-10/` (gitignored, elisão declarada) e foi emendado IN LOCO com desdobro por assunto — contratação = RN 557/2022 (arts. 5º/9º-14/15/16/29/39); penalidade = RN 489/2022, art. 23-A (redação dada pela RN 659/2025). O implementador do REG-1.1 e a ratificação do `GP7-MATRIZ-V1` (dossiê ANSWERS-V1, congelado p/ assinatura) devem consumir a forma canônica — sem re-decisão (D-03/D-04 já respondidas).

## W2 CORP — corpus v4 + F-M01 (M/L; owner-gated)

Alvo: SOMENTE `docs/processes/New_BPMN/Adapted_BPMN/`. CORP-2.1 catches mortos IA-02/03/04 **incl. a regressão própria do v4** (restaurar messageFlow a `UT_ES_CancelamentoPrestador`) · 2.2 produtores/defaults dos 8 timers/7 vars · 2.3 planos DI (2 sem id, 11 sobreposições) · 2.4 catcher do sinal `Signal_CA_AlertaDS` + nota `SEM_SLOT_EQUIVALENTE` · 2.5 as 2 notas de precisão do CORRECOES · 2.6 pinar o fix F-E01 como asserção de regressão · **2.7 disposição F-M01 (D-02; recomendação = re-scope)**: mapa elemento→capacidade→código/teste dos 355 elementos v4, emenda do INTEGRATION-PLAN, reconciliação re-rodada (zero none-por-silêncio); opção (b) ponte runtime = programa à parte. Aceitação: 5-gate PASS ×4 c/ Gate-3 set-level limpo; query de grafo prova o messageFlow do prestador.

## W3 BRDG — ponte comercial (L; gated por security-team)

**3.1 merge do PR #907** (`codex/commercial-20261022-maezo-recovery`; 88 arquivos; todos os checks verdes incl. job Java-mvn dentro do PR; bloqueado SÓ no flip-gate #39872763178 exigindo APPROVED de security-team ≠ autor — destravado por GOV-0.10/D-14) → **3.2 congelar o contrato compartilhado** (catálogo/rede versionada, cadastro progressivo, billing Tasy, pagamentos Stripe/InfinitePay/Asaas, reconciliação; sha registrado NOS DOIS repos; D-15) → **3.3 erguer admissões A1–A10** do portal (GAP-CATALOG resolvido pela autoridade em main; teste negativo por admissão) → 3.4 wiring do portal vs pin congelado (matriz de 51 interfaces re-verificada; CI+e2e do portal) → **3.5 lane Java-mvn em main** (vem no #907; fallback PR avulso) → 3.6 ingresso Lucas Compras/Suporte vs autoridade real (D-21). Aceitação: APPROVED de revisor security nomeado; sha do contrato nos 2 repos; A1–A10 c/ provas fail-closed; mvn verde em main. **Status 23/10:** **3.2 EXECUTADO** (D-15; registro `docs/delivery/commercial-platform/CONTRACT-FREEZE-C6-4-REGISTER.md` + `.json` schema `omni-commercial-contract-freeze.v1`, sha256 do registro `dd812799462b83acc608470357fedc4120cd23f371fcbcebdcc6e0131c98e538`; versão citável `C6-4-frozen@3c619d91`; sha do contrato `d1d796b8…` recomputado em main e batendo; congelamento ≠ admissão runtime — `runtime_admission` UNBOUND, A1–A10 seguem pendentes p/ 3.3; espelho do pin p/ o portal em `docs/delivery/commercial-platform/portal-mirror/`, cópia pelo dono) · **3.5 VERIFICADO** (job `plugin humano Java (mvn verify)` = success, run 40010989062, main `3c619d91`) · 3.3/3.4/3.6 pendentes.

## W4 PROV — cauda N2 (M; independente)

4.1 reconstruir SESSION-CLOSE-QUIESCENCE.json (ledger `23c106f3` + SV-01…10; re-pin) [F-M14] · 4.2 fechar recusa #60 (opção de desenho → v23 → 1º N2 POSITIVO → cauda) [F-P01] — **FECHADO 2026-10-27** (execução v29/D-50: recusa #60 fechada pelos dois desfechos provados; 1º N2 positivo inalcançável no conjunto fechado `{"0","0.2","4"}`; via v30 registrada como decisão do dono — ver §Status 2026-10-27 e bloco de fecho) · 4.3 lane Linux estável (n2-lane.yml entra em CI; hoje manual-only) [F-P08] · 4.4 GP3/GP4 admitir-ou-aposentar [F-M05]. Aceitação: kit c/ hash; #60 fechado/escalado c/ evidência; lane rodando em CI.

**Status 2026-10-26:** discriminador P4 executado (runs 40396401996 d0 / 40396951024 d200) — recusa não-decisiva em ambos os Δ (física medida 3,22s), D2' provado mecanicamente; adjudicação no kit `P4-RESULT-v1` (gate cego Tier-2 PASS). **D-42** rota v28 = novo regime Δ≥3,5s; **D-45** reparo §6-1 + P0' como PR standalone IMEDIATO. **D-46** (2026-10-27): design da rota (b) D2 pós-commit AUTORIZADO (missão de design gateada nesta janela — análise → gate cego → bank no kit); EXECUÇÃO (blob v29 + release + par P4 de dispatches) gated no 2º sinal do dono após gate PASS. **D-50** (2026-10-27): **2º sinal D2 DADO** (D-46 consumido) — execução do design `DESIGN-D2-POST-COMMIT-v1` (sha256 `8452f755…`; banked no kit com veredito `0e774e69`) AUTORIZADA para a próxima janela: blob v29 conforme o design (máquina COMMITTED-listener; cerca OBRIGATÓRIA F-3 do `:254` — disposição (i)/(ii) provada no PR), release FULL (schema NOVO com os 8 sítios de re-pin da §7.5, incluindo o builder `scripts/ci/build_provider_native_observation.py:40`), par-bracket `{"0.2","4"}` com grants `ROOT-N2-R29-<data>-D0P2/D4`; cadeia da casa integral (autor → gate cego → merge → byte-check; dispatches de lane SERIADOS).

**Status 2026-10-27 (janela pickup v6 — 4.2 FECHADO):** D-50 executada por inteiro — PR **#1053** (`9553f437`), build FULL r19/selo `n2fix-r17`, release `n2-lane-v29` (escopos d0p2 `4f4eeec2…`/d4 `c0a1ebd1…`; grants `ROOT-N2-R29-20261027-D0P2/D4`; incidente de cerimônia registrado na release body: 1 dispatch d4 abortado por grant re-datado errôneo, cancelado pré-aquisição, restaurado byte-exato). Par SERIADO: d0p2 run 40574553002 (recusa #63 esperada, sub-horizonte; Δ 0,200341649 == configurado "0.2") · d4 run 40590991584 (recusa com T3' "in progress" a Δ=4,004130915). Memo `P4-V29-RESULT-v1` BANKED sha256 `3f665380b625325eaae717e92eb465b1d0a3c89d8a8c0aac0e7aff308d9e36f3` (gate Tier-2 FAIL 3 reparos → re-gate PASS). Família NOVA **R29-F** e desacoplamento D2 provado in-run — registro integral no bloco **§Status 2026-10-27 (janela pickup v6 — fecho)**.

## W5 CLNT — plano de capacidades cliente/admin, em shadow (M/L)

5.1 PRs de habilitação dark por flag (flags de habilitação dark conforme `docs/plans/w5-5-1-flags-reancoragem.md` — inventário real: **31 interruptores/6 grupos**; a citação original a `settings.py:337-591` estava driftada, provado em #953/#967; revisor nomeado + janela de evidência por linha do `docs/review-queue.md`; flags SEGUEM OFF — ROLL flippa) · 5.2 endpoint HTTP de DSR (não existe) + resolução do guard `decisao_dsr` · 5.3 os 3 strict-xfails DSR (consertar fonte antes; flip 1 PR cada c/ delta do censo) · 5.4 as 2 pontes de identidade do erasure construídas dark (**41/41** decisões DPO — ver `docs/compliance/human-windows/LUCAS-DPO-OPERACAO-PACKAGE.md` — permanecem humanas) · 5.5 admin 7/7 (Documents/Operations/Administration hoje stubs + BFF de assignments) ou disposição por área · 5.6 pacotes de evidência para cada pendência humana (médico-auditor clínico; jurídico/DPO/operação do lucas). Aceitação: flags provadamente OFF em prod; e2e DSR verde; censo xfail com delta; 0 linhas DRAFT sem revisor nomeado.

## W6 DISC — gates, gaps e burn-down de ledger (M)

6.1 os 11 gates NOT-RUN: execução cega ou aposentadoria formal cada [F-M05/F-P09] · 6.2 marcador/annotação OP13 [F-M04] · 6.3 disposição OP17/OP19 [F-M03] · 6.4 fato Q10 [F-M13] · 6.5 triagem dos 130 itens de ledger {programa|ROLL|aposentar} [F-P05] · 6.6 244 DEFER-OWNER em janelas batched [F-P06] · 6.7 verificação dos 285/528 rows unverified. Aceitação: tabela de gates com zero NOT-RUN-sem-disposição (reconciliação maquinável); contagens antes/depois ratificadas pelo dono.

**Status 2026-10-26 (D-44):** janela própria da W6 após a conclusão do OP21; nenhum item W6 tem deadline (W8 alvo 2027-01-26). **D-47** (2026-10-27): janela executada NO RESTO DA JANELA de 2026-10-27 — pré-condição cumprida (OP21 concluído 5/5 no merge `853c7730` desta mesma janela); pacotes 6.6 saem prontos p/ janelas batched. **D-49** (2026-10-27): **W6 RATIFICADA/FECHADA pelo dono** — pacote `W6-RATIFICATION-PACKAGE-v1` (sha256 `b1ccf3fa…c10e3e`; gate cego Tier-2 PASS após 2 reparos literais F-1/F-2), apontamento `docs/audits/JOURNEYS-COMPLETENESS-2026-10/W6-RATIFICATION-PACKAGE-v1.md` (gitignored). Contagens antes→depois ratificadas: **11 gates NOT-RUN → 0 sem disposição** (GP5+VW1-P1 EXECUTADO-PASS, vereditos `7b9337b0`/`38f32348`; GP1/GP2 aposentados; GP3/GP4 defer-PW6; GP6-GP10 defer-registrados) · **triagem dos 130 itens = 87 aposentar + 20 programa + 22 ROLL** (D6 resolvida pelo dono: NH-14 → PROGRAMA) · **DEFER-OWNER 244 → 1 vivo** (+229 RETIRE-CHECKOUT-ARCHIVED) · 6.7: **286 rows unverified com plano** e baseline congelada `fd4a59eb…`. Cerca ratificada embutida (6.6-D): NENHUM DROP de archive enquanto a custódia física dos packs não for localizada ou declarada perdida (pasta `~/code/maezo-evidence-archive/fleet-retirement-20261022-104859/` ABSENT hoje; 231 recovery refs + 159 branches verificadas presentes).

**Status 2026-10-27 (janela pickup v6):** os 20 itens PROGRAMA da D-49 despachados e dispostos nesta janela — **5 APOSENTAR com prova de código direta** (PL-28/29/30/31/32, reconciliação cega `PL-TIER2-RECONCILE-v1` sha256 `04cc3ca0…`), **PL-34 SEGUE-ABERTO** (owner/DPO), **PL-36 SEGUE** (cauda viva 22 strict-xfails = 16 programa + 6 humana), **PL-40 → ROLL/owner**, **CO-08 FECHADO** por D-49 (reconciliação re-executada célula-a-célula), **CO-11 SERVE-ABERTO-brokered** (proposta appendada em COORDINATION.md 2026-10-27, pre-append sha256 `442b6061…`; dedupe só com ack), **CO-18 → ROLL** (contrassinaturas dos ratificadores; GP5–GP10 6/6 ratificados 2026-10-09), **NH-01/NH-11/NH-14/SV-09 FECHADO-com-prova** (NH-11 COM DIVERGÊNCIA de mecânica do merge #907 registrada — ver fecho), **NH-03/NH-15 SEGUE-parcelado**, **SV-02/SV-03 fechados com a adjudicação v29**, **GAP-01 executado** no fechamento da janela — `SESSION-CLOSE-QUIESCENCE.v3-2026-10-27.json` (kit `PROVIDER-IMPL-2026-10/20261023T000000Z/`, sha256 `0a8318c2…`, frozen_at 2026-10-28T04:21:36Z; o v2-rebuilt de 2026-10-23 permanece byte-intacto). Fontes: `NH-CO-RECONCILE-v1` (corpo sha256 `6b2e3fa8…`, verificação portável `head -n 209 | shasum -a 256`) + `PL-TIER2-RECONCILE-v1`. Tabela item-a-item no bloco **§Status 2026-10-27 (janela pickup v6 — fecho)**.

## W7 HARD — contínuo (S)

7.1 prova e2e do caminho SARIF (substituto-GHAS) — **EXECUTADO 2026-10-27 #1050** (`dba45168`; validador fail-closed do artefato `scripts/ci/validate_sarif.py` + e2e 5/5 contra o schema oficial OASIS, controle negativo corrompido exit 1; achado de estado: o gate GHAS CAIU — primeira análise code-scanning do repo 2026-10-26T22:42:29Z, ≥100 análises/29 alertas em 2026-10-27) · 7.2 **red-test** do SBOM/cosign (provar bloqueio, não só verde) — **EXECUTADO 2026-10-27 #1055** (`88844a57`; 17 testes novos = 2 canários + 15 recusas por transcript real do cosign v3.1.4; sonda de mutação: neutralizar o veredito do marcador derruba 7/11 testes — bloqueio provado; runbook `supply-chain-imagem.md` §3 reparado: flag inexistente no cosign verify removida) · 7.3 cercas de regressão nos 3 chokepoints (`start_process_idempotent`, contrato do `engine_deploy.py`, espelho do allowlist `effect_pep.py:174`) — segue pendente (não mandatado nesta janela) · 7.4 pin do tag CIB Seven — **EXECUTADO-com-apontamento 2026-10-27 #1058** (`1200eea9`; registro `docs/compliance/CIB-SEVEN-TAG-PIN.md`: veredito COMPLETO sem drift — imagem pinada 2.1.0 em todos os sítios Docker [compose tag; Dockerfiles tag+digest `20a2135b…`; ECS = digest ECR IMMUTABLE], jar Maven 2.2.0 em todos os poms — classes de artefato distintas, não inconsistência; pendências owner declaradas: revisita do DL-0006 [2.2.0 publicado no Hub 2026-05-31, `latest` já =2.2.0], vigilância HX-15/R-110 até 2027-03-04, ADR-0038 Proposed aguarda dono) · 7.5 dashboard de desvios shadow (muro 2027-02-09; só de dados rastreados) — **EXECUTADO 2026-10-27 #1052** (`81ab23f6`; artefato citável `docs/delivery/w7/shadow-deviation-dashboard.md`: 69 itens — 61 NO-PRAZO / 0 VENCIDO / 8 SEM-DATA; R-131 2026-11-11 = 1º a cruzar a janela de aviso ≤14d; 29 provas).

## W8 CLOSE (M; alvo 2027-01-26)

Reconciliação re-rodada em todos os inventários · verificação cega Tier-2 do relatório de completude · pacote de evidências de prazo (OP21 pré-2027; janela C2; muro) · matriz final no anexo + supercessão formal · backlog ROLL entregue ao dono (cada flip = mudança de dados + pacote estacionado) · assinatura do dono (D-29).

**Status 2026-10-27 (D-52):** janela W8 ANTICIPADA para dez-2026 — pacotes de evidência preparados antecipadamente; assinatura D-29 na janela. **4 pacotes banked** no bundle `JOURNEYS-COMPLETENESS-2026-10/` (W8-PRE-OP21-DOSSIER-v1 `aad491b0…` · W8-PRE-DEADLINE-PACKAGES-v1 `7c47d3e6…` · W8-PRE-ROLL-BACKLOG-v1 `6daff61d…` · W8-PRE-SESSION-PLAN-v1 `7bee3c66…`); multa-base desambiguada: R$ 506.250 = escalão de 1º/1/2027, R$ 337.500 = valor vigente até lá.

## §Decisões registradas (2026-10-23, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-01 | **RETOMADA — todas as ondas** | `state: ATIVO`; W0/W2/W4/W5 despacham já; W1 espera jurídico (D-03/D-04); W3 espera security (D-14) |
| D-02 | **Mapa primeiro + ponte delimitada SP-OP-nativa** (delegada pelo dono ao orquestrador: "excelência, sem workarounds, considerando impacto nas demais builds") — ver ruling abaixo | CORP-2.7 = mapa 355 elementos → capacidades → processos SP-OP → código/teste; ponte APENAS para lacunas provadas pelo mapa, como NOVOS process-keys SP-OP-* pelo caminho canônico (allowlist + engine_deploy + cercas), NUNCA como os 44 beans AI delegados (esses permanecem vocabulário de desenho mapeado às capacidades existentes do gateway) |
| D-05 | Timers CA_1 = **classes RN 623/2024 art. 12** (urgência imediata; demais 5 dias úteis; PAC/internação eletiva 10 dias úteis) | REG-1.2 |
| D-07 | LA-02 **modelado por inteiro** (coparticipação/faturamento/contas como objetos+tasks explícitos; reembolso 30d art. 12 VIII como timer) [corrigido 2026-10-28: inciso correto VI — ver REG-1.5/#1057] | REG-1.5 |
| D-09 | NBA reward **dark** (constrói flag-off; ruling art. 11 §4º é a condição de habilitação) | REG-1.8 |
| D-10 | **Enfrentar o muro** 2027-02-09 (W8 entrega pacotes de flip até 2027-01-26; sem segunda renovação) | W8 |
| D-11 | **v4 = linhagem** ratificada (v3 = referência de autoria imutável) | cerca §0(1) |
| D-12 | **Alvo agressivo 80%** ≥implemented global (inclui admin 7/7 vivo + DSR e2e; risco de agenda aceito contra o muro) | W5 integral |
| D-14 | **Ativar security-team AGORA** — runbook §GOV-0.10 abaixo; dono executa os passos admin (ou concede escopo) | destrava #907 + todos os flips |
| D-15 | Congelar contrato **logo pós-merge #907** | BRDG-3.2 |
| D-16 | Admissões A1–A10 **incrementais** (teste negativo por admissão) | BRDG-3.3 |
| D-21 | Lucas Compras/Suporte **monta pós-ponte** | BRDG-3.6 |
| D-30 | **Broker agora, 3 arquivos primeiro** (W2 começa em CA_0/CA_2/CA_3; proposta de coordenação CA_1 via canal broker) | CORP |
| D-31a | GAP-REGISTER = **espelho rastreado** `docs/compliance/GAP-REGISTER.yaml` gerado dos registros vivos + cerca CI anti-citação-pendente | GOV-0.8 |
| D-31b | deriva-dev = **ausência aceita** (nota datada do dono; sem novo papel AWS) | GOV-0.9 |
| D-33 | **Coordenação de PRs externos** (AMH #149; revisão do #907) — constava do §HDR sem linha nesta tabela; acrescida por acréscimo em 2026-10-25 | BRDG-3.1 concluída: #907 merged, base do congelamento C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`: `merge_pr: 907`) |
| Q10 | **Não** — canal vendor NÃO vende a entes públicos | OP17 desbloqueado do lado do fato (DISC-6.3/6.4) |

**Ruling D-02 (racional):** ponte total (44 beans AI) duplicaria capacidades que já existem no gateway/agentes (eventClassifierAI ≈ roteador helena; authorizationRequestService ≈ workers SP-OP-AUTH; recommendationRankingService ≈ NBA) criando forquilha de autoridade de efeitos — viola o chokepoint único (`start_process_idempotent`, PEP subtrativo, allowlist). Mapa-sozinho deixaria a riqueza normativa do v4 como documentação morta. A alternativa escolhida preserva autoridade única de runtime (SP-OP/engine), mantém o v4 como especificação viva (cada elemento ganha casa real), e reutiliza o substrato verificado/ratificado — impacto nas demais builds: W3 já é SP-OP-nativa (intacta), W5 ganha process-keys concretos, lanes do GUIA mapeiam sobre filas de workers existentes.

## §Decisões registradas (2026-10-25, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-34 | **Admissões A1+A2+A3 registradas EM CONJUNTO** (sessão de compra completa; atos owner-only na prateleira do portal) | BRDG-3.4 liberado quando os 3 registros existirem |
| D-35 | **A7 = teto `admitted_ceiling_orientation_only`** (literal do dossiê A7; única forma admitida do loader `commercial_portability_admission.py`) | Forma 1/fonte própria só por freeze sucessor |
| D-36 | **Interface de comissões a construir** (múltiplos ajustes por produto/canal/corretor; dados inseridos no futuro; interna/dark) | o dossiê A8 do contrato permanece `pending` — nem `admitted_out_of_scope` nem Forma 1 |
| D-37 | **A9 = recusa registrada (`refused`)** — literal do dossiê A9; loader `commercial_analytics_admission.py` | UNMEASURED persiste |
| D-38 | **A6+A10 em janela conjunta** | Track B grants/membership/canal + registros A6 (owner+DPO) e A10 (origem+comercial) juntos, na janela BRDG-3.4 |
| D-39 | **DSR send_response mantém shadow** | sem flip até exercício mais longo do endpoint #955 |
| D-40 | **Administration = perfil de leitura (novo D-*) + admin read-only a construir** | proposta do D-* vem no pacote; assinatura é do dono |
| D-41 | **Próxima janela começa pela cadeia D2' do N2** | design update → implementação → v27 → discriminador |

## §Decisões registradas (2026-10-26, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-42 | **Rota v28 N2 = (a) novo regime Δ≥3,5s** (blob novo com Δ≈4s — 3,22s medido + margem — §6-1/P0' no ramo de recusa EMBARCADOS na mesma cerimônia se couber, re-pin, v28; cadência §10.8 sancionada) | lane N2: primeiro N2 positivo viável via admissão clog-committed-t3-straddle; se recusar, escalar para (b) D2 pós-commit |
| D-43 | **OP21: PR3 (guard shadow) + PR4 (enforcing provado) na próxima janela**; PR5 (docs/registry) na sequência | prazo merge 2026-12-15 mantido com folga; exceções E-1..E-6 seguem em janelas humanas paralelas |
| D-44 | **W6 DISC: janela própria após conclusão do OP21** | nenhum item W6 tem deadline; W8 alvo 2027-01-26 |
| D-45 | **Reparo §6-1 + P0' como PR standalone IMEDIATO** (não dobrado no blob v28) | records de recusa voltam a carregar t3_* e a testemunha P0' já na próxima rodada; custo aceito: dois re-pins seguidos do módulo |

## §Decisões registradas (2026-10-27, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-46 | **Rota N2 = design da rota (b) D2 pós-commit AUTORIZADO AGORA; EXECUÇÃO GATED** — missão de DESIGN gateada nesta janela (opus SOMENTE-ANÁLISE → gate cego → bank no kit; zero dispatch/custo de lane); blob v29 + release + par P4 de dispatches SÓ com SEGUNDO SINAL do dono após gate PASS | racional registrado: rota (a) fechada por prova causal (memo `P4-V28-RESULT-v1` `8459724a` — commit da testemunha move com Δ; gap leitura→commit estruturalmente positivo); (c) inaplicável; parar deixaria a última variante ranqueada do design sem prova; dos dois desfechos de D2 (positivo ou impossibilidade terminal) ambos fecham W4.2 com evidência |
| D-47 | **W6 DISC: janela própria executada NO RESTO DESTA JANELA** (pré-condição D-44 cumprida — OP21 concluído 5/5 no merge `853c7730` desta mesma janela) | único bloco grande do programa não-bloqueado por atos do dono; pacotes 6.6 saem prontos p/ janelas batched |
| D-48 | **E-2 (codec C6-4) ADIADO PARA O ROLL** (byte-exato de `legitimacy_unproven`/`phi_in_commercial_input` no wire exige sucessor do freeze) | sem urgência: enforcing sem bind de produção; prazo 15/12 cumprido em shadow; wire renderiza indisponibilidade honesta; a cerimônia de sucessor AGREGA a outra metade de E-2 (discriminador de modalidade coletiva) numa ÚNICA passada nos 2 repos, no momento do ROLL (quando a janela shadow tiver medido falso-positivos) |

## §Decisões registradas (2026-10-27 — ratificação W6, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-49 | **W6-RATIFICATION-PACKAGE-v1 RATIFICADO** (sha256 `b1ccf3fa…c10e3e`; gate cego Tier-2 PASS após 2 reparos literais F-1/F-2; pacote em `docs/audits/JOURNEYS-COMPLETENESS-2026-10/W6-RATIFICATION-PACKAGE-v1.md`, gitignored) — contagens ratificadas: 11 gates NOT-RUN→**0 sem disposição** (GP5+VW1-P1 EXECUTADO-PASS, vereditos `7b9337b0`/`38f32348`; GP1/GP2 aposentados; GP3/GP4 defer-PW6; GP6-GP10 defer-registrados) · triagem 130 = **87 aposentar + 20 programa + 22 ROLL** · DEFER-OWNER 244→**1 vivo** (+229 RETIRE-CHECKOUT-ARCHIVED) · 6.7: 286 rows unverified com plano e baseline congelada `fd4a59eb…`; **D6 resolvida pelo dono: NH-14 → PROGRAMA** (triagem final 87/20/22) | **W6 ACEITA/FECHADA** — os 20 PROGRAMA viram compromisso do programa; os 22 ROLL vão ao backlog ROLL do W8; cerca 6.6-D vigente: NENHUM DROP de archive enquanto a custódia física dos packs não for localizada ou declarada perdida (pasta `~/code/maezo-evidence-archive/fleet-retirement-20261022-104859/` ABSENT hoje; 231 recovery refs + 159 branches verificadas presentes) |

## §Decisões registradas (2026-10-27 — fechamento de janela, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-50 | **2º SINAL D2 DADO (D-46 consumido): execução do DESIGN-D2-POST-COMMIT-v1** (sha256 `8452f755…`; banked no kit com veredito `0e774e69`) **AUTORIZADA para a próxima janela** — blob v29 conforme o design (máquina COMMITTED-listener; cerca OBRIGATÓRIA F-3 do `:254` — disposição (i)/(ii) provada no PR) · release FULL (schema NOVO com os 8 sítios de re-pin da §7.5 INCLUINDO o builder `scripts/ci/build_provider_native_observation.py:40`) · par-bracket `{"0.2","4"}` com grants `ROOT-N2-R29-<data>-D0P2/D4` | **cadeia da casa integral** (autor → gate cego → merge → byte-check); dispatches de lane SERIADOS |
| D-51 | **Retenção fleet-retirement: DROP COM PROVA `git cherry`** — as branches do fleet-retirement cujo conteúdo estiver PROVADAMENTE em main (prova cherry por branch, missão própria com registro) são dropadas; o que NÃO fechar prova FICA (fail-closed); os packs/documentos já foram descartados pelo dono (2026-10-27) | **fail-closed**: prova cherry por branch em missão própria com registro é pré-condição de qualquer DROP de branch do fleet-retirement; sem prova, a branch permanece |
| D-52 | **W8 CLOSE ANTICIPADA PARA DEZ-2026** (antes do alvo 2027-01-26, maximizando folga) | pacotes de evidência de prazo preparados antecipadamente; backlog ROLL (22 itens, da triagem D-49) entregue ao dono; assinatura D-29 na janela |

## §Decisões registradas (2026-10-29, dono, via seleção clicada em sessão do orquestrador W8)

| ID | Decisão | Efeito |
|---|---|---|
| D-53 | **D-29 EXECUTADO: GO** — programa FECHADO; supercessão D-11 efetivada | bundles c/ adendos `a3e6b875…`/`d41b99fc…`; ROLL vira fila pós-programa |
| D-54 | **ADR-0038: OPÇÃO 2 vigora** (manter forma congelada + janela de migração dual-read) | ADR/README/DL ver itens abaixo |
| D-55 | **Par CRED: Opção D-2 EXECUTAR test-scoped** | PR companheiro (censo 8→6) |
| D-56 | **Lane N2: NOVO DESENHO autorizado — re-pin da congruência terminal** (v29 §6 opção (iii); execução só após design+gate próprios) | design em autoria; lane em repouso até lá |

## Status 2026-10-27 (janela pickup v6 — fecho)

Registro do fechamento da janela — tabela fiel aos atos, nada acrescido/suavizado. Cadeia dos 11 PRs: autor especializado → gate cego opus (FAIL → reparos literais → re-gate quando aplicável) → squash → byte-check 0-diff.

### PRs merged (11) — estado e merge confirmados por API nesta sessão

| PR | merge em main | item |
|---|---|---|
| #1049 | `b75ef877` 2026-10-27T18:14:44Z | GOV-0.6/0.7 — linhas da tabela W0 → EXECUTADO (nota `docs/compliance/PROVIDER-COMMITS-PROVENANCE.md`; disposição `docs/compliance/PR646-CHAIN-DISPOSITION.md`) |
| #1050 | `dba45168` 2026-10-27T18:53:39Z | W7 7.1 — validador fail-closed do artefato SARIF (`scripts/ci/validate_sarif.py`) + e2e |
| #1051 | `6dd8f1b0` 2026-10-27T18:53:34Z | REG-1.7 — âncora LGPD correta no CA_0 v4 (art. 37 = registro; bases = arts. 7/11) |
| #1052 | `81ab23f6` 2026-10-27T18:32:47Z | W7 7.5 — dashboard de desvios shadow (artefato citável `docs/delivery/w7/shadow-deviation-dashboard.md`) |
| #1053 | `9553f437` 2026-10-27T19:22:12Z | v29 — blob observador + máquina JVM COMMITTED-listener + schema custody-only + re-pins |
| #1054 | `e68cefad` 2026-10-27T18:32:42Z | REG-1.13-residual — aquisição RN 566/2022 (molde MANIFEST) + anotação 404-LOG §3.2 |
| #1055 | `88844a57` 2026-10-27T19:42:18Z | W7 7.2 — red-test SBOM/cosign (bloqueio provado fail-closed) |
| #1056 | `cb8c9b03` 2026-10-27T21:58:12Z | REG-1.9 — desdobro art. 17 §4º (autorização expressa ANS) em SP-OP-CRED-001 (dark, confirmação jurídica pendente) |
| #1057 | `f57f0821` 2026-10-27T21:58:17Z | REG-1.5(b) — CA_2 v4 per D-07 (LA-02 por inteiro + timer reembolso 30d, Lei 9.656 art. 12, VI) |
| #1058 | `1200eea9` 2026-10-28T02:24:16Z (23:24 -03:00 de 27/10) | W7 7.4 — pin do tag CIB Seven |
| #1059 | `737f8ed5` 2026-10-28T02:03:13Z (23:03 -03:00 de 27/10) | REG-1.4 — âncora TISS RN 501/2022+RX8 em U4_T_Liquidar + disposição broker C3 (CA_1) |

Contagem: 11 PRs (os merges de #1058/#1059 caíram após a meia-noite UTC, ainda 2026-10-27 no fuso -03:00).

### N2 v29 (W4.2 FECHADO — D-50 executada)

- PR **#1053** (`9553f437`); build FULL r19/selo `n2fix-r17`; release `n2-lane-v29` (publicada 2026-10-27T19:53:56Z; escopos d0p2 `4f4eeec2…`/d4 `c0a1ebd1…`; grants `ROOT-N2-R29-20261027-D0P2/D4`; incidente de cerimônia registrado na release body: 1 dispatch d4 abortado por grant re-datado errôneo (`ROOT-N2-R29-20261028-D4`), cancelado em ~1 min de vida e ANTES de qualquer aquisição; restauração byte-exata à FASE A — asset re-publicado, conteúdo byte-idêntico provado por bateria + pins internos).
- Par SERIADO executado: d0p2 run 40574553002 (recusa #63 esperada, sub-horizonte; Δ 0,200341649 == configurado "0.2") · d4 run 40590991584 (recusa com T3' "in progress" a Δ=4,004130915).
- Memo **P4-V29-RESULT-v1 BANKED** sha256 `3f665380b625325eaae717e92eb465b1d0a3c89d8a8c0aac0e7aff308d9e36f3` (gate Tier-2 FAIL 3 reparos → re-gate PASS). Kit: `PROVIDER-IMPL-2026-10/20261023T000000Z/` (bundle gitignored).
- Família NOVA **R29-F**: **(F-1)** horizonte de commit do engine ~**+6,298s** pós-RELEASE (hipótese §2.6 era 1-3s; o membro "4" é sub-horizonte); **(F-2)** discriminador inalcançável no caminho de recusa — a recusa fecha o canal ANTES do COMMITTED; SEAL-07 morre em Broken pipe (SEVERE ENGINE-16004 banked); STAGE_CLOSED-08 nem tentado.
- **Desacoplamento D2 provado in-run**: engine COMMIT 2,280229s DEPOIS da leitura que recusou — 1ª observação de engine-commit da lane.
- **W4.2 fechado pelos dois desfechos (D-46)**; 1º N2 positivo inalcançável no conjunto fechado `{"0","0.2","4"}`; via v30 (membro Δ>horizonte, harness-only) REGISTRADA como decisão do DONO, sem recomendação.

### REG (audit-first — auditoria antes do despacho)

Auditoria `REG-RESIDUAL-AUDIT-v1` (sha256 `5f25e8a4490eb26eccea0ff52efcd51f184d52bc32714ce85db3f301154c0597`; bundle gitignored; base `edd7910b`): **REG-1.2/1.6/1.13 JÁ-COBERTOS** (269-gap register: 3 JA-COBERTO · 4 PARCIAL · 5 GAP-REAL · REG-1.1 FECHADO). Pacotes executados na janela:

| Item | PR | merge | Conteúdo |
|---|---|---|---|
| REG-1.7 | #1051 | `6dd8f1b0` | âncora LGPD correta no `CA_0 v4:656` (art. 37 = registro; bases = arts. 7/11) + guardrail de regeneração |
| REG-1.13-residual | #1054 | `e68cefad` | aquisição RN 566/2022 verbatim pelo molde MANIFEST; `docs/compliance/legal/MANIFEST.sha256` = 16/16 OK (re-verificado nesta sessão) |
| REG-1.5(b) | #1057 | `f57f0821` | CA_2 v4 per D-07 — LA-02 por inteiro + timer P30D ancorado no art. 12, **VI** |
| REG-1.9 | #1056 | `cb8c9b03` | desdobro art. 17 §4º em SP-OP-CRED-001 — dark, confirmação jurídica pendente |
| REG-1.4 | #1059 | `737f8ed5` | âncora TISS RN 501/2022+RX8 em `U4_T_Liquidar` + pendência broker C3 (CA_1) |

- **DRIFT corrigido de registro**: "Lei 9.656 art. 12, VIII" NÃO EXISTE → **art. 12, inciso VI** (linha REG-1.5 da tabela W1 emendada neste PR, com nota; verificado no Planalto pela auditoria).
- **REG-1.6 SUPERADO**: pin vivo já é set/2026 `04.03.00/01.06.00` (PR #657 `76e0094d`); prazo real de implantação **31/12/2026** a registrar.
- **Gated-human (SEGUE, sem engenharia a despachar)**: REG-1.3 (COUNSEL-5/D-06) · REG-1.8 (COUNSEL-4) · REG-1.10 (COUNSEL-5; art. 48 SEM prazo fixo sem norma ANPD pinnada) · REG-1.11 (SME jurídico) · REG-1.12 (COUNSEL).
- **Stale anotado**: `MAPA-ELEMENTOS-V4.md:106` PT48H → PT168H (residual pós-REG-1.2).

### W6 PROGRAMA — 20 itens dispostos

Fontes: `PL-TIER2-RECONCILE-v1` (sha256 `04cc3ca02eed39042ee0e67a3ff6011ea33cc57361ab65e82c64e2739dceb39b`) e `NH-CO-RECONCILE-v1` (corpo sha256 `6b2e3fa8596c7318cd59352b0542a544bd7e0e1ea1b68f68666af2e0f291a40d`, verificação portável `head -n 209 | shasum -a 256`) — ambos verificados nesta sessão.

| Item | Disposição | Prova/veículo |
|---|---|---|
| PL-28 | **APOSENTAR** | postura fail-close viva nos 4 callers (`best_effort=False`; notify_regulatorio deliberada+pinnada); 0 ids abertos do assunto no register |
| PL-29 | **APOSENTAR** | handoff NIP→ANS durável (at-least-once + fence idempotente, bridge `enabled: true`) |
| PL-30 | **APOSENTAR** | `_anchored` exige `non_blank(tenant_id)` estruturalmente nas regras do bridge |
| PL-31 | **APOSENTAR** | correlation-ids `agent_id`/`tenant_id` vivos no seam+metering |
| PL-32 | **APOSENTAR** | delegação A2A real (DL-0033 closed; outbox transacional + relay); 3 vizinhos A2A abertos de assunto DISTINTO permanecem no register |
| PL-34 | **SEGUE-ABERTO** | id aberto `ANDRE-DEFAULT-MIN-K-ANONYMITY-1` (k=1 vivo, `graph.py:345`); veículo = owner/DPO (k-floor) + MZO-050+/MZO-080 — humana, não engenharia |
| PL-36 | **SEGUE** | cauda viva = **22 strict-xfails** (16 programa + 6 humana; 3 DSR sob D-39); correção do texto do W6-pacote §2.a ("3 strict-xfails" → real) |
| PL-40 | **→ ROLL/owner** | hardening NÃO fechou: ADR-0038 Proposed — ratificação do dono; 7.4 executado (#1058); pin do tag CIB já satisfeito |
| CO-08 | **FECHADO** por D-49 | dedupe re-executada célula-a-célula (992→265→229/15/10/6/2/1/1/1 idêntica ao §3 do pacote); físicas ABSENT reconfirmadas |
| CO-11 | **SERVE-ABERTO-brokered** | proposta appendada em COORDINATION.md 2026-10-27, pre-append sha256 `442b6061…`; dedupe C001–C008 só com ack do detentor/owner |
| CO-18 | **→ ROLL** | pendência ESTREITADA: contrassinaturas dos ratificadores nomeados; GP5–GP10 6/6 ratificados 2026-10-09 |
| NH-01 | **FECHADO-com-prova** | 269 gaps vivo (soma 104=104; deltas explicados); sumário do register STALE `total_gaps: 260` → 269 (os 9 = `PM-B*`, PRs #607–#615) |
| NH-03 | **SEGUE-parcelado** | ADR-0041 vivo Proposto (assinatura dono); R-059 campos vazios (pauta SME pronta) |
| NH-11 | **FECHADO** | 6/6 passos GOV-0.10 medidos OK; **DIVERGÊNCIA REGISTRADA** (abaixo) |
| NH-14 | **FECHADO** | `required_approving_review_count=0` = deliberado rastreado (ADR-0023:52, CODEOWNERS:5, Tarefas_Pendentes:632/637) |
| NH-15 | **SEGUE-parcelado** | ADR-0039 R1 approver-table; retention MZO-060/DBA; vendor-DPA/DPO |
| SV-02/SV-03 | **FECHADO-com-adjudicação-v29** | recusa #60 frontier fechada com a execução v29 desta janela; memo banked |
| SV-09 | **FECHADO** | 43 = 43 recontado (`~/.codex/worktrees`); adjudicação 6.6-C segue dono puro |
| GAP-01 | **→ executado** (v3-2026-10-27, sha256 `0a8318c2…`) | re-freeze do fim da janela contendo os fechamentos desta; PROV-4.1 re-pinado |

**DIVERGÊNCIA NH-11 (registro fiel da mecânica real do merge #907):** o merge ocorreu 2026-10-23 **14:27:55 -03:00** — **13 min ANTES** de o `flip-path-review-gate` entrar nos required checks (14:40:34, version 54515229); no head `f178e5d9` o gate está VERMELHO (runs 39872763178/39874641180) e NÃO há APPROVED de security-team (única review: rodaquino-OMNI/COMMENTED); o merge foi viável pelo **bypass-always** do ruleset (actor_id 195689062 = rodaquino-OMNI). Resultado final OK (ativação completa — 3 humanos ≠ dono no time, WRITE, lint CODEOWNERS errors=0, 6 contexts com o flip-gate, checks verdes, #907 ancestral de main); a narrativa anterior do plano ("bloqueado SÓ no flip-gate — destravado por GOV-0.10/D-14") NÃO foi o mecanismo do merge. Daqui em diante o contexto é required e o bypass é recurso de emergência do dono.

### D-51 EXECUTADO (retenção fleet-retirement)

- Universo **159** (dedupe do `UNMERGED-RESOLVE-LEDGER.md`; cadeia 992 rows → 265 paths → 229 RETIRE-CHECKOUT-ARCHIVED → 159 branches nomeadas). Prova dupla por branch (`merge-base --is-ancestor` + `git cherry`), baseline `edd7910b`.
- **0/159 DROP-PROVADA — TODAS FICA** (fail-closed; 0/159 ancestrais de main; cherry min 1 / max 183 / mediana 103). **231 recovery refs MANTIDAS.**
- Entregáveis banked: `D51-CHERRY-PROOF-v1.md` sha256 `2fcf1b038f058152a93cdf957d2e60a08ab9645233bc311ff5d7159914818f23` + `D51-CHERRY-LEDGER.json` sha256 `84eb834ff3bd161829071f40c451f3ab16f36f5f24d878984a7de62df27a1a7b` (gate Tier-2 FAIL 5 reparos R1-R5 → re-emissão → re-gate PASS).
- Caso de borda (`codex/feedback-bpmn-refinement-closeout`, FORA do universo): cherry 0 `+` mas ancestry falha por squash → **FICA pela letra**; afrouxar o critério é decisão do dono.

### W7 — 4 de 5 itens executados na janela

7.1 ✓ **#1050** (e2e 5/5 + validador; achado: gate GHAS CAIU — primeira análise code-scanning 2026-10-26T22:42:29Z) · 7.2 ✓ **#1055** (2 canários + 15 recusas; mutação derruba 7/11) · 7.4 ✓ **#1058** (COMPLETO sem drift; distinção jar 2.2.0 × imagem 2.1.0; DL-0006 revisit owner; 2.2.0 já publicado upstream — insumo) · 7.5 ✓ **#1052** (69 itens: 61 NO-PRAZO / 0 VENCIDO / 8 SEM-DATA; R-131 2026-11-11 = 1º a cruzar a janela de aviso ≤14d). **7.3 intocado** (não mandatado nesta janela).

### W8 prep (D-52)

4 pacotes banked no bundle `JOURNEYS-COMPLETENESS-2026-10/`: W8-PRE-OP21-DOSSIER-v1 `aad491b0…` · W8-PRE-DEADLINE-PACKAGES-v1 `7c47d3e6…` · W8-PRE-ROLL-BACKLOG-v1 `6daff61d…` · W8-PRE-SESSION-PLAN-v1 `7bee3c66…` (shas verificados nesta sessão). Multa-base desambiguada: **R$ 506.250 = escalão de 1º/1/2027**; **R$ 337.500 = valor vigente até lá**.

## Status 2026-10-28 (janela W8 CLOSE — execução; D-52)

Registro da execução da janela — tabela fiel aos atos, nada acrescido/suavizado. Cadeia: autor especializado → gate cego opus (FAIL → reparos literais → re-gate de delta) → PR rastreado. Bundle vivo `docs/audits/JOURNEYS-COMPLETENESS-2026-10/` (gitignored); shas conferidos por `cp`+sha nesta sessão de registro.

### Entregas nucleares

- **Reconciliações A–E re-rodadas em 2026-10-28** — 5 arquivos do bundle, um por especialista: `W8-RECON-LEDGERC-v1` `ec76a1ed…` · `W8-RECON-CENSUS-OP21-v1` `4812d168…` · `W8-RECON-C2-MURO-v1` `74faa8a7…` · `W8-RECON-HYGIENE-v1` `dbd7241f…` · `W8-RECON-WAVES-v1` `973bf6eb…`.
- **Relatório de completude** `W8-COMPLETENESS-REPORT-v1.md` `3dd65c5f…` — gate cego Tier-2 **PASS** `72a93120…` (0 bloqueantes; observação menor O-1 reparada) → re-gate de delta **PASS** `c561135a…` (delta zero provado por reconstrução reversa, junto com o anexo).
- **Anexo final** `MASTER-PLAN-ANNEX.md` `db85848f…` — gate **FAIL 3F** `87191adf…` (defeitos citacionais F-1/F-2/F-3) → reparos literais → re-gate de delta **PASS** `c561135a…`; contém a **matriz final** "Estado final das ondas — janela W8" e a **supercessão formal ESTACIONADA** (ato do dono — D-11/D-29; bloco de assinatura vazio).
- **Pacote de assinatura D-29 ESTACIONADO** `W8-D29-SIGNATURE-PACKAGE-v1.md` `87df7c19…` (emenda §4-bis transcrevendo as 8 decisões brokeradas) — gate **FAIL 2F** `032784a7…` → reparos → **DELTA2 PASS** `8adb93ad…` (ESCOPO 1 = pacote D-29; ESCOPO 2 = PR #1065 head `524527d7…`; ESCOPO 3 = PR #1066 head `64640aee…`). **A assinatura D-29 é ato exclusivo do dono** (ADR-0007; bloco §6 do pacote vazio).
- **Backlog ROLL ENTREGUE ao dono** `W8-PRE-ROLL-BACKLOG-v1.md` `6daff61d…` — **35 linhas = 22 (triagem D-49) + 11 (Fora de escopo → ROLL) + 2 (D-48/D-39)**, soma checada; nenhum item executa/flipa por agente.
- **Sessões owner estacionadas**: `W8-OWNER-SESSION-A1A9-PACKAGE-v1.md` `d4d37b14…` (portal A1→A9; A1-A3 conjuntas primeiro, D-34) e `W8-OWNER-SESSION-DPO-PACKAGE-v1.md` `b224de31…` (Lucas DPO — só contrassinaturas; janela aberta desde 10/11), **com o achado de rótulo 43 ≠ 41**: a enumeração viva tem **43 relações** e o rótulo D-25 "41" ficou curto +2 (migrações `0029`/`0030` pós-correção de 2026-10-25) — **emenda do rótulo = dono** (o que o DPO assina é o plano, não o rótulo).

### Decisões brokeradas 2026-10-28

Fonte: `COORDINATION.md` "OWNER DECISIONS 2026-10-28" (sha `5ced0e09…`; pre-append do bloco `db24f43c…` reconstruído pelo gate). As 8:

| # | Decisão do dono | Execução na janela |
|---|---|---|
| 1 | **CO-11 ACK DADO** — dedupe C001–C008 autorizada (fecha GOV-0.4/F-E08) | Execução da dedupe **BLOQUEADA 2×** pelo classificador de permissão da sessão (edição de arquivo compartilhado exige autorização explícita do dono no transcript) — **PENDÊNCIA de re-autorização** |
| 2 | **v30 N2: GO** — Δ=8, harness-only, single-shot, na janela W8 | **EXECUTADA single-shot** (seção abaixo) |
| 3 | **D-51 borda** (`codex/feedback-bpmn-refinement-closeout`) | **EXECUTADA pela sessão v6** — drop com prova cherry 0+; recovery ref criada ANTES do drop (`refs/archive/maezo/fleet-retirement-20261022/codex-feedback-bpmn-refinement-closeout` @ `059010e9…`); refs/archive = **232** (reconcilia os drifts D3/D4) |
| 4 | **ADR-0038 RATIFICADA** — imagem 2.1.0 mantida; DL-0006 revisit = defer deliberado | Registrada no **PR #1065** (merge `213ab51c`; DL-0156 no topo do decisions-log; `docs/adr/README.md` 0038 → Accepted; desambiguação "opção não nomeada" segue dono; gate `b573b6bf…` + DELTA2 ESCOPO 2) |
| 5 | **REG-1.11 + REG-1.12 EXECUTAR** (verbatim já banked); 1.3/1.8/1.10 seguem COUNSEL | **EXECUTADOS** — REG-1.11 **#1066** (merge `4e568589`) · REG-1.12 **#1067** (merge `c7b2da70`); **1.3/1.8/1.10 seguem COUNSEL-4/5 + D-06** |
| 6 | **D-07: emenda com nota datada inline** (registro histórico preservado) | **Aplicada NESTE PR** — nota `[corrigido 2026-10-28: inciso correto VI — ver REG-1.5/#1057]` na linha D-07 |
| 7 | **2 sessões owner batched** — (i) portal A1→A9 (D-34 primeiro); (ii) Lucas DPO (só contrassinaturas; janela aberta desde 10/11) | **Pacotes estacionados** (`d4d37b14…` + `b224de31…` — seção "Entregas nucleares") |
| 8 | **W5 5.3: consertar FONTES dos 16 strict-xfails programa** (flips continuam ROLL; DSR shadow per D-39) | **14 consertados** — #1069 (merge `e7915159`, censo 22→9) + **#1071 (AX-02, censo 8) MERGED `988949f6`** (reabertura do #1070, fechado por deleção da branch-base empilhada); **par CRED deferido ao dono** (Opção D-2 do plano `bba389b7…` — fronteira de guarda); cadeia 22→9→8 = 2 cred (D-2) + 6 teto humano; gate `dc657f39…` |

### v30 N2 (opção (i) do v29 executada)

- Release **`n2-lane-v30`** (publicada 2026-10-28T23:31:59Z; grant `ROOT-N2-R30-20261028-D8`; PR lane **#1068** merge `864c15f2`; gates: FASE A `ded474b5…` PASS + memo `cdb47393…`); run **40789370785** (single-shot; `conclusion=success`, `outcome=FAIL` — a recusa é a evidência).
- **Família NOVA R30-F** (memo `P4-V30-RESULT-v1` `36b15725…`): o corner T3' de Δ=8 **ADMITIU** — 1ª admissão T3'-terminal da história da lane (1º `pg-s00-COMMITTING-*` banked) — **DERIVADA, inbancável no caminho de recusa** (a fidelidade do corner morre com a recusa; `t3_deferred_measured = "not-measured"`); **o muro mudou de lugar**: ~20-40 ms depois, a captura ROOT_SECOND leu o pid 81 **re-armado pelo emission-recheck** (SEVERE `ENGINE-16004` com stack do comando-recheck) → congruência fail-closed `:689`. Desacoplamento D2 provado na forma mais forte (commit + seal + confirmação + resposta + SEGUNDO comando dentro do sono do observador).
- **1º N2 positivo INALCANÇADO no conjunto {"0","0.2","4","8"} — para TODO Δ no desenho atual**: sub-horizonte ⇒ recusa #63; supra-horizonte ⇒ admissão + recusa de congruência; única exceção formal = a **faca do report-lag do `pg_stat_activity`** (janela nunca medida na lane — calibração, não via).
- **Próxima movida = DONO, sem recomendação** — opções (a) repouso · (b) desenho novo da aquisição pós-admissão · (c) re-despatch (benefício NENHUM: desfecho determinístico R30-F).

### Estado dos prazos (inalterados)

- **OP21 1º/1/2027** — escalonamento; multa-base R$ 506.250 = escalão (75%); R$ 337.500 vigente até lá; **65 dias** de 2026-10-28.
- **C2 (fecho da janela de evidências) 2027-01-26** — **90 dias**; na mesma data abre o aviso do gate (`WARNING_WINDOW_DAYS = 14`).
- **Muro 2027-02-09** — **104 dias**; **sem 2ª renovação (D-10)**; a partir daí todo PR fica vermelho no `validate-artifacts` enquanto `shadow`.
- **R-131 2026-11-11** — **janela de aviso ACESA** — única linha ≤14d no dashboard (14 dias exatos de 2026-10-28).

### Pendências-owner na saída

assinatura D-29 (pacote `87df7c19…` estacionado) · CO-11 re-autorização da dedupe · par CRED (Opção D-2) · emenda do rótulo D-25 (41→43) · desambiguação da Opção da ADR-0038 · REG-1.3/1.8/1.10 COUNSEL · sessões batched (i)/(ii) · v30 sucessor (dono, sem recomendação).

## §HDR — Registro de decisões humanas (agentes preparam pacotes, nunca decidem)

**Dono:** D-01 retomada+escopo · D-02 F-M01 (a)/(b) · D-05 valor PT48H · D-07 LA-02 · D-10 ações de prazo · D-11 ratificar supercessão v3↔v4 · D-12 alvos de completude (de 9,0% → ?) · D-15 congelamento do contrato · D-16 erguer A1–A10 · D-21 ingresso Lucas · D-29 go/no-go + fechamento · D-30 broker CA_1 · D-31 papel deriva + casa do GAP-REGISTER · D-33 coordenação de PRs externos (AMH #149; revisão do #907).
**Security-team:** D-14 ativação (GOV-0.10, 6 passos) + o APPROVED do #907 — poste mais longo; pré-estacionado.
**Jurídico:** D-03/D-04 (COUNSEL-2/3; bloqueiam OP21) · D-06 (COUNSEL-5) · D-08 (COUNSEL-6) · D-09 (COUNSEL-4 NBA) · COUNSEL-1/7/8 como insumos do REG-1.13.
**DPO:** **D-25 41 contras-assinaturas do erasure** [corrigido 2026-10-29: contagem viva = 43 (16+25+2 das migrações 0029/0030 pós-correção 2026-10-25 — ver W8-OWNER-SESSION-DPO-PACKAGE-v1.md b224de31…)] (16 originais + 25 relações acréscidas — 9 de ADR-0049 nas migrações 0001-0014 e 16 de migrações 0015-0028; correção 2026-10-25; contagem viva em `src/maezo/platform/lifecycle/erasure_plan.py:9-12` e `docs/compliance/human-windows/LUCAS-DPO-OPERACAO-PACKAGE.md` §0/§1.3) + insumos GP11/M2 + VW4 · D-26 pontes de identidade. **Médico-auditor:** ratificação auth-criteria (nada ratificado hoje), 4 DMNs de triage, C2 até 2027-02-09. **Jurídico/operação/financeiro:** R1–R5 (bloqueiam OP17); revisões de habilitação do lucas.

## Cobertura de findings, verificação e riscos

- **Cobertura:** os 37 findings + condições v4 1–3 + extras (GAP-REGISTER, Java-CI, SBOM/SARIF, deriva, DSR, ledgers) → matriz completa no anexo `docs/audits/JOURNEYS-COMPLETENESS-2026-10/MASTER-PLAN-ANNEX.md`.
- **Verificação:** por item a cadeia da casa (mudanças engine-adjacentes exigem lane live-PG — verde unitário já escondeu defeito fail-closed); por onda 1 gate cego Tier-2; por programa reconciliações com soma checada + lanes CI como aceitação + verdicts (flip-path, 5-gate); reparos de registro conferem sha-a-sha por terceiro.
- **Riscos top-5:** (1) security-team nunca ativada ⇒ #907 inmergeável — D-14 é ato admin na W0, fallback deferral datado, jamais merge forçado; (2) COUNSEL-2/3 deslizam ⇒ OP21 perde 15/12 — tela em shadow c/ bloqueio-dever-vigente agora, âncoras emendadas depois sem retrabalho; (3) colisão c/ WIP do orquestrador paralelo — cercas §0, só v4, CA_1 brokerada; (4) saturação do teto humano — janelas batched nos gates de onda + pacotes prontos (minutos, não sessões); (5) muro 2027-02-09 chega incompleto — W8 em 2027-01-26, dashboard semanal, lista de corte pré-acordada (só stubs W5 e profundidade 6.7 são cortáveis).
- **Fora de escopo (→ ROLL):** todos os flips shadow→enforcing (approvals `status/mode`, `binding_admitted`, flags em prod, estreitamento do allowlist) · endurecer branch-protection além de adicionar o flip-gate · editar v3 · resolver regiões de conflito · implementar OP17 (só disposição) · deploys de produção/AWS além do dev · preencher tabelas de aprovador (ADR-0039) · UI de compra dentro do portal maezo (a vitrine é o portal externo).

## Histórico

- 2026-10-23: plano escrito e aprovado pelo dono (este documento). GOV-0.2/0.3/0.5 executados na mesma data (reparos de registro); GOV-0.4 deferido à brokeragem (cerca §0). Anexo de cobertura publicado. PR de introdução deste doc pela cadeia da casa.
