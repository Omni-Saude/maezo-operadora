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
