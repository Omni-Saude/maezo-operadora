# Adendo 2026-09-21 — três assinaturas que a bateria da Helena deixou expostas

**Para:** dono do produto (roster SME) · **De:** engenharia (Leonardo Silva) · **Origem:**
`HELENA_ACHADOS_BATERIA_21-09-2026.md` (Diretoria de Tecnologia), seção "O que acho que falta
ou está errado na repo", itens 4, 5 e 6.

Este adendo **não assina nada**. Ele nomeia, para cada pendência, o artefato exato, quem assina,
o que a assinatura afirma e o texto proposto — para que o ato humano seja um "sim/não" sobre
algo concreto, e não uma investigação. Nenhum agente cria os arquivos de signoff em nome de
ninguém (`medico-auditor/PACKAGE.md`, passo 4).

O código dos sete achados (F1–F7) e dos itens 1–3 e 7 entrou em PR separado, sem depender destas
assinaturas. O que segue abaixo é o que **continua em rascunho** depois desse PR.

---

## A. Régua de extração — nunca ratificada (item 4)

| | |
|---|---|
| artefato | `docs/design/regua-de-extracao.md` (5 regras) + corpus `tests/evals/extracao/casos.json` (`extracao-v2`, 126 casos) |
| registro | `spec/revisao-das-reguas.yaml` → `reguas[id=extracao].ultima_revisao: null` |
| quem | **médico auditor** (`dono: medico auditor` — o mesmo que assina as 34 regras clínicas; o yaml diz "não há dois donos aqui") |
| prazo já declarado | `revisar_ate: 2026-12-15` ou 1000 conversas |
| o que a assinatura afirma | que as 5 regras de inferência (o que a Helena pode concluir de intensidade, código, população e idade a partir da mensagem) são clinicamente aceitáveis, **incluindo a regra de qualificador** acrescentada em 21/09: nenhum código com qualificador clínico (`subita`, `intensa`, `grave`, `ativo`…) sem o qualificador na fala da pessoa; na dúvida, código genérico + pergunta |
| ato | preencher `ultima_revisao: "AAAA-MM-DD"` e `nota` em `spec/revisao-das-reguas.yaml`, no mesmo PR que registra o nome do revisor. Os testes de `tests/unit/evals/test_corpus_de_extracao.py` já cobram que a versão do corpus apareça na régua |
| por que agora | o F3 da bateria (dor de cabeça simples virou `cefaleia_subita_intensa`, P1/emergência) é consequência direta de uma régua implementada e medida mas sem dono declarado |

**Texto proposto para a `nota`** (o revisor edita à vontade):

> Revisada em AAAA-MM-DD por [nome, CRM]. As cinco regras e a regra de qualificador de 21/09
> são aceitas como estão. Ponto de atenção registrado: o modelo pode obedecer ou não à regra —
> a medição contínua é o eval ao vivo (`tests/evals/test_extracao_live.py`) e a bateria do canal
> de teste; esta assinatura cobre a REGRA, não o comportamento do modelo em cada turno.

---

## B. Tabelas de triagem em DRAFT + valores de SLA (item 5)

| | |
|---|---|
| artefatos | `spec/processes/dmn/triage_redflag_adult.dmn`, `triage_redflag_pediatric.dmn`, `triage_redflag_gestante.dmn`, `triage_redflag_mental_health.dmn` (cabeçalho `CONTEUDO CLINICO: DRAFT`) e `spec/processes/dmn/escalation_routing.dmn` (cabeçalho "shape FINAL, SLAs DRAFT") |
| registro | `docs/review-queue.md` linhas 10–14; `docs/sme-dispatch/tracker.md` linha 8 — SP-OP-ESCALATION-001 **FINAL (v1.0.0) sem signoff**, "owner action — due 2026-09-19" (**vencido**) |
| quem | tabelas: médico auditor (adulto), pediatra, obstetra, psiquiatra/psicólogo — conforme `review-queue.md`; SLA do `escalation_routing`: gestão assistencial + compliance |
| o que a assinatura afirma | (tabelas) que os sintomas, limiares de intensidade/idade e prioridades P1/P2 de cada tabela são o que a operadora quer que decida uma triagem automatizada; (SLA) que **5 min de ciência para P1 e 30 min para P2**, e os prazos de resolução, são compromissos que a operação consegue honrar — a bateria mediu exatamente esses números saindo para o beneficiário |
| ato | `docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml` conforme `README.md` §"Signoff artifact spec"; trocar `DRAFT` → `FINAL` nos cabeçalhos das DMN no mesmo PR; remover a linha correspondente de `review-queue.md` |
| pacote já pronto | `docs/sme-dispatch/medico-auditor/PACKAGE.md` (retro-verificação da SP-OP-ESCALATION-001, pedido de 3 dias úteis) — só falta o roster nomear a pessoa |

---

## C. Dono clínico da Helena (item 6)

| | |
|---|---|
| artefato | `spec/agents/helena/agent.yaml` → `owners.clinico: ""` |
| registro | o próprio arquivo: "preenchido pelo mesmo ato que assina as 34 regras (Frente 4)" |
| quem | o médico que assinar B (é um ato só, por desenho) |
| ato | `owners.clinico: "Nome <e-mail>"` no mesmo PR de B. A cerca `test_helena_dono_declarado` compara `agent.yaml` com `PROMPT_VERSIONS` — não toca em `owners`, então o PR é seguro para o CI |
| por que importa | hoje a Helena tem dono técnico e nenhum clínico; qualquer achado clínico da bateria (F3, D3) não tem para quem escalar dentro da organização |

---

## O que a engenharia já fez para tornar o "sim" barato

- Régua e corpus estão versionados e cercados por teste (`extracao-v2` tem par mínimo/máximo
  para cada código com qualificador).
- Os textos que a Helena diz nos casos medidos (C1, E4, D2, D3, A2, A3, E5) estão no PR e podem
  ser lidos sem rodar nada.
- A bateria do diretor (`bateria-helena.js`, 26 casos) é reproduzível em 12 minutos no canal de
  teste depois do deploy — é o critério de pronto que o próprio relatório fixou.

**Uma decisão que não é de engenharia e continua aberta:** a lista de canais confirmados
(app "Austa Clinicas", "o portal do plano", "a central de atendimento do plano") foi definida pelo
dono do produto em 21/09 e está em código (`CANAIS_CONFIRMADOS`). Se algum desses canais não
resolve o que a lista diz (boleto, carteirinha, rede, histórico), a Helena está mandando gente para
o lugar errado com o nome certo — e isso só a operação confirma.
