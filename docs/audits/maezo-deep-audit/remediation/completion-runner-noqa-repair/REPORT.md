# Reparo focal de `noqa` inerte no runner

## Resultado

Partindo de `516902641c03208c2d2838b2b1d44749dd460cae`, o teste real de higiene
reproduziu exatamente dois achados `S310` inertes em
`scripts/dev/run_engine_integration.py`, linhas 1145 e 1155. O commit funcional
`0552d971` remove somente o marcador `noqa: S310` das duas linhas e conserva a
explicacao util como comentario comum: `# URL local fixa`.

Nao houve mudanca de regra Ruff, teste, core de evidencias, catalogo, engine,
Compose, lock, timeout, autenticacao de fonte ou semantica de `_run`. O arquivo
mantem 2.046 linhas. A AST completa e o bytecode compilado tem os mesmos digests
antes e depois; os digests estao em `fontes.txt`.

## Evidencia aceita deste reparador

- RED focal na base: 1 falha, com exatamente os dois achados esperados.
- GREEN focal no delta: 1 passou.
- Modulo completo `tests/unit/ci/test_noqa_hygiene.py`: 11 passaram em 1,22 s.
- Ruff check, Ruff format, `py_compile`, ausencia de `noqa:S310` e `diff --check`:
  PASS.
- `scripts/ci/pytest_execution_evidence.py` permaneceu no SHA-256 protegido
  `e48d07899db37702ebd001415e37c636d233ed609a86a46f5439bd66647db77c`.

Uma corrida exploratoria mais ampla foi interrompida depois do estreitamento do
escopo: terminou por `KeyboardInterrupt`, rc 2, com 38 casos passados em 248,18 s.
Ela e explicitamente INCOMPLETA e nao e usada como GREEN. Depois da interrupcao,
uma verificacao separada confirmou que nenhum processo pytest/runner desta
worktree e nenhum owner lock global permanecia ativo.

A full-unit congelada do orquestrador, engine real, Docker, coleta canonica e
gate global do ledger nao foram repetidos. Nenhum teste novo foi criado porque o
teste real existente detectou o defeito e provou o reparo diretamente.

## Custodia

O pacote foi criado em diretorio `0700`, com arquivos `0600`. `manifesto.json`
lista o conteudo, `argv.txt` registra as receitas, `stdout.txt` e `stderr.txt`
preservam as saidas, `fontes.txt` fixa as fontes e `digests.sha256` vincula os
artefatos. O estado segue reparado e nao autoaprovado, aguardando custodia final
independente de `engine_runner_assurance`.
