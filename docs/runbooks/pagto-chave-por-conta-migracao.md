# Chave PAGTO por conta (CONTAS → PAGTO): o defeito, as fases e o plano de migração

**Status:** código ESCURO (fase padrão `legado`). Decisão do dono de 2026-11-01, opção A ("pôr a conta
na chave de pagamento"). Mudança de formato de chave: pertence à janela única de migração de chaves do
ADR-0068 (`docs/adr/0068-migracao-chaves-maezo-payer-janela-unica-dual-read.md`, **Proposed**). Este
runbook é o plano; **executá-lo é ato do dono dentro da janela**, nunca de agente.

Contratos: `docs/processes/contracts/SP-OP-PAGTO-001.md` §"Business key" e
`docs/processes/contracts/SP-OP-CONTAS-001.md` §"Business key". Código:
`src/maezo/tools/workers/contas.py` (`handoff_pagamento`), `src/maezo/domain/chave_de_negocio.py`
(`pagto_business_key_por_conta`). Inventário: `scripts/ops/pagto_chave_conta_inventario.py`.

## 1. O defeito, em linguagem simples

A operadora pode analisar contas **uma a uma** (forma guia/conta de SP-OP-CONTAS-001: uma instância por
`CONTAS-{t}-{guia}-{conta}`), e várias contas do mesmo prestador chegam no mesmo lote. Quando uma conta é
aprovada, o worker `operadora.contas.handoff_pagamento` abre a ordem de pagamento (SP-OP-PAGTO-001) com a
chave `PAGTO-{t}-{lote}-{prestador}` — **sem a conta**. PAGTO é a família de dedup de start `PERMANENT`
(uma chave que já foi iniciada nunca é iniciada de novo, sem comparar o conteúdo). Resultado: a primeira
conta ganha a ordem; a segunda bate na ordem da primeira, recebia "já existe" e o handoff registrava
**sucesso**. A segunda conta ficava sem pagamento, sem ninguém saber.

## 2. A correção e as fases

A chave fica precisa e o dedup continua estrito: na forma guia/conta, a chave passa a ser
`PAGTO-{t}-C-{numero_guia_tiss}-{numero_conta}` (etiqueta `C`; guia com `-` recusada, decisão do dono
R-101), e o `ordem_pagamento_id` acompanha (`ORDEM-CONTA-{t}-C-{guia}-{conta}`). A forma lote
(`CONTAS-{t}-{lote}`) não muda de chave. O handoff semeia também `numero_conta` na ordem.

A fase vem de `MAEZO_PAGTO_CHAVE_CONTA_FASE` (worker-daemon; domínio fechado — valor desconhecido derruba
o boot):

| Fase | Escreve (forma guia/conta) | Lê antes de escrever |
|---|---|---|
| `legado` (padrão) | `PAGTO-{t}-{lote}-{prestador}` | nada (byte-idêntico na chave) |
| `leitura_dupla` | `PAGTO-{t}-{lote}-{prestador}` | a ordem DESTA conta em `PAGTO-{t}-C-…` → se existe, é ela |
| `escrita_alvo` | `PAGTO-{t}-C-{guia}-{conta}` | a ordem lote: desta conta → é ela; de OUTRA conta comprovada → segue; indeterminada → recusa |

**Em toda fase** um "já existe" do start só é sucesso quando a ordem existente é DESTA conta (conferido
no histórico do motor por `numero_guia_tiss`/`numero_conta`, costura `resolve_historic_instance` de
#1167). Senão o handoff **recusa** com `ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO` → incidente auditado em
`ST_HandoffPagamento*`.

## 3. Com o padrão (`legado`), o defeito continua — mas visível

Até a janela abrir, a segunda conta do mesmo prestador no mesmo lote **continua sem ordem**. A diferença
é que agora isso aparece:

- incidente no motor em `ST_HandoffPagamentoAuto`/`Humano`/`Parcial`, com a mensagem
  `... ja existe ordem SP-OP-PAGTO-001 sob '...' ... NAO e' comprovadamente desta conta (veredito=...)`;
- log de erro `contas_handoff_pagamento_ordem_de_outra_conta` (sem guia/conta no log), com
  `veredito=outra` (ordem de outra conta) ou `veredito=indeterminada` (ordem criada antes da conta entrar
  na semente, ou histórico que não acha a ordem).

**O que fazer com o incidente hoje:** não reprocessar (falha igual, de propósito) e não criar ordem à mão
fora do PAGTO. Registrar a conta na lista de pendências da janela. Na fase `escrita_alvo`, o "Retry" do
incidente faz o handoff ler a ordem lote, provar que é de outra conta e abrir a ordem própria desta
conta (seção 5, passo 5). Se o dono quiser uma mitigação ANTES da janela, é decisão dele (ver PR).

## 4. Rotação de réplicas: leitor antes de escritor

Uma réplica em `escrita_alvo` grava na chave `C`; uma réplica ainda em `legado` não lê essa chave e, num
reenvio da mesma conta, abriria uma segunda ordem na chave lote. Por isso: **todas** as réplicas em
`leitura_dupla` antes de **qualquer** uma em `escrita_alvo` (a mesma ordem do ADR-0068 §3).

## 5. Plano de migração das ordens já criadas sob a chave lote (executar NA janela)

Pré-condições: as do ADR-0068 §4, mais este código mergeado escuro e a suíte de integração verde.

1. **Exportar (somente leitura)** do motor, por tenant:
   - ordens PAGTO: `GET /history/process-instance?processDefinitionKey=SP-OP-PAGTO-001&tenantIdIn={t}`
     e, por instância, só estas variáveis por nome (`GET /history/variable-instance?processInstanceIdIn=…
     &variableName=…`): `tenant_id`, `numero_lote_tiss`, `prestador_id`, `numero_guia_tiss`, `numero_conta`;
   - instâncias CONTAS: idem para `SP-OP-CONTAS-001`, variáveis `numero_guia_tiss`, `numero_conta`,
     `pagto_instance_id`, `pagto_already_existed`.
   Gravar como listas JSON no formato do cabeçalho de `scripts/ops/pagto_chave_conta_inventario.py`. Nunca
   "todas as variáveis" (DL-0050).
2. **Inventariar:** `python scripts/ops/pagto_chave_conta_inventario.py --pagto pagto.json --contas
   contas.json --estrito > inventario.json`. O script não toca o motor nem o banco. Disposições:
   - `alias_por_conta` — ordem lote de UMA conta conhecida (semeada depois deste pacote): chave `C`
     proposta em `chave_por_conta`;
   - `forma_lote` — CONTAS rodou na forma lote: a chave lote é a identidade; nada a fazer;
   - `ja_por_conta` — nasceu em `escrita_alvo`;
   - `fora_de_escopo` — formas de Andre (`PAGTO-{t}-{ordem}`) e RECURSO (`PAGTO-{t}-{guia}-{glosa}`);
   - `indeterminada` — ordem lote criada antes de `numero_conta` entrar na semente: reconciliação humana
     (qual conta ela paga) contra as instâncias CONTAS do mesmo lote e prestador;
   - `alias_bloqueado_guia_com_hifen` — a guia tem `-`: a chave `C` recusa (R-101); humano decide;
   - `contas_sem_ordem` — contas guia/conta cujo handoff disse "já existe" para a ordem de outra conta:
     **os pagamentos perdidos**.
3. **Réplicas `legado` → `leitura_dupla`** (seção 4).
4. **Alias dos claims estritos (insert-only):** para cada `alias_por_conta` (e para cada `indeterminada`
   que o humano atribuir a uma conta), o job da janela do ADR-0068 §3 item 3 INSERE em `audit_emit_dedup`
   uma linha com `dedup_key = {t}:start:SP-OP-PAGTO-001:{chave_por_conta}`. A linha legada nunca é apagada
   nem alterada; a business key da instância viva nunca é reescrita. Conferir: alias = ordens atribuídas.
   (O job não existe neste PR — é do pacote da janela.)
5. **`leitura_dupla` → `escrita_alvo`** e, em seguida, "Retry" dos incidentes da seção 3. Cada retry lê a
   ordem lote, comprova que é de outra conta e abre a ordem própria — que passa pela
   `UT_AnaliseAdmissibilidade` humana obrigatória de PAGTO (I-PAGTO-1), como qualquer ordem. Uma
   `indeterminada` não atribuída continua recusando (correto: pode ser a mesma conta).
6. **Smoke em sintético:** duas contas do mesmo prestador no mesmo lote → duas ordens `C`; reenvio da
   mesma conta → a mesma ordem; forma lote → chave lote.

**Reversão.** `escrita_alvo` → `leitura_dupla` é sempre segura (a leitura dupla enxerga as ordens `C`).
`leitura_dupla` → `legado` só com ZERO ordens `C` no histórico e ZERO claims com chave `PAGTO-{t}-C-`:
em `legado` nada lê a chave `C`, e o reenvio de uma conta já paga sob ela abriria uma segunda ordem na
chave lote. Nenhuma reversão apaga alias nem reescreve chave.

## 6. Limites declarados

- **Claims órfãos legados:** na `escrita_alvo` a leitura dupla consulta o HISTÓRICO do motor da chave
  lote, não a tabela de claims. Um claim lote órfão (POST que falhou depois do claim) não é visto; o
  ADR-0068 §3 item 3 (inserir-depois-ler entre formas) é do pacote da janela.
- **Saída opaca (F-058):** a chave `C` carrega guia e conta em claro no motor, nos logs que já registram
  `pagto_business_key` e no `_business_key` dos eventos — como a forma guia+glosa de RECURSO já faz hoje.
  A forma opaca `{t}:pagto-001:hk1_…` sobre `C-{guia}-{conta}` é da janela (emenda proposta ao ADR-0068).
- **Andre sem chave do motor:** se a chave da instância não chegar a Andre, ele deriva
  `PAGTO-{t}-{ordem}` (ADR-0068 §3 item 10, pré-existente, também para a forma lote).
- **Correlação por business key** (`msg.pagto.dados_corrigidos`): quem correlacionar uma ordem `C` precisa
  da chave da própria instância, não de uma recomposta por lote/prestador.
