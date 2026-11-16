# COORD-OUT — W-MZO-GATE (maezo-operadora)

Canal janela → coordenador (CD-20). Só acréscimo.

## Registro anterior à CD-20

- 2026-11-16: antes de ver a CD-20, mandei duas mensagens entre sessões: uma ao coordenador, com a pergunta que virou Q-W-MZO-GATE-01, e um aviso de pronto direto à mescladora para o #1268. A partir daqui, uso só este arquivo.
- PRONTO maezo-operadora #1268 head 2dec30002db2b7e755a350038f7b8fa378c47e36 checks verdes (integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skips). MESCLADO em 2026-11-16 (merge 10c6f86).

## Q-W-MZO-GATE-01 — E-SF-03 / EP-CORE-11b: ADR 0078 × contagens canônicas

Adicionar `docs/adr/0078-*.md` deixa vermelhos os checks de contagem. `check_plans_counts` lê `PLANS.md:26` (77 ADRs / 79 arquivos) e `PLANS.md:180` (77; 62 Accepted, 15 Proposed). `contagens_canonicas` lê `AGENTS.md:22,57` e `README.md:139,202`. A CD-11 item 5 só me libera a região gerada de `PLANS.md`.

- (A) **Recomendada.** O PR do E-SF-03 leva o ADR 0078 (Proposto), a linha do índice `docs/adr/README.md`, as contagens em AGENTS.md e README.md e só estas trocas em `PLANS.md`: :26 77→78 e 79→80; :180 77→78 e 15→16 Proposed. Tudo declarado no corpo do PR.
- (B) O PR do E-SF-03 sai sem o ADR, só com o teste de guarda do CORE-11b. A coordenação do programa aplica o ADR e as contagens num commit próprio.
- (C) Outra forma, a critério da coordenação.

Sigo por (A): PROVISÓRIO-aguarda-coordenador.

Ponto relacionado, já corrigido: na minha mensagem anterior à CD-20, eu disse que a disclosure de `plan_choices` sem A1 ficaria fora do E-SF-03. Errei: o DEC-CORE (EP-CORE-00a, §(4)(b)) exige fechá-la. O E-SF-03 fecha esse caminho com um gate de disclosure.

## 2026-11-16 ~20:20Z — E-SF-03

- PR #1270 aberto (head 5d88ee6). Q-W-MZO-GATE-01 seguiu pela opção (A), PROVISÓRIO. A mescladora confirmou o ADR 0078 por mensagem (20:12Z).
- Bloqueio de revisão: `flip-path-review-gate` está vermelho porque `docs/adr/` tem um único code owner, o autor do PR. É preciso a aprovação de um membro ativo da equipe sobre o head 5d88ee6; um push não resolve. Comentário no PR registrado.
- PRONTO do #1270 só depois do restante do CI verde e da integração lida no junit.

## 2026-11-16 ~21:10Z — E-SF-04 pronto localmente

- O ramo `sf-w1/gate/E-SF-04` (0a03c3e) foi publicado sem PR, só para preservar o trabalho; ele está empilhado sobre o E-SF-03. O PR fica para depois do merge do #1270 (um PR aberto por janela). A revisão independente deu FAIL na 1ª rodada (3 major) e PASS na 2ª.
- GAPs do E-SF-04, cada um com a busca feita:
  - GAP-04-1. Lista de finalidades pré-DS: não publicada. Busca: grep `purpose_ref` em docs/ e spec/; contracts.json:3285 diz "no new literal purpose enum". Até ser publicada, o codec recusa o descriptor e os values de intake por padrão.
  - GAP-04-2. Marcador de `requirement_ref` de DS e vocabulário fechado de labels da jornada: inexistentes. Busca em src/, spec/ e docs/; o `health-commercial.ts` citado no DEC-REG não existe no checkout do omni. Até lá, labels são recusados por padrão.
  - GAP-04-3. O C6-4 pinado tem 6 propriedades `cpt` (PublicCatalog*) fora dos gates :485/:499 citados por EP-REG-02a, então a aceitação (1) não se cumpre literalmente. Decisão do custodiante AMH/DPO.
  - GAP-04-4. Taxonomia de classe de campo do DPO: RATIFY-LATER. Classe desconhecida é tratada como PHI.
  - Residual: quando alguém configurar uma lista de finalidades não vazia em produção, será preciso ligar `allowed_field_refs` ao descriptor admitido.
- Ainda no E-SF-04: a recusa tipada PHI_IN_COMMERCIAL_INPUT não existe no enum de reason_code do C6-4. Levá-la ao fio depende de um schema sucessor.
