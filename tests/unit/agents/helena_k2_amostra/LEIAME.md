# Amostra SINTETICA do pacote K2 (HEL-3a, HEL-3c)

O diretorio `pacote/` e' uma amostra **SINTETICA e FICTICIA**, escrita para os testes do leitor
`maezo.agents.helena.habilidades.cobertura.manual` (`tests/unit/agents/test_helena_manual_k2.py`), da camada
`regra_geral` e dos golden evals da cobertura. Nao e' conteudo do `regras-contratos-operadora` (rco), nao e'
regra de negocio vigente e nao pode ser usada para responder ninguem. Os ids comecam com `AMOSTRA-`, os
textos com "Texto fictício"; nao ha' dado pessoal.

- O FORMATO agora e' o contrato publicado pelo rco: `rco-k2-v1`, rco `main` fe1dc89,
  `docs/programa/contrato-k2.md` (sete arquivos: `manifesto.json` canonico, `manifesto.sha256`,
  `search-index.json`, `capitulo-geral.yaml`, `sinonimos.json`, `sinonimos-por-termo.json`,
  `avaliacoes.yaml`). O CONTEUDO continua sintetico.
- Como o pacote real de hoje, o manifesto traz `ratificado: false` (e `commit_rco` de 40 zeros): os testes
  carregam a amostra com `aceitar_nao_ratificado=True`, e o padrao do leitor a recusa.
- Os textos das regras trazem fatos verificaveis de proposito (prazo em dias uteis, autorizacao previa,
  quantidade): o verificador de fatos e os evals dependem deles.
- Quem mexer em qualquer arquivo de `pacote/` regenera o `manifesto.json` (bytes canonicos: chaves
  ordenadas, `ensure_ascii=False`, separadores `(",", ":")`, sem quebra de linha no fim) e o
  `manifesto.sha256` (`sha256:<hex>` e uma quebra de linha), e atualiza o pin dos testes.

Este LEIAME fica FORA de `pacote/` porque o leitor recusa qualquer arquivo alem dos sete do contrato.
