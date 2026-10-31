# R-006 / R-007: levantamento em fonte primária do calendário ANS (gatilhos do SP-OP-ANS-CRON-001)

**Natureza:** levantamento redigido pelo agente para o jurídico assinar (decisão do dono: "agent drafts, legal signs"). Informa e não decide (ADR-0007). As células de assinatura ficam em branco. Este documento não religa nenhum timer nem altera BPMN, DMN, contrato ou worker. Religar um gatilho exige o PR próprio descrito no contrato, depois da assinatura.

**Origem:**
- Itens R-006 (decisão humana; dono jurídico; flag COUNSEL) e R-007 (conformidade regulatória; depende do R-006) do roadmap da auditoria forense @f57f0821 (bundle FORENSIC-COMPLETENESS-2026-10, gitignored, consumido de snapshot).
- Achados F-002 e F-121.
- Decisão do dono DEC-03 (já aplicada em `main`): o timer do SIP saiu e os outros 4 gatilhos estão `isExecutable="false"` até haver parecer.

**Caminho do DoD do roadmap:** o roadmap registra docs/compliance/human-windows/R-006-dec-ans-cal.md. A missão Wave 1a pediu o documento nesta árvore. Mover o registro depois da decisão é ato separado.

**Estado medido:** `main` = `2be4515f`.

**Definição atual dos gatilhos no repo:**

| Gatilho (`report_type`) | BPMN (definition / timeCycle) | Contrato | Calendário / SLA / worker |
|---|---|---|---|
| `RN_209_UTILIZACAO` | `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn:80-88`, `R/P1M` em `:86`, `isExecutable="false"` | `docs/processes/contracts/SP-OP-ANS-CRON-001.md:196` | `spec/processes/dmn/ans_calendar.dmn:70-76` (mensal); `spec/processes/dmn/ans_sla.dmn:47`; `src/maezo/tools/workers/ans_cron.py:163` |
| `RN_388_QUALIDADE` | mesmo BPMN `:120-128`, `R/P1Y` em `:126`, desligado | `docs/processes/contracts/SP-OP-ANS-CRON-001.md:197` | `spec/processes/dmn/ans_calendar.dmn:79-85` (anual); `spec/processes/dmn/ans_sla.dmn:53`; `src/maezo/tools/workers/ans_cron.py:164` |
| `RN_424_TISS_MONITORAMENTO` | mesmo BPMN `:160-168`, `R/P1M` em `:166`, desligado | `docs/processes/contracts/SP-OP-ANS-CRON-001.md:198` | `spec/processes/dmn/ans_calendar.dmn:88-94` (mensal); `spec/processes/dmn/ans_sla.dmn:59`; `src/maezo/tools/workers/ans_cron.py:165` |
| `DIOPS_TRIMESTRAL` | mesmo BPMN `:200-208`, `R/P3M` em `:206`, desligado | `docs/processes/contracts/SP-OP-ANS-CRON-001.md:199` | `spec/processes/dmn/ans_calendar.dmn:97-104` (trimestral); `spec/processes/dmn/ans_sla.dmn:64-65`; `src/maezo/tools/workers/ans_cron.py:166` |
| SIP (`RN_124_SIP`), só backlog | definition removida (cabeçalho do BPMN, `:17-21`) | `docs/processes/contracts/SP-OP-ANS-CRON-001.md:195` | `spec/processes/dmn/ans_calendar.dmn:52-67` (backlog ou extinta; periodicidade "mensal" em `:58` e `:67`) |

**Legenda de confiança:**
- **alta:** obrigação, periodicidade e regra de data estão em texto normativo primário lido nesta sessão.
- **média:** a norma está identificada, mas a ligação ao gatilho do repo é inferência do agente, ou a regra de data vem de documento oficial infranormativo.
- **baixa:** não se aplica a nenhuma linha.

## Quadro-resumo

| Gatilho | Norma vigente | Obrigação | Periodicidade | Regra de data | Confiança | Assinatura do jurídico |
|---|---|---|---|---|---|---|
| `RN_209_UTILIZACAO` | Nenhuma norma de "utilização". A cadeia da RN 209 leva ao capital regulatório: RN 209 → RN 451/2020 → RN 526/2022 → **RN 569/2022** | Manter PLA ≥ capital regulatório "a qualquer tempo". Os dados de capital seguem **nas datas do DIOPS** | contínua; sem envio próprio | a do DIOPS (ver DIOPS) | alta (cadeia); média (não haver envio autônomo) | |
| `RN_388_QUALIDADE` | RN 388/2015 revogada pela RN 483/2022 (era fiscalização, não qualidade). Programa de qualidade vigente: **RN 505/2022** (PQO/IDSS) | A ANS calcula o IDSS com dados dos sistemas oficiais. Dever ativo da operadora: **divulgar o IDSS no sítio em até 30 dias** da divulgação pela ANS | anual (competência), por evento (divulgação) | até 30 dias após a divulgação do resultado pela ANS (art. 22) | média | |
| `RN_424_TISS_MONITORAMENTO` | RN 424/2017 é junta médica. O TISS é regido pela **RN 501/2022** (arts. 23, 25 e 27) + Componente Organizacional do Padrão TISS. A RN 639/2025 **não** trata do TISS | Envio mensal dos dados do Padrão TISS à ANS (monitoramento) | mensal | **até o dia 25 do segundo mês subsequente à competência** (Componente Organizacional set/2026, item 242) | alta (obrigação e periodicidade: RN 501, art. 25); média (regra de data: Componente Organizacional, item 242, documento infranormativo por delegação do art. 25) | |
| `DIOPS_TRIMESTRAL` | **RN 527/2022**, art. 8º, na redação da **RN 594/2023** | DIOPS/ANS XML trimestral; mensal simplificado para S1/S2 (art. 8º-A); dispensa para S4 ≤ 20 mil (art. 9º) | trimestral (+ mensal, conforme segmento) | 1T 15/05; 2T 15/08; 3T 15/11; 4T **28/02** do exercício seguinte | alta | |
| SIP (backlog até o 4T/2025) | **RN 551/2022**, art. 7º, para as competências até o 4T/2025. **RN 639/2025** revoga a 551 e desobriga após o 4T/2025, com vigência em 02/03/2026 | Envio trimestral do SIP; o último devido é o do 4T/2025 | trimestral | último dia útil de maio / agosto / novembro / fevereiro do ano seguinte | alta | |

---

## G1: `RN_209_UTILIZACAO`

**Pergunta do briefing:** a RN 209 foi revogada pela RN 451/2020. Que obrigação está em vigor?

**Achado:**
- A RN 209/2009 tratava de recursos próprios mínimos e provisões, e não de "utilização".
- A cadeia de revogação conferida em fonte primária é: RN 209 → revogada pela RN 451/2020 (art. 23) → RN 451 revogada pela RN 526/2022 (art. 21, I) → RN 526 revogada pela **RN 569/2022** (art. 14, II; o art. 1º anuncia o objeto), em vigor desde 01/01/2023 (art. 15).
- A obrigação vigente é **manter, a qualquer tempo**, Patrimônio Líquido Ajustado igual ou superior ao capital regulatório (art. 8º). É obrigação contínua, não envio periódico.
- Os dados de cálculo do capital seguem "nas mesmas datas de envio do DIOPS" ou em quadro auxiliar do DIOPS (Anexo IV, item 11; Anexo V, item 13.1). Na prática, isso se resolve no gatilho DIOPS.
- Nenhuma norma ANS vigente com obrigação periódica de "utilização de serviços" foi localizada. Os fluxos de dado de utilização assistencial que existem são o SIP (extinto; ver G5) e o Monitoramento TISS (G3).

**Proposta para o jurídico (não é decisão):** retirar o literal `RN_209_UTILIZACAO` da taxonomia, ou mapeá-lo para o DIOPS. Nenhum timer próprio.

**Texto primário (verbatim):**

> Art. 23. Revoga-se a RN nº 209, de 22 de dezembro de 2009.
>
> Art. 21. Revogam-se: I - a Resolução Normativa nº 451, de 06 de março de 2020;
>
> Art. 1º Esta Resolução Normativa dispõe sobre os critérios para definição do capital regulatório das operadoras de planos de assistência à saúde, altera a Resolução Normativa ANS nº 515, de 29 de abril de 2022 e revoga a Resolução Normativa ANS nº 526, de 29 de abril de 2022 e a Resolução Normativa ANS n° 514, de 29 de abril de 2022.
>
> Art. 14. Revogam-se: I - a Resolução Normativa nº 514, de 29 de abril de 2022; II - a Resolução Normativa nº 526, de 29 de abril de 2022;
>
> Art. 8º As operadoras deverão manter, a qualquer tempo, PLA equivalente ou superior ao capital regulatório.
>
> Art. 15. Esta Resolução Normativa entra em vigor em 1º de janeiro de 2023.
>
> 11. A operadora que utiliza a fórmula padrão do risco de subscrição para o cálculo de seu capital regulatório deve enviar periodicamente, nas mesmas datas de envio do Documento de Informações Periódicas das Operadoras de Plano de Assistência à Saúde - DIOPS/ANS, [...]

**Fontes:**
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2020/res0451_12_03_2020.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0526_04_05_2022.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0569_16_12_2022.html

Acesso em 2026-10-31.

**Confiança:** alta para a cadeia de revogação. Média para a conclusão de que não há envio autônomo, porque ausência de achado não é prova de inexistência.

**Pergunta citada, não respondida:** se "utilização" pretendia outro sistema de informação da ANS, como o SIB (cadastro de beneficiários). Não pesquisado; fora do subconjunto desta missão.

| Assinatura do jurídico (G1) | |
|---|---|
| Norma e cadência adotadas | |
| Ressalva | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## G2: `RN_388_QUALIDADE`

**Pergunta do briefing:** a RN 388 foi revogada pela RN 483/2022. Que obrigação está em vigor?

**Achado:**
- A RN 388/2015 tratava de ações fiscalizatórias e foi revogada pela RN 483/2022, art. 59, I.
- O programa de **qualidade** vigente é a **RN 505/2022**, Programa de Qualificação de Operadoras com o IDSS:
  - avaliação por competência anual (art. 7º);
  - indicadores com fonte nos sistemas oficiais da ANS e do MS (art. 9º), sem envio específico da operadora;
  - datas de corte e prazos fixados por ato da DIDES (art. 19);
  - dever ativo da operadora: divulgar o IDSS no próprio sítio em até 30 dias da divulgação pela ANS (art. 22), sob pena do art. 25.
- A ligação "RN 388 qualidade → RN 505" é inferência do agente, a partir do nome do literal e da cadência anual.

**Proposta para o jurídico (não é decisão):** não há envio anual a agendar. Se houver tarefa, ela é **por evento**: publicar o IDSS em 30 dias da divulgação da ANS.

**Texto primário (verbatim):**

> Art. 59. Revogam-se: I - as Resoluções Normativas nº 388, de 25 de novembro de 2015; nº 414, de 11 de novembro de 2016; nº 444, de 1º de abril de 2019;nº 326, de 22 de abril de 2013; e Resolução Normativa nº 369, de 23 de janeiro de 2015;
>
> Art. 7º A qualificação das operadoras avaliará, por competência anual, o desempenho das operadoras com registro ativo junto a ANS que operaram planos próprios médico-hospitalares, médico-hospitalares com odontologia ou exclusivamente odontológicos, nos doze meses do ano avaliado.
>
> Art. 9º Os indicadores terão como fonte os dados disponíveis nos sistemas oficiais de informação, em especial os sistemas de informação da ANS e do Ministério da Saúde.
>
> Art. 19. Serão definidos pelo comitê executivo e divulgados por meio de ato normativo específico da DIDES: I - as datas ou competências em que serão obtidos dos sistemas de informação os dados necessários para o cálculo dos indicadores; II - os critérios a serem utilizados para cálculo do índice de desempenho das dimensões; III - os prazos e meios pelos quais as operadoras poderão enviar os questionamentos citados no artigo 14 desta resolução; e [...]
>
> Art. 22. A Operadora deverá divulgar o resultado do IDSS geral e de cada uma das dimensões do Programa em seu sítio institucional na internet, no prazo máximo de 30 (trinta) dias, contado da data de divulgação dos resultados pela ANS, a partir do ano-base 2017 a ser processado e divulgado em 2018, contendo, no mínimo: I - o resultado do IDSS e suas dimensões mais recentes, como divulgado pela ANS e o respectivo ano avaliado em idêntico destaque; e II - o link do Programa no Portal da ANS.

**Fontes:**
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0483_31_03_2022.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0505_01_04_2022.html

Acesso em 2026-10-31. O Saúde Legis mostra o art. 12 da RN 505 com a marca "(NR)", sinal de que o portal incorporou alteração posterior. Alterações não foram varridas.

**Confiança:** média.

| Assinatura do jurídico (G2) | |
|---|---|
| Norma e cadência adotadas | |
| Ressalva | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## G3: `RN_424_TISS_MONITORAMENTO`

**Pergunta do briefing:** a RN 424 é norma de junta médica. Que norma e que cadência regem o monitoramento TISS, "agora sob a RN 639/2025"?

**Achado:**
- A RN 424/2017 trata de junta médica (texto-base novo `docs/compliance/legal/textos-base/RN-424-2017.md`).
- O TISS é regido pela **RN 501/2022**:
  - art. 23: institui o monitoramento do Padrão TISS;
  - art. 25: envio **mensal** à ANS, com cronograma da DIDES divulgado no sítio da ANS;
  - art. 27: o descumprimento do art. 25 é a infração do art. 35 da RN 489/2022.
- **Regra de data (cronograma da DIDES):** Componente Organizacional do Padrão TISS, versão setembro/2026, item 242: "até o dia 25 do segundo mês subsequente à competência informada". O item 215 diz que a obrigação só é cumprida com a incorporação completa dos dados na base da ANS.
- **RN 639/2025:** o texto publicado no DOU **não menciona TISS nem monitoramento**. Só revoga a RN 551 e desobriga o SIP. A migração do SIP para o TISS consta de **aviso** da ANS de 08/07/2025, que é comunicação oficial, mas não norma. A RN 639 não é a base do monitoramento TISS. A base continua sendo a RN 501.
- **Divergência registrada:** a RN 501, art. 25, diz "a partir da competência junho de 2014". O Componente Organizacional, item 312, diz "a partir da competência setembro de 2014". Não afeta competências correntes.
- **Exemplo de data (cálculo do agente):** competência 2026-09 vence em 25/11/2026, uma quarta-feira. O item 242 não diz o que acontece se o dia 25 cair em dia não útil. Ponto para o jurídico.

**Texto primário (verbatim):**

> Art. 23. Fica instituído o monitoramento do Padrão TISS, com base nos dados disponíveis na ANS e nos demais órgãos do Ministério da Saúde.
>
> Art. 25. O envio dos dados do Padrão TISS para a ANS é devido, mensalmente, pela operadora de plano privado de assistência à saúde a partir da competência junho de 2014 e o cronograma de envio será definido pela DIDES e divulgado no endereço eletrônico da ANS na internet.
>
> Art. 27. A inobservância à obrigação prevista no art. 25 desta Resolução configurará a infração administrativa prevista no artigo 35 da Resolução Normativa nº 489, de 29 de março de 2022, que dispõe sobre a aplicação de penalidades para as infrações à legislação dos planos privados de assistência à saúde.
>
> Componente Organizacional set/2026, item 242: "242. Os dados deverão ser enviados pelas operadoras para a ANS até o dia 25 do segundo mês subsequente à competência informada."
>
> Item 215: "215. A obrigação do envio de dados da operadora para a ANS somente é considerada cumprida com a completa incorporação, à base de dados da Agência, de todos os dados de atenção à saúde de seus beneficiários, na competência e prazos estabelecidos, conforme estabelecido Resolução Normativa nº 501/2022."
>
> Item 312: "312. O envio obrigatório dos dados do padrão TISS para a ANS é devido pela operadora de plano privado de assistência à saúde, mensalmente e de forma eletrônica, a partir da competência setembro de 2014. O cronograma de envio é definido pela DIDES e divulgado no endereço eletrônico da ANS na internet www.gov.br/ans/pt-br."
>
> RN 639/2025 (DOU 07/07/2025, Seção 1, edição 125, p. 332): RESOLUÇÃO NORMATIVA ANS Nº 639, DE 4 DE JULHO DE 2025 Revoga a Resolução Normativa ANS nº 551, de 11 de novembro de 2022, e desobriga as operadoras do envio do SIP. A DIRETORIA COLEGIADA DA AGÊNCIA NACIONAL DE SAÚDE SUPLEMENTAR - ANS, no uso das atribuições que lhe conferem o art. 4º, inciso XXXI, e o art. 10, incisos II e IV, ambos da Lei nº 9.961, de 28 de janeiro de 2000; e o art. 42, inciso IV, da Resolução Regimental nº 21, de 26 de janeiro de 2022, em reunião realizada em 04 de Julho de 2025, adota a seguinte Resolução Normativa e eu, Diretora-Presidente Interina, determino a sua publicação. Art. 1º Fica revogada a Resolução Normativa ANS nº 551, de 22 de março de 2022. Art. 2º As operadoras de planos de saúde ficam desobrigadas de enviar à ANS os arquivos com informações para o Sistema de Informações de Produtos - SIP após o envio do 4º trimestre de 2025. Art. 3º Esta Resolução entra em vigor no dia 02 de março de 2026
>
> Aviso ANS de 08/07/2025 (comunicação oficial, não norma): "As informações anteriormente enviadas via SIP passarão a ser consolidadas por meio do Padrão TISS (Troca de Informações na Saúde Suplementar), já utilizado pelas operadoras com a ANS."

**Fontes:**
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0501_01_04_2022.html
- https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/PadroTISS_ComponenteOrganizacional_202609.pdf (link obtido da página oficial "Padrão TISS – Setembro 2026")
- https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-639-de-4-de-julho-de-2025-640242915
- https://www.gov.br/ans/pt-br/assuntos/operadoras/avisos-para-operadoras/ans-revoga-obrigatoriedade-de-envio-do-sip-a-partir-de-2026

Acesso em 2026-10-31.

A página oficial "Monitora TISS" (https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/monitora-tiss, atualizada em 21/11/2025) também refere a "data limite mensal de envio - dia 25 do mês", sem dizer o mês relativo. O item 242 do Componente é o texto que fixa o "segundo mês subsequente".

**Confiança:** alta (obrigação e periodicidade: RN 501, art. 25); média (regra de data: Componente Organizacional, item 242, documento infranormativo por delegação do art. 25).

| Assinatura do jurídico (G3) | |
|---|---|
| Norma e cadência adotadas | |
| Regra para dia 25 em dia não útil | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## G4: `DIOPS_TRIMESTRAL`

**Pergunta do briefing:** qual é a norma primária?

**Achado:**
- A norma é a **RN 527/2022**, art. 8º, na redação da **RN 594/2023**, art. 2º, em vigor desde 01/01/2024 (art. 9º da RN 594):
  - 1º trimestre até 15 de maio;
  - 2º trimestre até 15 de agosto;
  - 3º trimestre até 15 de novembro;
  - 4º trimestre até **28 de fevereiro** do exercício seguinte. A redação original dizia 31 de março e foi alterada pela RN 594.
- O 4º trimestre vai acompanhado das demonstrações financeiras completas (§ 4º).
- **Mensal simplificado (art. 8º-A):** para operadoras S1/S2, salvo as com menos de 20 mil beneficiários, e para as em PAEF, direção fiscal ou Programa de Saneamento.
- **Dispensa (art. 9º):** S4 com até 20 mil beneficiários fica dispensada dos 1º a 3º trimestres, salvo exceções.
- A página oficial "DIOPS-XML 2026" (atualizada em 07/07/2026) confirma a RN 527 "e suas alterações posteriores" como base do sistema, mas não lista as datas.

**Exemplos de data (cálculo do agente):** 3T/2026 vence em 15/11/2026, um domingo e feriado nacional. 4T/2026 vence em 28/02/2027, também domingo. A norma não diz como tratar dia não útil. Ponto para o jurídico.

**Texto primário (verbatim):**

> RESOLUÇÃO NORMATIVA - RN Nº 527, DE 29 DE ABRIL DE 2022 Dispõe sobre a versão XML (Extensible Markup Language) do Documento de Informações Periódicas das Operadoras de Planos de Assistência à Saúde - DIOPS/ANS.
>
> Art. 8º O DIOPS/ANS versão XML deverá ser enviado nas seguintes datas: I - primeiro trimestre até o dia quinze de maio do mesmo exercício; II - segundo trimestre até o dia quinze de agosto do mesmo exercício; III - terceiro trimestre até o dia quinze de novembro do mesmo exercício; e IV - quarto trimestre até o dia trinta e um de março do exercício subsequente. [redação original]
>
> RN 594/2023, art. 2º (nova redação do art. 8º, IV, da RN 527): "IV - quarto trimestre até o dia vinte e oito de fevereiro do exercício subsequente."
>
> RN 594/2023, art. 2º (art. 8º-A, caput; as aspas iniciais são do original): "Art. 8º-A As operadoras enquadradas nos segmentos de classificação prudencial S1 e S2, conforme disposto na Resolução Normativa nº 475, de 23 de dezembro de 2021, com exceção das operadoras com número de beneficiários inferior a vinte mil, apurados na data de 31 de dezembro do exercício imediatamente anterior, deverão, adicionalmente, encaminhar o DIOPS/ANS em versão mensal simplificada, nas seguintes datas:
>
> RN 594/2023, art. 2º (art. 9º; as aspas iniciais são do original): "Art. 9º As operadoras enquadradas no segmento de classificação prudencial S4, conforme disposto na Resolução Normativa nº 475, de 2021, com até vinte mil beneficiários, número a ser apurado na data de 31 de dezembro do exercício imediatamente anterior, ficam dispensadas da obrigação de envio do DIOPS/ANS, versão XML, relativamente ao primeiro, segundo e terceiro trimestres, [...]
>
> Art. 9º Esta Resolução Normativa entra em vigor no dia 1º de janeiro de 2024.

**Fontes:**
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0527_04_05_2022.html
- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0594_20_12_2023.html
- https://www.gov.br/ans/pt-br/assuntos/operadoras/aplicativos-ans-2/aplicativos-diops/diops-financeiro/diops-xml-2026

Acesso em 2026-10-31.

**Residual:** alterações posteriores à RN 594 não foram localizadas; ausência de achado não é prova. Prorrogações pontuais por ato da ANS também existem. Exemplo: RN 578/2023, só em fonte secundária, não verificada. Conferir o calendário oficial da ANS antes de cada vencimento. A página "Calendário das Operadoras" em gov.br/ans hoje serve só um app shell (Mendix), sem conteúdo legível por `curl`.

**Pergunta citada, não respondida:** o segmento prudencial do tenant (S1/S2/S4, pela RN 475/2021, não lida) define se o mensal simplificado se aplica. É fato do tenant, não do agente.

**Confiança:** alta.

| Assinatura do jurídico (G4) | |
|---|---|
| Norma e cadência adotadas | |
| Regra para vencimento em dia não útil | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## G5: SIP, backlog até o 4º trimestre de 2025

**Pergunta do briefing:** o SIP foi extinto pela RN 639/2025, em vigor desde 2026-03-02. Que regra rege o backlog até o 4T/2025?

**Achado:**
- **Regra do backlog:** a **RN 551/2022**, art. 7º, rege as competências até o 4T/2025:
  - periodicidade **trimestral**;
  - 1T até o último dia útil de maio; 2T, de agosto; 3T, de novembro; 4T, de fevereiro do ano seguinte;
  - obrigatória para todas as operadoras com registro ativo (art. 2º), exceto administradoras de benefícios (§ 2º).
- **Extinção:** a **RN 639/2025** revoga a 551 (art. 1º), desobriga o envio "após o envio do 4º trimestre de 2025" (art. 2º) e vigora desde 02/03/2026 (art. 3º).
- **Último vencimento:** o do 4T/2025, no último dia útil de fevereiro de 2026. Pelo calendário civil seria sexta-feira, 27/02/2026; o cálculo é do agente e não aplica feriados. Nessa data a RN 551 ainda estava em vigor.
- O aviso da ANS de 05/03/2026 (atualizado em 26/10/2026) diz que o SIP "permanecerá ativo para recepção de arquivos referentes a competências anteriores ao 1º trimestre de 2026". O backlog, portanto, ainda pode ser enviado, mas fora do prazo normativo.

**Drift com evidência (repo × fonte primária):**
- `spec/processes/dmn/ans_calendar.dmn:58` e `spec/processes/dmn/ans_calendar.dmn:67` emitem periodicidade `"mensal"` para o SIP. A RN 551, art. 7º, é **trimestral**. O próprio DMN marca o valor como herdado e não verificado.
- O texto do DOU da RN 639 grafa no art. 1º "Resolução Normativa ANS nº 551, de 22 de março de 2022". A ementa diz "de 11 de novembro de 2022". A RN 551 é de 11/11/2022, publicada no DOU de 22/11/2022. A transcrição em `docs/compliance/rn-639-primary-source.md`, que veio do endpoint de legislação da ANS e não do DOU, traz "11 de novembro de 2022" também no art. 1º. A divergência é de grafia do DOU e não muda a identificação da norma revogada.

**Texto primário (verbatim):**

> RN 551/2022 (DOU 22/11/2022, Seção 1, edição 219, p. 125): Art. 2º O envio do SIP é obrigatório para todas as operadoras de planos de saúde com registro ativo na ANS. [...] Art. 7º As operadoras de que trata o art. 2º deverão enviar as informações assistenciais considerando os seguintes prazos e períodos: I - 1º trimestre - meses de janeiro a março: prazo até o último dia útil de maio; II - 2º trimestre - meses de abril a junho: prazo até o último dia útil de agosto; III - 3º trimestre - meses de julho a setembro: prazo até o último dia útil de novembro; e IV - 4º trimestre - meses de outubro a dezembro: prazo até o último dia útil de fevereiro do ano subsequente.
>
> RN 639/2025: ver o verbatim integral em G3.
>
> Aviso ANS de 05/03/2026: "O SIP permanecerá ativo para recepção de arquivos referentes a competências anteriores ao 1º trimestre de 2026, garantindo que eventuais pendências possam ser enviadas ou regularizadas."

**Fontes:**
- https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-551-de-11-de-novembro-de-2022-444965249
- https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-639-de-4-de-julho-de-2025-640242915
- https://www.gov.br/ans/pt-br/assuntos/operadoras/avisos-para-operadoras/ans-comunica-fim-da-obrigatoriedade-de-envio-de-dados-ao-sip-a-partir-de-2026

Acesso em 2026-10-31.

**Localização no DOU:** índices https://www.in.gov.br/leiturajornal?data=22-11-2022&secao=DO1 e https://www.in.gov.br/leiturajornal?data=07-07-2025&secao=DO1.

**Confiança:** alta.

| Assinatura do jurídico (G5) | |
|---|---|
| Regra do backlog adotada | |
| Ressalva | |
| Nome e papel | |
| Data | |
| Assinatura | |

---

## Perguntas fora do escopo (citadas, não respondidas)

- Mapeamento período → competência e a forma `YYYY-01` para o caso anual (`docs/processes/contracts/SP-OP-ANS-CRON-001.md:226-229`).
- Fuso civil da âncora (`America/Sao_Paulo`): pergunta 2b do despacho regulatório.
- Consumidor vivo da ponte (FINDING #2): engenharia, não jurídico.
- Calendário de feriados para os vencimentos em dia não útil: as perguntas estão nas linhas G3 e G4, mas a regra fica com o jurídico.

## Fontes (downloads desta sessão, 2026-10-31; sha256 do arquivo bruto)

| Conteúdo | URL | sha256 | bytes |
|---|---|---|---|
| RN 451/2020 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2020/res0451_12_03_2020.html | `680a5720b2228eaf73f49e0b5df6d50b9bc7a379643f88d57991c8a5de123490` | 28175 |
| RN 526/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0526_04_05_2022.html | `3d6e2fbce2123796339c8023974f455bf4b927828ab90618e1f932e501ec88b3` | 100922 |
| RN 569/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0569_16_12_2022.html | `a2d618a7a174d7f0c1fbdf6b109aae3bb670e4778bf1e301ce50bf4b24fa7fee` | 92808 |
| RN 483/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0483_31_03_2022.html | `7d735ce576022fc5ede4b2df295bb4c1cb930b1e71e0f0bb689412c56093a65f` | 63617 |
| RN 505/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0505_01_04_2022.html | `b52703ba28004fab66e2d64208f75ca66af2ba508faa9eeed759a30732a4a945` | 17419 |
| RN 501/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0501_01_04_2022.html | `f179f8974b52bbeab302cff8d4e4d7715684bf80adfb28b55ebf3eeb0b91a704` | 20980 |
| Padrão TISS, Componente Organizacional set/2026 (PDF, gov.br/ans) | https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/PadroTISS_ComponenteOrganizacional_202609.pdf | `fc973a683f772143d3355604e6e0566491756c49c657e1b12b0b33564d63d2d3` | 1925045 |
| Página Monitora TISS (gov.br/ans) | https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/monitora-tiss | `9dedeca9b08f4420444f56d9b6053293c06138225d943c3d5b0c4294255e9c66` | 295092 |
| RN 527/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0527_04_05_2022.html | `4341b4aad38f7612ff5bfaff810631d9397de5a79fc6399fc428a27cdf8aeb27` | 183273 |
| RN 594/2023 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0594_20_12_2023.html | `b45f97abb5c675f57ac3933fb95705778a6ecb53c258a01df598790332255a89` | 21307 |
| Página DIOPS-XML 2026 (gov.br/ans) | https://www.gov.br/ans/pt-br/assuntos/operadoras/aplicativos-ans-2/aplicativos-diops/diops-financeiro/diops-xml-2026 | `30aad8325eadfb732015a91cd27385376fff7a9a5d91ec02f9bca176e9fd871e` | 220847 |
| RN 551/2022 (DOU) | https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-551-de-11-de-novembro-de-2022-444965249 | `b690372602877a935264ef5b5f41100bf330f513f2736098f834f7e74ed3c1a5` | 180333 |
| RN 639/2025 (DOU) | https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-639-de-4-de-julho-de-2025-640242915 | `ca2ff7636138de9f6e4f2a173cbf7213eba8f259c8b93faaa4fc74077d43c4e0` | 85161 |
| Índice DOU 22/11/2022 DO1 | https://www.in.gov.br/leiturajornal?data=22-11-2022&secao=DO1 | `612c4c0716284e6b11f5ecf93dc94f82c9469ad8d964ee6417f4227fa32f7465` | 426648 |
| Índice DOU 07/07/2025 DO1 | https://www.in.gov.br/leiturajornal?data=07-07-2025&secao=DO1 | `1d0b504f3679a76fb96df69789b5cfb180a5d0f5607b9431d44e3f7f6833d9a3` | 575508 |
| Aviso ANS 05/03/2026 (fim do SIP) | https://www.gov.br/ans/pt-br/assuntos/operadoras/avisos-para-operadoras/ans-comunica-fim-da-obrigatoriedade-de-envio-de-dados-ao-sip-a-partir-de-2026 | `419aaf94f2c29b3a9f9ebfd8d175884ec6697e3682150bbdb0f20fda30870260` | 194992 |
| Aviso ANS 08/07/2025 (revogação do SIP) | https://www.gov.br/ans/pt-br/assuntos/operadoras/avisos-para-operadoras/ans-revoga-obrigatoriedade-de-envio-do-sip-a-partir-de-2026 | `195e7a7fbce4854932e3fbaaa3da52a6a7014f030bf301cbfc97bca639612149` | 195406 |

As páginas gov.br são dinâmicas: o sha256 fixa o conteúdo lido nesta data, não um arquivo estável (mesma ressalva do `docs/compliance/legal/404-LOG.md` §3, item 3). Os downloads brutos não são versionados.
