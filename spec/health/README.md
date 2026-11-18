# spec/health — artefatos de saúde (E-SF-25)

Só estrutura. Nenhum dado de saúde de pessoa vive aqui.

- `question-banks/`: bancos de perguntas da declaração de saúde, no formato `maezo.health.question-bank.v0`,
  validados por `src/maezo/platform/validation/question_banks.py` em `make validate-artifacts`.
- Hoje só existe banco **sintético** (`synthetic: true`, aprovações com prefixo `SINTETICO-`, perguntas
  marcadas `[SINTETICO]`). Banco sintético nunca carrega para uso.
- Banco real: o conteúdo é redigido e aprovado pergunta a pergunta pelo jurídico e pelo médico auditor. Ele só
  carrega com as duas aprovações (clínica e regulatória) resolvidas no registro de aprovações, que hoje é vazio.
- Modo `declaration`: só o conceito `known_condition`. A orientação traz as seções de CPT e de consequências
  da omissão; Agravo não é construído.
