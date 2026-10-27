# Plano — Programa de Completude Maezo (clientes, vendors, providers, equipe)

**Status:** **ATIVO** — retomada registrada 2026-10-23 (D-01, dono via sessão interativa; ver §Decisões). Todas as ondas despachadas conforme seus gates. **Data:** 23/10/2026 (`main` em `d21aed72`; auditoria-base `docs/audits/JOURNEYS-COMPLETENESS-2026-10/`, gate cego Tier-2/3 fechado). **Autor:** orquestrador (agente), a pedido do dono. **Este documento não aprova nada**: separa quem GERA de quem APROVA (§HDR) e todo bloqueio de aprovação/ratificação permanece com humanos nomeados.

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
| GOV-0.6 | Nota de proveniência: commits provider T1.7/Q2/C1 sem token `(#NNN)` | F-E13 | S | pendente |
| GOV-0.7 | Cadeia PR646 (PL-01…PL-05 `:1227-1231`): executar ou deferir por item | F-P05 (parte) | M | pendente |
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
| REG-1.5 | Prazo reembolso 30d (Lei 9.656 art. 12 VIII) + decisão LA-02 | F-M08 | D-07 |
| REG-1.6 | TISS pin → Julho/2025 (04.02.00/01.05.00) | F-E05 | — |
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

4.1 reconstruir SESSION-CLOSE-QUIESCENCE.json (ledger `23c106f3` + SV-01…10; re-pin) [F-M14] · 4.2 fechar recusa #60 (opção de desenho → v23 → 1º N2 POSITIVO → cauda) [F-P01] · 4.3 lane Linux estável (n2-lane.yml entra em CI; hoje manual-only) [F-P08] · 4.4 GP3/GP4 admitir-ou-aposentar [F-M05]. Aceitação: kit c/ hash; #60 fechado/escalado c/ evidência; lane rodando em CI.

**Status 2026-10-26:** discriminador P4 executado (runs 40396401996 d0 / 40396951024 d200) — recusa não-decisiva em ambos os Δ (física medida 3,22s), D2' provado mecanicamente; adjudicação no kit `P4-RESULT-v1` (gate cego Tier-2 PASS). **D-42** rota v28 = novo regime Δ≥3,5s; **D-45** reparo §6-1 + P0' como PR standalone IMEDIATO. **D-46** (2026-10-27): design da rota (b) D2 pós-commit AUTORIZADO (missão de design gateada nesta janela — análise → gate cego → bank no kit); EXECUÇÃO (blob v29 + release + par P4 de dispatches) gated no 2º sinal do dono após gate PASS. **D-50** (2026-10-27): **2º sinal D2 DADO** (D-46 consumido) — execução do design `DESIGN-D2-POST-COMMIT-v1` (sha256 `8452f755…`; banked no kit com veredito `0e774e69`) AUTORIZADA para a próxima janela: blob v29 conforme o design (máquina COMMITTED-listener; cerca OBRIGATÓRIA F-3 do `:254` — disposição (i)/(ii) provada no PR), release FULL (schema NOVO com os 8 sítios de re-pin da §7.5, incluindo o builder `scripts/ci/build_provider_native_observation.py:40`), par-bracket `{"0.2","4"}` com grants `ROOT-N2-R29-<data>-D0P2/D4`; cadeia da casa integral (autor → gate cego → merge → byte-check; dispatches de lane SERIADOS).

## W5 CLNT — plano de capacidades cliente/admin, em shadow (M/L)

5.1 PRs de habilitação dark por flag (flags de habilitação dark conforme `docs/plans/w5-5-1-flags-reancoragem.md` — inventário real: **31 interruptores/6 grupos**; a citação original a `settings.py:337-591` estava driftada, provado em #953/#967; revisor nomeado + janela de evidência por linha do `docs/review-queue.md`; flags SEGUEM OFF — ROLL flippa) · 5.2 endpoint HTTP de DSR (não existe) + resolução do guard `decisao_dsr` · 5.3 os 3 strict-xfails DSR (consertar fonte antes; flip 1 PR cada c/ delta do censo) · 5.4 as 2 pontes de identidade do erasure construídas dark (**41/41** decisões DPO — ver `docs/compliance/human-windows/LUCAS-DPO-OPERACAO-PACKAGE.md` — permanecem humanas) · 5.5 admin 7/7 (Documents/Operations/Administration hoje stubs + BFF de assignments) ou disposição por área · 5.6 pacotes de evidência para cada pendência humana (médico-auditor clínico; jurídico/DPO/operação do lucas). Aceitação: flags provadamente OFF em prod; e2e DSR verde; censo xfail com delta; 0 linhas DRAFT sem revisor nomeado.

## W6 DISC — gates, gaps e burn-down de ledger (M)

6.1 os 11 gates NOT-RUN: execução cega ou aposentadoria formal cada [F-M05/F-P09] · 6.2 marcador/annotação OP13 [F-M04] · 6.3 disposição OP17/OP19 [F-M03] · 6.4 fato Q10 [F-M13] · 6.5 triagem dos 130 itens de ledger {programa|ROLL|aposentar} [F-P05] · 6.6 244 DEFER-OWNER em janelas batched [F-P06] · 6.7 verificação dos 285/528 rows unverified. Aceitação: tabela de gates com zero NOT-RUN-sem-disposição (reconciliação maquinável); contagens antes/depois ratificadas pelo dono.

**Status 2026-10-26 (D-44):** janela própria da W6 após a conclusão do OP21; nenhum item W6 tem deadline (W8 alvo 2027-01-26). **D-47** (2026-10-27): janela executada NO RESTO DA JANELA de 2026-10-27 — pré-condição cumprida (OP21 concluído 5/5 no merge `853c7730` desta mesma janela); pacotes 6.6 saem prontos p/ janelas batched. **D-49** (2026-10-27): **W6 RATIFICADA/FECHADA pelo dono** — pacote `W6-RATIFICATION-PACKAGE-v1` (sha256 `b1ccf3fa…c10e3e`; gate cego Tier-2 PASS após 2 reparos literais F-1/F-2), apontamento `docs/audits/JOURNEYS-COMPLETENESS-2026-10/W6-RATIFICATION-PACKAGE-v1.md` (gitignored). Contagens antes→depois ratificadas: **11 gates NOT-RUN → 0 sem disposição** (GP5+VW1-P1 EXECUTADO-PASS, vereditos `7b9337b0`/`38f32348`; GP1/GP2 aposentados; GP3/GP4 defer-PW6; GP6-GP10 defer-registrados) · **triagem dos 130 itens = 87 aposentar + 20 programa + 22 ROLL** (D6 resolvida pelo dono: NH-14 → PROGRAMA) · **DEFER-OWNER 244 → 1 vivo** (+229 RETIRE-CHECKOUT-ARCHIVED) · 6.7: **286 rows unverified com plano** e baseline congelada `fd4a59eb…`. Cerca ratificada embutida (6.6-D): NENHUM DROP de archive enquanto a custódia física dos packs não for localizada ou declarada perdida (pasta `~/code/maezo-evidence-archive/fleet-retirement-20261022-104859/` ABSENT hoje; 231 recovery refs + 159 branches verificadas presentes).

## W7 HARD — contínuo (S)

7.1 prova e2e do caminho SARIF (substituto-GHAS) · 7.2 **red-test** do SBOM/cosign (provar bloqueio, não só verde) · 7.3 cercas de regressão nos 3 chokepoints (`start_process_idempotent`, contrato do `engine_deploy.py`, espelho do allowlist `effect_pep.py:174`) · 7.4 pin do tag CIB Seven · 7.5 dashboard de desvios shadow (muro 2027-02-09; só de dados rastreados).

## W8 CLOSE (M; alvo 2027-01-26)

Reconciliação re-rodada em todos os inventários · verificação cega Tier-2 do relatório de completude · pacote de evidências de prazo (OP21 pré-2027; janela C2; muro) · matriz final no anexo + supercessão formal · backlog ROLL entregue ao dono (cada flip = mudança de dados + pacote estacionado) · assinatura do dono (D-29).

**Status 2026-10-27 (D-52):** janela W8 ANTICIPADA para dez-2026 — pacotes de evidência preparados antecipadamente; assinatura D-29 na janela

## §Decisões registradas (2026-10-23, dono, sessão interativa)

| ID | Decisão | Efeito |
|---|---|---|
| D-01 | **RETOMADA — todas as ondas** | `state: ATIVO`; W0/W2/W4/W5 despacham já; W1 espera jurídico (D-03/D-04); W3 espera security (D-14) |
| D-02 | **Mapa primeiro + ponte delimitada SP-OP-nativa** (delegada pelo dono ao orquestrador: "excelência, sem workarounds, considerando impacto nas demais builds") — ver ruling abaixo | CORP-2.7 = mapa 355 elementos → capacidades → processos SP-OP → código/teste; ponte APENAS para lacunas provadas pelo mapa, como NOVOS process-keys SP-OP-* pelo caminho canônico (allowlist + engine_deploy + cercas), NUNCA como os 44 beans AI delegados (esses permanecem vocabulário de desenho mapeado às capacidades existentes do gateway) |
| D-05 | Timers CA_1 = **classes RN 623/2024 art. 12** (urgência imediata; demais 5 dias úteis; PAC/internação eletiva 10 dias úteis) | REG-1.2 |
| D-07 | LA-02 **modelado por inteiro** (coparticipação/faturamento/contas como objetos+tasks explícitos; reembolso 30d art. 12 VIII como timer) | REG-1.5 |
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

## §HDR — Registro de decisões humanas (agentes preparam pacotes, nunca decidem)

**Dono:** D-01 retomada+escopo · D-02 F-M01 (a)/(b) · D-05 valor PT48H · D-07 LA-02 · D-10 ações de prazo · D-11 ratificar supercessão v3↔v4 · D-12 alvos de completude (de 9,0% → ?) · D-15 congelamento do contrato · D-16 erguer A1–A10 · D-21 ingresso Lucas · D-29 go/no-go + fechamento · D-30 broker CA_1 · D-31 papel deriva + casa do GAP-REGISTER · D-33 coordenação de PRs externos (AMH #149; revisão do #907).
**Security-team:** D-14 ativação (GOV-0.10, 6 passos) + o APPROVED do #907 — poste mais longo; pré-estacionado.
**Jurídico:** D-03/D-04 (COUNSEL-2/3; bloqueiam OP21) · D-06 (COUNSEL-5) · D-08 (COUNSEL-6) · D-09 (COUNSEL-4 NBA) · COUNSEL-1/7/8 como insumos do REG-1.13.
**DPO:** **D-25 41 contras-assinaturas do erasure** (16 originais + 25 relações acréscidas — 9 de ADR-0049 nas migrações 0001-0014 e 16 de migrações 0015-0028; correção 2026-10-25; contagem viva em `src/maezo/platform/lifecycle/erasure_plan.py:9-12` e `docs/compliance/human-windows/LUCAS-DPO-OPERACAO-PACKAGE.md` §0/§1.3) + insumos GP11/M2 + VW4 · D-26 pontes de identidade. **Médico-auditor:** ratificação auth-criteria (nada ratificado hoje), 4 DMNs de triage, C2 até 2027-02-09. **Jurídico/operação/financeiro:** R1–R5 (bloqueiam OP17); revisões de habilitação do lucas.

## Cobertura de findings, verificação e riscos

- **Cobertura:** os 37 findings + condições v4 1–3 + extras (GAP-REGISTER, Java-CI, SBOM/SARIF, deriva, DSR, ledgers) → matriz completa no anexo `docs/audits/JOURNEYS-COMPLETENESS-2026-10/MASTER-PLAN-ANNEX.md`.
- **Verificação:** por item a cadeia da casa (mudanças engine-adjacentes exigem lane live-PG — verde unitário já escondeu defeito fail-closed); por onda 1 gate cego Tier-2; por programa reconciliações com soma checada + lanes CI como aceitação + verdicts (flip-path, 5-gate); reparos de registro conferem sha-a-sha por terceiro.
- **Riscos top-5:** (1) security-team nunca ativada ⇒ #907 inmergeável — D-14 é ato admin na W0, fallback deferral datado, jamais merge forçado; (2) COUNSEL-2/3 deslizam ⇒ OP21 perde 15/12 — tela em shadow c/ bloqueio-dever-vigente agora, âncoras emendadas depois sem retrabalho; (3) colisão c/ WIP do orquestrador paralelo — cercas §0, só v4, CA_1 brokerada; (4) saturação do teto humano — janelas batched nos gates de onda + pacotes prontos (minutos, não sessões); (5) muro 2027-02-09 chega incompleto — W8 em 2027-01-26, dashboard semanal, lista de corte pré-acordada (só stubs W5 e profundidade 6.7 são cortáveis).
- **Fora de escopo (→ ROLL):** todos os flips shadow→enforcing (approvals `status/mode`, `binding_admitted`, flags em prod, estreitamento do allowlist) · endurecer branch-protection além de adicionar o flip-gate · editar v3 · resolver regiões de conflito · implementar OP17 (só disposição) · deploys de produção/AWS além do dev · preencher tabelas de aprovador (ADR-0039) · UI de compra dentro do portal maezo (a vitrine é o portal externo).

## Histórico

- 2026-10-23: plano escrito e aprovado pelo dono (este documento). GOV-0.2/0.3/0.5 executados na mesma data (reparos de registro); GOV-0.4 deferido à brokeragem (cerca §0). Anexo de cobertura publicado. PR de introdução deste doc pela cadeia da casa.
