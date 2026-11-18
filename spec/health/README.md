# spec/health — artefatos de saúde (E-SF-25)

Só estrutura. Nenhum dado de saúde de pessoa vive aqui.

- `question-banks/`: bancos de perguntas da declaração de saúde, no formato `maezo.health.question-bank.v0`,
  validados por `src/maezo/platform/validation/question_banks.py` em `make validate-artifacts`.
- Hoje só existe banco **sintético** (`synthetic: true`, aprovações com prefixo `SINTETICO-`, perguntas
  marcadas `[SINTETICO]`). Banco sintético nunca carrega para uso.
- Banco real: o conteúdo é redigido e aprovado pergunta a pergunta pelo jurídico e pelo médico auditor. A
  aprovação de cada pergunta é NOMINAL e fica no próprio banco (`approval: {medical, legal}`), não no
  CODEOWNERS; no banco sintético esses campos ficam vazios. O banco real só carrega com cada pergunta aprovada
  e com as duas aprovações do banco (clínica e regulatória) resolvidas no registro, que hoje é vazio.
- Modo `declaration`: só o conceito `known_condition`. A orientação traz as seções de CPT e de consequências
  da omissão; Agravo não é construído.
