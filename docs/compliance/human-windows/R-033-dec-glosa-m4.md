# R-033: categoria de glosa desconhecida na `glosa_triage` (M-4 / OQ-10): registro da decisão do dono para ratificação

**Natureza:** registro de decisão para ratificação humana. Segue a regra de ouro desta árvore: informa, não decide (ADR-0007). A decisão abaixo foi tomada pelo dono e está transcrita da fonte. O bloco de ratificação (§5) fica em branco. Nenhum agente preenche nome, data de ratificação ou assinatura.

**Origem:**
- Item R-033 do roadmap da auditoria forense @f57f0821 (bundle FORENSIC-COMPLETENESS-2026-10, árvore gitignored, consumida de snapshot local; os trechos relevantes estão transcritos aqui). Tipo: decisão humana; flag COUNSEL; lastro F-033 e PEND-454.
- Pergunta do roadmap (verbatim): "Categoria de glosa desconhecida em glosa_triage (OQ-10/M-4): hoje PAGAR sem humano. Qual comportamento?" Opções: "(a) rotear a humano (fail-closed)", "(b) PAGAR e registrar", "(c) negar". Recomendação registrada: "(a): fail-closed; negar seria decisao adversa (hard)".
- Item construtivo dependente: R-062 (F-033 + F-022), entregue no mesmo PR que este registro.

## 1. A decisão do dono (fonte)

- **Fonte:** comentário do dono na issue #1084, https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6545662766, autor `rodaquino-OMNI`, criado em **2026-10-31T15:52:05Z**. Título do comentário: "Decisões do dono (registradas 2026-10-31 via sessão do orquestrador; ADR-0007: data + este issue)".
- **Trecho (verbatim, Rodada 1):** "DEC-14 glosa desconhecida: (a) rotear a humano."
- **Trecho relacionado (verbatim, Rodada 2, Pagamentos):** "Origens do auto: humana + sem glosa + recurso deferido (triagem PAGAR fora até M-4)."
- **Opção escolhida:** **(a) rotear a humano (fail-closed).**
- **Data da decisão:** **2026-10-31.**

## 2. Estado antes da decisão (medido em `origin/main` = `331f7c4f`)

| Fato | Evidência |
|---|---|
| `r_pagar` era a row 1 da `glosa_triage` (hitPolicy FIRST) | `spec/processes/dmn/glosa_triage.dmn` em `331f7c4f`, `<rule id="r_pagar">` logo após as declarações de output |
| A célula `categoria_normalizada` de `r_pagar` era o conjunto NEGATIVO `not("tecnica","clinica")`, que admite `desconhecida`, vazio e qualquer literal fora do domínio | mesma rule, `r_pagar_i2` |
| `desconhecida` é o default do worker e o catch-all da normalização | `src/maezo/tools/workers/contas.py` (`categoria_normalizada = reason_analysis.get("categoria_normalizada", "desconhecida")`); `spec/processes/dmn/glosa_reason_normalization.dmn` (descrição: "rota conservadora a humano a jusante na glosa_triage") |
| Com `item_conforme_tabela=true`, `divergencia_valor=false` e `documentacao_anexa=true`, a conta ia a `End_ContaAprovadaIntegral` sem nenhuma User Task de contas | `tests/integration/processes/test_sp_op_contas_001.py` em `331f7c4f`, `test_m4_categoria_desconhecida_para_em_admissibilidade_humana` (media o resíduo) |
| Repro do roadmap reproduzia o defeito | `python3 -I docs/audits/FORENSIC-COMPLETENESS-2026-10/repro/F-033_glosa_unknown_category_pagar.py` saía com código 1 |

## 3. O que a opção (a) significa no modelo (implementado no item R-062, mesmo PR)

- **Nenhum caminho novo.** A categoria desconhecida passa a seguir o caminho humano que o BPMN CONTAS já modela: catch-all `r_catchall` → `roteamento = ANALISE_HUMANA` → fluxo default `Flow_GWTriagem_Humano` de `GW_Triagem` → `ST_PrepareTriageDossier` → `UT_AnalistaContas`.
- **Mudança na DMN (dado de spec, ADR-0028):** `r_pagar` usa o conjunto POSITIVO fechado `"documental","administrativa","valor"` e foi movida para a posição 4 (específico antes de permissivo). É a correção pré-especificada em `spec/processes/dmn/glosa-triage-shadow-candidate.yaml` (DECISAO-A, opção (a)). O hit policy e as outras quatro rows ficam inalterados. O domínio de `roteamento` continua `{PAGAR, ANALISE_HUMANA}`: nenhuma saída glosa (L0 hard intocado).
- **Por que não (c):** negar ou glosar é decisão adversa (`hard`); só humano decide.
- **Por que não (b):** era o próprio defeito M-4.

## 4. O que esta decisão NÃO fecha (segue aberto)

- **ACHADO-4:** `documental`/`administrativa` com anexos presentes continuam em PAGAR (`docs/review-queue.md`, pergunta ao dono da tabela). DEC-14 não decide isso. O conjunto positivo preserva o comportamento atual dessas categorias de propósito.
- **ACHADO-3:** `tipo_item` continua como input decorativo (wildcard em todas as rows).
- **Mapa TISS reason_code → categoria** (`glosa_reason_normalization`, DRAFT/verify; ADR-0040 OQ-5).
- **Âncora normativa de glosa (F-022):** neste mesmo PR, o catálogo e o contrato deixam de citar a Lei 9.656, art. 18. A âncora substituta (RN 503/2022, art. 14, II e parágrafo único; Lei 9.656, art. 17-A, § 2º, II; RN 501/2022, art. 3º, I) é a proposta VERIFICADA da linha L4 de `docs/compliance/legal/R-054-RATIFICACAO-CITACOES-POR-LINHA.md`. Ela está marcada DRAFT/verify até a ratificação do jurídico (R-054).

## 5. Bloco de ratificação (preenchimento exclusivo de humanos; agentes não preenchem)

- **Opção ratificada:** [  ]
- **Ratificante (papel: owner / dono da tabela `glosa_triage`):** [nome]   **Data:** [  ]   **Assinatura:** [  ]
- **Auditoria de contas + compliance (ciência, `docs/review-queue.md`):** [nome]   **Data:** [  ]
- **Gate cego (quando aplicável):** [  ]
- **Ratificação do manifesto `glosa-triage-shadow-candidate.yaml` (`ratificado` / `revisor` / `ratificado_em`):** [  ]

**Nota:** a decisão do §1 é do dono e tem data e fonte. O bloco acima é a ratificação formal que o DoD do R-033 exige. Enquanto ele estiver em branco, o R-033 segue aberto no roadmap, mesmo com a correção técnica do R-062 já implementada.
