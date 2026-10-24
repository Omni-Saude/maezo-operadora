# PACOTE REG-1.13 — Aquisição de textos-base (RN 528, 529, 489, 515, 557 + RN 659 e RN 195 para os pacotes D-03/D-04)

**Missão:** W1 REG — REG-1.13 do plano mestre ("Aquisição de textos-base: RN 528/529/489/515/557 verbatim + URLs DOU; fecha GAPs do sweep; dependência COUNSEL-7/8").
**Autor:** analista regulatório BR (agente). **Data de aquisição:** 2026-10-23.
**Natureza:** INFORMATIVO — registro de aquisição e verificação; nenhuma decisão aqui.
**Método:** para cada norma, localização da matéria no DOU (Imprensa Nacional) via índice da edição (`/leiturajornal?data=<dd-mm-aaaa>&secao=DO1`, que embute o JSON das matérias com `urlTitle`), download da página da matéria, extração mecânica do corpo (`div.texto-dou`), e conferência dos artigos-alvo. Todas as URLs foram testadas nesta data (tabela no `404-LOG.md`, com HTTP status e sha256 do download). Fonte terciária (LegisWeb) usada APENAS para localizar datas de DOU; nenhuma citação normativa deriva dela.
**Site da ANS:** a árvore `https://www.gov.br/ans/pt-br/assuntos/normas-rh/...` (incluída no briefing como fonte candidata) responde **404** em toda a árvore nesta data — o portal da ANS foi migrado; o endpoint legado `ans.gov.br/component/legislacao/?...format=raw` hoje redireciona para o shell do novo portal (`componentes-portal.ans.gov.br/index.html`), sem texto de norma. **DOU (in.gov.br) é a âncora primária durável** — ver `404-LOG.md` §3.

## 1. Inventário de aquisição

| Norma | Identidade verificada no DOU | DOU (Seção 1) | URL da matéria | Texto-base | Artigos-alvo conferidos nesta sessão |
|---|---|---|---|---|---|
| RN 489/2022 | "Dispõe sobre a aplicação de penalidades para as infrações à legislação dos planos privados de assistência à saúde." (29/03/2022) | 31/03/2022, p. 310 | https://www.in.gov.br/web/dou/-/resolucao-normativa-rn-n-489-de-29-de-marco-de-2022-389844061 | `textos-base/RN-489-2022.md` | Arts. 1º (§§1º–3º abrangência), 2º (VI), 23, 24, 119 (vigência); AUSÊNCIA do art. 23-A no original (GAP-5) |
| RN 515/2022 | "Dispõe sobre a Administradora de Benefícios." (29/04/2022) | 04/05/2022, p. 498 | https://www.in.gov.br/web/dou/-/resolucao-normativa-n-515-de-29-de-abril-de-2022-397547297 | `textos-base/RN-515-2022.md` | Arts. 2º (caput, I), 6º (corretores/corretoras fora do conceito de AB — GAP-7), 11 (revoga RN 196/2009), 12 (vigência 1º/6/2022) |
| RN 528/2022 | "Dispõe sobre o Plano de Contas Padrão da ANS para as operadoras de planos de assistência à saúde e as administradoras de benefícios." (29/04/2022) | 06/05/2022, p. 103 | https://www.in.gov.br/web/dou/-/resolucao-normativa-rn-n-528-de-29-de-abril-de-2022-398200916 | `textos-base/RN-528-2022.md` | Anexo, Cap. I, itens 6.3.9 / 6.3.9.1 "a) processos de comercialização e comissionamento" (GAP-3); arts. 1º–3º (vigência 1º/6/2022; revoga RN 472/2021, IN 47/2011, SN 18/2011) |
| RN 529/2022 | "Dispõe sobre a identificação de clientes, manutenção de registros e prevê relação de operações e situações que podem configurar indícios de ocorrência dos crimes previstos na Lei nº 9.613, de 3 de março de 1998." (02/05/2022) | 04/05/2022, p. 535 | https://www.in.gov.br/web/dou/-/resolucao-normativa-rn-n-529-de-2-de-maio-de-2022-397547537 | `textos-base/RN-529-2022.md` | Arts. 2º (§§1º, 5º), 3º, 4º, 5º (I "f"), 6º, 13 (revoga RN 117/2005 e RN 244/2011), 14 (vigência 1º/6/2022) — GAP-4 |
| RN 557/2022 | "Dispõe sobre a classificação e características dos planos privados de assistência à saúde e regulamenta a sua contratação…" (14/12/2022 — data distinta da até agora assumida em notas internas) | 30/12/2022, p. 349 | https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-557-de-14-de-dezembro-de-2022-455003645 | `textos-base/RN-557-2022.md` | Arts. 5º, 15 (§§3º–4º), 16, 29 (I–III, §§1º–2º), 39, 40 (revogações — GAP-2/COUNSEL-3), 41 (vigência 1º/2/2023) |
| RN 659/2025 (apoio D-03) | "Altera a Resolução Normativa nº 489, de 29 de março de 2022…" (22/12/2025) | 31/12/2025, p. 394 | https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-659-de-22-de-dezembro-de-2025-679014284 | `textos-base/RN-659-2025.md` | Arts. 1º, 2º (bloco arts. 23/23-A/24/24-A), 3º (escalonamento), 4º (revogações internas), 5º (vigência 1º/5/2026), Anexo (linha do art. 23-A) |
| RN 195/2009 (apoio D-04) | "Dispõe sobre a classificação e características dos planos privados de assistência à saúde…" (14/07/2009) | 15/07/2009, p. 135–136 | http://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?data=15/07/2009&jornal=1&pagina=135 (+ `pagina=136`; binário via servlet INPDFViewer) | `textos-base/RN-195-2009.md` | Arts. 5º e 9º (verbatim integral, os citados pelo checklist do OP21), arts. 10, 23; status: REVOGADA pela RN 557/2022 art. 40, I |

## 2. Status dos GAPs do sweep de 2026-10-22

| GAP | Descrição no sweep | Status nesta aquisição |
|---|---|---|
| GAP-2 | RN 195/2009 vs RN 557/2022 — revocation scope unverified | **FECHADO (fonte primária):** RN 557/2022 art. 40, I revoga expressamente a RN 195/2009 (norma inteira), vigência 1º/2/2023. Data correta da RN 195: 14/07/2009. Ver `PACOTE-D04-COUNSEL-3.md` §2. |
| GAP-3 | RN 528/2022 item 6.3.9.1,a verbatim | **FECHADO:** Anexo, Cap. I, Normas Gerais, item 6.3.9.1 "a) processos de comercialização e comissionamento;" — verbatim em `textos-base/RN-528-2022.md`. Residual: o render HTML do DOU serve a matéria em blocos e CORTA o fim do Anexo (Cap. IV, manual de contabilização, a partir do item 6.2 do manual); captura completa do anexo requer o PDF certificado (rota documentada no textos-base). Para os fins do GAP-3 o item citado está íntegro. |
| GAP-4 | RN 529/2022 arts. 2º–5º verbatim | **FECHADO:** texto integral extraído do DOU (não do PDF do COAF): arts. 2º–14 + Anexo (Ficha Cadastral). Detalhe relevante ao COUNSEL-7: o art. 2º do caput inclui "corretores" na lista de cadastro AML — é dever de identificação de clientes (Lei 9.613), NÃO registro de corretor; e a retenção de 5 anos está no art. 4º (não no art. 2º). |
| GAP-5 | RN 489/2022 base text (rol; art. 23-A original) | **FECHADO:** texto integral DOU 31/03/2022; sem art. 23-A no original; abrangência (art. 1º) não inclui corretores; únicos tipos coletivos originais = arts. 23 e 24. |
| GAP-6 | ANS raw URLs servem app shell; pinar DOU | **CONFIRMADO HOJE E GENERALIZADO:** em 2026-10-23 TODOS os ids testados do endpoint raw (incl. `id=NDU5Ng==`, que em 22/10/2026 servia o texto da RN 623/2024) redirecionam para `https://componentes-portal.ans.gov.br/index.html`. As âncoras DOU pinnadas aqui (RN 489/515/528/529/557/659) devem ser adotadas como duráveis. **ALERTA FORA DO ESCUPO mas crítico:** o verbatim da RN 623/2024 usado no sweep (e nas decisões D-05/REG-1.2) foi pinnado na URL raw hoje degradada — recomendado re-pin em DOU numa missão própria (registro no `404-LOG.md` §3; NÃO executado aqui por escopo). |
| GAP-7 | RN 515/2022 art. 6º verbatim | **FECHADO:** "Art. 6º Não se enquadram como Administradoras de Benefícios os Corretores e Corretoras regulamentados pela Lei nº 4.594, de 29 de dezembro de 1964." |

COUNSEL-7 (RN 529 = AML/KYC; citações de artigos unverified) e COUNSEL-8 (RN 489 rol claim pending base text): **insumos primários agora disponíveis** nos textos-base; as conclusões jurídicas continuam com o jurídico (nada ratificado aqui).

## 3. Integridade da aquisição (checksums dos downloads brutos)

Ver `404-LOG.md` §2 (sha256 de cada HTML/PDF baixado, com URL e hora). As extrações verbatim dos textos-base correspondem 1:1 aos downloads brutos; normalizações mecânicas documentadas no cabeçalho de cada textos-base.

## 4. Residuais (para o jurídico/próxima varredura)

1. RN 528/2022: cauda do Anexo (Cap. IV) truncada no render HTML — captura completa via PDF certificado pendente (não bloqueia nenhum item do programa; o item GAP-3 está íntegro).
2. Histórico de emendas posteriores de RN 557/2022, RN 515/2022 e RN 489/2022 (após suas publicações; exceto as alterações já capturadas da RN 659/2025) — não varrido nesta sessão.
3. Re-pin da RN 623/2024 em DOU (ver GAP-6 acima) — fora do escopo REG-1.13, recomendado como item próprio.
4. Endpoints oficiais de consulta consolidada da ANS (novo portal Mendix) — sem API pública identificada nesta sessão; monitorar estabilidade das URLs DOU.
