# R-070 — Teto de auto-aprovação do REEMBOLSO ("D-07" da Tarefas_Pendentes): proposta para decisão do dono

**Natureza:** MINUTA DE DECISÃO. Segue a regra de ouro desta árvore: informa, não decide (ADR-0007). O bloco de decisão (§6) fica em branco para o dono e a atuária preencherem. Nenhum agente preenche nome, data ou assinatura.

**Origem:**
- Item R-070 do roadmap da auditoria forense @f57f0821: decisão humana; dono: owner; flags COUNSEL e ACTUARIAL.
- DEC-26 na issue #1084.
- Pedido do dono em 2026-10-30, durante a revisão do PR #1074: "use deep think and suggest rigid ceiling decisions (R-070)".

**Distinção de IDs:** o "D-07" deste documento é o teto da `docs/Tarefas_Pendentes.md:877`. Não é o D-07 do plano (`docs/plans/maezo-completion-program.md`, LA-02).

## 1. Estado medido em `main` = `2e38fc74`

| Fato | Evidência |
|---|---|
| Teto atual do tenant AMH = 0 (fail-closed) | `spec/policies/autonomy/tenants-amh.yaml:21-22`; `spec/policies/autonomy/L0-core.yaml:24` |
| Trava de pagamento automático desligada | `src/maezo/tools/workers/reembolso.py:214` (`REEMBOLSO_AUTO_PAGAMENTO_LIBERADO: bool = False`) |
| Nenhum dinheiro se move: o pagamento só é registrado | `src/maezo/tools/workers/reembolso.py:971` (`REGISTERED_PENDING_CNAB`) |
| Resolvedor de teto fail-closed | `src/maezo/tools/workers/ceilings.py:213` (`within_l2_ceiling`) |
| Precedente AUTH: teto de R$ 500, escolhido sobre a distribuição real das guias (72% das guias com valor) | `spec/policies/autonomy/tenants-amh.yaml:7` e seguintes |
| Lições do AUTH que este teto herda: valor ausente/zero nunca satisfaz o teto; arredondamento para cima | `src/maezo/tools/workers/auth.py:299` (`_FIN_VALOR_ZERO`); `src/maezo/tools/workers/ceilings.py:259` (`math.ceil`) |

**PR #1074 (REEMBOLSO v2, aprovado pelo dono em 2026-10-30):** passa 100% dos pedidos para análise humana. A faixa automática fica **adiada, não abolida**. Os critérios contratuais por beneficiário (contratos no Tasy / lake AMH, via contrato canônico AMH, ADR-0037) estão em construção em paralelo.

## 2. Moldura regulatória (fontes primárias; detalhes na revisão do #1074)

- **Lei 9.656/1998, art. 12, VI** e **RN 566/2022, art. 10 §1º:** o reembolso respeita os limites contratuais e é pago em até 30 dias.
- **RN 529/2022, art. 5º, I, n:** pagar acima do valor da despesa é sinal de alerta de lavagem de dinheiro. Por isso o pagamento nunca excede a despesa.
- **Aprovação INTEGRAL automática:** não há vedação em texto primário.
- **Parcial, redução e negativa:** humanas. A política L0 `hard` do repo exige isso. Para mérito clínico, a Lei 12.842/2013, art. 5º, II e a RN 424 também. Para negativa: termo motivado (RN 623, art. 14), reanálise pela Ouvidoria (art. 16) e revisão a pedido (LGPD, art. 20).
- **Itens do art. 10-B da Lei 9.656** (bolsas de colostomia e correlatos) não podem ter limite de valor. Ficam fora da faixa automática.

## 3. Proposta do orquestrador, rígida, a pedido do dono (não vinculante)

### 3.1 Invariantes inegociáveis (código + política)
1. **Só APROVAR integral.** A faixa automática nunca aprova parcialmente, nunca reduz e nunca nega.
2. **Valor aprovado = `min(valor_solicitado, limite_contratual_do_beneficiário, teto)`.** Se o solicitado passar do teto, o pedido vai a humano; nunca é pago "até o teto".
3. **Valor ausente, zero ou não verificado vai a humano.** É a lição `_FIN_VALOR_ZERO` do AUTH.
4. **Comparação após arredondar os centavos para cima** (`math.ceil`).
5. **Kill-switch duplo:** constante de código + teto na política. Cada um fecha a faixa sozinho; configuração inválida fecha.
6. **O teto só roteia.** Nunca é motivo de pagamento menor.

### 3.2 Elegibilidade da fase 1 (listas fechadas)
- **Categoria:** consulta eletiva, em livre escolha.
- **Excluídos:** urgência e emergência; exames; terapias; itens que exigem avaliação clínica; itens do art. 10-B.
- **Valor da NF verificado** (chave NF-e/NFS-e ou OCR com tolerância). Valor apenas declarado não basta.
- **Limite contratual do beneficiário** vindo do contrato (link AMH em construção). Sem esse dado, o pedido vai a humano.

### 3.3 Teto por pedido
- **Fórmula:** `teto_categoria = min(limite_contratual, P60 dos valores historicamente aprovados integralmente na categoria, TETO_DURO)`.
- **TETO_DURO da fase 1: R$ 300 por pedido** (60% do teto do AUTH). Isso é **julgamento**, não dado. A justificativa é que no reembolso a documentação é enviada pelo próprio beneficiário, uma verificação mais fraca do que a guia do prestador.
- **O P60 sai do histórico de reembolsos no lake AMH** (Tasy), pelo mesmo método do AUTH. **Até esse dado existir, o teto continua R$ 0.**

### 3.4 Exposição acumulada (números propostos sem dado; recalibrar após a sombra)
- **Por beneficiário:** no máximo 2 aprovações automáticas e R$ 600 por 30 dias corridos.
- **Por tenant:** orçamento diário da faixa de R$ 3.000. Ao atingir, a faixa fecha até o dia seguinte.

### 3.5 Disparos automáticos de desligamento (exigem religar manual)
- Qualquer reversão ou redução humana de uma aprovação automática.
- Duplicidade detectada (mesmo sha256 de NF; ou mesma pessoa + data + valor).
- Configuração de teto ou política inválida ou ausente.
- Concordância da sombra abaixo de 98%.

### 3.6 Promoção (rígida)
1. **Sombra:** o agente recomenda e o humano decide. Mínimo de 200 casos elegíveis **ou** 60 dias. Exige ≥ 98% de concordância e **zero** casos em que o humano negou ou reduziu um pedido marcado como elegível.
2. **Ativo:** só com assinatura do dono e da atuária no §6.
3. **Subida do teto:** no máximo +50% por trimestre, sempre com nova evidência de sombra. **Nunca acima de R$ 500 (teto do AUTH) sem novo ADR.**
4. **Nova categoria:** cada uma passa pela própria sombra.

### 3.7 Dependências antes do ativo
- (a) Valor da NF verificado (guarda G2 da revisão do #1074).
- (b) Contrato AMH de condições de reembolso por beneficiário.
- (c) Distribuição histórica de reembolsos no lake AMH (para o P60).
- (d) Jurídico (#1081): tipos de reembolso elegíveis.

## 4. Opções para o dono (com consequências)

| Opção | Teto por pedido | Consequência |
|---|---|---|
| A — status quo | R$ 0 | Nenhuma automação. Fila humana integral (v2). |
| B — proposta rígida (§3) | min(limite contratual, P60, R$ 300); R$ 0 até haver dados | Faixa estreita, calibrada por dado, com sombra antes do ativo. |
| C — paridade com o AUTH | R$ 500 | Exposição maior com verificação mais fraca do que no AUTH. Exige a mesma calibração por dado. |

## 5. O que o agente constrói depois da decisão

- **Opção B ou C:**
  - PR com a DMN `reembolso_auto_approval` nova (só aprovar integral) e o worker `ST_ValidarCriteriosAuto` (calcula, não ecoa), no molde do `ValidateAutoCriteriaWorker` do AUTH;
  - contadores de exposição e kill-switch duplo;
  - modo **sombra** (recomendação visível ao analista), sem efeito automático;
  - o teto em `tenants-amh.yaml` só muda no PR de ativação, depois do §6.
- **Opção A:** nada.

## 6. Bloco de decisão (preenchimento exclusivo do dono e da atuária; agentes não preenchem)

- **Opção escolhida:** [  ]
- **Teto por pedido / por categoria:** [  ]
- **Exposição por beneficiário e por tenant:** [  ]
- **Categorias e tipos elegíveis:** [  ]
- **Fonte de verificação do valor da NF:** [  ]
- **Critérios de promoção sombra → ativo:** [  ]
- **Decisor (dono):** [nome]   **Data:** [  ]   **Assinatura:** [  ]
- **Atuária:** [nome]   **Data:** [  ]   **Assinatura:** [  ]
- **Jurídico (ciência sobre tipos elegíveis):** [nome]   **Data:** [  ]
