# PARECER OP21 — RESPOSTA DO JURÍDICO (Lucas) — REGISTRO 2026-10-26

**Natureza deste arquivo:** REGISTRO de resposta humana recebida. **Não decide, não interpreta além do texto e não preenche bloco de ratificação** (ADR-0007). Transcreve a resposta do Diretor Jurídico ao parecer-modelo OP21 e mapeia, factualmente, os efeitos sobre o programa. Reparos de citação e a implementação do OP21 saem em PRs próprios da cadeia da casa.

**Responde:** D-03 (COUNSEL-2) e D-04 (COUNSEL-3) — perguntas de `PACOTE-D03-COUNSEL-2.md` §1 e `PACOTE-D04-COUNSEL-3.md` §1.

---

## 1. Proveniência

| Item | Valor |
|---|---|
| Título do documento (linha 1 do texto) | "Triagem de legitimidade da pessoa jurídica contratante em planos coletivos (OP21)" |
| Autor | Lucas, Diretor Jurídico (linha "De: Lucas, Diretor Jurídico") |
| Destinatário | Rodrigo (linha "Para: Rodrigo") |
| Data do próprio texto | "Data: 26 de outubro de 2026" |
| Fonte viva | Google Doc **"PARECER-OP21-aprovado"** — https://docs.google.com/document/d/1-elk5LeF5cM4Yi2_fscZqWs_5Wry9FufNC-pFKy9lo8/edit (compartilhado pelo dono) |
| Método de captura | Export `.txt` do Google Docs, baixado em **2026-10-26** |
| Snapshot canônico | `textos-base/PARECER-OP21-2026-10-26-LUCAS.txt` |
| sha256 do snapshot | `d80740cc603fe783a4ef1cfa87f291c53b2b323059696535798873acde9a187b` |
| Rodapé do documento | "Minuta aprovada — uso interno · pág." (presente no snapshot; cópia byte-a-byte) |

**Forma canônica de citação deste parecer:** o snapshot `.txt` acima (sha256 fixado). O Google Doc é documento vivo/editável — alterações posteriores nele NÃO afetam o snapshot registrado aqui; a citação é ao snapshot, com o hash. Qualquer divergência entre o Doc vivo e o snapshot é registrável como emenda nova, nunca sobreescrita silenciosa deste arquivo.

---

## 2. Mapa pergunta → resposta (6/6 respondidas)

Correspondência 1:1 verificada contra `PACOTE-D03-COUNSEL-2.md` §1 (3 perguntas) e `PACOTE-D04-COUNSEL-3.md` §1 (3 perguntas). Trechos entre aspas são verbatim do snapshot.

### D-03 (COUNSEL-2)

**Q1 — Forma canônica de citação** → parecer §1 ("1. Como citar a infração").
Pergunta do pacote: qual forma obrigatória entre "RN 659/2025 art. 23-A" e "RN 489/2022, art. 23-A (redação dada pela RN 659/2025)".
Resposta, verbatim: "Usar 'RN 489/2022, art. 23-A (redação dada pela RN 659/2025)', e a forma curta 'RN 489/2022, art. 23-A' na tela. A forma que circula hoje, 'RN 659/2025, art. 23-A', é imprecisa: a RN 659/2025 apenas alterou a RN 489/2022, onde o artigo está."

**Q2 — Conteúdo implementável desde já** → parecer §2 ("2. O que verificar desde já").
Pergunta do pacote: cobrir tudo desde já ou existe padrão menor admissível até 2027.
Resposta, verbatim: "Cobrir o conteúdo integral desde a primeira versão. Como o dever vale desde maio de 2026, não vemos base para uma versão reduzida."

**Q3 — Abrangência subjetiva / "intermediar"** → parecer §3 ("3. Quem responde por 'intermediar'").
Pergunta do pacote: o canal de venda pode ser enquadrado como quem "intermedeia", ou a responsabilidade é da operadora e da administradora de benefícios.
Resposta, verbatim: "A RN 489/2022 (art. 1º) se aplica a operadoras, administradoras de benefícios, seus administradores e prestadores de serviços de saúde; não menciona corretores. A RN 515/2022 (art. 6º) diz que corretores não são administradoras de benefícios." Segue no texto: "* A — a responsabilidade é da operadora (celebrar/manter) e da administradora de benefícios; o canal, em si, não seria alcançado." E: "Recomendação técnica. Não caracterizar juridicamente o papel do canal na tela nem em textos exibidos a terceiros."

### D-04 (COUNSEL-3)

**Q1 — Re-ancoragem do checklist + desdobro de "RN 557/659"** → parecer §4 ("4. Base normativa do checklist").
Pergunta do pacote: em quais dispositivos da RN 557/2022 re-ancorar o checklist (RN 195/2009 revogada) e em quais citações distintas desdobrar "RN 557/659".
Resposta, verbatim: "Apoiar o checklist na RN 557/2022 — arts. 5º (empresarial), 15 e 16 (adesão), 29 (formas de contratação) e 39 (ingresso irregular); arts. 9º a 14 para empresário individual. Separar as citações por assunto: contratação na RN 557/2022 e penalidade na RN 489/2022, art. 23-A. Hoje um registro interno junta as duas como 'RN 557/659'."

**Q2 — Critérios da tela + evidências (art. 12)** → parecer §5 ("5. O que a tela verifica e o que guarda").
Pergunta do pacote: quais critérios entram na tela do OP21 e com quais evidências retidas (art. 12 da RN 557/2022).
Resposta — "Proposta de critérios:", verbatim:
- "tipo de contratação (empresarial ou adesão) e forma (direta, com administradora ou com administradora coestipulante — art. 29);"
- "empresarial: vínculo do beneficiário com a contratante (art. 5º);"
- "adesão: contratante na lista do art. 15 e constituída há pelo menos um ano (art. 16, salvo conselhos, entidades de classe e sindicatos);"
- "empresário individual: documentos do art. 9º e reconfirmação anual (art. 10);"
- "nenhuma inclusão de beneficiário sem a pessoa jurídica legitimada (art. 29, § 1º)."
Sobre registros, verbatim: "A guarda dos documentos é dever da operadora e/ou da administradora (art. 12). Propomos que a Maezo guarde o registro da verificação no banco de dados (AMH datalake) e a referência aos documentos, sem duplicar a custódia."

**Q3 — Art. 39 no estado bloqueante** → parecer §6 ("6. Ingresso irregular (art. 39 da RN 557/2022)").
Pergunta do pacote: o art. 39 (ingresso irregular → vínculo direto individual equiparado a plano individual) é efeito a modelar no estado bloqueante ou consequência externa.
Resposta — o §6 é a única seção sem rótulo "Resposta:"; a orientação consta no próprio enunciado da seção, verbatim: "O controle deve tratar a regra do art. 39 — beneficiário que entra sem cumprir os requisitos passa a ter plano individual com a operadora — oriento a bloquear e registrar (proposta) alem de também sinalizar o efeito do art. 39." (grafia do original preservada).

O parecer traz ainda um "Anexo — Trechos das normas citadas" com os dispositivos RN 489/2022 art. 23-A e art. 1º §3º, RN 659/2025 art. 5º, RN 557/2022 arts. 15 §3º, 29 §1º, 39 e 40 I, e RN 515/2022 art. 6º — transcritos no snapshot.

---

## 3. Efeitos no programa (mapa factual)

1. **D-03 e D-04 RESPONDIDOS em 2026-10-26.** As duas dependências humanas da linha REG-1.1 (coluna "Dep: D-03, D-04" em `docs/plans/maezo-completion-program.md` §W1 REG) estão atendidas; as âncoras do OP21 (`legitimacy.screen`) foram validadas pelo jurídico. W1 REG desbloqueado — REG-1.1 despachável. O prazo de merge do OP21 em main (**2026-12-15**, por REG-1.1) permanece o registrado; esta resposta não o altera.
2. **Obrigação de reparo de citação (nasce da resposta, executa em PR próprio):** a forma "RN 659/2025, art. 23-A" é IMPRECISA e deve virar **"RN 489/2022, art. 23-A (redação dada pela RN 659/2025)"** — e, na tela, a forma curta **"RN 489/2022, art. 23-A"** — em TODOS os materiais do controle (tela, checklist, registros e testes). O registro interno que junta as normas como "RN 557/659" deve ser desdobrado: contratação = RN 557/2022; penalidade = RN 489/2022, art. 23-A.
3. **Escopo do controle:** conteúdo INTEGRAL desde a primeira versão — o dever vale desde maio de 2026 (vigência da RN 659/2025 em 1º/5/2026, art. 5º, conforme anexo do parecer) e não há, no texto do parecer, base para versão reduzida até 2027.
4. **Abrangência:** a responsabilidade é da operadora (celebrar/manter) e da administradora de benefícios; "o canal, em si, não seria alcançado". Recomendação técnica do parecer: NÃO caracterizar juridicamente o papel do canal na tela nem em textos exibidos a terceiros.
5. **Critérios da tela** (lista do §5, acima transcrita): tipo/forma de contratação (art. 29); vínculo empresarial (art. 5º); adesão — lista do art. 15 + constituída ≥ 1 ano (art. 16, salvo conselhos, entidades de classe e sindicatos); empresário individual — docs do art. 9º + reconfirmação anual (art. 10); vedada inclusão sem PJ legitimada (art. 29 §1º).
6. **Registros:** a Maezo guarda no banco (AMH datalake) o registro da VERIFICAÇÃO e a referência aos documentos, SEM duplicar a custódia — a guarda dos documentos é dever da operadora e/ou da administradora (art. 12).
7. **Art. 39 (estado bloqueante):** bloquear + registrar + sinalizar o efeito — ingresso irregular constitui vínculo direto e individual com a operadora, equiparado a plano individual/familiar (redação do art. 39 no anexo do parecer).

---

## 4. O que este registro NÃO faz

- **Não ratifica** nada (nenhum bloco de decisão/ratificação é preenchido — ADR-0007 permanece com o dono).
- **Não decide** novas D-*: a numeração/atualização do registro de decisões é ato do dono em `docs/plans/maezo-completion-program.md` §Decisões, não tocado aqui.
- **Não altera testes nem código:** o reparo de citação do item 2 e a implementação do OP21 (REG-1.1) saem em PRs próprios da cadeia da casa.
- **Não interpreta** além do texto: os itens da §3 são extração literal do que o parecer afirma.
