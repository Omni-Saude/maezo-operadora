# Tabela do K1 do rco que disparava a cerca de perspectiva (b8444f52)

Copia SOMENTE LEITURA, byte a byte, de `dmn/regras-gerais/geral_prazos_conta.dmn` do rco no commit `b8444f52`
(procedencia em `ORIGEM.json`). E a unica das 236 tabelas desse commit com hit da cerca de perspectiva (ADR-0040
D7): duas frases do pagador que recebe o recurso de glosa, lidas pela regra R2-CTX X2. A extensao exata do
vocabulario (`perspective.RECEBIMENTO_PELO_PAGADOR`) e testada contra esta copia em
`tests/unit/platform/test_validation_perspective_rco_k1.py`. Nao vai para o pin nem para `spec/`.
