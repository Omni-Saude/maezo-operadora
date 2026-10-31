# Condições contratuais de reembolso por beneficiário — proposta de contrato AMH `tina-reembolso` e consumidor dark

> **Status:** PROPOSTA (30/10/2026). Contrato AMH **não publicado**; consumidor Maezo **dark** (flag desligada, sem
> chamador no fluxo vivo). Nada aqui escolhe valor, teto ou tabela de reembolso.
> **Artefatos:** proposta OpenAPI em `docs/design/condicoes-reembolso-contratuais.openapi.proposta.yaml`; port
> `TinaReembolsoPort` em `src/maezo/ports/tina.py`; método `AmhTinaAdapter.get_condicoes_reembolso` em
> `src/maezo/adapters/amh/tina.py`; composição `compor_condicoes_reembolso` no mesmo módulo; flag
> `WorkerRuntimeSettings.reembolso_condicoes_contratuais` (`MAEZO_REEMBOLSO_CONDICOES_CONTRATUAIS`, padrão `False`).

## 1. Por quê

Decisão do dono (Rodrigo, 30/10/2026): aprovar o REEMBOLSO v2 (PR #1074 — 100% dos pedidos em análise humana) e,
**em paralelo**, trazer para o Maezo os critérios de reembolso **de cada beneficiário**, que variam muito por
contrato/produto e estão registrados nos contratos do Tasy (e, por consequência, no lago da AMH).

A revisão de desenho publicada no #1074 mostrou que a auto-aprovação na `main` já está apagada (teto
`reembolso_auto_approval.max_value_brl: 0`, `REEMBOLSO_AUTO_PAGAMENTO_LIBERADO = False`, pagamento só registrado) e
propôs uma **faixa L2 futura e estreita** (só aprovação integral, abaixo de um teto baixo, em shadow primeiro). Os dois
bloqueios dessa faixa são: (i) uma fonte verificada do valor da despesa (NF) e (ii) os **limites contratuais por
beneficiário**. Este documento cobre a metade (ii). O teto (R-070, issue #1084 DEC-26) continua com o dono; nenhum valor
é escolhido aqui.

Base regulatória (fontes primárias citadas na revisão do #1074; conferência final é do regulatório):

- Lei 9.656/1998, art. 12, VI — reembolso "nos limites das obrigações contratuais", em até 30 dias da entrega da
  documentação adequada.
- RN 566/2022, art. 10, §1º — prazo de 30 dias para o reembolso.
- RN 529/2022, art. 5º, I, "n" — sinal de alerta de pagamento acima da despesa: nunca pagar mais que o gasto.

Consequência para o desenho: o Maezo precisa **ler** o limite contratual (fato) e **aplicar** `min(despesa, limite)` em
DMN (regra). O contrato da AMH entrega o fato; a regra nunca vive em Python nem no contrato.

## 2. Restrições de arquitetura (não violáveis)

| Fonte | O que impõe aqui |
|---|---|
| ADR-0037 (fronteira AMH) | Contrato canônico é da AMH; o Maezo consome contrato publicado e pinado em `config/integrations/amh/contracts.lock.json`. Consumir, não duplicar. O Maezo **nunca** lê o Tasy, nem escreve nele. |
| ADR-0013 / ADR-0019 | Caminho do dado: Tasy → CDC → plataforma de dados AMH (lago de registro) → contrato TINA → Maezo. |
| ADR-0006 (zonas) | A leitura acontece na zona onde roda o REEMBOLSO; a resposta não traz dado de saúde, mas é dado pessoal contratual: `Cache-Control: no-store`, nada em log. |
| AGENTS.md regra 5 | Valor contratual é DADO da AMH; decisão é DMN; prazo regulatório é timer BPMN. |
| `_hard_frozen.yaml` | `authorization_denial` é L0 hard: o consumidor nunca nega nem reduz. Um limite menor que o pedido **manda para humano**, não reduz. |
| W7 | Comportamento novo nasce dark: flag padrão `False`, ligar é ato humano (ROLL). |

## 3. Contrato proposto: rota `GET /interop/tina/v1/subjects/{portable_subject_ref}/condicoes-reembolso`

Nome de trabalho do artefato: `tina-reembolso` (caminho proposto `schemas/openapi/maezo/v1/tina-reembolso.openapi.yaml`
em `Omni-Saude/amh-data-platform`). Nome, versão do manifest aditivo e escopo OAuth **a acordar com a AMH**.

### 3.1 Pedido

| Parâmetro | Onde | Obrigatório | Proposta |
|---|---|---|---|
| `portable_subject_ref` | path | sim | Ref portátil (XRD-05); a resposta fala só deste sujeito (o adaptador recusa resposta de outro sujeito). |
| `purpose_of_use` | query | sim | Finalidade FIXA `analise_reembolso` (nova; a confirmar com AMH e DPO). |
| `data_atendimento` | query | sim | Data da despesa (`AAAA-MM-DD`): as condições são as do contrato **vigente nessa data**. |
| `X-Amh-Request-Ref` | header | sim | Id opaco da solicitação de reembolso no Maezo, para amarrar as duas trilhas de auditoria. |

### 3.2 Resposta (fatos; campo ausente = `null` + `qualidade.campos_ausentes`)

A coluna "Fonte Tasy/lago" está, **toda ela**, "a confirmar com a AMH": o Maezo não conhece as tabelas do Tasy e não
adivinha nomes.

| Campo | Tipo | Significado | Fonte Tasy/lago |
|---|---|---|---|
| `vinculo.produto_ref` / `contrato_ref` | ref opaca | Produto e contrato vigentes na data (nunca o código/número cru). | a confirmar com AMH |
| `vinculo.registro_ans_plano`, `tipo_contratacao`, `vigencia_inicio`, `vigencia_fim` | texto/data | Identificação regulatória e vigência do vínculo. | a confirmar com AMH |
| `livre_escolha` | bool \| null | O produto prevê reembolso por livre escolha. | a confirmar com AMH |
| `regra_valor.tipo` | enum | `tabela_propria` \| `multiplo_tabela_referencia` \| `valor_rede` \| `sem_regra_cadastrada`. | a confirmar com AMH |
| `regra_valor.tabela_referencia`, `multiplo` | texto/número | Qual tabela e qual múltiplo o contrato usa. | a confirmar com AMH |
| `limites[]` (`categoria`, `tipo_limite`, `valor_cents`, `quantidade`, `periodo`) | lista | Limites por categoria de despesa (consulta, exames, terapias…), por evento ou período. Até 50 linhas; `limites_truncados`. | a confirmar com AMH |
| `coparticipacao.aplica`, `regras[]` (`percentual`, `valor_fixo_cents`, `teto_cents`) | bool/lista | Coparticipação/franquia aplicável ao reembolso. | a confirmar com AMH |
| `prazos.pagamento_dias`, `pagamento_contagem`, `solicitacao_dias` | inteiro/enum | Prazo contratual de pagamento (≤ prazo legal) e prazo para o beneficiário pedir. | a confirmar com AMH |
| `documentos_exigidos[]` | enum fechado | NF, recibo, comprovante, pedido médico, relatório, laudo, outro. | a confirmar com AMH |
| `natureza` | constante | `informacao_contratual_nao_e_decisao`. | — |
| `avisos[]` | enum fechado | `contrato_pode_ter_aditivo_nao_refletido`, `regra_valor_sem_tabela_vinculada`, `vinculo_com_mais_de_um_produto`. | — |
| `fonte.origem`, `fonte.atualizada_em` | enum/data-hora | Proveniência (`tasy_via_lake`) e idade da projeção. | lago AMH |

Dinheiro sempre em **inteiro-centavos** (como `*_cents` no contrato SP-OP-REEMBOLSO-001). As quatro primeiras
categorias são o vocabulário de `categoria_procedimento` da DMN `reembolso_calculo` (`consulta`, `exame_simples`,
`exame_especial`, `terapia`), para a DMN futura casar sem tradução.

### 3.3 Erros, truncamento e limite — espelho das rotas TINA publicadas

| HTTP | Significado | Recusa no port |
|---|---|---|
| 400 | finalidade, ref, data ou cabeçalho inválidos | `INVALID_REQUEST` |
| 401 | token ausente/inválido | `NOT_AUTHENTICATED` |
| 403 `consent_denied` / `purpose_not_permitted` / `scope_not_supported` | fail-closed | `CONSENT_REQUIRED` / `PURPOSE_DENIED` / `SCOPE_NOT_SUPPORTED` |
| 404 | rota desligada, sujeito desconhecido ou sem vínculo na data (resposta idêntica) | `NOT_FOUND` |
| 429 | limite por sujeito | `RATE_LIMITED` |
| 500 | erro interno ou falha da auditoria | `UPSTREAM_UNAVAILABLE` |
| 200 fora do schema, de outro sujeito, ou incoerente | violação de contrato | `CONTRACT_VIOLATION` |

Coerências que o schema não expressa e que o adaptador confere (desacordo = `CONTRACT_VIOLATION`, nunca "arrumado"):
`data_atendimento` ecoada igual à pedida; `multiplo` só com `multiplo_tabela_referencia`; limite de valor
(`valor_por_evento`/`valor_por_periodo`) traz `valor_cents`, limite de quantidade traz `quantidade`;
`coparticipacao.aplica = false` sem regras.

### 3.4 Finalidade e consentimento

Mesmo padrão das rotas TINA: `consent_decision_ref` (referência da base legal) viaja como metadado de auditoria do
gateway, nunca na query; a finalidade é fixa e validada pelo schema do parâmetro antes do despacho; a rota tem executor
e escopo OAuth próprios. Base legal proposta: execução de contrato (LGPD art. 7º, V). Confirmação do DPO pendente.

## 4. Consumidor no Maezo (este PR, DARK)

- **Port:** `TinaReembolsoPort.get_condicoes_reembolso(...)` em `src/maezo/ports/tina.py`, com
  `CondicoesReembolsoView` e afins — tipos espelham o contrato; campo ausente = `None`.
- **Adaptador:** `AmhTinaAdapter(..., reembolso_openapi_bytes=, reembolso_executor=)`. Fail-closed por construção: os
  bytes só sobem se o sha256 estiver no pin (`artifact_digests[ARTIFACT_REEMBOLSO]`). **Hoje não está** (nenhum
  manifest da AMH traz o artefato), então passar os bytes faz o construtor recusar (`TinaContractError`); sem os bytes,
  `get_condicoes_reembolso` recusa `SCOPE_NOT_SUPPORTED`. A operação **não** entra em `tina.OPERATIONS` nem no gateway
  (`SCOPE_BY_OPERATION`): não há executor de produção.
- **Composição:** `compor_condicoes_reembolso(ligado=..., ...)` devolve `None` com a flag desligada (sem ler bytes) e
  `None` com a flag ligada enquanto o pin não tiver o digest — a flag sozinha não liga nada.
- **Flag:** `MAEZO_REEMBOLSO_CONDICOES_CONTRATUAIS` (padrão `False`). Ligar é ato humano.
- **Testes:** `tests/unit/adapters/amh/test_tina_reembolso.py`, contra a PROPOSTA OpenAPI como fixture de contrato
  (digest injetado num pin de teste por `monkeypatch`; o lock real não é tocado). Provam também que, com o lock real,
  o construtor recusa.
- **Não feito de propósito:** nenhum chamador no SP-OP-REEMBOLSO-001. O #1074 (REEMBOLSO v2) está aberto; para não
  conflitar, nenhum arquivo do REEMBOLSO é tocado. A leitura informativa no painel do analista entra depois do merge do
  #1074, atrás da mesma flag.

## 5. Como isto alimenta a faixa L2 futura (sem valores)

Quando o contrato for publicado e pinado, e o R-070 decidido pelo dono:

1. O worker de abertura lê `get_condicoes_reembolso` e escreve **fatos** de processo (ex.: `livre_escolha`,
   `limite_categoria_cents`, `prazo_contratual_dias`, `fonte_condicoes_atualizada_em`).
2. Uma DMN (nova versão de `reembolso_calculo` ou tabela irmã, a desenhar) calcula `min(despesa verificada, limite
   contratual)` e a elegibilidade para a faixa. Linha catch-all → `ANALISE_HUMANA`.
3. `AUTO_APROVAR` só para aprovação **integral**: despesa verificada ≤ limite contratual **e** ≤ teto R-070 **e**
   documentos exigidos presentes **e** dentro do prazo. Qualquer fato `None`, truncado ou com aviso → humano.
4. Shadow primeiro; o flip é ROLL humano.

Nada disso está implementado aqui.

## 6. Perguntas abertas

### 6.1 Para a AMH (steward de contratos)

1. Os critérios de reembolso estão no Tasy por **produto**, por **contrato** ou por **beneficiário** (ou com
   precedência entre eles, como nas carências)? Qual a precedência?
2. Quais tabelas/colunas do Tasy carregam livre escolha, regra de valor, tabela de referência e múltiplo, limites,
   coparticipação, prazos e documentos? Já chegam ao lago pelo CDC (ADR-0013) ou exigem nova captura?
3. A tabela de referência (e seus valores por procedimento) também é exposta? Em outro contrato (dado de referência,
   como o catálogo de procedimentos)?
4. Aditivos contratuais: estão no mesmo cadastro? Se não, o aviso `contrato_pode_ter_aditivo_nao_refletido` basta?
5. Como representar contrato vigente numa data passada (histórico de vigência no lago)?
6. Nome do artefato, versão do manifest aditivo, escopo OAuth (`interop/tina.reembolso`?) e finalidade
   (`analise_reembolso`?) — aceitam os propostos?
7. Limite por sujeito (429) e frescor esperado da projeção.
8. O vocabulário de `categoria` e de `documentos_exigidos` casa com o cadastro? Quais valores faltam?

### 6.2 Para o dono (Rodrigo)

1. **R-070 (teto de auto-aprovação, #1084 DEC-26):** continua aberto; nada aqui depende dele, mas a faixa L2 depende.
2. Aprovar o pedido do contrato à AMH com esta forma (ou indicar quem na AMH é o dono do pedido).
3. Base legal e finalidade `analise_reembolso`: levar ao DPO para ratificação.
4. Mostrar as condições ao analista (painel do portal) antes da faixa L2 — sim/não, e quando (após o merge do #1074).
5. Fonte verificada do valor da NF (a outra metade do bloqueio da faixa L2): quem é o dono dessa frente.

## 7. Limites declarados

- O contrato é PROPOSTA do consumidor; a AMH pode publicar algo diferente. Quando publicar, o pin XRG-3 confere os
  bytes publicados, e o port/adaptador se ajusta a eles (não o contrário).
- As referências regulatórias acima vêm da revisão do #1074; a conferência final de vigência é do regulatório.
- Nenhum valor real de contrato aparece aqui; todos os exemplos são sintéticos.
