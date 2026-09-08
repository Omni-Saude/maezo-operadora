# Delta independente dos achados FLTV-01–04

**Veredito: APPROVE, qualificado ao delta FLTV-01–04 no SHA abaixo.**
Nenhum achado bloqueante remanescente foi identificado neste escopo.

**Candidato verificado:** `4ea318f28923c73bf5aacdc01eab647398c2aaee`, tree
`43172b5c3cf21b95bfe3b49cc7a9a4681b1b5644`. O delta funcional é sobre
`ac775f8dd86418402bc42bae1eb11756fe90c038`; `4ea318f2` adiciona somente o pacote de
evidência à implementação congelada `c97631d9865b87842c014037b3ac6a5c7025d26c`.

Esta é a revisão delta pelo MESMO verificador que emitiu REVISE em `ac775f8d`.
O reparo foi feito por outro especialista. O relatório original e seu commit de evidência
`68378e314a4e9f1a1ca4cca35533604ddffb8454` permanecem intactos. Worktree novo e próprio:
`/Users/familia/code/maezo-completion-wt/fleet-ledger-tooling-delta`, com ambiente offline
`uv sync --offline --frozen --extra dev`, CPython 3.12.13. Nenhuma correção de fonte,
produto ou ledger foi feita pelo verificador.

## Controles originais e resultado por achado

O arquivo de controles original foi copiado sem editar um byte: SHA-256
`b1bdf14e5c9fdbe46b1615caa47bf13c088406e0fc31461929e340c71996024f`.
A saída integral está em `raw/original-controls.txt`.

- **FLTV-01 fechado:** o helper externo `outside.txt` executado por SourceFileLoader,
  anteriormente invisível, agora produz rc1 e `historical source import escaped archived
  tree`. A receita atual ainda passa, mas a prova histórica falha: 1/2, sem fallback.
  O audit hook registra caminhos de código executado independentemente de sufixo; nomes
  sintéticos continuam explicitamente tratados. A suíte exercita `.py`, `.txt`, `.pyw`
  e arquivo sem extensão, além do caso positivo realmente arquivado.
- **FLTV-02 fechado:** as duas mutações originais — declaração `sha257:` e data anterior
  à convenção — agora retornam rc1 com erro de esquema, antes de qualquer `SKIP legacy`.
  A suíte também cobre data ausente e não analisável. Todo marcador explícito em linha
  de tabela é validado globalmente; linhas legadas sem marcador conservam seu contrato.
- **FLTV-03 fechado:** um commit de lock não relacionado, com a sucessão inteira no base,
  passa com zero provas selecionadas. Controles próprios adicionais demonstram que replay
  selecionado falha com lock ausente, sujo ou commitado diferente. Um blob histórico forjado
  continua rejeitado globalmente mesmo quando a sucessão não foi recém-adicionada; a
  validação estrutural não foi afrouxada para corrigir a seleção de execução.
- **FLTV-04 fechado:** o DSN original com `?host=db.example.invalid&port=5432` agora gera
  o ValueError esperado no gate. Uma matriz própria recusa 20 variantes envolvendo host,
  hostaddr, port, service, servicefile, encoding, caixa, chaves repetidas, socket Unix,
  esquema inadequado, porta inválida, fragmento e query incompleta. Três DSNs simples
  IPv4/localhost/IPv6 são aceitos e resolvidos como loopback pelo parser libpq instalado.
  Nenhuma conexão, resolução DNS, serviço externo ou credencial real foi usada.

O script original termina com **rc1 esperado no DSN**, portanto não alcança o bloco de
prazo que vem depois. Isso não é apresentado como falha de teste nem como verificação
completa desse bloco. O deadline foi executado separadamente em `raw/delta-controls.py`:
subprocesso real adormecido, prazo temporário apenas em memória de 0.2s, término em 0.204s,
rc=-1, counts={}, passed=None e duas violações. A constante é restaurada para **1800s**.

## Evidência independente no candidato final

- Checker/supersession/release-floor: **162 passed em 94.61s**, rc0, execução
  `21:15:19.288647Z`–`21:16:55.187766Z` no próprio ambiente.
- Controles próprios: cinco casos de lock/proveniência, vinte DSNs recusados, três DSNs
  loopback preservados e deadline real; roteiro completo rc0.
- Ruff check/format, mypy strict, `git diff --check`: rc0.
- Ledger stacked independente: **21/21 provas**, 20 rows selecionadas, zero legacy, rc0,
  sobre `3125fea992e607836a6adccbfb8593a1cc062a46`. Execução coordenada com ROOT, sessão
  93535, `21:17:39.312538Z`–`21:19:13.932288Z`, **94.620s**; janela liberada ao término.
  Inclui EVAL histórico 7/51 fontes, ESC histórico 49/33, FLC histórico em `3009bbe5`
  22/11 e a receita sucessora atual com 45 resultados, hash
  `0cf6b113432cdbd276475b1dee21b9f1b491f84333b04b1a12a2534adbe46e89`.
- Cell count stacked: **20 rows**, todas com oito células, rc0.
- O corpo AST de `capture_pytest_recipe` é idêntico ao de `7b34cf44` e de `ac775f8d`:
  argv, herança ambiental e comportamento de plugins da receita atual permanecem iguais.
- `generate_release_floor.py` é byte-idêntico à base `ac775f8d`, SHA-256
  `c6ff686d85a5c133e097600854fb105e915dff2b163e6327cacdbd4965f5520b`.
  Piso, seleção e prazo 1800s não foram alterados neste reparo.

## Preservação e identidade

Foram lidos os arquivos exatos em
`/Users/familia/code/maezo-operadora/docs/audits/maezo-deep-audit/remediation/completion-fleet-ledger-boundary-repair/`.
Os hashes do REPORT (`dfb304f7…`) e MANIFEST (`84177268…`) fornecidos pelo ROOT conferem.
Todas as 18 entradas do MANIFEST foram recalculadas, incluindo os quatro caminhos de
fonte/ledger no checkout do candidato. As duas tentativas de launcher com rc1 permanecem
registradas como falhas operacionais; nenhuma foi contada como gate aprovado.

O ledger congelado `ac775f8d` (603 linhas, 480 rows, 1.623.016 bytes) é prefixo literal do
candidato: uma linha acrescentada, zero remoções. O prefixo anterior `7b34cf44`
(588 linhas, 477 rows, 1.618.623 bytes) também foi comprovado. Estado final: **604 linhas,
481 rows, 1.625.194 bytes**, SHA-256
`1663c58dfd21aa991585599e89b15ae5da1fbbba0e002d1abd73bb504f251918`.

A nota do autor distingue corretamente o Fleet quality de 1242.60s da invocação do floor.
Esta revisão não reclassifica quality como floor concluído: as medições históricas são
os dados dos seus respectivos runs, não novas medições integrais deste verificador.

## Limites da revisão

A conclusão vale somente para o reparo FLTV-01–04 e sua integração ao tooling Fleet no
SHA exato acima. Não é aprovação de release, do programa completo, de deploy ou de CI ainda
não executado no novo SHA. Nenhum full-unit, `--all`, `--allow-live`, Docker, CIB7, PostgreSQL,
push ou merge foi executado aqui. Nenhum engine foi mockado. O consumidor PFV01 continua
fora do escopo. Esta revisão intermediária não é um dos dois gatekeepers finais.
