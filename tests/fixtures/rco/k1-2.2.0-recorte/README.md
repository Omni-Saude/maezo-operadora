# Recorte do pacote K1 2.2.0 do rco (composicao `pares_mutuamente_exclusivos_ortop`)

Copia SOMENTE LEITURA. Origem: `Omni-Saude/regras-contratos-operadora`, commit
`a19783753b5b7ef839bc6ec5a565699097265177` (`a1978375`, merge do PR #171, `formato_manifesto` 2.2.0). Os arquivos sao
byte a byte os do rco; o sha256 de cada um e a sua origem (`pacote:` = arquivo do pacote gerado; `repo:` = arquivo do
repositorio no commit) estao em `ORIGEM.json`, e `tests/unit/platform/test_rco_k1_2_2_conformidade.py` reprova se algum
divergir ou sobrar. Nao edite: atualize a copia a partir de um commit novo do rco e regrave `ORIGEM.json`.

O que ha aqui:

- `manifesto.json` e `manifesto.sha256`: o manifesto K1 REAL, inteiro e sem alteracao (236 decisoes, `composicoes[]`
  com o grupo de ortopedia), gerado pelo `scripts/pacote.py` do rco no modo de nao publicacao (`publicar=False`, o
  mesmo modo do recorte 1.1.0). O modo de publicacao (`make pacote`) recusa esse commit: o conjunto do dicionario de
  entradas mudou desde a versao publicada 1.2.0 sem versao nova (o rco tem de subir `versao` em
  `docs/programa/k1-entradas-origem.yaml` antes de publicar). O manifesto continua dizendo `versao` 1.2.0 no
  `dicionario_entradas`. Comandos exatos em `ORIGEM.json` (`gerador`); duas geracoes deram o mesmo hash.
- `vocabulario.json` e `k6-filas.json`: anexos do pacote (pequenos). `entradas.json` (cerca de 300 KB) fica de fora:
  `verificar_pacote` sobre o recorte acusa exatamente essa falta e nada mais.
- `dmn/`: as tres decisoes da composicao, como o pacote as leva (id da `<decision>` com o prefixo `rco_`; tirar o
  prefixo devolve os bytes de `dmn/ortopedia-opme/` do rco).
- `validacao/casos/ortopedia-opme/`: os casos-teste do rco para as tres decisoes (o campo `dmn` de cada arquivo aponta o
  caminho na fonte do rco, sem prefixo; o `decisao`, o id sem prefixo).

Uso: so em testes (`tests/unit/platform/test_rco_k1_2_2_conformidade.py` e
`tests/integration/dmn/test_rco_k1_2_2_composicao_engine.py`, carga em `tests/unit/platform/rco_k1_2_2_recorte.py`).
Nao esta em `src/`, em `config/` nem em `spec/` (o pin do rco segue como esta).
