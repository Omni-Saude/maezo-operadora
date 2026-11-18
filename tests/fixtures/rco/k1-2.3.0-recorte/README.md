# Recorte do pacote K1 2.3.0 do rco (composicao `composicao_carencia_cpt`, `mais_restritiva`)

Copia SOMENTE LEITURA. Origem: `Omni-Saude/regras-contratos-operadora`, commit
`aed187d4bada72e365381b48c6e96921d34c8815` (`aed187d4`, `main` publicada do rco, PR #174, unidade PROG-RCO-13,
`formato_manifesto` 2.3.0, dicionario de entradas 1.5.0, K6 1.1.0). Os arquivos
sao byte a byte os do rco, salvo `entradas-recorte.json` (abaixo); o sha256 de cada um e a sua origem (`pacote:` =
arquivo do pacote gerado; `repo:` = arquivo do repositorio no commit; `recorte:` = recortado de um arquivo do pacote)
estao em `ORIGEM.json`, e `tests/unit/platform/test_rco_k1_2_3_conformidade.py` reprova se algum divergir ou sobrar.
Nao edite: atualize a copia a partir de um commit novo do rco e regrave `ORIGEM.json`.

Historico da copia: a primeira vinha da branch nao publicada 5dc95b12; a de `main` d9b8adfa (PR #175) mudou so o
manifesto (`OMNI-GERAL-CAR-0004` 3.0.1, redacao) e acrescentou o caso C022 de `geral_cpt`. A atual, de `main`
aed187d4 (PR #174), em relacao a d9b8adfa:
- `dmn/geral_carencia.dmn`: o label da entrada `urgenciaAposVinteQuatroHorasCobertura` (278 caracteres, acima do
  `varchar(255)` do historico de decisao do motor; DL-0183 item 7) foi encurtado e o texto longo foi para a
  `<description>` da entrada; o rco ganhou o lint L15 dos limites. `dmn/geral_cpt.dmn` igual;
- `manifesto.json`: `origem.commit_sha`, `dicionario_entradas` 1.5.0, as regras e o sha256 das decisoes cujas
  regras subiram de versao no rco (PATCH, entre elas CAR-0001/0002/0003 em `rco_geral_carencia`) e as entradas
  declaradas das decisoes de OPME; 236 decisoes, as mesmas composicoes;
- `entradas-recorte.json` (dicionario 1.5.0): todo termo `parametro` traz o valor estruturado (`valor` com
  `unidade`, `referencia`, `valor_por_contrato` ou `lacuna`), com `horasMinimasUrgencia` = 24 `horas`, e a
  formula da urgencia traz `referencia_por_tipo_guia`;
- `validacao/composicao/regras-gerais/composicao_carencia_cpt.json`: caso novo C009 (urgencia com 24 horas
  cumpridas e `cptVigente` nula: `revisao_humana` na fila `cadastro`, com o aviso); os casos por decisao, o
  vocabulario e o K6 sao iguais.

O que ha aqui:

- `manifesto.json` e `manifesto.sha256`: o manifesto K1 REAL, inteiro e sem alteracao (236 decisoes; `composicoes[]`
  com `composicao_carencia_cpt`, `mais_restritiva`, e `pares_mutuamente_exclusivos_ortop`), gerado pelo
  `scripts/pacote.py` do rco no modo de PUBLICACAO (`make pacote`; o dicionario de entradas 1.5.0 tem a impressao
  registrada). Duas geracoes deram o mesmo hash. Comandos exatos em `ORIGEM.json` (`gerador`).
- `vocabulario.json` (`ordem_restricao`, `sinalAuditoria`) e `k6-filas.json` (`avisos`): anexos do pacote.
- `dmn/`: as duas decisoes da composicao, como o pacote as leva (id da `<decision>` com o prefixo `rco_`).
- `entradas-recorte.json`: `k1/entradas.json` do pacote (cerca de 330 KB) RECORTADO para as 39 entradas das decisoes
  dos dominios `regras-gerais` e `diarias-taxas` (os do montador), na mesma ordem, com a mesma `versao` (1.5.0) e o
  mesmo `sha256_conteudo` (o do dicionario COMPLETO). O sha256 do `entradas.json` inteiro esta em `ORIGEM.json`
  (`entradas_json_sha256`); `verificar_pacote` sobre o recorte acusa exatamente a falta de `entradas.json`.
- `validacao/casos/regras-gerais/`: os casos-teste do rco das duas decisoes; `validacao/composicao/regras-gerais/`:
  os casos do GRUPO (`esperado` com `decisao`, `decisao_de`, `idRegra`, `fila`, `sinais`).

Uso: so em testes (`tests/unit/platform/test_rco_k1_2_3_conformidade.py` e
`tests/integration/dmn/test_rco_k1_2_3_composicao_engine.py`, carga em `tests/unit/platform/rco_k1_2_3_recorte.py`).
Nao esta em `src/`, em `config/` nem em `spec/` (o pin do rco segue como esta).
