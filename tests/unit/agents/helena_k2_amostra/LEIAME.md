# Amostra SINTETICA do pacote K2 (HEL-3a)

O diretorio `pacote/` e' uma amostra **SINTETICA e FICTICIA**, escrita a mao para os testes do leitor
`maezo.agents.helena.habilidades.cobertura.manual` (`tests/unit/agents/test_helena_manual_k2.py`).
Nao e' conteudo do `regras-contratos-operadora` (rco), nao e' regra de negocio vigente e nao pode ser usada
para responder ninguem. Os ids comecam com `AMOSTRA-`; nao ha dado pessoal.

- O formato `rco-k2-v1` e' a especificacao ESPERADA pelo consumidor e ainda NAO foi confirmado pelo rco
  (sem acesso de leitura ao rco na sessao da HEL-3a).
- Quando houver acesso, substituir por uma amostra copiada do indice real
  `gerado/manual/beneficiario/site/search-index.json` (e do capitulo geral), anotando aqui a ORIGEM e o
  COMMIT do rco de onde veio, e recalcular os sha256 do `manifesto.json` (e o pin dos testes).
- Os textos das regras trazem fatos verificaveis de proposito (prazo em dias uteis, autorizacao previa,
  quantidade): a unidade seguinte constroi um verificador de fatos sobre eles.

Este LEIAME fica FORA de `pacote/` porque o leitor recusa arquivo nao listado no manifesto; o manifesto da
amostra leva a mesma marca no campo `_amostra` (chaves com `_` sao ignoradas pelo leitor).
