# Reparo dos limites do tooling CI Fleet

**Candidato funcional congelado:** `c97631d9865b87842c014037b3ac6a5c7025d26c`
(tree `da95cbc5c53949a0c48c56c1bf310f5a786ee859`), sobre a base explícita
`ac775f8dd86418402bc42bae1eb11756fe90c038`. O candidato contém os commits de
implementação `ea56426938ac1cba8be265bab046d39b684c9d89` e
`07ebb91d398c76b7e747a824c35122eb81a876ab`, além dos dois commits que posicionam a
nova linha no fim físico do ledger. Este pacote de evidência será um commit posterior,
sem alterar o candidato funcional.

Worktree independente:
`/Users/familia/code/maezo-completion-wt/fleet-ledger-boundary-repair`, branch
`completion/fleet-ledger-boundary-repair`. O ambiente próprio foi criado offline por
`uv sync --offline --frozen --extra dev`, CPython 3.12.13. Foram lidos AGENTS, PLANS,
`docs/plan.md`, o prompt completo de execução e o relatório/manifesto congelado do
verificador independente. O escopo é exclusivamente o tooling de evidência CI; nenhuma
regra SP-OP, regra de negócio, piso ou timeout mudou.

## Resultado dos quatro reparos

### FLTV-01 — atestado de todo código Python realmente executado

O source guard em `scripts/ci/check_evidence_ledger_hashes.py:993` agora registra todo
caminho real observado pelo audit hook, independentemente de sufixo. Nomes sintéticos
como `<string>` continuam explicitamente fora do conjunto. O limite de confiança
continua restrito à árvore arquivada, ao próprio guard e ao stdlib/dependências do
interpretador travado.

O controle original, copiado byte a byte do verificador, executava um helper externo
`outside.txt` por `SourceFileLoader`: antes passou com 2/2 provas; depois a prova histórica
falhou por escape desse caminho. Testes parametrizados cobrem `.py`, `.txt`, `.pyw` e
arquivo sem extensão, usando execução Python real. Os casos positivos de fonte arquivada
e a recusa prévia de `.py` externo continuam na suíte.

### FLTV-02 — marcador v1 nunca cai silenciosamente como legacy

`build_supersession_plan`, em `scripts/ci/check_evidence_ledger_hashes.py:545`, valida
globalmente cada linha de tabela que contém marcador `ledger-supersedes:v1`. A linha deve
ser um sucessor declarado pela forma exata `sha256:<digest> (tests/...py)` e ter data
qualificada pela convenção. Declaração ausente ou malformada, data anterior, ausente ou
inválida agora terminam o gate com erro, antes da seleção por range. Linhas históricas que
realmente não optaram pelo marcador preservam o caminho legacy.

O controle imutável mostra que a troca `sha256`→`sha257` e a data `2000-01-01`, antes
classificadas como legacy com rc0 e zero provas, agora retornam rc1. A suíte adiciona ainda
data vazia e data não analisável, ambas sem `SKIP (legacy)`.

### FLTV-03 — lock atual exigido somente para replay selecionado

A construção global do grafo ainda valida marcador, identidade única, alvo exato, commit
ancestral, digest da linha histórica, caminho/teste histórico e digest do `uv.lock`
arquivado. Ela deixou de comparar todo lock histórico com o lock do HEAD corrente antes
de saber quais provas serão executadas.

`verify_historical_row`, em `scripts/ci/check_evidence_ledger_hashes.py:1326`, aplica as
vinculações dinâmicas somente a uma prova histórica selecionada: `uv.lock` da worktree deve
ser idêntico ao HEAD e o lock do commit histórico deve ser idêntico ao ambiente corrente.
Não existe fallback para HEAD, solver novo ou waiver amplo. O controle original passou de
rc1 para rc0 com zero linhas selecionadas após uma atualização de lock não relacionada; o
novo controle complementar confirma rc1 quando a própria sucessão é selecionada sob lock
divergente.

### FLTV-04 — destino PostgreSQL não pode ser substituído pela query libpq

A validação de coordenadas live foi separada por protocolo. Para PostgreSQL, somente
`postgres`/`postgresql`, autoridade loopback, porta válida e ausência de params/fragmento
são aceitos. `parse_qsl(..., strict_parsing=True)` decodifica os nomes da query, e as chaves
libpq que escolhem destino (`host`, `hostaddr`, `port`, `service`, `servicefile`) são
recusadas, inclusive chave percent-encoded e host de socket Unix. Opções sem mudança de
destino, como `sslmode=disable`, continuam válidas. HTTP/HTTPS e Kafka também passam por
gramáticas próprias, sem conexão de rede.

O DSN do controle, `postgresql://127.0.0.1/maezo?host=db.example.invalid&port=5432`,
agora é recusado antes de qualquer consumidor. A suíte cobre todas as chaves de destino,
loopback IPv4/`localhost`/IPv6 e gramáticas inválidas. A ressalva do verificador permanece:
asyncpg não dava precedência à query nesse caso, mas libpq dava; o reparo fecha a garantia
genérica sem alegar um escape no fixture asyncpg corrente.

## Preservação e proveniência

- O ledger original em `7b34cf44ae9e531092f92994a8347fbe0116784c` permanece prefixo
  literal: 588 linhas físicas, 477 rows de dados, 1.618.623 bytes, SHA-256
  `b5e659d64a277b776d413dbb378a219305084cd24b5132f9d266e77acd5720cb`.
- Todo o ledger da base autora `ac775f8dd86418402bc42bae1eb11756fe90c038`
  também permanece prefixo literal: 603 linhas, 480 rows, 1.623.016 bytes. O candidato
  tem 604 linhas, 481 rows e 1.625.194 bytes; o diff contra a base é uma adição e zero
  remoções.
- A única row nova tem oito células e Task ID
  `FLEET-LEDGER-BOUNDARY-REPAIR / FLTV-01 / FLTV-02 / FLTV-03 / FLTV-04`. Ela sucede a
  row FLC exata em `3009bbe5490e28b89b606dc6e656d6b2b3ad98d8`, com os hashes históricos
  de row, teste e lock declarados literalmente. Não houve reescrita de evidência passada.
- A receita pytest atual mantém o argv exato
  `python_exe -m pytest test_path -v --tb=no -p no:cacheprovider` e herda o ambiente,
  como em `7b34`, `ac775` e a fonte final. A nova receita possui 45 linhas de resultado e
  hash `sha256:0cf6b113432cdbd276475b1dee21b9f1b491f84333b04b1a12a2534adbe46e89`.
- `scripts/ci/generate_release_floor.py` permanece byte a byte com SHA-256
  `c6ff686d85a5c133e097600854fb105e915dff2b163e6327cacdbd4965f5520b`; timeout 1800,
  seleção e pisos não foram alterados.

## Verificação executada

- Controle adversarial original, SHA-256
  `b1bdf14e5c9fdbe46b1615caa47bf13c088406e0fc31461929e340c71996024f`, conforme o
  arquivo congelado do verificador, foi reproduzido sem edição.
  O RED da base está em `raw/adversarial-controls-before.txt`; o delta está em
  `raw/adversarial-controls-after.txt`. Depois dos quatro resultados esperados, o script
  encerra no `ValueError` esperado do DSN e por isso não alcança seu bloco final de deadline.
  O comportamento de deadline permanece coberto pela suíte focada de release floor.
- Suítes focadas checker/supersession/release-floor: **162 passed em 58.03s**, rc0,
  `20:47:34.744388Z`–`20:48:33.598182Z`. Ruff check/format, mypy strict e diff-check:
  rc0.
- Receita declarada de supersession: **45 result lines**, hash exato acima, rc0.
- Gate stacked coordenado com ROOT, sem serviços e sem `--allow-live`: **21/21 provas**,
  20 rows selecionadas, zero legacy, rc0, `20:58:06.253526Z`–`20:59:41.198093Z`.
  Inclui EVAL histórico (51 fontes arquivadas), ESC histórico (33), FLC histórico exato
  em `3009bbe5` (22 resultados/11 fontes) e a receita atual (45 resultados). A janela foi
  reservada e liberada; ROOT confirmou ausência de sobreposição.
- Cell count: uma row adicionada, oito células, rc0. A preservação literal dos dois
  prefixos foi recalculada em bytes.

Duas tentativas anteriores do launcher stacked são mantidas integralmente como evidência
negativa operacional. A primeira passou `.venv/bin/python` relativo, que deixou de existir
ao executar dentro do arquivo temporário; a segunda seguiu o symlink da venv até o Python
base sem pytest. São erros somente de launcher, rc1, não resultados do gate. A execução
válida seguinte usou o caminho absoluto da venv e passou integralmente.

## Correção de redação histórica sobre o floor

O relatório FLC-02 anterior chamou a execução Fleet quality de 1242.60s de “same full
pytest tests/ -q”. Essa frase mistura duas invocações diferentes: a quality run excluía os
662 testes live, enquanto o floor usa explicitamente `pytest tests/ -q` com o ambiente que
produz skips. A medição de 1242.60s pertence à Fleet quality, não a uma execução concluída
do Fleet floor. Permanecem evidências históricas válidas a execução R6 floor de 885.98s e o
timeout real do Fleet floor aos 900s; elas sustentam o timeout bounded de 1800s. A row e o
relatório históricos foram preservados, e esta nota corrige somente a interpretação.

Não foram executados full-unit, gate global `--all`, Docker, PostgreSQL, CIB7, integração
live, CI remoto, push ou merge. Não houve rede nem credenciais. Este relatório é a entrega
do especialista de reparo e não aprova a própria correção; o mesmo verificador original
deve revisar o delta. O trabalho também não constitui gatekeeper final do programa.
