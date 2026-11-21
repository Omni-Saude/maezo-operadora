# Test spec — SP-OP-SUPP-001 (NÃO PROMOVIDO / DRAFT)

> **NÃO PROMOVIDO / DRAFT.** Este test-spec é documental (E-SF-12r). Não promove o processo, não
> registra aprovação e não tem aprovador, data nem evidência.
>
> **O processo SP-OP-SUPP-001 NÃO é iniciável por agente e NÃO está nas listas de chaves de processo**
> (`src/maezo/tools/process_allowlist.py` e `src/maezo/gateway/effect_pep.py`, ADR-0016: 15 chaves).
> Ele nasce de pedido externo do titular (`src/maezo/platform/privacy/program_publication.py`).

Contrato: `docs/processes/contracts/SP-OP-SUPP-001.md` (DRAFT). BPMN:
`spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn`. DMN:
`spec/processes/dmn/suppression_routing.dmn`.

Dados sintéticos apenas. Nunca telefone, nome ou documento real.

## 1. Estado fechado (unitário — já executável)

Arquivo: `tests/unit/gateway/test_e_sf_12r_nao_contato_variante_a.py`.

- Chave fora das duas listas, 15 chaves em cada, listas iguais; `ensure_allowed` recusa a chave por
  pertinência, não por formato; um tenant não consegue acrescentá-la.
- Temporizadores `BT_Lembrete50`, `BT_Escala80` e `BT_Timeout100` inalterados: `timeDate` sobre
  `t_lembrete_iso`, `t_escalonado_iso` e `t_deadline_iso`; só o de 100% interrompe a tarefa.
- Nenhum contato proativo a beneficiário: a supressão aceita só `vendedor` e `lead`; o registro
  publicado de templates e a composição de produção do `OutboundGate` não têm tipo de titular com
  oposição executável.
- Nenhum registro de admissão A6 versionado.

## 2. Integração (contra o engine real, marker `integration`)

Arquivo existente: `tests/integration/processes/test_sp_op_supp_001.py`. Cobre o caminho honrado com o
relógio do registro, a rota humana com os três temporizadores e as recusas por store ou transporte
ausentes. Este test-spec não acrescenta caso de integração: qualquer caso novo depende do registro de
admissão A6, que não existe.

## 3. O que fica fora

- Promoção às listas de chaves, mudança da contagem do ADR-0016 e qualquer edição de `src/`, BPMN, DMN
  ou do arquivo de aprovações.
- Suporte a beneficiário na supressão.
- Distinção explícita entre comunicação obrigatória e proativa no código: hoje só o caminho proativo
  passa pelo `OutboundGate`.
