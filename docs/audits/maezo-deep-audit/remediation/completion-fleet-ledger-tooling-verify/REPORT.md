# Verificação independente do tooling CI Fleet

**Veredito: REVISE.** Candidato exato `ac775f8dd86418402bc42bae1eb11756fe90c038`
(tree `4090bc7449f1b10dfda2c3397aa2cef5077fa0e0`), filho somente de evidência da implementação
`3009bbe5490e28b89b606dc6e656d6b2b3ad98d8`. Baseline
`7b34cf44ae9e531092f92994a8347fbe0116784c`. Quatro controles novos reproduzem problemas;
o sucesso dos testes existentes não os cobre. Nenhuma correção foi feita pelo verificador.

Worktree independente: `/Users/familia/code/maezo-completion-wt/fleet-ledger-tooling-verify`.
Ambiente próprio criado por `uv sync --frozen --extra dev`, CPython 3.12.13. Inspeção guiada
por AGENTS, PLANS, `docs/plan.md` §2/§5 tooling, prompt de execução, convenção do ledger,
DL-0015/0016 (isolamento e coordenação), e finding histórico
`residue-ledger-false-merge-claim` (verificar refs reais). O escopo é tooling de evidência;
nenhuma regra SP-OP ou decisão de negócio muda.

## Achados bloqueantes

### FLTV-01 — P1: código Python externo passa pelo atestado por ter sufixo diferente

Em `scripts/ci/check_evidence_ledger_hashes.py:938`, `_record` descarta tudo cujo sufixo não
seja `.py`, `.pyc` ou `.pyo`, incluindo eventos `exec` já conhecidos como execução Python.
O controle `non_python_suffix_escape` cria um helper real `outside.txt` fora do arquivo Git,
carrega-o com `SourceFileLoader`, executa seu código e verifica `VALUE == 37`. A receita
histórica e a atual passam: **rc0, 2/2 provas**, com apenas dois arquivos arquivados atestados.
O arquivo externo efetivamente executado fica invisível. Não há engine, mock ou rede.

A correção deve registrar caminhos reais dos eventos de execução independentemente da
extensão, mantendo o tratamento explícito de filenames sintéticos e o limite de confiança
archive/interpreter. Aceitação: o mesmo controle retorna falha de escape; o caso positivo
arquivado e o controle externo `.py` continuam corretos.

### FLTV-02 — P1: marcador explícito v1 pode cair silenciosamente no caminho legacy

Em `:556-568`, a seleção de linhas declaradas precede o tratamento do marcador. O segundo
loop valida apenas a gramática interna do marcador, mas não exige uma linha declarada e
qualificada. `missing_declaration` altera somente `sha256:` para `sha257:` na linha sucessora;
`predated_successor` muda somente a data para `2000-01-01`. Ambos preservam um marcador v1
completo. Ambos retornam **rc0, PASS: 0 rows verified, 1 legacy rows skipped**. Nem a prova
histórica explícita nem a prova atual são executadas.

Um marcador explícito é opt-in e deve exigir esquema/data/hash/path válidos; nunca deve
herdar o escape para linhas antigas sem marcador. Aceitação: os dois controles falham
com erro de esquema, incluindo data ausente/inválida e declaração ausente/malformada;
linhas históricas genuínas sem marcador preservam a compatibilidade.

### FLTV-03 — P2: lock de sucessões antigas passa a bloquear todo PR futuro

Em `:1393`, `build_supersession_plan` processa todo o ledger antes de calcular o range;
`:651` exige que cada lock histórico continue idêntico ao HEAD atual. O controle
`unrelated_lock_update` põe a sucessão inteira no base, faz um novo commit alterando somente
`uv.lock` e não adiciona nenhuma linha ao ledger. O gate retorna **rc1** por divergência de
lock histórico. Depois do merge, qualquer atualização legítima de dependências sofre esse
bloqueio mesmo sem solicitar replay histórico. A disciplina append-only torna impossível
apagar ou reescrever as sucessões antigas para contorná-lo.

Separar validação estática da cadeia e requisitos do ambiente de execução. Exigir igualdade
com o lock atual somente para provas históricas efetivamente selecionadas/scheduled pelo
range (ou por `--all`), mantendo a recusa de replay real sob lock divergente. Aceitação:
zero provas históricas selecionadas + lock novo não falha por esta razão; replay selecionado
+ lock diferente continua falhando. Não introduzir fallback, solver novo ou reescrita de rows.

### FLTV-04 — P2: hostname textual não garante destino loopback de um DSN libpq

Em `:779-780`, a verificação aceita qualquer URL cujo `urlparse(...).hostname` seja loopback.
O controle local `dsn_query_host_override_without_network` passa
`postgresql://127.0.0.1/maezo?host=db.example.invalid&port=5432` como
`MAEZO_TEST_DATABASE_URL`. O gate aceita, mas `psycopg.conninfo.conninfo_to_dict` resolve
`host=db.example.invalid`: a query do DSN substitui o destino no consumidor libpq.
Nenhum DNS, conexão ou credencial foi usado.

Limite da constatação: o parser asyncpg instalado dá precedência ao host da autoridade,
então este controle não prova escape nos fixtures atuais que usam diretamente asyncpg.
Ele invalida a garantia genérica anunciada para receitas históricas com consumidor libpq,
que está disponível no ambiente pinado. Endurecer a gramática por coordenada: esquemas
permitidos, porta válida e rejeição/validação fechada de opções que substituem host/hostaddr,
serviço ou socket. Aceitação: DSN acima recusado e DSN simples loopback preservado; fazer
somente parsing nos controles, nunca conectar a alvo externo.

## Evidência positiva reproduzida

- **139 passed em 49.65s**, suites checker/supersession/release-floor, próprio Python.
- Ruff check e format dos três arquivos alterados: rc0; mypy strict dos dois scripts: rc0.
- Gate stacked sobre `3125fea992e607836a6adccbfb8593a1cc062a46`: **20/20 provas**, 19 rows
  selecionadas, rc0. Receitas históricas EVAL: 7 resultados/51 arquivos; ESC: 49/33.
  Receitas atuais: EVAL 11 e ESC 75. Executado 20:16:53.800211Z–20:17:37.463165Z
  (43.663s), sessão 98556; janela concedida pelo ROOT e liberada ao terminar.
- Cell count: **19/19 linhas, oito células**, rc0.
- Todos os oito digests declarados no MANIFEST do autor foram recalculados e conferem.
- Ledger baseline: **588 linhas físicas, 477 rows de dados, 1.618.623 bytes**, SHA-256
  `b5e659d64a277b776d413dbb378a219305084cd24b5132f9d266e77acd5720cb`.
  É prefixo literal do candidato: **15 adições, zero perda**, 603 linhas finais.
- O comando atual pytest mantém argv e herança ambiental exatos em relação ao baseline;
  a alteração funcional de isolamento é restrita ao replay histórico.
- O timeout do floor muda apenas 900→1800s; seleção `pytest tests/ -q` e pisos não mudam.
  Controle independente usa subprocesso realmente adormecido e prazo reduzido apenas em
  memória para 0.2s: termina em 0.204s, rc=-1, counts={}, passed=None, duas violações.
  A constante é restaurada; nenhuma alteração de produto. Timeout continua não aprovado.

As medições completas de 885.98s/1242.60s são evidência do autor/ROOT, não novas execuções
integrais deste verificador. Não executei full-unit, `--all`, Docker, serviços, integração
mockada, CI remoto, push ou merge. O futuro consumidor PFV01 permanece fora deste reparo.
O relatório não é um dos dois gatekeepers finais do programa.

`raw/adversarial-controls.py` reproduz as falhas e o deadline; o rc0 do script significa
que o roteiro completou, **não aprovação do candidato**. Os `gate_rc` individuais e outputs
estão em `raw/adversarial-controls.txt`. MANIFEST fixa argv, datas, revisão, diff e hashes.
É necessário reparo por especialista distinto e delta independente neste mesmo verificador.
