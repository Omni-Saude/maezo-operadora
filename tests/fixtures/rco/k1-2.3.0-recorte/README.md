# Recorte do pacote K1 2.3.0 do rco (composicao `composicao_carencia_cpt`, `mais_restritiva`)

Copia SOMENTE LEITURA. Origem: `Omni-Saude/regras-contratos-operadora`, commit
`d9b8adfafc0d7cf92db32cf255f61cfc8ae45fa7` (`d9b8adfa`, `main` publicada do rco, PR #175, unidade
PROG-RCO-carencia-urgencia-aviso, `formato_manifesto` 2.3.0, dicionario de entradas 1.4.0, K6 1.1.0). Os arquivos
sao byte a byte os do rco, salvo `entradas-recorte.json` (abaixo); o sha256 de cada um e a sua origem (`pacote:` =
arquivo do pacote gerado; `repo:` = arquivo do repositorio no commit; `recorte:` = recortado de um arquivo do pacote)
estao em `ORIGEM.json`, e `tests/unit/platform/test_rco_k1_2_3_conformidade.py` reprova se algum divergir ou sobrar.
Nao edite: atualize a copia a partir de um commit novo do rco e regrave `ORIGEM.json`. A copia anterior vinha da
branch nao publicada 5dc95b12; a publicada mudou so o manifesto (`OMNI-GERAL-CAR-0004` 3.0.1, redacao) e acrescentou
o caso C022 de `geral_cpt` (DMN, vocabulario, K6 e dicionario de entradas iguais).

O que ha aqui:

- `manifesto.json` e `manifesto.sha256`: o manifesto K1 REAL, inteiro e sem alteracao (236 decisoes; `composicoes[]`
  com `composicao_carencia_cpt`, `mais_restritiva`, e `pares_mutuamente_exclusivos_ortop`), gerado pelo
  `scripts/pacote.py` do rco no modo de PUBLICACAO (`make pacote`; o dicionario de entradas 1.4.0 tem a impressao
  registrada). Duas geracoes deram o mesmo hash. Comandos exatos em `ORIGEM.json` (`gerador`).
- `vocabulario.json` (`ordem_restricao`, `sinalAuditoria`) e `k6-filas.json` (`avisos`): anexos do pacote.
- `dmn/`: as duas decisoes da composicao, como o pacote as leva (id da `<decision>` com o prefixo `rco_`).
- `entradas-recorte.json`: `k1/entradas.json` do pacote (cerca de 330 KB) RECORTADO para as 39 entradas das decisoes
  dos dominios `regras-gerais` e `diarias-taxas` (os do montador), na mesma ordem, com a mesma `versao` (1.4.0) e o
  mesmo `sha256_conteudo` (o do dicionario COMPLETO). O sha256 do `entradas.json` inteiro esta em `ORIGEM.json`
  (`entradas_json_sha256`); `verificar_pacote` sobre o recorte acusa exatamente a falta de `entradas.json`.
- `validacao/casos/regras-gerais/`: os casos-teste do rco das duas decisoes; `validacao/composicao/regras-gerais/`:
  os casos do GRUPO (`esperado` com `decisao`, `decisao_de`, `idRegra`, `fila`, `sinais`).

Uso: so em testes (`tests/unit/platform/test_rco_k1_2_3_conformidade.py` e
`tests/integration/dmn/test_rco_k1_2_3_composicao_engine.py`, carga em `tests/unit/platform/rco_k1_2_3_recorte.py`).
Nao esta em `src/`, em `config/` nem em `spec/` (o pin do rco segue como esta).
