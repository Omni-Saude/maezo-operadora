# R-054: folha de ratificação das citações normativas, linha a linha (DEC-23, opção b)

**Natureza:** minuta de ratificação. Este documento informa e não decide (ADR-0007). As células "Ratificação do jurídico" ficam em branco. Nenhum agente preenche decisão, nome, data ou assinatura. Quem prepara é o agente; quem ratifica, linha a linha, é o jurídico (Lucas).

**Origem:**
- Item R-054 do roadmap da auditoria forense @f57f0821 (bundle FORENSIC-COMPLETENESS-2026-10, árvore gitignored, consumido de snapshot local; os trechos relevantes estão transcritos aqui). O item é de decisão humana, dono jurídico, flag COUNSEL.
- Decisão do dono **DEC-23, opção (b)**: "ratificar por linha (parcial) com pareceres separados".
- Achados de lastro: F-019 (NIP), F-020 (CANCEL), F-021 (descredenciamento), F-022 (glosa), F-023 (Lei 9.656 arts. 12/13/35-C), F-025 (LBI art. 63), F-026 (art. 17 § 4º, REG-1.9 dark) e F-122 (RN 566, "tempos e distâncias").
- Errata RN 259, aprovada pelo dono: a RN 259 foi revogada pela RN 566, art. 16, I. A ratificação jurídica está pendente (linha L9).

**Estado medido:** `main` = `2be4515f` (checkout limpo do worktree da branch docs/w1a-citacoes-calendario-ans). Todo `arquivo:linha` abaixo foi medido nesse commit.

**Caminho do DoD do roadmap:** o roadmap registra como artefato de ratificação o caminho docs/compliance/human-windows/R-054-dec-cit-rn.md. A missão Wave 1a pediu o documento nesta árvore (`docs/compliance/legal/`). Depois que o jurídico decidir, mover ou copiar o registro para o caminho do DoD é ato separado, que este documento não pratica.

**Legenda de status:**
- **VERIFICADO:** texto lido nesta sessão (2026-10-31) em fonte primária oficial (planalto.gov.br, in.gov.br/DOU, bvsms.saude.gov.br/Saúde Legis ou gov.br/ans), com sha256 do download na §Fontes.
- **NÃO VERIFICÁVEL:** fonte primária não obtida. Nenhuma linha desta folha caiu nessa condição. Os residuais parciais estão declarados linha a linha.

**Limite comum a todas as linhas com fonte Saúde Legis (bvsms):** o portal traz o texto **original** da norma, com a advertência "Este texto não substitui o publicado no Diário Oficial da União". Alterações posteriores só foram varridas onde a linha diz isso.

## Quadro-resumo

| Linha | Tema | Citação atual no repo (resumo) | Citação corrigida | Status | Ratificação do jurídico |
|---|---|---|---|---|---|
| L1 | NIP | RN 388/2016 | RN 483/2022, art. 10, I, II, § 1º e § 2º (RN 388/2015 revogada pela RN 483, art. 59, I) | VERIFICADO | |
| L2 | Cancelamento a pedido | "RN 593 consolida/substitui RN 412"; "RN 593: efeito imediato" | RN 561/2022, art. 15, II (efeito imediato), art. 18 (comprovante em 10 d.u.), art. 25 (revoga RN 412); RN 593/2023 só no ramo de inadimplência | VERIFICADO | |
| L3 | Descredenciamento / substituição | RN 567 para hospital; "RN 567/2018"; P15D | Não hospitalar: RN 567/2022, arts. 3º e 10. Hospitalar: RN 585/2023, art. 6º, com comunicação à ANS + Lei 9.656, art. 17, § 1º | VERIFICADO | |
| L4 | Glosa | Lei 9.656, art. 18; "RN 305/2016" | Fora do art. 18. Prazos contratuais: RN 503/2022, art. 14, II e parágrafo único + Lei 9.656, art. 17-A, § 2º, II. Padrão: RN 501/2022, art. 3º, I | VERIFICADO (a âncora é escolha do jurídico) | |
| L5 | Lei 9.656, arts. 12/13/35-C | art. 12 (inciso VIII no plano), art. 13, art. 35-C "(imediato)" | art. 12, VI (não existe inciso VIII); art. 13, parágrafo único, II; art. 35-C (cobertura, sem prazo). O "imediato" vem da RN 623/2024, art. 12, I | VERIFICADO | |
| L6 | RN 566 (adequação de rede) | "RN 259 tempos e distâncias" | RN 566/2022, art. 1º, § 1º, e arts. 3º a 5º; não há critério de distância na norma | VERIFICADO | |
| L7 | LBI | Lei 13.146 "DRAFT/verify", sem artigo | Lei 13.146/2015, art. 63, caput e § 1º | VERIFICADO | |
| L8 | Art. 17, § 4º | REG-1.9 (dark) | Lei 9.656, art. 17, § 4º, só para redução de rede **hospitalar** + RN 585/2023, arts. 2º, VI, e 10 | VERIFICADO | |
| L9 | Errata RN 259 (negativa) | "RN 259 proíbe negativa sem médico auditor" | Mérito clínico: Lei 12.842/2013, art. 5º, II + RN 424/2017. Negativa administrativa: ADR-0005 (política interna) + RN 623/2024, arts. 14 e 16 + LGPD, art. 20 | VERIFICADO | |

---

## L1: NIP

**Citação atual no repo:**
- `docs/processes/contracts/SP-OP-NIP-001.md:5` ("RN 388/2016 (NIP ...)"), `docs/processes/contracts/SP-OP-NIP-001.md:172`, `docs/processes/contracts/SP-OP-NIP-001.md:256-257`.
- `spec/processes/dmn/nip_sla.dmn:10`, `spec/processes/dmn/nip_sla.dmn:67`, `spec/processes/dmn/nip_sla.dmn:77`, `spec/processes/dmn/nip_sla.dmn:87` (`fonte_regulatoria` = "RN 388/2016 ...").
- `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn:37`, `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn:246`, `spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn:321`.

**Citação corrigida:** RN 483/2022, art. 10:
- inciso I: 5 dias úteis na NIP assistencial;
- inciso II: 10 dias úteis na NIP não assistencial;
- § 1º: notificação considerada feita na data de disponibilização;
- § 2º: contagem a partir do primeiro dia útil seguinte.

A RN 388 é de **2015** (o repo grafa 2016) e foi revogada pela RN 483, art. 59, I.

**Texto primário (verbatim):**

> RESOLUÇÃO NORMATIVA - RN Nº 483, DE 29 DE MARÇO DE 2022
>
> Art. 10. Recebida a demanda de reclamação pela ANS, a operadora será notificada para que adote as medidas necessárias para a solução da demanda junto ao beneficiário nos seguintes prazos: I - até 5 (cinco) dias úteis na NIP assistencial; e II - até 10 (dez) dias úteis na NIP não assistencial. § 1º A operadora se considera notificada na data da disponibilização da notificação no espaço próprio do endereço eletrônico da ANS na Internet (www.gov.br/ans). § 2º O prazo para adoção das medidas necessárias para a solução da demanda começará a ser contado a partir do primeiro dia útil seguinte à data da notificação.
>
> Art. 59. Revogam-se: I - as Resoluções Normativas nº 388, de 25 de novembro de 2015; nº 414, de 11 de novembro de 2016; nº 444, de 1º de abril de 2019;nº 326, de 22 de abril de 2013; e Resolução Normativa nº 369, de 23 de janeiro de 2015;
>
> Art.60. Esta Resolução Normativa entra em vigor em 31 de março de 2022.

**Fonte:** https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0483_31_03_2022.html. Acesso em 2026-10-31.

**Status:** VERIFICADO. Residual: texto original; alterações posteriores ao art. 10 não foram varridas.

**Nota de modelagem (não é decisão):** hoje o repo usa P5D/P10D **corridos** a partir de `data_recebimento_nip_iso`. Em dias úteis isso é igual ou menor que o prazo legal (leitura conservadora). O § 2º, porém, desloca o início para o 1º dia útil seguinte. Se o modelo conservador basta, cabe ao jurídico dizer.

| Ratificação do jurídico (L1) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L2: Cancelamento a pedido (CANCEL)

**Citação atual no repo:**
- `docs/processes/contracts/SP-OP-CANCEL-001.md:9`: "RN 593/2023 (regras de cancelamento/rescisao e suspensao por inadimplencia — consolida/substitui RN 412/2016 ...)". Ver também `docs/processes/contracts/SP-OP-CANCEL-001.md:23` e `docs/processes/contracts/SP-OP-CANCEL-001.md:105`.
- `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:36-37`, `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:151`, `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:173-174` ("RN 593: o cancelamento a pedido tem efeito imediato").
- `spec/processes/dmn/cancel_admissibility.dmn:10`, `spec/processes/dmn/cancel_admissibility.dmn:20`.
- `spec/processes/dmn/cancel_sla.dmn:53-58`: pedido → análise P5D, alerta P3D.
- `docs/processes/catalog.md:17`: "RN 412".

**Citação corrigida:**
- **Cancelamento a pedido / exclusão de beneficiário → RN 561/2022:**
  - art. 15, II: efeito imediato e irrevogável a partir da ciência da operadora ou administradora;
  - art. 18: comprovante do efetivo cancelamento em 10 dias úteis;
  - art. 7º, § 3º, e art. 11, §§ 2º e 3º: efeito imediato nos coletivos;
  - art. 25: revoga a RN 412/2016.
- **RN 593/2023:** rege a notificação por inadimplência, ou seja, o ramo de rescisão por inadimplência do mesmo processo. Seu art. 18 só cancela a Súmula Normativa nº 28. O texto da RN 593 tem **zero** ocorrências de "412". A RN 593 não consolida nem substitui a RN 412.

**Divergência com a redação da pergunta DEC-23 (registrada, não resolvida):** a pergunta fala em "RN 593 consolidadora". A fonte primária refuta "consolidadora da RN 412". A leitura que o texto sustenta é: RN 593 vigente para o ramo de inadimplência; RN 561 para o pedido do beneficiário. Qual redação adotar é decisão do jurídico.

**Texto primário (verbatim):**

> RESOLUÇÃO NORMATIVA ANS Nº 561, DE 15 DE DEZEMBRO DE 2022 Dispõe sobre a solicitação de cancelamento do contrato do plano de saúde individual ou familiar, e de exclusão de beneficiário de contrato coletivo empresarial ou por adesão.
>
> Art. 15. Recebida pela operadora ou administradora de benefícios, a solicitação do cancelamento do contrato de plano de saúde individual ou familiar ou de exclusão de beneficiários em plano coletivo empresarial ou coletivo por adesão, a operadora ou administradora de benefícios, destinatária do pedido, deverá prestar de forma clara e precisa, no mínimo, as seguintes informações: [...] II - efeito imediato e caráter irrevogável da solicitação de cancelamento do contrato ou exclusão de beneficiário, a partir da ciência da operadora ou administradora de benefícios;
>
> Art. 18. A partir do fornecimento do comprovante de recebimento da solicitação de cancelamento ou exclusão, a operadora ou a administradora de benefícios deverá encaminhar, no prazo de dez dias úteis, o comprovante do efetivo cancelamento do contrato ou desligamento do beneficiário, por qualquer meio que assegure sua ciência.
>
> Art. 7º, § 3º: "§3º A exclusão tem efeito imediato a partir da data de ciência pela operadora."
>
> Art. 25. Fica revogada a Resolução Normativa n˚ 412, de 10 de novembro de 2016. Art. 26. Esta Resolução Normativa entra em vigor em 01 de fevereiro de 2023.
>
> RESOLUÇÃO NORMATIVA ANS Nº 593, DE 19 DE DEZEMBRO DE 2023 Dispõe sobre a notificação por inadimplência à pessoa natural contratante de plano privado de assistência à saúde e ao beneficiário que paga a mensalidade do plano coletivo diretamente à operadora, e cancela a Súmula Normativa nº 28, de 30 de novembro de 2015.
>
> Art. 18. Fica cancelada a Súmula Normativa nº 28, de 30 de novembro de 2015. Art. 19. Esta Resolução entra em vigor no dia 1º de abril de 2024.

**Fontes:**
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0561_30_12_2022.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0593_20_12_2023.html

Acesso em 2026-10-31.

**Status:** VERIFICADO. Residual: texto original; alterações posteriores não foram varridas.

**Nota de modelagem (não é decisão):** `cancel_sla` modela o pedido com "análise P5D". A norma dá efeito imediato ao pedido. O prazo de 10 d.u. do art. 18 é o do comprovante e hoje não tem timer. Se o P5D é só prazo operacional de cadastro, compatível com o efeito imediato, é ponto para o jurídico.

| Ratificação do jurídico (L2) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L3: Descredenciamento e substituição de prestador

**Citação atual no repo:**
- `spec/processes/dmn/cred_prior_notice.dmn:6`, `spec/processes/dmn/cred_prior_notice.dmn:10`.
- `spec/processes/dmn/cred_prior_notice.dmn:40-46`: hospital, citando "RN 567 — descredenciamento hospitalar ...", P30D.
- `spec/processes/dmn/cred_prior_notice.dmn:49-55`: com beneficiários, P30D.
- `spec/processes/dmn/cred_prior_notice.dmn:63-64`: sem beneficiários, **P15D**.
- `docs/processes/contracts/SP-OP-CRED-001.md:5`: "RN 567 ... descredenciamento de prestador hospitalar". Ver também `docs/processes/contracts/SP-OP-CRED-001.md:147-149` e `docs/processes/catalog.md:20`.
- `docs/processes/contracts/SP-OP-FRAUDE-001.md:5` e `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn:28`: "RN 567/2018".

**Citação corrigida:**
- **Não hospitalar → RN 567/2022**, de 16/12/2022 (o ano 2018 está errado):
  - art. 3º: comunicação aos beneficiários com 30 dias de antecedência;
  - art. 10, § 1º: portal, 30 dias antes, com a informação acessível por 180 dias;
  - art. 10, § 2º, I e II: exceção; comunicar na data do conhecimento e substituir em 60 dias.
- **Hospitalar → RN 585/2023, art. 6º:** comunicação aos consumidores **e à ANS** com 30 dias de antecedência, nos termos da Lei 9.656, art. 17. O § 1º do art. 17 tem o mesmo comando.
- **Canal da comunicação à ANS:** Anexo II da RN 585, itens 1 e 3, na redação da RN 677/2026 (vigente desde 03/08/2026): "sistema eletrônico para a alteração de produto" no Portal Operadoras.
- **P15D "sem beneficiários":** nenhum texto da RN 567 sustenta 15 dias. O único prazo do art. 3º é de 30 dias e vale para a substituição. Se o descredenciamento sem beneficiário vinculado exige comunicação, e com que prazo, é questão para o jurídico.

**Texto primário (verbatim):**

> RESOLUÇÃO NORMATIVA ANS Nº 567, DE 16 DE DEZEMBRO DE 2022 Dispõe sobre a substituição de prestadores de serviços de atenção à saúde não hospitalares.
>
> Art. 3º É facultada a substituição de prestadores de serviços de atenção à saúde não hospitalares, desde que por outro equivalente e mediante comunicação aos beneficiários com trinta dias de antecedência.
>
> Art. 10. O portal corporativo e a central de atendimento das operadoras devem manter atualizadas as informações das substituições havidas em sua rede assistencial não hospitalar para consulta pelos beneficiários. § 1º A informação de que trata o caput deve ser disponibilizada com antecedência mínima de trinta dias, contados da data da efetiva substituição, e deve permanecer acessível por cento e oitenta dias. § 2º Excepcionalmente, nos casos em que houver suspensão definitiva do atendimento por parte do prestador, sem cumprimento do prazo para notificação ou não renovação contratual estabelecido entre as partes, ou rescisão contratual por fraude ou infração das normas sanitárias e fiscais em vigor, a operadora deverá: I - comunicar aos beneficiários a exclusão do prestador na data em que tomou conhecimento do fato; e II - providenciar sua substituição e comunicá-la aos beneficiários no prazo de sessenta dias, contados da data em que tomou conhecimento da suspensão do atendimento.
>
> RESOLUÇÃO NORMATIVA ANS Nº 585, DE 18 DE AGOSTO DE 2023 Dispõe sobre os critérios para as alterações na rede assistencial hospitalar no que se refere à substituição de entidade hospitalar e redimensionamento de rede por redução; [...]
>
> Art. 6º Às operadoras de planos de assistência à saúde é permitido realizar substituição de entidades hospitalares desde que sejam equivalentes e mediante comunicação aos consumidores e à ANS com 30 (trinta) dias de antecedência, nos termos do art. 17 da Lei nº 9.656, de 3 de junho de 1998.
>
> Lei 9.656/1998, art. 17, § 1º: "§ 1o É facultada a substituição de entidade hospitalar, a que se refere o caput deste artigo, desde que por outro equivalente e mediante comunicação aos consumidores e à ANS com trinta dias de antecedência, ressalvados desse prazo mínimo os casos decorrentes de rescisão por fraude ou infração das normas sanitárias e fiscais em vigor."
>
> RN 677/2026 (DOU 21/07/2026, Seção 1, edição 135, p. 168), art. 2º, nova redação do item 1 do Anexo II (as aspas iniciais são do original): "1. A solicitação de substituição de entidade hospitalar ou de redimensionamento de rede por redução, nos termos previstos na Resolução Normativa ANS nº 585, de 2023, deverá ser realizada por meio de sistema eletrônico para a alteração de produto disponibilizado no Portal Operadoras na página institucional da ANS na internet. [...] Art. 3º Esta Resolução Normativa entra em vigor no dia 3 de agosto de 2026.

**Fontes:**
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0567_30_12_2022.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0585_24_08_2023.html
- https://www.planalto.gov.br/ccivil_03/leis/L9656compilado.htm
- https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-677-de-20-de-julho-de-2026-720521002
- https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-620-de-25-de-novembro-de-2024-598247020

Acesso em 2026-10-31.

**Alterações da RN 585 varridas:**
- **RN 620/2024** (DOU 27/11/2024, primária): altera o § 2º do art. 13, o § 2º do art. 21 e o Anexo, e acrescenta o Anexo II. Art. 5º: "Art. 5º Esta Resolução Normativa entra em vigor em 31 de dezembro de 2024."
- **RN 677/2026** (primária): altera itens do Anexo II.

Nenhuma das duas toca o art. 6º.

**Vigência da RN 585:** a redação original do art. 29 fixava "01 de março de 2024". A RN 598/2024, art. 1º, deu nova redação: "entrará em vigor 01 de setembro de 2024" (https://bvsms.saude.gov.br/bvs/saudelegis/ans/2024/res0598_15_02_2024.html). A RN 609/2024, art. 1º, deu nova redação: "entrará em vigor 31 de dezembro de 2024 e será revisitada em 24 meses de sua vigência" (https://bvsms.saude.gov.br/bvs/saudelegis/ans/2024/res0609_24_06_2024.html). Acesso em 2026-10-31. A RN 620, art. 5º, fixa a vigência da própria RN 620 na mesma data e não é fonte da vigência da RN 585.

**Status:** VERIFICADO.

| Ratificação do jurídico (L3) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L4: Glosa fora do art. 18 da Lei 9.656

**Citação atual no repo:**
- `docs/processes/catalog.md:12`: SP-OP-CONTAS-001, "prazo contratual; Lei 9.656 art. 18". Ver também `docs/processes/catalog.md:13`.
- `spec/processes/dmn/contas_sla.dmn:73-76`: P30D / P20D sem base normativa.
- `docs/processes/contracts/SP-OP-RECURSO-001.md:274`: "RN 305/RN 501 TISS"; o roadmap registra o ano errado "RN 305/2016" no achado F-022.
- `docs/processes/contracts/SP-OP-CONTAS-001.md:5`.

**Citação corrigida (proposta para o jurídico escolher):**
- **Retirar a Lei 9.656, art. 18, como âncora de glosa.** O art. 18 trata das obrigações e direitos do prestador que aceita ser credenciado: não discriminar, priorizar a marcação, não exclusividade. Não trata de adjudicação de conta.
- **Âncora dos prazos de glosa (contratuais):**
  - RN 503/2022, art. 14, II: o contrato escrito deve expressar os prazos de contestação da glosa, de resposta da operadora e de pagamento;
  - RN 503/2022, art. 14, parágrafo único: prazo de contestação igual ao prazo de resposta;
  - Lei 9.656, art. 17-A, § 2º, II: o contrato define os prazos e procedimentos de faturamento e pagamento.
- **Padrão:** RN 501/2022, art. 3º, I (o TISS padroniza os "recursos de glosas"). O texto integral da RN 501 foi lido e **não fixa prazo** de análise nem de recurso de glosa. Seus únicos prazos são o envio mensal à ANS (art. 25) e a implantação de versões (art. 26).
- **Consequência para o modelo (não é decisão):** P30D/P20D seriam parâmetros por contrato, não prazo regulatório.
- **Ano da RN 305:** é 2012, não 2016. A norma foi revogada pela RN 501, art. 31.

**Texto primário (verbatim):**

> Lei 9.656/1998, art. 18: "Art. 18. A aceitação, por parte de qualquer prestador de serviço ou profissional de saúde, da condição de contratado, referenciado, credenciado ou cooperado de uma operadora de produtos de que tratam o inciso I e o § 1o do art. 1o desta Lei implica as seguintes obrigações e direitos: (Redação dada pela Lei nº 13.003, de 2014) I - o consumidor de determinada operadora, em nenhuma hipótese e sob nenhum pretexto ou alegação, pode ser discriminado ou atendido de forma distinta daquela dispensada aos clientes vinculados a outra operadora ou plano; II - a marcação de consultas, exames e quaisquer outros procedimentos deve ser feita de forma a atender às necessidades dos consumidores, privilegiando os casos de emergência ou urgência, assim como as pessoas com mais de sessenta e cinco anos de idade, as gestantes, lactantes, lactentes e crianças até cinco anos; III - a manutenção de relacionamento de contratação, credenciamento ou referenciamento com número ilimitado de operadoras, sendo expressamente vedado às operadoras, independente de sua natureza jurídica constitutiva, impor contratos de exclusividade ou de restrição à atividade profissional."
>
> Lei 9.656/1998, art. 17-A, § 2º, II: "§ 2o O contrato de que trata o caput deve estabelecer com clareza as condições para a sua execução, expressas em cláusulas que definam direitos, obrigações e responsabilidades das partes, incluídas, obrigatoriamente, as que determinem: [...] II - a definição dos valores dos serviços contratados, dos critérios, da forma e da periodicidade do seu reajuste e dos prazos e procedimentos para faturamento e pagamento dos serviços prestados;"
>
> RESOLUÇÃO NORMATIVA ANS Nº 503, DE 30 DE MARÇO DE 2022 [...] Art. 14. A rotina de auditoria administrativa e técnica deve ser expressa, inclusive quanto a: I - hipóteses em que o Prestador poderá incorrer em glosa sobre o faturamento apresentado; II - prazos para contestação da glosa, para resposta da operadora e para pagamento dos serviços em caso de revogação da glosa aplicada; e III - conformidade com a legislação específica dos conselhos profissionais sobre o exercício da função de auditor. Parágrafo único. O prazo acordado para contestação da glosa deve ser igual ao prazo acordado para resposta da operadora.
>
> Art. 3º São finalidades do Padrão TISS: I - padronizar as ações administrativas de verificação, solicitação, autorização, cobrança, demonstrativos de pagamento e recursos de glosas; [...] Art. 31. Revogam-se as Resoluções Normativas nº 305, de 09 de outubro de 2012, e nº 341, de 27 de novembro de 2013.

**Fontes:**
- https://www.planalto.gov.br/ccivil_03/leis/L9656compilado.htm
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0503_04_04_2022.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0501_01_04_2022.html

Acesso em 2026-10-31.

**Status:** VERIFICADO quanto aos textos. A escolha da âncora é do jurídico. Residual: o texto da RN 503 é o original; alterações posteriores não foram varridas.

| Ratificação do jurídico (L4) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L5: Lei 9.656/1998, arts. 12, 13 e 35-C

**Citação atual no repo:**
- `spec/processes/dmn/reembolso_sla.dmn:10-11`: "Lei 9.656/1998 art. 12 reembolso na livre escolha; prazo de reembolso ~30 dias".
- `spec/processes/dmn/reembolso_sla.dmn:35` e `spec/processes/dmn/reembolso_sla.dmn:40`: "Lei 9.656/1998 art. 12/35-C".
- `spec/processes/dmn/reembolso_sla.dmn:46` e `spec/processes/dmn/reembolso_sla.dmn:54`: P30D.
- `spec/processes/dmn/inadimplencia_purga.dmn:49`, `spec/processes/dmn/inadimplencia_purga.dmn:57`, `spec/processes/dmn/inadimplencia_purga.dmn:65`: P60D, inclusive na linha de coletivo.
- `spec/processes/dmn/auth_sla.dmn:38`: "Lei 9.656/1998 art. 35-C (imediato)".
- `docs/plans/maezo-completion-program.md:71` e `docs/plans/maezo-completion-program.md:133`: correção de registro VIII → VI.

**Citação corrigida:**
- **Art. 12, VI:** reembolso em **urgência ou emergência**, quando não é possível usar a rede, pagável em até 30 dias após a entrega da documentação. O art. 12 termina no inciso **VII** (filho adotivo). "Art. 12, VIII" não existe, o que confirma a correção do plano.
- **Reembolso em livre escolha, fora da urgência:** a base não é o art. 12, VI. É o contrato. A RN 566/2022, art. 10, § 1º, manda reembolsar "nos limites do estabelecido contratualmente". O prazo de 30 dias do caput do art. 10 vale para o descumprimento dos arts. 4º a 6º (texto-base `docs/compliance/legal/textos-base/RN-566-2022.md`).
- **Art. 13, parágrafo único, II:** só para contratos **individuais**. Rescisão ou suspensão só por fraude, ou por não pagamento por mais de 60 dias, consecutivos ou não, nos últimos 12 meses, com notificação até o 50º dia. Se o P60D da linha de coletivo em `inadimplencia_purga.dmn` tem base, é questão para o jurídico (ver também a RN 593, art. 4º, § 3º, sobre a regra das duas mensalidades).
- **Art. 35-C:** obrigação de cobertura em emergência, urgência e planejamento familiar. **Não fixa prazo:** a palavra "imediato" não está no art. 35-C. A resposta imediata está na RN 623/2024, art. 12, I (texto-base `docs/compliance/legal/textos-base/RN-623-2024.md`).

**Texto primário (verbatim):**

> Art. 12, VI: "VI - reembolso, em todos os tipos de produtos de que tratam o inciso I e o § 1o do art. 1o desta Lei, nos limites das obrigações contratuais, das despesas efetuadas pelo beneficiário com assistência à saúde, em casos de urgência ou emergência, quando não for possível a utilização dos serviços próprios, contratados, credenciados ou referenciados pelas operadoras, de acordo com a relação de preços de serviços médicos e hospitalares praticados pelo respectivo produto, pagáveis no prazo máximo de trinta dias após a entrega da documentação adequada;" | VII: "VII - inscrição de filho adotivo, menor de doze anos de idade, aproveitando os períodos de carência já cumpridos pelo consumidor adotante."
>
> Art. 13, parágrafo único: "Parágrafo único. Os produtos de que trata o caput, contratados individualmente, terão vigência mínima de um ano, sendo vedadas: (Redação dada pela Medida Provisória nº 2.177-44, de 2001) I - a recontagem de carências; (Redação dada pela Medida Provisória nº 2.177-44, de 2001) II - a suspensão ou a rescisão unilateral do contrato, salvo por fraude ou não-pagamento da mensalidade por período superior a sessenta dias, consecutivos ou não, nos últimos doze meses de vigência do contrato, desde que o consumidor seja comprovadamente notificado até o qüinquagésimo dia de inadimplência; e (Redação dada pela Medida Provisória nº 2.177-44, de 2001) III - a suspensão ou a rescisão unilateral do contrato, em qualquer hipótese, durante a ocorrência de internação do titular."
>
> Art. 35-C: "Art. 35-C. É obrigatória a cobertura do atendimento nos casos: (Redação dada pela Lei nº 11.935, de 2009) I - de emergência, como tal definidos os que implicarem risco imediato de vida ou de lesões irreparáveis para o paciente, caracterizado em declaração do médico assistente; (Redação dada pela Lei nº 11.935, de 2009) II - de urgência, assim entendidos os resultantes de acidentes pessoais ou de complicações no processo gestacional; (Redação dada pela Lei nº 11.935, de 2009) III - de planejamento familiar. (Incluído pela Lei nº 11.935, de 2009) Parágrafo único. A ANS fará publicar normas regulamentares para o disposto neste artigo, observados os termos de adaptação previstos no art. 35."
>
> RN 623/2024, art. 12, I (DOU 19/12/2024): "I - as solicitações de procedimentos ou serviços de urgência e emergência devem ser respondidas imediatamente pela operadora, observadas as normas legais e infralegais em vigor; e"
>
> RN 566/2022, art. 10, caput e § 1º (DOU 02/01/2023): "Art. 10. Na hipótese de descumprimento do disposto nos arts. 4º, 5º ou 6º, caso o beneficiário seja obrigado a pagar os custos do atendimento, a operadora deverá reembolsá-lo integralmente no prazo de até trinta dias, contado da data da solicitação de reembolso, inclusive as despesas com transporte. § 1º Para todos os produtos que prevejam a opção de acesso a livre escolha de prestadores, o reembolso será efetuado nos limites do estabelecido contratualmente."

**Fontes:**
- https://www.planalto.gov.br/ccivil_03/leis/L9656compilado.htm (acesso em 2026-10-31; HTTP 200 com user-agent de navegador; com o user-agent padrão do `curl` o servidor reseta a conexão)
- textos-base RN 623 e RN 566 (matérias DOU pinnadas em 2026-10-24 e 2026-10-27)

**Status:** VERIFICADO. Isto fecha a lacuna "planalto inacessível" do F-023.

| Ratificação do jurídico (L5) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L6: RN 566/2022 e a adequação de rede ("tempos e distâncias")

**Citação atual no repo:**
- `docs/processes/contracts/SP-OP-ADEQUACAO-001.md:5`: "RN 259/2011 (garantia de atendimento — tempos e distancias maximas de acesso ...)". Ver também `docs/processes/contracts/SP-OP-ADEQUACAO-001.md:9` e `docs/processes/contracts/SP-OP-ADEQUACAO-001.md:43`.
- `spec/processes/dmn/adequacao_sla.dmn:10`, `spec/processes/dmn/adequacao_sla.dmn:45`, `spec/processes/dmn/adequacao_sla.dmn:53`, `spec/processes/dmn/adequacao_sla.dmn:61`, `spec/processes/dmn/adequacao_sla.dmn:69`.
- `docs/processes/catalog.md:21`: "RN 259 geografia".

**Citação corrigida:**
- RN 566/2022:
  - art. 1º, § 1º: definições de área geográfica de abrangência, área de atuação, município da demanda, região de saúde, indisponibilidade e inexistência;
  - art. 3º: prazos máximos;
  - art. 4º: indisponibilidade; atendimento no mesmo município, fora da rede, ou em município limítrofe;
  - art. 5º: inexistência; municípios limítrofes, região de saúde ou transporte.
- O corpo normativo da RN 566 tem **zero** ocorrências de "distância" e de "quilômetro". Os limiares de `adequacao_sla.dmn` são política interna, não norma ANS.
- RN 259 → RN 566 (art. 16, I; ver L9).

**Texto primário (verbatim, texto-base DOU):**

> Art. 4º Na hipótese de indisponibilidade de prestador integrante da rede assistencial que ofereça o serviço ou procedimento demandado, no município pertencente à área geográfica de abrangência e à área de atuação do produto, a operadora deverá garantir o atendimento em: I - prestador não integrante da rede assistencial no mesmo município; ou II - prestador integrante ou não da rede assistencial nos municípios limítrofes a este.
>
> Art. 5º Na hipótese de inexistência de prestador, seja ele integrante ou não da rede assistencial, que ofereça o serviço ou procedimento demandado, no município pertencente à área geográfica de abrangência e à área de atuação do produto, a operadora deverá garantir atendimento em: I - prestador integrante ou não da rede assistencial nos municípios limítrofes a este; ou II - prestador integrante ou não da rede assistencial na região de saúde à qual faz parte o município.

**Fonte:** texto-base `docs/compliance/legal/textos-base/RN-566-2022.md`, matéria DOU https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-566-de-29-de-dezembro-de-2022-455439257 (pinnada em 2026-10-27 com sha256 no cabeçalho). Contagem de "distância" e "quilômetro" refeita em 2026-10-31 sobre o corpo transcrito.

**Status:** VERIFICADO.

| Ratificação do jurídico (L6) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L7: LBI, art. 63

**Citação atual no repo:** `docs/design/requisitos-acessibilidade-ui.md:134-138`: "Lei 13.146/2015 ... DRAFT/verify". O documento deixa em aberto a aplicabilidade a operadora privada e não cita artigo.

**Citação corrigida:** Lei 13.146/2015, art. 63:
- caput: acessibilidade obrigatória nos sítios mantidos por **empresas com sede ou representação comercial no País**;
- § 1º: símbolo de acessibilidade em destaque. O símbolo hoje não consta dos requisitos.

**Texto primário (verbatim):**

> Art. 63. É obrigatória a acessibilidade nos sítios da internet mantidos por empresas com sede ou representação comercial no País ou por órgãos de governo, para uso da pessoa com deficiência, garantindo-lhe acesso às informações disponíveis, conforme as melhores práticas e diretrizes de acessibilidade adotadas internacionalmente. § 1º Os sítios devem conter símbolo de acessibilidade em destaque.

**Fonte:** https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13146.htm. Acesso em 2026-10-31.

**Status:** VERIFICADO.

| Ratificação do jurídico (L7) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L8: Lei 9.656, art. 17, § 4º (só rede hospitalar)

**Citação atual no repo:**
- `docs/processes/contracts/SP-OP-CRED-001.md:275-300`: desdobro REG-1.9, dark, aplicado ao ramo de descredenciamento.
- Cerca estática: `tests/unit/docs/test_reg19_art17_par4_cred_desdobro.py:49-52`.

**Citação corrigida:**
- Lei 9.656, art. 17, § 4º: autorização expressa da ANS **só** no "redimensionamento da **rede hospitalar** por redução".
- RN 585/2023, que regulamenta o dispositivo:
  - art. 1º: objeto;
  - art. 2º, VI: definição de "Redimensionamento da Rede Hospitalar por Redução";
  - art. 10, I a III: autorização expressa, limite de impacto e comunicação.
- A substituição ou o descredenciamento de prestador **não hospitalar** segue a RN 567 (L3), sem autorização da ANS.

**Texto primário (verbatim):**

> Art. 17, § 4º: "§ 4o Em caso de redimensionamento da rede hospitalar por redução, as empresas deverão solicitar à ANS autorização expressa para tanto, informando: (Incluído pela Medida Provisória nº 2.177-44, de 2001) I - nome da entidade a ser excluída; (Incluído pela Medida Provisória nº 2.177-44, de 2001) II - capacidade operacional a ser reduzida com a exclusão; (Incluído pela Medida Provisória nº 2.177-44, de 2001) III - impacto sobre a massa assistida, a partir de parâmetros definidos pela ANS, correlacionando a necessidade de leitos e a capacidade operacional restante; e (Incluído pela Medida Provisória nº 2.177-44, de 2001) IV - justificativa para a decisão, observando a obrigatoriedade de manter cobertura com padrões de qualidade equivalente e sem ônus adicional para o consumidor."
>
> Art. 1º Esta Resolução dispõe sobre os critérios para as alterações na rede assistencial hospitalar no que se refere à substituição de entidade hospitalar e redimensionamento de rede por redução, regulamentando o disposto no artigo 17 da Lei nº 9.656, de 1998. [...] VI - Redimensionamento da Rede Hospitalar por Redução - supressão de estabelecimento hospitalar da rede do produto; [...] Art. 10. Às operadoras de planos de assistência à saúde é permitido realizar redimensionamento de rede hospitalar por redução, nos termos da Lei nº 9.656, de 1998, desde que: I - tenha obtido autorização expressa da ANS; II - o impacto sobre a massa assistida não ultrapasse os limites definidos pela ANS; e III - os beneficiários sejam comunicados de forma efetiva, nos termos do disposto na presente Resolução Normativa.

**Fontes:**
- https://www.planalto.gov.br/ccivil_03/leis/L9656compilado.htm
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0585_24_08_2023.html

Acesso em 2026-10-31.

**Status:** VERIFICADO. A vigência da RN 585 está em L3.

| Ratificação do jurídico (L8) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## L9: Errata RN 259 (fundamento da regra "negativa só por médico auditor")

**Citação atual no repo (RN 259 como fundamento da negativa humana):**
- `spec/policies/autonomy/L0-core.yaml:7`: "negativa: medico auditor (RN 259)".
- `src/maezo/gateway/pep.py:65`.
- `src/maezo/gateway/credential_vault.py:25`.
- `docs/adr/0005-hitl-architectural-guarantee.md:6`: "RN 259 proibe negativa de cobertura sem medico auditor."
- `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:326`.
- `docs/processes/catalog.md:10`.
- `docs/processes/contracts/SP-OP-REEMBOLSO-001.md:5`.

**Citação corrigida (errata aprovada pelo dono; ratificação jurídica pendente):**
- A **RN 259/2011 foi revogada** pela RN 566/2022, art. 16, I.
- **Negativa por mérito clínico:**
  - Lei 12.842/2013, art. 5º, II: perícia e auditoria médicas são privativas de médico;
  - RN 424/2017, quando houver divergência técnico-assistencial: junta; parecer do desempatador acatado (art. 6º, § 4º); a não realização indicada pelo desempatador não é negativa indevida se o rito foi cumprido (art. 20). Texto-base novo: `docs/compliance/legal/textos-base/RN-424-2017.md`.
- **Negativa administrativa:**
  - política interna ADR-0005 (escolha de desenho, não norma);
  - RN 623/2024, art. 14: redução a termo, com motivo e com a cláusula ou o dispositivo legal;
  - RN 623/2024, art. 16: reanálise pela Ouvidoria;
  - LGPD, art. 20: revisão de decisão tomada unicamente com base em tratamento automatizado.
- **Aviso de honestidade:** nenhuma dessas normas diz, com essas palavras, que "toda negativa exige médico auditor". A regra L0 hard do ADR-0005 é mais restritiva que o texto legal, e é uma escolha interna. A errata troca a fonte citada; não muda a regra.

**Texto primário (verbatim):**

> RN 566/2022, art. 16, I (DOU 02/01/2023): "Art. 16. Ficam revogados: I - a Resolução Normativa - nº 259, de 17 de junho de 2011;"
>
> Lei 12.842/2013, art. 5º: "Art. 5º São privativos de médico: I - (VETADO); II - perícia e auditoria médicas; coordenação e supervisão vinculadas, de forma imediata e direta, às atividades privativas de médico;"
>
> RN 424/2017 (DOU 27/06/2017, grafia do HTML do DOU; ver o texto-base): "§ 4º O parecer do desempatador será acatado para fins decobertura." [...] "Art.20. A indicação constante do parecer conclusivo dodesempatador pela não realização do procedimento, não caracterizanegativa de cobertura assistencial indevida por parte da operadora,desde que cumpridos todos os procedimentos estabelecidos nesta Resolução,inclusive quanto às notificações do profissional assistente edo beneficiário."
>
> RN 623/2024, art. 14, caput (DOU 19/12/2024): "Art. 14. Havendo negativa de autorização para realização do procedimento ou serviço assistencial solicitado nos termos do art. 10, §1°, seja o profissional de saúde credenciado ou não, a operadora deverá reduzi-la a termo e informar, detalhadamente, em linguagem clara e adequada, ao beneficiário, dentro do prazo de resposta previsto no art. 12, independentemente de solicitação, o motivo da negativa de autorização do procedimento, indicando a cláusula contratual ou o dispositivo legal que a justifique."
>
> RN 623/2024, art. 16, caput: "Art. 16. Ao beneficiário de plano de saúde é assegurada a faculdade de requerer a reanálise de sua solicitação, a qual será apreciada pela Ouvidoria da operadora, instituída na forma da Resolução Normativa n° 323, de 3 de abril de 2013, ou norma que vier a sucedê-la."
>
> LGPD (Lei 13.709/2018), art. 20, caput: "Art. 20. O titular dos dados tem direito a solicitar a revisão de decisões tomadas unicamente com base em tratamento automatizado de dados pessoais que afetem seus interesses, incluídas as decisões destinadas a definir o seu perfil pessoal, profissional, de consumo e de crédito ou os aspectos de sua personalidade."

**Fontes:**
- https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12842.htm
- https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709compilado.htm

Ambas com acesso em 2026-10-31. Mais os textos-base RN 566, RN 623 e RN 424, todos com matéria DOU e sha256.

**Status:** VERIFICADO.

| Ratificação do jurídico (L9) | |
|---|---|
| Decisão (ratifica / ratifica com ressalva / devolve) | |
| Ressalva ou parecer separado | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## Perguntas fora do escopo desta folha (citadas, não respondidas)

- **F-024:** prazo de 15 dias atribuído ao art. 18 da LGPD em `docs/processes/catalog.md:11`. É item de DPO, não de R-054.
- **RN 424, art. 4º:** a remissão à RN 259 revogada. Ponto COUNSEL próprio (`docs/processes/contracts/SP-OP-AUTH-001.md:608`).
- **Inadimplência:** âncora do 50º dia e regra das duas mensalidades (RN 593, art. 4º e § 3º). Itens R-019/R-027 do roadmap.
- **AUTH:** os "21 dias úteis" atribuídos a prazo de resposta (RN 566, art. 3º, XIII, contra RN 623, art. 12, § 4º).
- **Calendário ANS:** documento irmão `docs/compliance/legal/R-006-R-007-CALENDARIO-ANS-LEVANTAMENTO.md`.

## Fontes (downloads desta sessão, 2026-10-31; sha256 do arquivo bruto)

| Conteúdo | URL | sha256 | bytes |
|---|---|---|---|
| RN 483/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0483_31_03_2022.html | `7d735ce576022fc5ede4b2df295bb4c1cb930b1e71e0f0bb689412c56093a65f` | 63617 |
| RN 561/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0561_30_12_2022.html | `a04577312886993a9aeafbbb9b27f7d49ec58f587534a2506e9fec5c883be7ed` | 21476 |
| RN 593/2023 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0593_20_12_2023.html | `da07d104cd71bcb2d34964c0050fd817ffcc7a6296be8edfe5f6281b747fe2f9` | 19160 |
| RN 567/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0567_30_12_2022.html | `6b809bc2f8d8c0cfdc6d176527ff7c328ffcb7dca7212a8d5f9972284c600334` | 18975 |
| RN 585/2023 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0585_24_08_2023.html | `fadbf0ed213d687692f85bb84aa46ba8d2716be1f64ad4683949bd7850ad4038` | 34670 |
| RN 620/2024 (DOU) | https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-620-de-25-de-novembro-de-2024-598247020 | `0c5d96f601134e8e05c949a3ed59d9a795498ce4b09242bfc5bc3e211a287afc` | 117430 |
| RN 598/2024 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2024/res0598_15_02_2024.html | `1db5af5eddcf652f49ed8288396e4d2f26248fa8b8ec17fab6ad998e24895972` | 2486 |
| RN 609/2024 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2024/res0609_24_06_2024.html | `72b272d292dc1ab8f8bfb3b2fb51f2020b8fb75307106ccbdf728076cbd4529c` | 2546 |
| RN 677/2026 (DOU) | https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-677-de-20-de-julho-de-2026-720521002 | `aa84508df9b3802803b9f97391a4497396f1a28776e0a92696c6527dc6f9c7b7` | 89153 |
| Lei 9.656/1998 compilada (Planalto) | https://www.planalto.gov.br/ccivil_03/leis/L9656compilado.htm | `cab78614a7f5e68950f376c43754ac0a3bf13668a3e0098424a9b8df81feca11` | 173591 |
| RN 503/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0503_04_04_2022.html | `2e71d6e45066f5c4c42dae8ef05795403d97a630ebf68d2bbc4250a32033adc9` | 30107 |
| RN 501/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0501_01_04_2022.html | `f179f8974b52bbeab302cff8d4e4d7715684bf80adfb28b55ebf3eeb0b91a704` | 20980 |
| Lei 13.146/2015 (Planalto) | https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13146.htm | `56611feca07cc01dd69c47e72f267fb35c85e74cd545b7067e1cbfb2f0cf70b6` | 243670 |
| Lei 12.842/2013 (Planalto) | https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12842.htm | `60432dba2394977c4f96bd8ee417d04a1eef5de01b298848a0a4d71024fc4879` | 25307 |
| Lei 13.709/2018 compilada (Planalto) | https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709compilado.htm | `11e450581f62c1c219d16d602d14b53c24c615fd09b13b158ca8b513dc90d5dd` | 230913 |
| RN 424/2017 (DOU) | https://www.in.gov.br/web/dou/-/resolucao-normativa-rn-n-424-de-26-de-junho-de-2017-19140213 | `a4833f5ce0a73fab89dc1ee574b27c4b8275827b590c0591085d6b71bfd8bddb` | 106304 |

Os textos-base já pinnados (RN 566, RN 623) mantêm os checksums dos próprios cabeçalhos e do `docs/compliance/legal/MANIFEST.sha256`. Os downloads brutos não são versionados, conforme a convenção desta árvore e o `docs/compliance/legal/404-LOG.md`. Os checksums acima permitem re-baixar e conferir.
