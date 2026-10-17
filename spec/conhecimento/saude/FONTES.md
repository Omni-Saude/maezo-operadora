# Base de conhecimento de saúde da Helena — lista FECHADA de fontes

**Decisão:** ADR-0064 ("Base de conhecimento de saúde de fontes públicas — informar sem orientar") e
DL-0118. **Status:** aceito pelo dono para dev (17/10/2026); **revisão médica e jurídica antes de produção**.

Esta lista é **fechada**. Fonte que não está aqui **reprova o CI**
(`tests/unit/agents/test_helena_base_saude_conteudo.py`, pela função `classificar_fonte` de
`src/maezo/conhecimento/base_saude.py`). Para acrescentar uma fonte é preciso um PR que mude **este arquivo,
o ADR-0064 e `classificar_fonte`**, com a aprovação do dono (dev) e, antes de produção, do médico e do
jurídico.

## 1. Para RESPONDER (citação literal curta, com crédito e link)

O conteúdo do gov.br é **CC BY-ND 3.0**: permite uso comercial, **exige crédito** e **proíbe obra
derivada**. Por isso a citação é **literal** (`tipo_texto: citacao_literal`), curta, com a linha de crédito
e o link; nada de reescrever o texto da fonte.

| Fonte | Endereço aceito | Licença |
|---|---|---|
| Saúde de A a Z (Ministério da Saúde) | `https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/...` | CC BY-ND 3.0 |
| INCA — **páginas web** (não os PDFs) | `https://www.gov.br/inca/pt-br/...` (sem `.pdf`, `/arquivos/`, `/media/`) | CC BY-ND 3.0 |
| Calendário Nacional de Vacinação (Ministério da Saúde) | `https://www.gov.br/saude/...vacinacao...` ou `...calendario...` | CC BY-ND 3.0 |
| ANS — RN 566/2022 e Rol de Procedimentos (ato oficial) | `https://www.gov.br/ans/...`, `https://www.ans.gov.br/...`, `https://bvsms.saude.gov.br/bvs/saudelegis/ans/...` | ato oficial |
| Bula do paciente (ANVISA) — **por ora só LINK** | `https://consultas.anvisa.gov.br/...` (`link_leitura` e fonte de texto **próprio**) | reprodução **pendente no jurídico**: nunca `citacao_literal` |

## 2. TEXTOS PRÓPRIOS (escritos pela operadora)

Curtos, em linguagem simples, citando a fonte oficial em que se apoiam (`tipo_texto: proprio`; a linha de
crédito diz "Texto da operadora com base em …"). **Revisão médica pendente.** Usados para:
especialidades, procedimentos, cirurgias, materiais, exames sem fonte leiga e o "para que serve" de
medicamentos (texto próprio curto + `link_leitura` para a bula da ANVISA).

## 3. SÓ VOCABULÁRIO / MAPEAMENTO (nunca vira resposta)

Servem para achar sinônimos e códigos; **nunca** são a fonte creditada de um texto:

- TUSS (ANS);
- dados abertos de medicamentos da ANVISA (`dados.anvisa.gov.br`);
- SIGTAP (DATASUS);
- CBO (Classificação Brasileira de Ocupações);
- CID-11 (OMS, `icd.who.int`);
- DeCS — **exige acordo de licença: NÃO incorporar ainda** (reprovado pelo CI enquanto isso).

## 4. NÃO USAR (reprovado pelo CI)

- BVS — Dicas em Saúde (e as publicações da BVS fora do Saúde Legis);
- Caderno de Atenção Básica nº 28;
- cartilhas e PDFs "não comercial" do INCA e do Ministério da Saúde;
- OPAS/OMS (`paho.org`, `who.int`, exceto o CID-11 como vocabulário);
- Fiocruz;
- Manual MSD.

## 5. Regras do conteúdo (o CI confere)

- Arquivo `spec/conhecimento/saude/<categoria>/<id>.yaml`; categorias `especialidade`, `medicamento`,
  `exame`, `procedimento`, `cirurgia`, `material`, `condicao`, `vacina`, `sintoma`; o `id` tem o prefixo
  da categoria (`esp-`, `med-`, `exa-`, `pro-`, `cir-`, `mat-`, `con-`, `vac-`, `sin-`) e é o nome do arquivo.
- `texto` com no máximo 700 caracteres, frases de até 20 palavras (régua de tom) e passando nas cercas de
  saída da Helena.
- Proibido no `texto`: dose/posologia, quantidade (mg, ml, gotas...), "tome", "pode tomar", "não é grave",
  "é normal", diagnóstico ("você tem..."), promessa de cobertura.
- Toda fonte com `url` https, `licenca` e `coletado_em` (AAAA-MM-DD); `sha256_trecho` é opcional (o
  verificador de fontes compara quando houver).
- `revisao.revisar_ate` no futuro: o ciclo de revisão é de **6 meses**. Vencido, o CI reprova e a Helena
  deixa de responder aquele assunto.
- Sinônimo não pode se repetir entre dois assuntos.
- `sintoma`: 1 a 3 `perguntas_alerta` fechadas (sim/não), `sinais_alarme` (escalam na hora, com 192) e
  `populacao` (`adulto`, `crianca`, `gestante` ou `todos`).

Como adicionar, atualizar e verificar: `docs/runbooks/helena-base-saude.md`.
