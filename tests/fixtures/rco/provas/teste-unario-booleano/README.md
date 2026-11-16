# Copia da fixture `teste-unario-booleano` (prova do motor, rco)

Copia SOMENTE LEITURA. Origem: `Omni-Saude/regras-contratos-operadora`, commit `323fec5c2d241988f05d30da14c559adef8ba07f`
(`323fec5c`), pasta `validacao/fixtures/teste-unario-booleano/`. Os arquivos sao byte a byte os do rco; o
sha256 de cada um esta em `ORIGEM.json` e `tests/unit/platform/test_rco_provas_fixture.py` reprova se algum
divergir. Nao edite: atualize a copia a partir de um commit novo do rco e regrave `ORIGEM.json`.

Uso: so em testes (`tests/integration/dmn/test_rco_provas_engine.py` roda os casos no CIB Seven;
`tests/unit/platform/test_rco_provas.py` usa o carregador). Nao esta em `src/` nem em `config/`.

Significado de `casa`: cada decisao `celNN` tem hit policy FIRST com duas linhas; a linha 1 e a celula
testada (`resultado = "casa"`), a linha 2 e `-` (`resultado = "nao_casa"`). `casa` = a celula casou.
