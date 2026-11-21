# `spec/processes/dmn/rco/` — tabelas do pacote de regras K1 (rco → Maezo)

Esta pasta recebe as tabelas DMN do pacote **K1** publicado pelo repositório
`regras-contratos-operadora` (rco). O rco decide **"o quê"** (cobertura, DUT, carência,
autorização, limites, pacotes, OPME e glosa); as tabelas de **"como"** (SLA, triagem,
roteamento, alçada, escalonamento) continuam no primeiro nível de `spec/processes/dmn/`.
Desenho de referência: [ADR-0075](../../../../docs/adr/0075-regras-da-operadora-vem-do-rco-pacote-k1.md), seção (b).

## Quem escreve aqui

**Só a atualização do pin** (`config/integrations/rco/rules.lock.json`). Os arquivos chegam
byte a byte como o rco publica, com hit policy `UNIQUE`. Não edite uma tabela desta pasta à mão:
mudar um byte quebra o `sha256` fixado no pin. Regra nova ou corrigida se pede no rco.

## Regras que valem só para esta pasta

- **Id de decisão com prefixo `rco_`.** No motor a chave da decisão é global por tenant; o
  prefixo impede que uma decisão do K1 vire nova versão de uma decisão do Maezo. A validação
  (`make validate-artifacts`) reprova id sem o prefixo.
- **Nome do recurso no deploy = caminho relativo** `rco/<arquivo>.dmn`, para não colidir com
  um arquivo de mesmo nome do primeiro nível.
- **Só arquivos diretamente nesta pasta.** Subpasta dentro de `rco/` não é varrida por nenhum
  ponto (deploy, validação, cercas): a varredura é explícita, não `rglob`.
- **Isenções por pasta** (cada uma testada; as tabelas do primeiro nível continuam checadas):
  - órfã/crossref: decisão sob `rco/` é consumida por worker, sem `decisionRef`, e é governada
    pelo `rules.lock.json`, não pelo `orphans-allowlist.yaml`;
  - cerca de entradas mortas: `rco/` é varrida, e `UNIQUE` é aceita aqui;
  - registro de aprovações (`spec/aprovacoes-de-regras.yaml`): isenção **provisória**, até a
    decisão pendente do dono **P7** (aprovação em lote por versão do manifesto do K1).
- Sem isenção: schema DMN, unicidade de id em toda a árvore, cerca de perspectiva (ADR-0040 D7)
  e classificação PHI das variáveis.

## O arquivo que está aqui hoje

`rco_exemplo_sintetico.dmn` é um **exemplo sintético**. Não é regra de negócio, não veio do rco
e nenhum BPMN ou worker o consome. Existe só para exercitar a infraestrutura acima (e, depois,
o lint do vocabulário de saída do K1: `decisao`, `classeAdverso`, `fila`). Sai quando o primeiro
pacote K1 de verdade for fixado.
