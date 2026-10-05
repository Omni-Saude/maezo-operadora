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
| status conciliado | **critério a decidir (financeiro/PO)**. Desde 05/10 o lago tem `tasy_titulo_receber_liq` (2,79 mi) e `tasy_titulo_receber_cobr`; a ocorrência CNAB (`OBTER_OCORRENCIA_TIT_ESCRIT`) é função PL/SQL e ainda precisa de view no Tasy | escala a humano; nunca confirma pagamento |
| boleto (mascarado) | existe | responde sem citar número |
| pagador (pessoa ou empresa) | **existe desde 05/10** (`amh_omni_bronze.tasy_pls_contrato_pagador`, 83.189 linhas) — falta o adaptador ler | tratado como `desconhecido` até o contrato expor |
| `portable_subject_ref` do tenant da operadora | **0 de 187.646 pessoas com cobrança** | sem referência não há consulta: segue como hoje (fonte simulada só em dev; em produção escala) |

## Identidade (Helena e Lucas): contrato `subject-resolution`
| Campo | Lago hoje | Se faltar, o agente... |
|---|---|---|
| telefone → pessoa | **bruto existe desde 05/10** (`amh_omni_bronze.tasy_compl_pessoa_fisica`, 665.815 linhas, `nr_telefone`/`ds_email`); falta o hash de telefone e a ligação no MPI | Helena pergunta e segue sem identidade; Lucas escala |
| idade | **bruto existe desde 05/10** (`amh_omni_bronze.tasy_pessoa_fisica`, 243.481 linhas, `dt_nascimento`/`ie_sexo`/`nm_pessoa_fisica`); o MPI da operadora continua vazio (0 de 215.280) até ser repopulado | Helena pergunta a idade (como hoje). O contrato não traz sexo: a triagem decide por população e idade |
| plano ativo, vigência, carência | existe (`fhir_coverage`, `carencia_beneficiario`) | não afirma cobertura |
| titular / dependente | existe | pergunta quando houver mais de um candidato |

## O que depende da AMH (lado do lago)
1. Mintar `portable_subject_ref` para o tenant `omni`.
2. ~~Ingerir o cadastro de pessoa física do Tasy PLS (telefone, e-mail, nascimento) e a tabela de pagador.~~ **Feito em 05/10** (amh-data-platform#181: 13 tabelas em `amh_omni_bronze`, inclusive rescisão, reajuste e cobrança ativa). Falta repopular o MPI a partir delas.
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
