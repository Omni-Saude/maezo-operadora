# W5 CLNT 5.1 — Re-ancoragem das flags de habilitação dark + inventário real (fase 1)

**Status:** `MAPA/CERCA` — pacote de re-ancoragem do item 5.1 (fase 1): o inventário REAL do que existe, a prova mecanizada de que as flags do programa seguem OFF e a fila do que exige humano. **Data:** 2026-10-24 (`main` em `dc544a0e`). **Nenhuma flag foi flipada** — todos os defaults de código continuam exatamente como estão; virar chave é ato da onda ROLL (§5). Base de código: checkout limpo de `origin/main` `dc544a0e` (worktree). Regra de leitura: toda afirmação de fato traz `arquivo:linha` ou `PR#`; o que não foi executado está marcado.

---

## §1 A divergência da citação do plano — provada, com o snapshot da época

O item 5.1 do programa diz: "PRs de habilitação dark por flag (**16 flags do `settings.py:337-591**`; revisor nomeado + janela de evidência por linha do `docs/review-queue.md`; flags SEGUEM OFF — ROLL flippa)".

**A citação não corresponde a nenhum conjunto identificável em nenhuma fonte atual — nem no snapshot da época:**

1. **O maior `settings.py` do repo é o da Helena** — `src/maezo/platform/webhooks/whatsapp/settings.py`, 827 linhas. O mapa 5.5 (`docs/plans/w5-admin-disposition.md` §6.4) já tinha provado que suas linhas 337-591 contêm campos da Helena, não flags de admin. Este pacote foi além e testou a hipótese restante — que o range capturasse 16 flags no snapshot em que o plano foi escrito:
   - No commit do plano (`5eb267d5`, 2026-10-23 14:49) e no estado imediatamente anterior (`c1a6a64d`, 2026-10-22): o arquivo é byte-a-byte o layout de hoje (827 linhas).
   - As linhas 337-591 contêm **14 campos `bool`** (13 `default=False` + `acesso_limite_cpf` `default=True` em `:514`) intercalados com campos `Literal`/`str`/`int`/`float` do roteador e da cobertura (`:395-522`) — não 16.
   - O arquivo inteiro tem **19 campos `bool`** (17 `default=False`, 2 `default=True`).
   - Nenhum recorte (bool no range, bool no arquivo, default-False no arquivo) produz 16.
2. **Nenhum outro `settings.py` chega perto:** `worker_runtime` 195 linhas (2 flags dark), `intake_dispatch` 131, `inference` 139, `amh` 150, `agent_runtime` 104 (1 flag), `agente_remoto` 43, `gateway` 83 (nenhuma). Nenhum registra "16 flags de habilitação".
3. **A única ocorrência de "16 flags" em todo o repo + bundle de auditoria é `MASTER-PLAN-ANNEX.md:53`** (sha256 `8d1d4be7…`, snapshot consumido sob protocolo casa): a linha de mapeamento "| 16 flags OFF + stubs admin | CLNT-5.1/5.5/5.6 |" — **sem enumeração**. O "16" mais plausível é o eco do próprio plano, que usa 16 em três outros lugares: "16/16 decisões DPO permanecem humanas" (item 5.4, `docs/plans/maezo-completion-program.md:97`), as 16 IDs do §Decisões (15 linhas, com D-31a e D-31b) e as "16 contra-assinaturas do erasure" do DPO (D-25). É uma caracterização de anexo, não um inventário.
4. **A segunda premissa do 5.1 também não se sustenta como dito:** "`docs/review-queue.md`" organiza revisor + janela **por ARTEFATO** (tabelas DMN, BPMN, contratos, XSD), não por flag — nenhuma linha da fila nomeia `lgpd_dsr_channel`, `staff_documents_panel`, `staff_operations_panel`, as pontes de erasure, `denial_notice_owner` ou qualquer flag de Helena (grep na fila, `dc544a0e`). As DUAS entradas da fila que citam um interruptor de habilitação com revisor nomeado são `REEMBOLSO-AUTO-OVERPAY` (`docs/review-queue.md:1750`) e o pin TISS T2.6-2 (`docs/review-queue.md:690`) — ambas já com o build dark merged (§3, grupo F).

**Conclusão da re-ancoragem:** o "16" não é um conjunto verificável e NENHUMA flag foi inventada para fechá-lo. O §2 é o inventário real do que existe hoje; a cerca do §4 entrega o único subproduto do 5.1 que faltava e não exige decisão humana ("flags provadamente OFF em prod", critério de aceitação do 5.1); o §5 registra a fila humana.

## §2 Critério do inventário

"Flag de habilitação dark" = interruptor nomeado (campo de `BaseSettings`, constante de módulo, par `env`+literal) cuja ligação habilita uma superfície/feature que hoje não existe em produção, com default OFF e a ligação reservada a um ato próprio (ROLL/dono/DL). Ficam de fora: fatos de worker (`bool=False` de DTOs em `tools/workers/*`), knobs de config não-habilitadores e perfis de `capabilities` (conjunto fechado `Literal`, `src/maezo/portal/api/config.py:77-79`).

## §3 Inventário real — soma checada: 31 interruptores em 6 grupos

### Grupo A — Flags dark do Programa de Completude W5 (5; todas com PR dark merged; alvo da cerca deste pacote)

| # | Flag (env) | Local | Default | PR dark | Revisor/janela da habilitação |
|---|---|---|---|---|---|
| A1 | `lgpd_dsr_channel` (`MAEZO_PORTAL_LGPD_DSR_CHANNEL`) | `src/maezo/portal/api/config.py:52` | `False` | **#955** (5.2) | ROLL a nomear — a docstring shipped da flag reserva "janela de evidência e revisor nomeados" para o ato da ROLL (`config.py:46-51`); OFF responde 403 antes de qualquer efeito (`test_dsr_endpoint.py`) |
| A2 | `staff_documents_panel` (`MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL`) | `config.py:61` | `False` | **#960** (5.5-p1) | ROLL a nomear (`config.py:53-60`); OFF = stub byte-a-byte + sessão sem sinal (`test_human_session.py`, `StaffCapabilities.test.tsx`) |
| A3 | `staff_operations_panel` (`MAEZO_PORTAL_STAFF_OPERATIONS_PANEL`) | `config.py:71` | `False` | **#963** (5.5-p2) | ROLL a nomear (`config.py:62-70`); idem |
| A4 | `MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED` | `src/maezo/platform/identity_bridges.py:92` | OFF — só o literal `true` habilita (`bridge_flag_enabled`, `:203-213`) | **#965** (5.4) | ROLL a nomear (`identity_bridges.py:19-24`); OFF = recusa `flag_off` antes de qualquer travessia; zero caller de produção pinado |
| A5 | `MAEZO_ERASURE_BRIDGE_THREAD_ENABLED` | `identity_bridges.py:95` | OFF (idem) | **#965** (5.4) | idem |

**Soma parcial: 5.** Nenhuma menção a nenhum dos 5 nomes de env em `deploy/**` (grep em `dc544a0e`; provado pela cerca do §4).

### Grupo B — Portões interinos DL-0049 do portal (3; já cercados por CI, fora da cerca nova)

| # | Flag | Local | Default | Estado |
|---|---|---|---|---|
| B1 | `direct_completion` (`MAEZO_PORTAL_DIRECT_COMPLETION`) | `src/maezo/portal/api/config.py:37` | `False` | Cercado por `scripts/ci/check_portal_direct_completion.py` (7 cercas) + `tests/unit/ci/test_check_portal_direct_completion.py`; end-state = REMOÇÃO com o relay de #1145 (`config.py:31-36`) |
| B2 | `direct_completion` do perfil de produção (`PortalProductionSettings`) | `src/maezo/gateway/staff_cases/production_config.py:176` | `False` | Mesmo env, portão INDEPENDENTE por desenho (`production_config.py:169-175`); mesma cerca |
| B3 | `cors_origins` (`MAEZO_PORTAL_CORS_ORIGINS`) | `config.py:45` | `""` (nenhuma origem cross-origin) | Mesma cerca (curinga proibido, recorte por caminho obrigatório) |

**Soma parcial: 3** (A+B = 8).

### Grupo C — Runtime de workers/agentes (3; postura própria anterior ao programa)

| # | Flag (env) | Local | Default | Estado |
|---|---|---|---|---|
| C1 | `denial_notice_owner` (`MAEZO_DENIAL_NOTICE_OWNER`) | `src/maezo/runtime/worker_runtime/settings.py:83` | `False` | LÁPIDE desde o R-004 (F-032; decisão do dono DEC-01, issue #1081): o dono nativo de `operadora.auth.send_denial_notice` (WP-J1-06) foi removido; ligada, o daemon recusa registrar os workers (`settings.py:69-83`). A entrega por escrito da negativa tem a sua própria flag dark, `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` |
| C2 | `document_request_host_installed` (`MAEZO_AUTH_DOCUMENT_REQUEST_HOST`) | `settings.py:141` | `False` | WP-J1-03/decisão #18 do dono: exclusividade do tópico de documentos; ligar sem instalar a ponte deixa o tópico sem consumidor — visível em `/readyz` (`settings.py:126-141`) |
| C3 | `agent_ingress_enabled` (`MAEZO_AGENT_INGRESS_ENABLED`) | `src/maezo/runtime/agent_runtime/settings.py:71` | `False` | **Declarada em deploy dev** `deploy/aws-ecs/envs/dev-sa-east-1/service-agents.tf:244` — ligada SÓ para o agente `rafael` (`each.key == "rafael" ? "1" : "0"`); postura própria, fora do escopo da cerca nova |

**Soma parcial: 3** (A..C = 11).

### Grupo D — Lembretes (1)

| # | Flag (env) | Local | Default | Estado |
|---|---|---|---|---|
| D1 | `habilitado` (`MAEZO_LEMBRETE_COBRANCA`) | `src/maezo/platform/lembretes/config.py:33` | `False` (+ `dry_run=True` em `:34`) | Declarada em deploy dev via variável com default false (`deploy/aws-ecs/envs/dev-sa-east-1/task-lembrete-cobranca.tf:7,81`); duplo portão habilitado+dry_run |

**Soma parcial: 1** (A..D = 12).

### Grupo E — Helena/Lucas WhatsApp (17 default-False + 2 exceções default-True; o bloco a que a citação driftada apontava)

`src/maezo/platform/webhooks/whatsapp/settings.py` — todos com `MAEZO_*` alias, default `False`, cada um com a decisão (DL-*/ADR-*/decisão do dono) citada na própria docstring do campo e registrada em `docs/decisions-log.md`; postura: SO' em DEV, reversível, vários pendentes de ratificação (médico-auditor/DPO) — os envs são variáveis declaradas em `deploy/aws-ecs/envs/dev-sa-east-1/variables.tf:776+` com default false:

| # | Flag | Linha | # | Flag | Linha |
|---|---|---|---|---|---|
| E1 | `ack_then_queue` | `:179` | E10 | `helena_busca_semantica` | `:456` |
| E2 | `devolve_turno` | `:258` | E11 | `helena_consultas_amh` | `:487` |
| E3 | `roteador_lucas_enabled` | `:270` | E12 | `acesso_beneficiario` | `:498` |
| E4 | `helena_identidade_amh` | `:314` | E13 | `lucas_consulta_valores` | `:536` |
| E5 | `helena_historico` (DL-0080) | `:341` | E14 | `cancel_pf_v1` | `:546` |
| E6 | `helena_triagem_v2` (DL-0104) | `:350` | E15 | `lucas_v4` (DL-0114) | `:559` |
| E7 | `helena_v4` (DL-0115) | `:360` | — | **exceções default-True (2, por decisão):** | |
| E8 | `helena_carteirinha_completa` | `:369` | E16 | `memoria_clinica_enabled` | `:195` |
| E9 | `helena_dependentes_nome` | `:378` | E17 | `acesso_limite_cpf` | `:514` |

(`helena_base_saude` `:388` e `helena_saude_gerativa` `:442` completam os 17 default-False; a coluna dupla acima lista 15 + as 2 exceções + os 2 desta nota = 19 campos no total, dos quais **17 default-False**. Validadores de dependência entre flags: `settings.py:565-599` — `_lucas_v4_exige_valores`, `_consultas_exigem_identidade`, `_valores_exigem_verificacao`.)

**Soma parcial: 19** (A..E = 31).

### Grupo F — Interruptores com par (flag, revisor, janela) COMPLETO na review-queue (2; build dark merged, flip exige humano)

| # | Interruptor | Local | Default | Revisor nomeado + janela |
|---|---|---|---|---|
| F1 | `REEMBOLSO_AUTO_PAGAMENTO_LIBERADO` | `src/maezo/tools/workers/reembolso.py:214` | `False` | **COMPLETO** — `docs/review-queue.md:1750` (`REEMBOLSO-AUTO-OVERPAY`, R-058/R-059): revisor atuária (finanças) + regulatório verifica; janela "pendente de assinatura de atuária"; ordem obrigatória R-157: assinatura ANTES do teto D-07 subir de zero — virar a constante é o MESMO ato que traz a fórmula assinada. Build dark merged (bloqueio genérico fail-closed no `Flow_GW_AutoAprovar`) |
| F2 | Ratificação do pin TISS (T2.6-2, data-gated — não wired) | `spec/policies/ans/tiss-schema-pin.yaml` | `status: DRAFT`, `ratificado: false` | **COMPLETO** — `docs/review-queue.md:690`: regulatório-ANS + jurídico/regulatório (XSD real) + segurança/compliance (CODEOWNERS do pin); ativação = 4 campos independentes (loader recusa ratificação forjada, `test_forged_ratificado_true_with_status_still_draft_refuses`); consumir o portão em `ans_submit.validate_data` é mudança revisada separada — não decidida |

**Soma parcial: 2. TOTAL GERAL: 5+3+3+1+19+2 = 31 interruptores enumerados** (29 default-OFF + 2 default-True por decisão explícita registrada).

## §4 O que este pacote entrega — a cerca das flags do programa (grupos A)

O subproduto do 5.1 que faltava e NÃO exige decisão humana é o critério de aceitação "flags provadamente OFF em prod". Até este pacote, a proibição de deploy das 5 flags do programa era só a frase de cada docstring; a cerca de DL-0049 cobria só `direct_completion`/`cors_origins` e a reconciliação de env (`check_chart_env_reconciliation.py`) confere declared-vs-read, nunca ausência de flag.

**Entrou (molde `check_portal_direct_completion.py` + `check_helm_entrypoints.py`):**

- `scripts/ci/check_program_dark_flags.py` — 3 cercas fail-closed: (1) defaults **literalmente `False`** para A1/A2/A3 por AST (campo não-literal, duplicado, ausente, classe ausente, arquivo ilegível e `env_prefix` ≠ `"MAEZO_PORTAL_"` são REPROVAÇÃO, não silêncio); (2) **zero menção** a qualquer um dos 5 nomes de env em qualquer arquivo de `deploy/**` (um template não tem ambiente provável — nem comentado passa; `deploy/` ausente/vazio reprova: varrer nada não é prova); (3) as constantes `TITULAR_BRIDGE_FLAG_ENV`/`THREAD_BRIDGE_FLAG_ENV` continuam valendo os nomes pinados (renomear flag sem re-pin da cerca fica vermelho).
- `tests/unit/ci/test_check_program_dark_flags.py` — **24 testes** no molde da casa: árvore real verde **não-vácua**; prova de mutação (default `→ True`, default não-literal, campo removido, classe removida, `env_prefix` trocado — cada um vermelho nomeando); deploy (.tf/.yaml/compose/.md, menção em subdiretório, `:linha` no achado); vacuidade (deploy ausente/vazio); pinos das pontes (renomeada/ausente → vermelho).
- Wiring: `make check-program-dark-flags` + step **"Program dark-flags provably OFF gate (W5 CLNT 5.1)"** no job `artifact-validation` do `.github/workflows/ci.yml`.

**Escopo honesto da prova:** a cerca prova a ÁRVORE rastreada (defaults + `deploy/**`), que é o que o CI pode ver; não substitui o comportamento fail-closed de cada flag (já pinado por `test_dsr_endpoint.py`, `test_human_session.py`, `test_identity_bridges.py`, `StaffCapabilities.test.tsx`) nem prova o ambiente deployado em execução. **Nenhuma flag coberta por este pacote tem menção em `deploy/**` hoje** (o verde da cerca em `dc544a0e` é a prova atual).

**Caminho de flip (inalterado):** ROLL liga cada flag com revisor nomeado + janela de evidência próprios (a nomear NO ato, conforme a docstring shipped de cada flag), num PR que re-pin Esta cerca junto (o teste de pino das pontes e a asserção de default tornam o flip de default um PR consciente, nunca um acidente).

## §5 Fila humana explícita — o que este pacote NÃO fez de propósito

1. **Revisor + janela POR flag (grupos A/C/D/E):** o desenho shipped das flags do programa reserva a nomeação para o ato da ROLL — nomear revisor agora seria inventar governança que as docstrings não declaram; para Helena/Lucas, a base de habilitação é o registro DL-* de cada campo (decisões do dono) + ratificações pendentes (médico-auditor/DPO), não a review-queue. **Dono decide** se quer antecipar a nomeação por flag (emenda do 5.1) ou manter como está.
2. **F1 (reembolso automático):** assinatura de atuária + verificação regulatória (`review-queue.md:1750`) — humano, fora do escopo de engenharia.
3. **F2 (pin TISS):** XSD real da ANS + ratificação 4-campos + decisão de wiring (`review-queue.md:690`) — humano.
4. **Área administration (5.5):** permanece DISPOR-DEFER condicionada à decisão de autoridade de leitura do dono (`docs/plans/w5-admin-disposition.md` §3.7).
5. **Itens 5.3 (xfails DSR) e 5.6 (pacotes de evidência):** ondas próprias do W5, não tocados aqui.

## §6 Cercas respeitadas e fora de escopo

- **Nenhuma flag flipada; nenhum default alterado; nenhum `deploy/**` tocado.** A cerca reprova justamente o que este pacote não fez.
- Arquivos tocados: `scripts/ci/check_program_dark_flags.py` (novo), `tests/unit/ci/test_check_program_dark_flags.py` (novo), `Makefile` (+alvo), `.github/workflows/ci.yml` (+step), `docs/plans/w5-5-1-flags-reancoragem.md` (este).
- `/scripts/ci/`, `/Makefile` e `/.github/` são CODEOWNED (`@rodaquino-OMNI @Omni-Saude/security-team`, `.github/CODEOWNERS:288`); a fila nomeia o mesmo dono para mudanças nesses caminhos (`docs/review-queue.md:1583`, linha AF-01/R-001).
- Não tocados: PLANS.md, COORDINATION.md, `docs/processes/New_BPMN/*_v3.bpmn`, `docs/processes/provider-orchestrators/`, `.claude/` do checkout principal (WIP do orquestrador paralelo), `portal/admin/*` (cerca "never public portal routes").
- Snapshot de auditoria consumido sob protocolo casa (cópia + sha256 antes da leitura): `MASTER-PLAN-ANNEX.md` `8d1d4be7…` (bate com o pin do mapa 5.5, §7), `FINDINGS.md` `ba2ed41e…`, `INVENTORY.md` `30dee152…`.

## Histórico

- 2026-10-24: re-ancoragem escrita + cerca das 5 flags do programa (pacote W5 CLNT 5.1 fase 1; zero flip).
