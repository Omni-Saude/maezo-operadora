# Dados do lago que Helena e Lucas precisam: o que existe, o que falta e o que o Maezo faz sem eles

**Status:** registro vivo, aberto em 05/10/2026. Origem: levantamento no Athena (sa-east-1) de 04/10/2026 e o
Excel `Dados_Helena_e_Lucas_x_Lago.xlsx`. Regra da repo (ADR-0037): o Maezo **não lê o lago**; consome só contrato
publicado pela AMH. Os rascunhos dos dois contratos estão em `Omni-Saude/amh-data-platform`, branch
`proposta/maezo-cobranca-e-identidade-v1` (`schemas/openapi/maezo/v1/`), **não publicados**.

## A lógica
1. O Maezo constrói o lado dele contra o contrato, com dados de exemplo nos testes.
2. Todo campo tem estado **ausente**. Dado que o lago não tem vem `null`; o Maezo nunca o completa.
3. Quando o time da AMH ingerir e publicar, só muda o lado do lago e o pin em
   `config/integrations/amh/contracts.lock.json`. O código do Maezo não muda.
4. Este arquivo lista, campo a campo, o que depende do lago e o que o agente faz enquanto isso.

## Cobrança (Lucas): contrato `billing-status`
| Campo | Lago hoje | Se faltar, o Lucas... |
|---|---|---|
| competência, parcela, vencimento, situação, valores, liquidação | existe (`amh_omni_gold.mensalidade_beneficiario`) | escala a humano (catch-all da DMN) |
| ciclos sem conciliação | derivável (competências distintas `vencida`) | escala a humano |
| status conciliado | **critério a decidir (financeiro/PO)**: não há retorno bancário | escala a humano; nunca confirma pagamento |
| boleto (mascarado) | existe | responde sem citar número |
| pagador (pessoa ou empresa) | tabela não ingerida | tratado como `desconhecido` |
| `portable_subject_ref` do tenant da operadora | **0 de 187.646 pessoas com cobrança** | sem referência não há consulta: segue como hoje (fonte simulada só em dev; em produção escala) |

## Identidade (Helena e Lucas): contrato `subject-resolution`
| Campo | Lago hoje | Se faltar, o agente... |
|---|---|---|
| telefone → pessoa | **não existe** | Helena pergunta e segue sem identidade; Lucas escala |
| idade | **vazia** (0 de 215.280 no MPI da operadora) | Helena pergunta a idade (como hoje). O contrato não traz sexo: a triagem decide por população e idade |
| plano ativo, vigência, carência | existe (`fhir_coverage`, `carencia_beneficiario`) | não afirma cobertura |
| titular / dependente | existe | pergunta quando houver mais de um candidato |

## O que depende da AMH (lado do lago)
1. Mintar `portable_subject_ref` para o tenant `omni`.
2. Ingerir o cadastro de pessoa física do Tasy PLS (telefone, e-mail, nascimento) e a tabela de pagador.
3. Fixar UM esquema de hash de telefone e popular a ligação telefone → pessoa.
4. Propósito LGPD de atendimento ao beneficiário (hoje o vocabulário não tem).
5. Critério de "conciliado" e política de atraso (financeiro/PO).
6. Construir e hospedar o serviço que atende os dois contratos; conceder permissões do lago.

## O que o Maezo constrói (ordem)
1. Este registro. 2. Adaptador de cobrança no lugar da fonte simulada. 3. Adaptador de identidade (vários candidatos,
nenhum candidato). 4. Executor do gateway para essas leituras. 5. Ligação no processo e cerca da fonte simulada.
6. PRs de infraestrutura preparados, sem aplicar.

## Fora de escopo (decisão de produto)
Entregar 2ª via (PDF, link ou e-mail): o contrato proposto não habilita, e o Lucas tem cerca que recusa
"vou emitir a segunda via".
