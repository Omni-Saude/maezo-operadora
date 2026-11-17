# COORD-OUT — W-MZO-DOC (onda 1, Funil de Vendas)

Ramo só de coordenação; nunca vira PR. Só acréscimo.

## Q-W-MZO-DOC-01 — E-SF-33: blobs de origem omni já presentes no maezo (PROVISÓRIO-aguarda-coordenador)
A guarda 09a encontra 9 arquivos rastreados do maezo idênticos a blobs de `omni_sales_platform@b3de4b4` que apareceram primeiro no omni (contratos candidatos C4, C5, C6-1..C6-4 em `tests/candidate_artifacts/commercial/`, `ROOT-OWNERSHIP.md`, dois SOURCE-FREEZE). Outros 16 idênticos nasceram no maezo e não são recusados.
Opções: (a) catraca exata por (caminho, sha) que só encolhe, sem valor de licença; (b) guarda vermelha até adjudicação; (c) retirar os 9 arquivos.
Recomendação: (a), adotada no ramo `sf-w1/doc/E-SF-33`. A origem jurídica dos 9 é ponto jurídico (CORE-09, CA-01 aponta AMH para os C*): não decidido pela janela.

## Q-W-MZO-DOC-02 — E-SF-33: PRs só de agente/pacote não rodam `tests/governance` (PROVISÓRIO-aguarda-coordenador)
As guardas novas rodam no shard `unit (restante)` da trilha plataforma. PRs da trilha agente/pacote e o push do merge deles não as rodam; só o noturno pega.
Opções: (a) aceitar e declarar (feito em `docs/governance/README.md`); (b) job novo em `ci.yml` ligado ao agregador obrigatório, o que muda na prática a lista de checks obrigatórios (CD-11 item 8 proíbe sem decisão).
Recomendação: (a) nesta onda; (b) só com decisão da coordenação do programa.

## Q-W-MZO-DOC-03 — E-SF-32: 5 citações antigas da RN 561 por cópia não-DOU (PROVISÓRIO-aguarda-coordenador)
A guarda `test_rn561_cited_from_dou` acusa 5 linhas pré-existentes em `docs/compliance/legal/R-054-RATIFICACAO-CITACOES-POR-LINHA.md` e `docs/compliance/rn-currency-review.md`.
Opções: (a) lista de dívida exata, uma ocorrência por entrada, que só encolhe, com errata proposta; (b) editar os documentos de ratificação; (c) guarda vermelha.
Recomendação: (a), adotada; (b) mexe em registro de ratificação e fica para quem o custodia.

ACHADO docs/compliance/legal/MANIFEST.sha256 — a linha de `R-054-RATIFICACAO-CITACOES-POR-LINHA.md` não confere desde `581fc51` (arquivo editado depois da selagem); confirmado em `211c6ae`. A janela não re-hasheou (re-selar seria forjar custódia).
ACHADO docs/plans/maezo-completion-program.md:251 — cita MAPA-ELEMENTOS-V4:106; após E-SF-30 o alvo está em :108 (caminho fora da janela).
PRONTO maezo-operadora #1267 head 0a39b325cffb8c0dd7153e5fe37a1a573d627b12 checks verdes (integração: 5 shards verdes; o PR não traz teste de integração próprio; 2 pushes, o 2º corrige S307 do test_noqa_hygiene)
PRONTO maezo-operadora #1272 head 1333992760eb45a5d20d95a3acc2123574acbea9 checks verdes (integração: 5 shards verdes; o PR não traz teste de integração próprio; 2 pushes, o 2º corrige falso positivo do gitleaks)
PRONTO maezo-operadora #1272 head 726b72b63d65a82025f2e5a90c8870282c598939 checks verdes (após Update branch da mescladora; 5 shards de integração verdes; aviso direto enviado à mescladora, CD-42)
PRONTO maezo-operadora #1272 head 2b7c7256da46969795613f21e19c067262e2420d checks verdes (após 2º Update branch com #1273/#1274/#1275; sem conflito no MANIFEST; sha256sum -c confere; 5 shards de integração verdes; aviso direto à mescladora)

## Q-W-MZO-DOC-04 — E-SF-31 (#1280): teste de engine fora da partição do runner de integração (BLOQUEIA o PRONTO do #1280)
CD-18 item 2 pôs `tests/processes/test_bpmn_engine_parse_deploy.py` (marcado `integration`) fora de `tests/integration/`. O CI do #1280 (head c5d91cc) reprova `unit (dev-runner)`: `tests/unit/dev/test_engine_integration_runner.py` acusa "partição core/chaos/db-unit não cobre a coleção integration exatamente uma vez". Causa: `scripts/dev/run_engine_integration.py:1005-1028` coleta `-m integration` em `tests` inteiro, mas particiona só `tests/integration` (core/chaos) e `tests/unit` (db-unit).
Opções: (a) mover o arquivo para `tests/integration/processes/test_bpmn_engine_parse_deploy.py` (CD-11 item 5 autoriza editar tests/integration; rodo `make xfail-census-check` e, se preciso, `-write`, aceitando só a região gerada de PLANS.md), um push; (b) estender a partição do runner para `tests/processes/` (caminho do programa, fora da janela — não recomendo); (c) tirar o marcador e não ter teste de engine no PR (perde o critério 5 do EP-PROC-04).
Recomendação: (a). Preparada localmente, sem push, até a resposta (CD-18: "volte a mim antes de mover o arquivo"). O #1280 já está parado só por aprovação humana no flip-path-review-gate.
PRONTO maezo-operadora #1281 head 4c849ed8869671fc88b6209915d80c23a82bea92 checks verdes (5 shards de integração verdes; dependency review reexecutado 1x após HTTP 502; flip-path-review-gate vermelho só por aprovação humana de code owner em .github/workflows/omni-trust-receipt.yml; aviso direto à mescladora)
HOLD maezo-operadora #1281 — segurado pela mescladora (repo público × inventário de repo privado); respostas às 3 perguntas dela enviadas direto (forma mínima: só hashes; binding/registros só do contrato C6-4 do Maezo; citações a arquivos do pacote em #1267, #1270, #1272, #1273). Nenhum push até decisão do dono.
PRONTO maezo-operadora #1280 head 92002ee3b53af285768db546c2ee7c265c5ea12a checks verdes (integração: junit do shard restante com 94/94 casos do teste de engine executados, 0 skip; bpmn-gates verde; flip-path-review-gate vermelho só por aprovação humana de code owner; 2 pushes, o 2º aplica CD-44; aviso direto à mescladora)

## RELATÓRIO FINAL — W-MZO-DOC (onda 1)
- E-SF-01b (EP-PROC-01b, documental): veredito cego FAIL (3 reparos: fonte da baseline, pin do 35-C, base 7985366) → reparo literal → PASS. Entregue em omni `WAVE1-OUT/W-MZO-DOC/E-SF-01b/` (bundle 4eb510f). Sem PR (CD).
- E-SF-30: #1267 MESCLADO. Revisor: FAIL (F1 guarda de flips contornável) → PASS; delta S307 PASS. Segunda fonte cega de lanes OK; achados X-01/X-02 no corpo. EP-PROC-08 (matriz CA_1, documental): FAIL→PASS; critério 3 parcial (16 DO/ADE sem épico dono no plano: AGENDA/NBA/PERFIL); critério 5 GAP-12.
- E-SF-31: #1280 MESCLADO. Revisor: FAIL (corpus CA rejeitado pela engine) → PASS; delta CD-44 PASS. Junit restante: 94/94 do teste de engine executados, 0 skip. EP-PROC-05 documental (ato do dono, campo vazio). EP-PROC-09 não entra (EM-02).
- E-SF-32: #1272 MESCLADO. Revisor: FAIL (M-1 lista de dívida) → PASS; delta gitleaks PASS. 12 filhos documentais + 15 pacotes de ratificação do adendo §5.2 (147 conferido): FAIL→PASS. EP-REG-27c fora (CD-05).
- E-SF-33: #1281 SEGURADO pela mescladora (repo público × inventário do omni privado), aguarda o dono. Checks verdes exceto flip-path. Revisor: FAIL (registro 09b ausente; catraca só por caminho) → PASS. Proposta pronta: manifesto só com hashes, sem caminhos/tamanhos; nada pushado.
- Não verificado: URLs normativas primárias (sem rede); integral das normas (GAP); render da Carta (modelo não pinado); recibo omni-trust (segredo não provisionado); canvas bpmn-js; timer avançado (coberto pelas suítes SP-OP).
- Portões fechados: nenhuma licença redigida; docs/governance/licensing vazio; RIPD e 15 pacotes com campos de decisão vazios; 8 flips do MAPA "pendente"; nenhuma regra DMN ratificada; nenhum envio/medição.
- Deriva: manifest de toolchain e job de CI sem caminho no plano (CD-18); teste de engine movido (CD-44); 3/4 arquivos CA não fazem parse (ENGINE-09005, cercado); "44 delegateExpressions" = 44 distintos/75 ocorrências; MANIFEST R-054 já falhava (re-selado no #1275); PROHIBITED_FIELD_CLASSES só em vendor_suppression.py.
- Citações ao pacote privado em PRs mesclados (#1267, #1272; e #1270, #1273 de GATE): listadas à mescladora; PR de higiene oferecido.
- Ledger pedido: linhas para E-SF-30 (#1267), E-SF-31 (#1280), E-SF-32 (#1272). DL sugeridos: (a) teste `integration` só em tests/integration ou tests/unit (partição do runner); (b) nada de inventário de repo privado em repo público; (c) gitleaks: nome de constante com TOKEN_ + string dispara generic-api-key.
- CI: #1267 2 pushes, #1272 2 pushes (+2 updates da mescladora), #1280 2 pushes (+1 update), #1281 1 push + 1 rerun (HTTP 502).
