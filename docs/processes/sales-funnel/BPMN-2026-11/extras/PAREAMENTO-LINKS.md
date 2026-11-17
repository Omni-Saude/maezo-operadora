# Pareamento de link-events — sales-funnel BPMN 2026-11

Gerado mecanicamente (fleet-scale, namespace-aware) — 14 artefatos.

| Status | Nome | Throw | Catch |
|---|---|---|---|
| FULL | `ABANDONO-E02` | E02-qualificacao | F02-recuperacao-e-mensagens |
| FULL | `ABANDONO-E03` | E03-cotacao-e-comparador | F02-recuperacao-e-mensagens |
| FULL | `ABANDONO-E04` | E04-proposta-e-termos | F02-recuperacao-e-mensagens |
| FULL | `ABANDONO-E05` | E05-aceite | F02-recuperacao-e-mensagens |
| FULL | `ABANDONO-E06` | E06-contrato-e-pagamento | F02-recuperacao-e-mensagens |
| FULL | `E01-HUMANO` | E01-entrada-com-preco | F03-canal-de-vendas |
| FULL | `E02-HUMANO` | E02-qualificacao | F03-canal-de-vendas |
| FULL | `E02-ORIGEM` | E02-qualificacao | F03-canal-de-vendas |
| FULL | `E03-HUMANO` | E03-cotacao-e-comparador | F03-canal-de-vendas |
| FULL | `E04-LEG-COLETIVA` | E04-proposta-e-termos | F03-canal-de-vendas |
| FULL | `E05-HUMANO` | E05-aceite | F03-canal-de-vendas |
| FULL | `E08-ES1` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `E08-ES2` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `E08-ES3` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `E08-ES4` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `E08-ES5` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `E08-ES6` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `E08-ES7` | E08-admissao-e-acompanhamento | F01-mediacao |
| FULL | `ETAPA9-CICLO-VIGENTE` | E09-pos-venda | F04-retencao |
| HALF | `F1-EMITE-ES1` | F01-mediacao | — |
| HALF | `F1-EMITE-ES2` | F01-mediacao | — |
| HALF | `F1-EMITE-ES3` | F01-mediacao | — |
| HALF | `F1-EMITE-ES4` | F01-mediacao | — |
| HALF | `F1-EMITE-ES5` | F01-mediacao | — |
| HALF | `F1-EMITE-ES6` | F01-mediacao | — |
| HALF | `F1-EMITE-ES7` | F01-mediacao | — |
| HALF | `F3-CORRETOR-ENTRADA` | — | F03-canal-de-vendas |
| FULL | `F3-LEG-COLETIVA-VALIDA` | F03-canal-de-vendas | E04-proposta-e-termos |
| FULL | `F4-ETAPA9-CANCELAMENTO` | F04-retencao | E09-pos-venda |
| FULL | `F4-ETAPA9-RENOVACAO` | F04-retencao | E09-pos-venda |

## Leitura
- **FULL**: par fechado — 1 throw e 1 catch, grafia idêntica (missão §5.2).
- **HALF by-design (não reparar)**: `F1-EMITE-ES1..7` — sinais da F1 para sistema não modelado (portão fechado; F01-VEREDITO §regras duras). `F3-CORRETOR-ENTRADA` — canal de entrada do corretor, catch aguardando canal externo (F03-VEREDITO). Inventar receptor = invenção (§8).
- **DUP resolvido nesta onda**: os 4 throws do E2E (ABANDONO-E02, E04-LEG-COLETIVA, E08-ES1, ETAPA9-CICLO-VIGENTE) foram REMOVIDOS — regra do dono: o throw vive onde o evento ACONTECE (diagrama de detalhe); E2E não lança em nome da etapa.

## Gates finais por artefato

| Artefato | sha256(8) | eye | DI a/b | domínio | bpmnlint |
|---|---|---|---|---|---|
| E01-entrada-com-preco | `93fd477961f4370f` | 0 | 0/0 | 0 | 0 |
| E02-qualificacao | `93136fc59c59553b` | 0 | 0/0 | 0 | 0 |
| E03-cotacao-e-comparador | `b060b69cbc2446c5` | 0 | 0/0 | 0 | 0 |
| E04-proposta-e-termos | `b7f6928e1c154bdb` | 0 | 0/0 | 0 | 0 |
| E05-aceite | `378a4b269ba0bb1c` | 0 | 0/0 | 0 | 0 |
| E06-contrato-e-pagamento | `bd39e7d125bd86a8` | 0 | 0/0 | 0 | 0 |
| E07-declaracao-de-saude | `eb19a831a844cf69` | 0 | 0/0 | 0 | 0 |
| E08-admissao-e-acompanhamento | `03cb8d88ccf1f169` | 0 | 0/0 | 0 | 0 |
| E09-pos-venda | `920374ac5cf0dbec` | 0 | 0/0 | 0 | 0 |
| E2E-ponta-a-ponta | `d2d6ae739b4ca562` | 0 | 0/0 | 0 | 0 |
| F01-mediacao | `a6610bcf20bdbcbb` | 0 | 0/0 | 0 | 0 |
| F02-recuperacao-e-mensagens | `a81b77132cc7f0f0` | 0 | 0/0 | 0 | 0 |
| F03-canal-de-vendas | `8fb3d807f8cbf9af` | 0 | 0/0 | 0 | 0 |
| F04-retencao | `796f948ca4c4cc65` | 0 | 0/0 | 0 | 0 |
