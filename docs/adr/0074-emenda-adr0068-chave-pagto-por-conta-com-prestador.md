# ADR-0074: Emenda ao ADR-0068 — forma `C` da chave PAGTO por conta (com o prestador) e absorção de `MAEZO_PAGTO_CHAVE_CONTA_FASE` em `MAEZO_CHAVES_JANELA`

**Status:** Proposed — DRAFT redigido por agente a partir das decisões do dono DEC-30 D12 e D13 (issue #1084, 2026-11-02). A escolha do dono está citada abaixo; o aceite deste ADR é ato separado do dono (`/docs/adr/` é CODEOWNERS). Nenhum bloco de aprovação foi preenchido (ADR-0007). Nenhuma chave de engine muda por este documento: o código da forma `C` já existe ESCURO (fase padrão `legado`).
**Data:** 2026-11-02
**Área:** Integração / Dados / Privacidade / Pagamentos

## Contexto

### O defeito e a correção já mergeada

O handoff `operadora.contas.handoff_pagamento` (SP-OP-CONTAS-001 → SP-OP-PAGTO-001) abre a ordem de
pagamento com a chave `PAGTO-{t}-{numero_lote_tiss}-{prestador_id}`, sem a conta. CONTAS admite uma
instância por guia/conta (`CONTAS-{t}-{guia}-{conta}`) dividindo o mesmo lote, e PAGTO é a família de
dedup de start `PERMANENT`. A segunda conta do mesmo prestador no mesmo lote batia na ordem da primeira
e ficava sem pagamento. Decisão do dono de 2026-11-01 (opção A): pôr a conta na chave. O PR #1180
(merge `0506ee55`, 2026-11-02) entregou isso ESCURO, atrás de `MAEZO_PAGTO_CHAVE_CONTA_FASE`
(`legado` | `leitura_dupla` | `escrita_alvo`, padrão `legado`), com a forma
`PAGTO-{t}-C-{guia}-{conta}`, e transformou o sucesso silencioso em incidente visível em toda fase.

O ADR-0068 (Accepted, DEC-29, 2026-11-01) é o desenho da janela única de migração de chaves. A forma
por conta é mudança de formato de chave e por isso pertence a essa janela, mas a tabela por processo do
ADR-0068 (§2, linha 4, PAGTO) lista só três formas (`O`, `L`, `G`), e a §3 define UMA configuração de
fase (`MAEZO_CHAVES_JANELA`). O #1180 deixou isso como "emenda proposta"
(`docs/processes/contracts/SP-OP-PAGTO-001.md`, seção "PROPOSTA (aguarda janela)").

### As decisões do dono que este ADR registra

Issue #1084, DEC-30, comentário de 2026-11-02
(https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6575240466), seção
"Pagamentos — chave por conta (#1180)", texto literal:

- "D11: **A** — manter o incidente visível + alerta e procedimento no runbook antes da janela ADR-0068."
- "D12: **em aberto** (detalhamento pedido pelo dono)."
- "D13: **B** — a chave por conta inclui o **prestador** além de guia e conta."

Complemento de D12, mesmo issue, 2026-11-02
(https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6575300581), texto literal:
"a chave PAGTO por conta é registrada por **ADR nova (ADR-0074) que emenda o ADR-0068** (sem editar o
ADR Accepted): acrescenta a forma `PAGTO-{t}-C-{prestador}-{guia}-{conta}` → `H(C,prestador,guia,conta)`
(dado pessoal) e **absorve `MAEZO_PAGTO_CHAVE_CONTA_FASE` em `MAEZO_CHAVES_JANELA`** (uma só janela de
migração); ordens lote conhecidas recebem alias `C` pelo job da janela. Escolha do dono no chat da janela 3."

### Precedente de forma

O ADR-0069 emendou o ADR-0023 por arquivo novo, sem editar o ADR aceito (`docs/adr/README.md`, regra 2).
Este ADR segue o mesmo molde: o arquivo do ADR-0068 não é tocado (os bytes dele são fixados por
`tests/unit/docs/test_adr_aceite_dec29.py`).

## Decisão (proposta)

Este ADR EMENDA o ADR-0068 em dois pontos. Todo o resto do ADR-0068 vale como está.

### 1. Forma `C` na linha 4 (PAGTO) da tabela da §2

A linha 4 do ADR-0068 §2 passa a ler-se com uma quarta forma:

| # | Process key atual → nova | Business key atual | Nova (motor, Q1-c) | Dado pessoal / ID cru | Saída |
|---|---|---|---|---|---|
| 4 | `SP-OP-PAGTO-001` → `maezo-payer-pagto-001` | `PAGTO-{t}-{ordem}`; `PAGTO-{t}-{lote}-{prestador}`; `PAGTO-{t}-{guia}-{glosa}`; **`PAGTO-{t}-C-{prestador}-{guia}-{conta}`** | `H(O,ordem)`; `H(L,lote,prestador)`; `H(G,guia,glosa)`; **`H(C,prestador,guia,conta)`** | G: pessoal; L: pessoal só se o prestador for PF; O: ID cru; **C: pessoal** | idem |

Regras da forma `C`:

- **Etiqueta `C`, componentes nesta ordem:** prestador, guia, conta (D13). O lote NÃO entra: uma conta
  reapresentada noutro lote continua sendo a mesma conta e não pode ser paga duas vezes.
- **Quando é cunhada:** só quando CONTAS adjudicou na forma guia/conta (`numero_guia_tiss` E
  `numero_conta` presentes — o predicado único `maezo.domain.chave_de_negocio.contas_adjudica_por_conta`).
  A forma lote (`L`) não muda.
- **Compositor único:** `maezo.domain.chave_de_negocio.pagto_business_key_por_conta`, com a mesma
  validação de segmento dos demais (`componente`: `str`, não vazio, sem espaço/controle) e o `tenant_id`
  sem `-`. O `ordem_pagamento_id` acompanha a forma: `ORDEM-CONTA-{t}-C-{prestador}-{guia}-{conta}`.
- **Recusa nova na gramática legada (fail-closed):** além da guia com `-` (R-101, já no ADR-0068 §1.3),
  o **prestador com `-`** é recusado na forma `C` em claro. Os dois são segmentos do meio; com `-` em
  qualquer um deles a string deixaria de se decompor de um só jeito. A conta, último segmento, pode
  conter `-`. Na forma alvo `H(C,prestador,guia,conta)` a codificação netstring é injetiva por
  construção e nenhuma dessas recusas é necessária (ADR-0068 §1.3).
- **Conferência de identidade:** um `ALREADY_*` só é sucesso quando a ordem existente é desta conta,
  conferida no histórico do motor por `prestador_id`, `numero_guia_tiss` e `numero_conta` (lidos por
  nome, DL-0050). Prestador ausente no histórico não prova nada (`indeterminada` → recusa).
- **Classificação:** dado pessoal (conta de um beneficiário), a confirmar pelo DPO como as demais
  linhas do ADR-0068 (Q1/Q3). Opaca no motor e na saída sob a Q1-c.

### 2. `MAEZO_PAGTO_CHAVE_CONTA_FASE` é absorvida por `MAEZO_CHAVES_JANELA`

- Há UMA configuração de fase da janela: `MAEZO_CHAVES_JANELA` (ADR-0068 §3). A fase da forma `C` é a
  mesma da janela; não existe virada separada para PAGTO por conta.
- **Mesmas fases.** `legado`, `leitura_dupla` e `escrita_alvo` têm em `MAEZO_PAGTO_CHAVE_CONTA_FASE` o
  mesmo significado que em `MAEZO_CHAVES_JANELA` (leitor antes de escritor). A fase **`so_alvo`** entra
  para a forma `C` pela janela: escreve só a forma alvo, lê só a alvo mais o alias.
- **Até o pacote da janela,** `MAEZO_PAGTO_CHAVE_CONTA_FASE` continua sendo a única chave desta forma
  (padrão `legado`). O pacote da janela remove essa configuração ao introduzir `MAEZO_CHAVES_JANELA`;
  as duas nunca coexistem em produção.
- **Alias `C` pelo job do item 3.** O job de alias da janela (ADR-0068 §3, item 3: só inserção, nenhuma
  linha apagada, nenhuma chave de instância viva reescrita) também insere, para cada ordem lote
  conhecida de UMA conta, a linha de alias da forma `C`. A lista vem do inventário somente-leitura
  `scripts/ops/pagto_chave_conta_inventario.py` (disposição `alias_por_conta`); as disposições
  `indeterminada` e `alias_bloqueado_*` exigem humano antes do alias.
- **Exclusão mútua entre formas.** A ordem inserir-depois-ler do ADR-0068 §3 item 3 vale também entre
  as formas `L` e `C` de PAGTO. O código de #1180 lê o histórico antes de iniciar (ler-depois-inserir);
  por isso o bloqueio de corrida do runbook `docs/runbooks/pagto-chave-por-conta-migracao.md` (§5,
  passo 5) é pré-condição dura da virada para `escrita_alvo`.

### 3. Antes da janela (D11)

Na fase `legado` o defeito continua, visível: incidente auditado em `ST_HandoffPagamento*`
(`ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO`), contador `maezo_contas_pagto_ordem_recusada_total{veredito,fase}`
(rótulos fechados) e alerta `MaezoContasPagtoOrdemDeOutraConta`. O procedimento do financeiro está no
runbook de migração (§3) e em `docs/runbooks/alerts/MaezoContasPagtoOrdemDeOutraConta.md`: identificar a
conta, registrá-la no inventário, reprocessar só depois de `escrita_alvo`, nunca pagar fora do sistema
sem a ordem PAGTO.

## Consequências

**Positivas:**
- A forma por conta ganha registro no ADR da janela, com classificação e forma alvo declaradas.
- Uma só configuração de fase para toda a janela; a forma `C` não pode virar fora de ordem em relação
  às demais famílias.
- Duas contas com a mesma guia/conta de prestadores distintos nunca convergem na mesma ordem.

**Negativas (aceitas):**
- Na gramática legada, prestador com `-` é recusado na forma `C` (incidente). O formato de
  `prestador_id` aceito em outros pontos do repositório admite `-`
  (`src/maezo/platform/prestador_status/ledger.py::PRESTADOR_ID_RE`); para esses prestadores a conta só
  ganha ordem própria na forma alvo opaca.
- A chave de CONTAS (`CONTAS-{t}-{guia}-{conta}`) continua sem o prestador. D13 é decisão sobre a
  chave PAGTO; a chave de CONTAS segue o ADR-0068 como está.
- A forma `C` em claro carrega prestador, guia e conta no motor e nos logs que já registram a chave,
  até a forma opaca da janela (F-058, mesma situação da forma `G` de RECURSO hoje).

## Perguntas abertas (dono; DPO na classificação)

1. **Prestador com `-` na forma `C` em claro.** Se os identificadores reais de prestador contiverem `-`,
   (a) manter a recusa e esperar a forma opaca, (b) antecipar a forma opaca só para PAGTO `C`, ou
   (c) outra codificação do segmento? Nenhuma recomendação foi decidida aqui.
2. **Classificação da forma `C` como dado pessoal** — confirmação do DPO, junto com a Q1/Q3 do ADR-0068.

## Supersedes

—. Emenda o ADR-0068 (§2 linha 4 e §3 "Configuração") sem editar o arquivo dele. Não altera nenhum
outro ADR.
