# Canal W2-MZO-GP

## 2026-11-17 — BLOQUEIO

- Passo "LEIA PRIMEIRO": a anexação somente-leitura do repositório privado do pacote
  (via `add_repo`) foi **negada** pela política de permissões desta sessão
  (motivo informado: acesso não autorizado). Não houve tentativa de contorno.
- Sem o pacote não tenho o texto vinculante comum nem as respostas CD da onda 1 e da
  onda 2 (incluindo a partição de posse e os números confirmados). Conforme o brief,
  **paro aqui**: nenhuma linha iniciada, nenhum ramo de linha criado, nenhum PR aberto.
- Para destravar: o dono precisa permitir `add_repo` (somente leitura) para esta sessão
  ou reiniciar a janela num ambiente que já tenha o pacote anexado.

STATUS: BLOQUEADO linhas=0

## 2026-11-17 ~13:45Z — DESBLOQUEIO e linha 1

- Leitura do pacote liberada pelo dono; hashes dos seis arquivos conferem. Lidos o brief comum, CD-52 a CD-60 e as CD da onda 1 pedidas.
- Fila seguida (CD-52 + CD-58/59): ligação de E-SF-36 → GTM-E-66 → PR de ajustes do mapa de elementos → E-SF-20r → E-SF-25 → E-SF-26 + parse CA → ...
- Linha 1 (ligação de E-SF-36): PR maezo-operadora #1295, ramo `sf-w2/mzo-gp/esf36-ligacao`, head `2c2e55a3ded2c3c0bbaf17f5a29e955d3fbc6988` (contém a main `14ce9cda`). Revisão independente PASS (achados reparados e reconferidos; um reparo revertido por quebrar a opacidade do payload no núcleo). CI em curso.
  Arquivos: `src/maezo/gateway/commercial_admission.py`, `src/maezo/gateway/commercial_disclosure_checklist.py`, `tests/unit/gateway/test_esf36_disclosure_{checklist,wiring}.py`, `tests/unit/test_commercial_admission_a{3,4,5}.py`. Nenhum caminho fora da posse; nenhum CODEOWNERS/ledger/DL.
- ACHADO: na suíte completa local, 448 falhas/erros reproduzem idênticas na main limpa (clone raso: `tests/unit/dev/test_historical_*` e consumidores do ledger; sem `helm`: `tests/unit/deploy/*`; root: `test_audit_anchor.py::test_worm_store_read_only_mode_is_not_integrity_against_the_owner`). Ambiente, não código.

## 2026-11-17 ~14:15Z — linha 2 pronta localmente; ACHADO

- Linha 2 (E-SF-21, só GTM-E-66): ramo `sf-w2/mzo-gp/gtm-e-66` (commit local `c0bc8a71`, sem push até o merge do #1295). Gatilho de aniversário em `src/maezo/gateway/commercial_legitimacy_anniversary.py` (nova `screen_revision`, nunca UPDATE; `would_block` só registrado; sem art. 39; sem efeito; não montado) + testes `tests/unit/gateway/test_esf21_gtm66_anniversary.py` (inclui `test_revalidacao_aniversario_nunca_update` e `test_revalidacao_sem_efeito_adverso`). Revisão independente: FAIL → reparado → PASS. O reparo troca a chave de idempotência do guard (`commercial_legitimacy_guard.py`, `_register`) de `would_block` para `art39_signaled`, para a linha do aniversário não suprimir o registro do art. 39; sem efeito nas linhas já gravadas pelo guard.
- GAP: o catálogo dos guardas GT-GTM-039/045/053/056 não está no pacote (procurado em todo o ramo do pacote); entram só os dois testes nomeados e cercas próprias.
- ACHADO (pré-existente, fora da linha): `src/maezo/portal/api/legitimacy_screen.py` `revalidate` lê a cabeça e salva `N+1` com INSERT … ON CONFLICT DO NOTHING; se o guard (ou, quando montado, o gatilho) gravar `N+1` entre a leitura e o save, a staff recebe 200 com a linha do outro escritor e a revalidação se perde em silêncio. Sugestão: o serviço comparar `created_by` da linha devolvida e responder `stale_revision`.

## 2026-11-17 ~15:20Z — PRONTO linha 1

PRONTO maezo-operadora #1295 head 27708686c0ed842a5efd59a48f857d041509fa86 checks verdes (integração lida do junit: 5 shards PASS, 951/53/129/128/138). Ramo contém a main 208ec575 (houve um "Update branch" próprio: a main andou durante o primeiro CI). Simulação local de merge sobre a main: limpa. Linha `pronto` enviada à coordenação do programa.

## 2026-11-17 ~15:30Z — linha 1 mesclada; ordem nova

- #1295 incorporado (main `da078bc0`). Linha 1: MESCLADA.
- Recebido da coordenação do programa: linha 2 (GTM-E-66) só abre depois do #1296 da T-MZO-A (corrida da revalidação: `portal/api/legitimacy_screen.py`, `gateway/commercial_legitimacy_screen.py`), rebaseada sobre a main nova e com teste de que o gatilho de aniversário trata `stale_revision`. Linha 2 segue local, sem push.
- Em preparo local: PR só de documento dos ajustes do mapa de elementos (CD-58 item 7), com reconferência no head dos flips 2, 3, 5, 6 e 7.

## 2026-11-17 ~16:10Z — Q-W2-MZO-GP-01 (PR dos ajustes do mapa de elementos)

PR local pronto no ramo `sf-w2/mzo-gp/mapa-ajustes` (commit `412fb211`, sobre main `da078bc0`; sem push). Toca só `docs/processes/New_BPMN/Adapted_BPMN/MAPA-ELEMENTOS-V4.md` e `tests/processes/test_mapa_elementos_v4_sync.py`. Disposição de CD-58 item 7 aplicada: #1 e #4 adotados; #2, #3, #5, #6, #7 adotados após reconferência no head (nenhum ingresso montado; evidência no texto do §7.1); #8 rejeitado; registro como decisão operacional por delegação. Contagem recalculada: 207 CASA + 160 LACUNA = 367 (era 214 + 153); nenhum outro documento cita o número. Três negativos do teste de sync reescritos para os flips adotado/rejeitado + um negativo novo. Revisão independente: FAIL (afirmação falsa sobre starts por mensagem) → reparado → PASS. Suítes `tests/processes` (79) e `validate-artifacts` verdes. Nenhum ACHADO de ingresso montado.
Opções: (a) abrir já — só documento, sem interseção com os arquivos do #1296; (b) esperar o #1296 e abrir depois, antes da linha 2. Recomendação: (a). Sigo pela conservadora (b) até resposta: PROVISÓRIO-aguarda-coordenador.

## 2026-11-17 ~16:00Z — linha 2 aberta; ACHADO

- Linha 2 (E-SF-21, só GTM-E-66): ramo `sf-w2/mzo-gp/gtm-e-66`, head `5347ffd7959274c9d95b59a11da961bfdfe1a928` (contém a main `dfaf6e6c`, com o #1296). Gatilho trata `revision_taken` (corrida perdida = nada gravado); testes da guarda na corrida com o aniversário (shadow inalterada; enforcing `unavailable/source_unavailable`, motivo marcado como provisório). Revisão independente PASS. PR aberto; CI em curso.
  Arquivos: `src/maezo/gateway/commercial_legitimacy_anniversary.py` (novo), `src/maezo/gateway/commercial_legitimacy_guard.py` (só a chave de idempotência de `_register`), `tests/unit/gateway/test_esf21_gtm66_anniversary.py` (novo).
- ACHADO (guard existente, fora da linha): na corrida guard shadow × outro escritor, o `save` do guard levanta `revision_taken` e o `suppress` único da shadow engole também o `_signal`: aquele comando não emite `legitimacy.expired`/`art39_signaled` (o próximo emite). Sugestão para o PR da enforcing: tratar `revision_taken` em `_register` como "outro escritor registrou primeiro" e seguir para o sinal; decidir aí também o motivo da recusa enforcing na corrida.
- Q-W2-MZO-GP-01 (PR do mapa) fica para depois deste: um PR aberto por vez.

## 2026-11-17 ~16:35Z — #1297: shard de integração vermelho, corrigido

- O shard "integration (restante)" do #1297 reprovou em `tests/unit/platform/test_legitimacy_screens_migration_live_pg.py::test_enforcing_persiste_a_observação_do_bloqueio_e_os_checks_seguem_válidos`. Causa deste PR: a chave de idempotência do guard (`art39_signaled` só) pulava o registro sobre cabeça com art. 39 sem `would_block` (permitida pelo substrato; a fixture live-pg a usa). Reproduzido com PostgreSQL local (falha no ramo, passa na main).
- Correção: o guard só pula quando a cabeça carrega o par completo (`would_block` e `art39_signaled`); teste unitário novo cobre esse caso. Revisão independente do delta: PASS. Local, com Postgres: 449 passed nos 21 arquivos que citam legitimidade. Segundo push feito (head 46a01065f898ee236b74c3f4da1ec9b570dfb691); CI em curso.

## 2026-11-17 ~16:55Z — #1297: "unit (ci)" vermelho, corrigido (terceiro push)

- Causa deste PR: `tests/unit/ci/test_w7_shadow_deviation_dashboard.py` ancora `def collective_legitimacy_shadow(` na linha 499 de `commercial_legitimacy_guard.py`; a docstring nova de `_register` empurrou para 501. Correção: docstring compactada, o arquivo mantém a contagem de linhas da main (só +3/−3). "lint / type / unit" e o agregado da integração ficaram vermelhos só por dependerem desse job.
- Falha minha de processo: no push anterior não rodei `tests/unit/ci` (regra de CD-39). Rodado agora antes do push: painel shadow 29 passed; o resto de `tests/unit/ci`, `adapters` e `ports` só tem as falhas de ambiente já reproduzidas na main limpa (clone raso, sem helm, root).
- Head `f29ab8b2ef06a2ade3acec28a6240f83cba8c6ff` (contém a main `dfaf6e6c`). CI em curso.

## 2026-11-17 ~17:50Z — PRONTO linha 2

PRONTO maezo-operadora #1297 head f29ab8b2ef06a2ade3acec28a6240f83cba8c6ff checks verdes (integração lida do junit: 5 shards PASS, 952/138/53/129/128). Ramo contém `dfaf6e6c`; main atual `4ab70ea5` (#1298) não toca nenhum arquivo do PR; simulação local de merge sobre `4ab70ea5`: limpa. Linha `pronto` enviada à coordenação do programa, com a explicação da chave de idempotência (`commercial_legitimacy_guard.py:379`: `would_block` → `would_block and art39_signaled`).

## 2026-11-17 ~17:55Z — linha 2 mesclada; CAMINHO do PR do mapa

- #1297 incorporado (main `a5fedc8e`). Linha 2 (E-SF-21, só GTM-E-66): MESCLADA.
- Pendente herdado por E-SF-20r (CD-73/CD-82), registrado: (i) em `revision_taken`, o gatilho de aniversário hoje devolve "nada gravado" sem reler a cabeça — a releitura única com decisão sobre a revisão vigente (CD-73 item 1) fica para E-SF-20r; (ii) o motivo da recusa enforcing na corrida (`unavailable/source_unavailable`) é provisório e fecha em E-SF-20r; (iii) o sinal na corrida em sombra (CD-82 item 2).
- CAMINHO (CD-80 item 2): o PR dos ajustes do mapa de elementos toca `docs/processes/New_BPMN/Adapted_BPMN/MAPA-ELEMENTOS-V4.md` (§3–§7.1 e Changelog) e `tests/processes/test_mapa_elementos_v4_sync.py` (três negativos reescritos + um novo). Não toca `INVENTARIO-CA.generated.json` nem `scripts/bpmn-gates/baseline.json` (o inventário confere sem regeneração). Ramo `sf-w2/mzo-gp/mapa-ajustes` já contém `a5fedc8e`, sem push. Aguardo a CD para abrir (CD-78 pediu abertura; CD-80 manda serializar arquivos compartilhados).

## 2026-11-17 ~18:00Z — PR do mapa aberto

Autorização recebida da coordenação do programa (PR sozinho entre os que tocam MAPA/INVENTARIO). PR maezo-operadora #1300, ramo `sf-w2/mzo-gp/mapa-ajustes`, head `27e54920bff6bf6e15fea572aba59b36ef8a4811` (contém `a5fedc8e`). Toca só `MAPA-ELEMENTOS-V4.md` e `tests/processes/test_mapa_elementos_v4_sync.py`. CI em curso. Próximo da fila: E-SF-20r (com os requisitos de CD-73/CD-82).

## 2026-11-17 ~18:50Z — PRONTO #1300

- head `27e54920bff6bf6e15fea572aba59b36ef8a4811`, contém a main `a5fedc8e`; run 44142603818, 32 jobs verdes.
- Integração (live-pytest PASS): cancel-contas 138, restante 956, recurso-nip 128, cred-ans 129, reembolso 53.
- Simulação de merge sobre origin/main `a5fedc8e`: limpa; arquivos: `MAPA-ELEMENTOS-V4.md`, `tests/processes/test_mapa_elementos_v4_sync.py`.
- Pronto enviado à coordenação. Próxima da fila depois do merge: E-SF-20r (CD-59, com CD-73/CD-82).

## 2026-11-17 ~20:55Z — #1300 incorporado; PR E-SF-20r aberto

- #1300 (ajustes do mapa) mesclado às 18:49Z; main `dd52a051`, depois `972130a0` (com o #1299).
- PR maezo-operadora #1302 (E-SF-20r), ramo `sf-w2/mzo-gp/esf20r`, head `96061c44b4d6ec8102b5a8350d7af4ed472158b5` (contém `972130a0`), aberto às 20:10Z. CI em curso.
- Requisitos (CD-59, CD-73, CD-82): postura que recusa já existente, não montada; porta da flag recusa a classe (teste nomeado); `revision_taken` relê a cabeça uma vez no guard e no gatilho; colisão dupla ou releitura que falha = `unavailable/source_unavailable`; cabeça relida pendente/inválida/vencida/ausente = `denied/legitimacy_unproven`; vigente segue; em sombra o sinal sai uma vez e a linha não duplica. GTM-E-36: canal vendor só liga com a postura enforcing (classe exata).
- Arquivos: `commercial_legitimacy_guard.py`, `commercial_legitimacy_anniversary.py`, `capabilities/vendor_journey/binding.py`, `scripts/w7/shadow_deviation_dashboard.py` e `docs/delivery/w7/shadow-deviation-dashboard.md` (âncora 499→542), testes em `tests/unit/gateway/`. Nenhum arquivo de contagem compartilhado (CD-80).
- Mutação em clone isolado: 5 violações, todas vermelhas. `tests/unit/ci` rodado antes do push.
- Processo: o commit/push deste canal foi negado pela permissão automática da sessão às ~18:50Z (entrada do PRONTO #1300 ficou local); estas linhas foram gravadas quando a permissão foi liberada.

## 2026-11-17 ~21:00Z — PRONTO #1302 (E-SF-20r)

- head `96061c44b4d6ec8102b5a8350d7af4ed472158b5`, contém a main `972130a0`; run 44166471914, check runs verdes (2 skipped de matriz/nightly).
- Integração (live-pytest PASS): cancel-contas 138, restante 956, recurso-nip 128, cred-ans 129, reembolso 53.
- Simulação de merge sobre origin/main `972130a0`: limpa; 9 arquivos (os listados no PR).
- Pronto enviado à coordenação. Próxima da fila depois do merge: E-SF-21r (CD-61).

## 2026-11-17 ~21:10Z — #1302 incorporado; E-SF-21r em ramo

- #1302 (E-SF-20r) mesclado às 20:58Z; main `4d44a8ec`.
- E-SF-21r pronta em ramo `sf-w2/mzo-gp/esf21r` (CD-61, CD-92): critério de seleção do lembrete pelo aceite do termo de contato, finalidade 2, lido antes de qualquer fato de cobrança; oposição pelo PARAR executável só com o gancho de entrada ligado (não montada); envio fechado; lembrete só antes do vencimento (J68). Só `src/maezo/platform/lembretes/`, os testes dela (sem `test_optout_config_envio.py`, da W2-MZO-CA) e o runbook do lembrete. GTM-E-61 (BPMN/DMN de reajuste) fora: depende de 13r/57r e de caminho novo.
- Ordem: a mensagem da coordenação do programa das 20:58Z indica E-SF-25 como próxima; sigo CD-61/CD-92 (E-SF-21r antes de E-SF-25). Se a ordem mudou, aviso por CD e troco.
- PR abre depois da suíte local (`tests/unit/ci` antes do push).

## 2026-11-17 ~22:20Z — PR E-SF-21r aberto

- PR maezo-operadora #1305, ramo `sf-w2/mzo-gp/esf21r`, head `c8348daa01d08cd1380ffa1dfecfdea0dfc1ba87` (contém a main `093357a2`). CI em curso.
- Arquivos: `src/maezo/platform/lembretes/{aceite.py (novo),job.py,optout.py,leitura.py}`, `docs/runbooks/lembrete-cobranca.md`, `tests/unit/platform/lembretes/{test_esf21r_aceite_e_oposicao.py (novo),test_esf13_lembrete_fechado.py}`. Nenhum arquivo de contagem compartilhado (CD-80); `test_optout_config_envio.py` e `gateway/communications/` não tocados.
- Mutação em clone isolado: 6 violações, todas vermelhas. Revisão independente PASS (7 achados baixos corrigidos). `tests/unit/ci` rodado sobre a main nova antes do push.

## 2026-11-18 ~00:05Z — PRONTO #1305 (E-SF-21r)

- head `95dfeed29fd8df2e23b15bf97766663524c3d2ad`, contém a main `4826d014` (com o #1304 das classes fechadas); run 44197173597, check runs verdes (2 skipped de matriz/nightly).
- Integração (live-pytest PASS): cancel-contas 138, restante 956, recurso-nip 128, cred-ans 129, reembolso 53.
- Simulação de merge sobre origin/main `4826d014`: limpa; 7 arquivos (os listados no PR).
- Consumo de CI: dois pushes. O primeiro caiu em `tests/unit/sec/test_sem_assert_nem_except_pass_em_src.py` (um `assert` em `src/maezo/platform/lembretes/job.py`); falha de processo minha: `tests/unit/sec` não estava na rodada local. Regra da janela a partir daqui: `tests/unit/sec` junto de `tests/unit/ci` antes de todo push.
- Pronto enviado à coordenação. Próxima da fila depois do merge: E-SF-25 (banco de questionário, esqueleto e lint; `spec/health/` novo com a linha de CODEOWNERS no PR — CODEOWNERS é arquivo compartilhado, CD-80: gravo CAMINHO antes de abrir).

## 2026-11-18 ~00:20Z — #1305 incorporado; conferências; CAMINHO de E-SF-25

- #1305 (E-SF-21r) mesclado; main `6520e4a1`. Ordem confirmada pela coordenação do programa.
- CD-98 (valores do critério de seleção nos validadores do #1304): no ramo de E-SF-21r já com a main `4826d014`, rodei `tests/unit/gateway/test_cd87_classes_fechadas.py` (inclui os valores reais do produtor de lembretes) junto de `tests/unit/platform/lembretes` e `tests/unit/gateway/communications`: 482 passed. O critério novo não muda as variáveis: o produtor e o template seguem os mesmos.
- #1306 da W2-MZO-CA: toca só `gateway/communications/{outbound_gate.py,whatsapp_templates.py}` e testes do gateway; NÃO toca `platform/lembretes`. Nenhuma atualização de ramo necessária por causa do #1305.
- **CAMINHO (CD-80) — E-SF-25** (questionário da declaração de saúde: esqueleto e lint, banco só sintético):
  - novo `spec/health/question-banks/` (README + um banco SINTÉTICO, aprovação com prefixo sintético, nunca carregado como real);
  - novo validador `src/maezo/platform/validation/question_banks.py` + ligação em `validation/cli.py` (`make validate-artifacts`);
  - testes em `tests/unit/platform/validation/`;
  - `.github/CODEOWNERS`: UMA linha nova `/spec/health/  @rodaquino-OMNI @Omni-Saude/security-team` e, no mesmo arquivo, a contagem esperada do comentário passa de 35 para 36 (`tests/unit/ci/test_codeowners_rules.py` exige). Pergunta: dono da linha — só `security-team` (36 erros, uma por linha) ou também `@Omni-Saude/medical-team` (o lint conta um erro por token de time, então 37 e o texto da contagem muda de forma)? Padrão proposto: só `security-team`.
  - Nada em `PLANS.md`, decisions-log, ledger, `spec/policies/autonomy/`, `_hard_frozen.yaml`. Sem migração. Sem conteúdo clínico real (J47: banco é do jurídico + médico auditor); sem Agravo (E22/J38).
  - Começo a construir em ramo; abro só depois da CD sobre o CAMINHO.
- CAMINHO (acréscimo, E-SF-25): o ramo `sf-w2/mzo-gp/esf25` também toca `Makefile` (um token: `spec/health` na linha de `validate-artifacts`; `/Makefile` é linha do CODEOWNERS). O validador entra como raiz nova reconhecida pela estrutura (`question-banks/`), sem mudar as raízes existentes. Ramo pronto localmente; aguardo a CD antes de abrir.

## 2026-11-18 ~01:55Z — PR E-SF-25 aberto (CD-103)

- PR maezo-operadora #1308, ramo `sf-w2/mzo-gp/esf25`, head `0ff8d8caff475c255d260bdb6da4a1304b47e594` (contém a main `6520e4a1`). CI em curso.
- Arquivos: `spec/health/{README.md,question-banks/ds-sintetico-v0.yaml}`, `src/maezo/platform/validation/{question_banks.py (novo),cli.py}`, `tests/unit/platform/test_validation_question_banks.py`, `.github/CODEOWNERS` (linha `/spec/health/` + contagem 35→36), `Makefile` (token `spec/health`). Edições compartilhadas listadas linha a linha no corpo; dono da linha = padrão, `medical-team` como reparo se a coordenação do programa pedir.
- Mutação em clone isolado: 6 violações, todas vermelhas. Revisão independente: 1ª rodada FAIL (exceção com YAML hostil), reparada; 2ª rodada PASS. Uma falha local nova (`test_validation_perspective.py` fixa a linha de import do CLI) reparada antes do push. `tests/unit/ci` + `tests/unit/sec` rodados sobre o head antes do push.

## 2026-11-18 ~01:30Z — BLOQUEIO #1308: flip-path-review-gate (revisão humana)

- `flip-path-review-gate` RED no head `0ff8d8ca`: o PR toca dois caminhos com dono, `.github/CODEOWNERS` (regra L247) e `Makefile` (L297), ambos `@rodaquino-OMNI` + `@Omni-Saude/security-team`. A porta exige APPROVED de membro VERIFICADO ativo do `security-team` sobre o head atual; o autor do PR é `@rodaquino-OMNI`, que é o único dono-usuário, e autoaprovação não conta. Não há caminho por push: é a separação de revisor que o próprio cabeçalho do CODEOWNERS descreve (item 3).
- Não é defeito do diff. Não comento no PR. O resto do CI segue; mando o pronto só com todos os checks verdes, então este PR depende de uma aprovação do dono/time de segurança (ou de decisão da coordenação, p.ex. separar CODEOWNERS/Makefile num PR próprio do dono e deixar este só com `spec/health/` + validador, que não exige a porta).
- Proposta para decisão: (a) o dono aprova o #1308 como membro do `security-team`; ou (b) eu retiro `.github/CODEOWNERS` e `Makefile` deste PR (o validador continua testado; `spec/health` fica fora do `validate-artifacts` até um PR do dono acrescentar o token e a linha), num segundo push. Aguardo CD.

## 2026-11-18 ~03:00Z — #1308: segundo push (CD-104, CD-106, CD-108)

- head `bef2c74586ce19e7ddee56c7c398e3d26b063d1b`, contém a main `a226697d` (merges de `22790308` e `a226697d`, sem rebase).
- CD-104: aprovação NOMINAL por pergunta no próprio banco (`approval: {medical, legal}`); vazia no sintético; banco real com pergunta sem as duas aprovações → `DS_QUESTION_APPROVAL_MISSING` (teste negativo existe; mutação mostrou 4 testes vermelhos). Lacuna do time médico registrada no corpo do PR. Linha do CODEOWNERS segue só com `security-team`.
- CD-106: nada retirado; o `flip-path-review-gate` segue vermelho por construção; o pronto vai com a frase dos caminhos.
- CD-108: contagem do CODEOWNERS fecha em 36 depois do merge. `tests/unit/ci` + `tests/unit/sec` rodados sobre o head com `22790308` (3827 passed; só as falhas de ambiente). O merge seguinte (`a226697d`, só dois arquivos do console do portal, disjuntos) foi coberto por `tests/unit/sec` + validador + CLI + contagem + teste do console (309 passed), não pela rodada inteira de `tests/unit/ci` de novo: digo isso para a coordenação decidir se basta.
- Consumo de CI: dois pushes neste PR.

## 2026-11-18 ~04:00Z — PRONTO #1308 (E-SF-25)

- head `bef2c74586ce19e7ddee56c7c398e3d26b063d1b`, contém a main `a226697d`; run 44227217199: todos os check runs verdes (2 skipped de matriz/nightly) exceto o `flip-path-review-gate`.
- Integração (live-pytest PASS): cancel-contas 138, restante 958, recurso-nip 128, cred-ans 129, reembolso 53.
- flip-path-review-gate vermelho; caminhos que o disparam: `.github/CODEOWNERS` (linha de `/spec/health/` + contagem 35→36) e `Makefile` (token `spec/health` em `validate-artifacts`). (CD-106)
- A main andou para `fd352899` (#1310 e #1311, nenhum arquivo em comum): simulação de merge sobre ela limpa, 7 arquivos. Não dei terceiro push; faço se a coordenação pedir o ramo com `fd352899`.
- Pronto enviado. Próxima depois do merge: E-SF-26 + parse dos BPMN CA (toca MAPA/INVENTARIO/baseline: CAMINHO antes de abrir, CD-80).

## 2026-11-18 ~04:20Z — #1308 incorporado; CAMINHO de E-SF-26 + parse dos BPMN CA (CD-80)

- #1308 (E-SF-25) mesclado; main `98e4e37b`.
- **CAMINHO — E-SF-26 + parse CA** (uma fatia, em série; ramo `sf-w2/mzo-gp/esf26-parse-ca`), arquivos:
  1. `docs/processes/New_BPMN/Adapted_BPMN/CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn`, `CA_3_SUPORTE_v4_adaptado.bpmn`;
  2. `scripts/bpmn-gates/baseline.json`: saem as 11 entradas `G2-IMPL` (3 CA_0, 2 CA_1, 6 CA_3) quando o deploy passar;
  3. `docs/processes/New_BPMN/Adapted_BPMN/INVENTARIO-CA.generated.json` (regenerado por `scripts/docs/gerar_inventario_ca.py`) e `MAPA-ELEMENTOS-V4.md` (linhas dos elementos novos do CA_1, contagens de §4/§7 e Changelog);
  4. testes: novo `tests/processes/test_esf26_ca1_declaracao_saude.py` (`test_ca1_cpt_decision_is_human_task`, `test_ds_node_fail_closed_until_vw0_d16`) e, se a contagem mudar, `tests/processes/test_mapa_elementos_v4_sync.py`. `tests/integration/processes/test_bpmn_engine_parse_deploy.py` não muda (lê a baseline).
  - Nada em CODEOWNERS, `PLANS.md`, decisions-log, ledger, `spec/processes/` (o corpus CA é só documento e parse), migração ou DMN.
- **Decisões que peço (desenho), padrão proposto entre parênteses:**
  a. Binding das 11 serviceTasks "Escalar…" sem implementação: (`camunda:type="external"` + `camunda:topic="corpus.ca.<id do elemento>"`, sem worker; só para o parse passar, a documentation da tarefa continua dizendo "binding de implementação não fornecido"). Alternativa: virar `userTask` — mudaria o tipo do elemento no mapa, por isso não proponho.
  b. E-SF-26 no CA_1 (que hoje é a compra de cuidado e não tem Carta nem DS): subprocesso novo "Declaração de Saúde e CPT" = Carta → nó DS → `userTask` de decisão de CPT SEM implementação automática; o nó DS é fail-closed: gateway exclusivo cujo único caminho sem ratificação vai a um fim de erro tipado (`DS_NAO_RATIFICADO`), sem efeito; sem Agravo. (Os elementos novos entram no mapa como LACUNA.) "Oferta com CPT nunca chega ao Express": não há Express no CA_1 — registro como GAP, não invento o nó.
  - Sem a CD, não abro. Começo a montar em ramo com o padrão proposto.

## 2026-11-18 ~08:30Z — E-SF-26 retomada (CD-116/117); Q-W2-MZO-GP-02

- Lido CD-116 a CD-125. Commit local de parse (`e1822aa2`, ramo `sf-w2/mzo-gp/esf26-parse-ca`, nunca enviado) descartado: ramo novo `sf-w2/mzo-gp/esf26` sobre a main `8c509a09`; CA_0, CA_3 e os bindings não entram.
- Subprocesso montado no C2 do CA_1 (decisão (b)): gateway "Oferta com CPT?" (padrão = caminho de hoje) → subprocesso "Declaração de Saúde e CPT" (Carta → gateway de ratificação cujo padrão é fim de erro `DS_NAO_RATIFICADO` → declaração → decisão de CPT humana) → junta → aceite; borda de erro do subprocesso → fim sem efeito. Só userTask, gateway e evento: o gate 2 não acusa motivo novo no CA_1.
- **Q-W2-MZO-GP-02 (bloqueia o PR, não o trabalho):** as duas entradas `G2-IMPL` do CA_1 (`C3_T_EscalarParecer`, `C3_T_EscalarRenegociar`) guardam o NÚMERO DA LINHA na mensagem; os elementos novos do C2 vêm antes delas no arquivo, então as linhas passam de 975→1049 e 1003→1077. Mesmo elemento, mesmo código, mesmo motivo — mas o gate e o teste de engine leem a linha, e CD-116 diz "baseline NÃO muda". Opções: (i) mudar só o número da linha dessas duas entradas (nada mais na baseline; teto igual); (ii) escrever o XML novo do C2 sem linhas novas antes do C3 (blocos em linha única), baseline intocada, arquivo menos legível. Padrão proposto: (i). Sigo com mapa, inventário e testes; não abro antes da CD.

## 2026-11-18 ~09:40Z — PR E-SF-26 aberto (CD-116/117/126/127)

- PR maezo-operadora #1318, ramo `sf-w2/mzo-gp/esf26`, head `0d886c7ba15dce8089002ce1b3e52d7f76f07739` (contém a main `ee1ac446`). CI em curso.
- Arquivos: `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn`, `INVENTARIO-CA.generated.json`, `MAPA-ELEMENTOS-V4.md`, `scripts/bpmn-gates/baseline.json` (SÓ o número da linha das duas entradas `G2-IMPL` do CA_1: 975→1049, 1003→1077; conjunto (arquivo, elemento, código, motivo) idêntico, 31 entradas, diff de 2 linhas), `tests/processes/test_esf26_ca1_declaracao_saude.py` (novo), `tests/processes/test_mapa_elementos_v4_sync.py` (âncoras). CA_0, CA_3 e os bindings não tocados.
- Revisão independente PASS com ressalvas; corrigidas: condição de CPT tolerante à variável ausente; nó da declaração fechado por constante `${false}` (antes, a tarefa da Carta poderia gravar a variável e abrir o caminho); nota de pareamento. Pendente declarado no PR: no processo pai, o fim "declaração indisponível" cai no padrão "sem decisão" e publica venda perdida — desfecho próprio exige elemento novo fora do caminho aceito.
- Mutação: 4 violações, todas vermelhas. Suítes: `tests/unit/ci`, `sec`, `processes` e `tests/processes` sobre o head (só falhas de ambiente). Falha de processo: uma rodada local caiu por disco cheio (pastas temporárias de pytest acumuladas); limpei só temporários meus e a rodada refeita usa `--basetemp` apagado ao fim.
- ACHADO (CD-127 item 5, para o relatório final): o gate 2 e o teste de engine identificam a pendência pelo número da linha.

## 2026-11-18 ~09:10Z — #1318: bpmn-gates vermelho pelo critério "arquivo tocado"; Q-W2-MZO-GP-03

- CI do #1318 (head `0d886c7b`): `bpmn-gates (1-5 + engine pinada)` falhou no passo "Gates 1-5 — perfil corpus-doc". O deploy só-para-parse na engine pinada PASSOU (94 passed, CA_1 incluído). Demais checks concluídos verdes; unit e integração ainda em curso.
- Causa: o workflow passa `--touched` (critério 2 do PROC-04, "0 findings nos arquivos tocados"). Arquivo `.bpmn` tocado pelo PR perde a isenção da baseline, então os 3 achados PRÉ-EXISTENTES do CA_1 contam como NOVO: `G2-IMPL C3_T_EscalarParecer` (linha 1049), `G2-IMPL C3_T_EscalarRenegociar` (linha 1077) e `G3-CORR C4_B_SemSlot` (erro `SEM_SLOT_EQUIVALENTE` capturado sem emissor). Nenhum achado vem dos elementos novos do C2. Nenhum PR tocou o CA_1 desde que o gate existe, por isso a regra não tinha aparecido.
- **Q-W2-MZO-GP-03 (bloqueia o pronto do #1318):** ficar verde exige zerar achados do CA_1 fora do escopo de E-SF-26. Opções:
  (i) exceção da coordenação para o `bpmn-gates` do #1318, como no `flip-path-review-gate`: os 3 achados são pré-existentes, a baseline segue com as mesmas 31 entradas e a engine parseia o CA_1;
  (ii) zerar os 3 no CA_1: binding nas duas serviceTasks (recusado em CD-116 a) ou virá-las `userTask` (muda o tipo no mapa), e um emissor de `SEM_SLOT_EQUIVALENTE` no C4 (elemento novo fora do caminho aceito);
  (iii) separar: um PR anterior só de saneamento do CA_1 por (ii), e E-SF-26 depois.
  Padrão proposto: (i). Não envio pronto nem dou push até a CD. Sigo acompanhando o resto do CI.
- ACHADO (relatório final): o critério "arquivo tocado" faz qualquer edição do corpus CA herdar as pendências antigas do arquivo; nem o CAMINHO nem as CDs anteriores o anteciparam.

## 2026-11-18 ~09:25Z — #1318: segundo vermelho (pino do CA_1 de outra linha), corrigível dentro do escopo

- `unit (restante)`: 1 failed, 26490 passed. `tests/unit/agents/test_esf17_ledger_compras.py::test_ca1_bpmn_inalterado_por_gtm01` fixa o sha256 do CA_1 (guarda de E-SF-17: GTM-01 não edita BPMN). O próprio comentário do pino diz que mudança legítima vinda de outro épico atualiza o pino. A integração foi pulada por causa desse job (não rodou; não é falha de shard).
- Conserto: só o pino passa a `6ad4983d…` (sha do CA_1 deste PR), com uma linha de comentário. A guarda continua: qualquer outra edição do CA_1 a deixa vermelha. O hash de custódia em `CORRECOES_BPMN_2026-10.md` é registro histórico e não muda.
- Este conserto vale em qualquer resposta à Q-W2-MZO-GP-03, por isso dou o push dele (CI completo de novo). O `bpmn-gates` continua vermelho até a CD.
- Falha de processo (relatório final): minha rodada local antes do push não incluía `tests/unit/agents`. Passa a incluir quando o diff toca o corpus CA.

## 2026-11-18 ~10:00Z — CD-131/CD-132 lidas; parado à espera de aprovação do dono

- Lidas CD-131 e CD-132. Sigo: não zero os achados do CA_1, não mando pronto, não reenvio para reacender o CI.
- O pino do CA_1 (`tests/unit/agents/test_esf17_ledger_compras.py`, só o sha256 e uma linha de comentário) está editado no disco e testado (6 passed), mas o commit no ramo `sf-w2/mzo-gp/esf26` foi negado pelo modo automático. Não contorno (CD-132 item 6). Ao ser liberado: commit do pino, merge de `c06620f0`, suítes `tests/unit/ci`, `sec`, `processes` e `agents` mais `make lint type validate-artifacts`, um push só (CD-132 item 4), e o corpo do PR com o pino e a limitação do "sem decisão" (itens 3 e 5).

## 2026-11-18 ~10:40Z — CD-133: E-SF-26 refeito em arquivo novo (só árvore de trabalho; commit à espera do dono)

- **Nome do arquivo novo (CD-133 item 1):** `docs/processes/New_BPMN/Adapted_BPMN/CA_4_DECLARACAO_SAUDE_CPT_v4_adaptado.bpmn`. O gerador do inventário exige `CA_<n>_…`, por isso CA_4. Processo `Process_CA_DECLARACAO_SAUDE`; ids com prefixo `DSC_`/`DSCS_` (`DS_` já existe no CA_0).
- Conteúdo: o mesmo subprocesso revisado (Carta → gateway de ratificação com padrão no fim de erro `DS_NAO_RATIFICADO` e caminho `${false}` → declaração → decisão de CPT humana, `governanca-clinica`), borda de erro e fim sem efeito. O gateway "Oferta com CPT?" fica como LIGAÇÃO PENDENTE na documentation da colaboração e no MAPA (§13 nova).
- Gates 1-5 com o CA_4 como arquivo tocado: `novos=0 baseline=12 obsoletos=0`. G1/G5 zero, G4 sem erro (as 2 warnings são as de antes).
- Desfeito na árvore, contra a base do ramo: CA_1, `baseline.json` e o pino de `tests/unit/agents/test_esf17_ledger_compras.py` idênticos à main (o commit do pino nunca saiu). Diff restante: CA_4 (novo), `INVENTARIO-CA.generated.json`, `MAPA-ELEMENTOS-V4.md` (§1 com linha do CA_4 e totais 379, §2, bloco do §7, total 207 CASA + 172 LACUNA, §13, Changelog), `tests/processes/test_esf26_ca4_declaracao_saude.py` (renomeado de `…ca1…`; o teste do plano `test_ca1_cpt_decision_is_human_task` vira `test_ca4_…`, porque o nome mentiria) e âncoras de `tests/processes/test_mapa_elementos_v4_sync.py` (mais a seção "13" na lista de seções de elementos).
- Local: `tests/processes`, `tests/unit/processes` e o pino: 205 passed; `--check` do inventário verde. Mutação em cópia isolada (4 violações: decisão de CPT automática, gateway aberto, código do erro trocado, CA_1 citando o processo): todas vermelhas.
- ACHADO (relatório final): a docstring de `scripts/docs/gerar_inventario_ca.py` diz "os 4 arquivos"; com o CA_4 são 5. Não toco (fora do CAMINHO); a contagem real vem do glob e está certa.
- **Bloqueio:** o commit no ramo `sf-w2/mzo-gp/esf26` segue negado pelo modo automático. Não contorno. Ao ser liberado: commit (sem force-push), merge de `c06620f0`, suítes `tests/unit/ci`, `sec`, `processes`, `agents`, `tests/processes` e `make lint type validate-artifacts`, um push só, e o corpo do PR reescrito para o arquivo novo.

## 2026-11-18 ~10:10Z — CD-134 lida

- E-SF-07: nenhum trabalho local (nem ramo, nem arquivo). A E-SF-26 da CD-133 virá sobre a main `b2540f9b` quando o commit for liberado pelo dono.

## 2026-11-18 ~12:00Z — #1318 atualizado (CD-133/138/142); CI em curso; CD-149 lida

- Dono liberou commit, merge e push no ramo esf26 (~11:00Z, nesta sessão). Push único: head `ca609e7da24b7cd91ab5e6d7ef15cf0af90e7ff4` (commit `f3e836e8` da CD-133 + merges das mains `b2540f9b` e `7fefa48d`; sem rebase nem force-push). Título e corpo do PR reescritos para o CA_4, com o histórico do PR.
- `git diff --name-only origin/main...HEAD`: CA_4 novo, `INVENTARIO-CA.generated.json`, `MAPA-ELEMENTOS-V4.md`, `tests/processes/test_esf26_ca4_declaracao_saude.py`, `tests/processes/test_mapa_elementos_v4_sync.py`. Sem CA_1 (sha256 `cd571d6d…8f79`), sem baseline, sem o teste do pino.
- Gates do próprio diff, sobre o head com `7fefa48d` (CD-138): gate BPMN com `--touched` (só CA_4) corpus-doc `novos=0 baseline=12 obsoletos=0`, sp-op `novos=0 baseline=19 obsoletos=0`; `gerar_inventario_ca --check` confere; `make lint type validate-artifacts` verde; `tests/unit/sec`+`processes`+`agents`+`tests/processes` 8450 passed; `tests/unit/ci` 3403 passed, 28 failed + 141 errors, todos na lista conhecida do ambiente, nome a nome (um da lista antiga agora passa).
- Baseline=12, não 9 (diferente do que o ROOT esperava; já explicado a ele): com o CA_1 não tocado, as 3 entradas dele voltam a ser isentas.
- Falha de processo: uma rodada local caiu por disco cheio (19G de `--basetemp` do `tests/unit/ci` + 9,6G de sobra de rodada interrompida). Apaguei só temporários meus; as suítes passaram a rodar em partes, com o `--basetemp` apagado ao fim de cada parte.
- CD-149: lida. Passivo conferido: os 6 ramos meus já mesclados (esf36-ligacao, mapa-ajustes, gtm-e-66, esf20r, esf21r, esf25) estão na lista do pacote, nada faltando. `git status` limpo. Ramo local `sf-w2/mzo-gp/esf26-parse-ca` (commit `e1822aa2`, nunca enviado): só registrado.
- Próximo: CI completo do head `ca609e7d`; verde → pronto ao ROOT com os comandos e contagens acima, e depois LIMPEZA no merge.

## 2026-11-18 ~12:20Z — CD-155 lida; estado do #1318

- Push da correção feito (~11:50Z), head `ca609e7d`. CI desse head: 37 success, 2 skipped, 0 vermelho (`bpmn-gates` incluído); 4 shards de integração em curso. Main em `7fefa48d`: 0 merges atrás. Sem push novo até o CI terminar (CD-155: um push por rodada).

## 2026-11-18 ~12:50Z — PRONTO #1318 (E-SF-26, CA_4)

- **PRONTO** enviado ao ROOT: maezo-operadora #1318, head `ca609e7da24b7cd91ab5e6d7ef15cf0af90e7ff4`. 42 success, 2 skipped, 0 vermelho; `live-pytest] PASS` em cred-ans 129, restante 965, recurso-nip 128, cancel-contas 138, reembolso 53. mergeable=true, clean.
- Arquivos: CA_4 (novo), INVENTARIO, MAPA, `tests/processes/test_esf26_ca4_declaracao_saude.py` (novo), `tests/processes/test_mapa_elementos_v4_sync.py`. CA_1, baseline e pino fora do diff.
- Gates do diff e suítes: ver a entrada das ~12:00Z.
- 1 merge atrás: #1322 (`211db979`, E-SF-07), disjunto, não trazido (CD-155/156). Simulação de merge sobre ele limpa, mesmos 5 arquivos.
- Depois do merge: LIMPEZA #1318 (CD-149), depois E-SF-05r (CAMINHO antes).

## 2026-11-18 ~12:55Z — #1318 mesclado (main `bef44a63`); LIMPEZA #1318 (CD-149)

- #1318 mesclado: main `bef44a63`, que contém o head `ca609e7da24b7cd91ab5e6d7ef15cf0af90e7ff4`.
- **LIMPEZA #1318**
  - Ramos para apagar (o dono apaga; não tento):
    - `sf-w2/mzo-gp/esf26` @ `ca609e7da24b7cd91ab5e6d7ef15cf0af90e7ff4` (remoto e local).
    - Já mesclados antes e já na lista do pacote: `esf36-ligacao`, `mapa-ajustes`, `gtm-e-66`, `esf20r`, `esf21r`, `esf25`.
  - Ramos locais nunca enviados: `sf-w2/mzo-gp/esf26-parse-ca` @ `e1822aa2` (só no meu disco, descartado; só registro, CD-149 item 6).
  - PR órfão ou substituído: nenhum. Nenhum PR meu aberto no maezo.
  - Título e corpo do #1318 descrevem o que entrou (CA_4 novo; CA_1 intocado; histórico da primeira versão).
  - Disco: apaguei os temporários meus das linhas encerradas (scratchpad com 8K). Removi sem `--force` a worktree do `gtm-e-66`, já mesclado; ela só tinha a `.venv` que criei. Restam a worktree do canal e o clone principal. `git status` limpo.
- Próxima da fila: E-SF-05r (CD-114 item 3). Antes de abrir PR, CAMINHO com os arquivos.

## 2026-11-18 ~13:30Z — CAMINHO E-SF-05r (CD-159 item 2b); sem ramo, sem push

Variante: J1 só a 1ª perna (catálogo com preço que pode ser desconhecido; preço público numérico FECHADO); sem CPF; canal presencial = fato do dono não dado (parâmetro vazio); E1/J4/J28 opção A; registros A1–A4 sem assinatura → nada liga.

**Fatos do código (main `bef44a63`)** que mudam o desenho do plano:
- Raiz de produção EXISTE (o plano dizia não localizada): `src/maezo/portal/api/production.py:36` chama `create_app(identity)` sem gateway comercial → as duas rotas são 503 hoje em produção. `create_app` (`app.py:242`) recebe gateways prontos e os guarda uma vez (`:407-412`); nenhum `read_*_admission` tem chamador em `src/`.
- `AuthenticatedSalesAdmission.transport` já recusa `valid_until` vencido e `connection_ref` divergente (`commercial_admission.py:136-143`), mas nenhum teste cobre essas duas condições. Não existe verificador concreto, nem fonte de identidade de máquina em `src/` (só o OIDC humano).
- O contrato congelado é fechado (`additionalProperties:false`) e não tem `presential_offer`, `signature_mode` nem recibo de plano-referência/aviso prévio. Criar esses campos exige contrato sucessor (todos os pinos A1–A5 movem) e eu não toco `contract-c6-4.json`. Essas pernas ficam FECHADAS por recusa.
- Nenhum check relaciona `ansRegistry` a `offerState` no catálogo público, e nada mantém o preço público desconhecido. Hoje 6 testes servem como `usable` um produto com `ansRegistry` unknown e preço numérico.
- Cobertura: `CoverageGatedCodec` (`commercial_admission.py:263-325`) já recusa cobertura não-unknown sem A4, com 7 testes negativos, mas aceita `unknown` sem `unknownReason`.
- As linhas do plano para CORE-03b não batem: o teste do sha é `test_composition_refuses_a_coverage_admission_off_the_wired_contract` (a4 :832). Achado: com A4 inválida e sem A2, a composição ignora a A4 em silêncio em vez de recusar (`:636-651`).
- CPF: zero ocorrências no caminho comercial e no contrato.

**Proposta: 3 PRs em série** (o épico inteiro num PR passaria de 15 arquivos e misturaria superfícies).

**05r-a — vitrine pública (TB-E-01-a, J1 1ª perna, REG-G1a, REG-06a).** Arquivos:
- `src/maezo/gateway/commercial_public_fences.py` (novo): wrapper de codec no padrão de `CollectiveHoldCatalogCodec`. Recusa o catálogo INTEIRO, sem filtrar (filtrar quebraria o digest e a REG-06a), quando:
  1. algum produto está `usable` com `ansRegistry` ou `operatorAnsRegistry` unknown;
  2. algum produto traz preço numérico (B2 fechada);
  3. o fato do canal presencial está ausente (parâmetro vazio por padrão).
- `src/maezo/gateway/commercial_admission.py`: instalar o wrapper em `compose_public_catalog_gateway` (perto de `:753`) e aceitar o parâmetro do fato presencial (padrão `None`).
- `tests/unit/gateway/test_esf05r_public_fences.py` (novo): `test_public_catalog_never_usable_with_unknown_ans_registry`, `test_public_price_never_numeric_while_b2_closed`, `test_online_showcase_requires_presential_fact`, `test_online_eligibility_not_hardcoded` (bytes idênticos à fonte; nenhuma lista de produto em `src/`).
- Fixtures a inverter: `tests/unit/test_commercial_c6.py`, `test_commercial_admission_a1.py`, `test_commercial_admission_a2.py`, `tests/unit/gateway/test_esf20_collective_hold.py`. Os positivos passam a usar registro conhecido, preço unknown e fato presencial injetado. `…preserves_numeric_values` vira a recusa (o preço numérico deixa de ser servido).
- Fica fechado: com o parâmetro vazio, a vitrine recusa sempre (`source_unavailable`, 503), e é o teste do fato presencial que fecha.

**05r-b — sessão, aceite, cobertura (CORE-02b, REG-17a parcial, TB-E-15-b, CORE-03b).** Arquivos:
- `src/maezo/gateway/commercial_admission.py`: `CoverageGatedCodec` passa a recusar `unknown` sem `unknownReason`; a composição recusa A4 inválida também no ramo sem A2.
- `tests/unit/test_commercial_admission_a2.py`: `test_concrete_verifier_with_stale_connection_refuses_before_body` (verificador que devolve `valid_until` vencido ou `connection_ref` alheio → 403 com corpo exato e zero leitura de corpo).
- `tests/unit/test_commercial_admission_a4.py`: `test_never_issued_to_active` e `test_unknown_without_reason_is_refused`; os testes de CORE-03b ficam como regressão, sem teste novo.
- `tests/unit/gateway/test_esf05r_acceptance.py` (novo): `test_declarations_never_in_acceptance_payload` (a tela de entrada já recusa chave `declaration*`; aqui fica pinado no `acceptance.record`).
- Fica fechado: `test_acceptance_refused_without_plano_referencia_receipt` e `test_signature_mode_other_refused_until_ratified` NÃO entram (não há campo no contrato). Hoje todo `accept` já recusa no portão RN 413 (`terms_review_required`), o que é pinado como regressão. Verificador concreto: NÃO entra (sem fonte de identidade de máquina; GAP).

**05r-c — raiz de composição (CORE-00b).** Arquivos:
- `src/maezo/gateway/commercial_record_resolver.py` (novo): guarda o caminho do registro; a cada chamada faz `read_*_admission` + `compose_*`; `FileNotFoundError` ou recusa → não ligado.
- `src/maezo/portal/api/app.py` e `src/maezo/portal/api/commercial.py`: as rotas resolvem o gateway por chamada quando há resolvedor.
- Teste novo `test_composition_root_without_record_file_keeps_both_routes_closed` em `tests/unit/test_commercial_admission_a1.py`, pelo `create_app` real.
- `production.py` NÃO muda: sem registro assinado e sem transporte de registro (GAP-RECORD-TRANSPORT), a raiz de produção segue sem resolvedor.

**Subperguntas (padrão conservador entre parênteses):**
1. Dividir em 3 PRs na ordem a → b → c (sim).
2. Vitrine com preço numérico: recusar o catálogo inteiro (sim) ou rebaixar só o campo para unknown (não: rebaixar reescreve o wire da fonte).
3. Fato presencial: parâmetro único do dono (`None` = vitrine fechada), nunca lista de produtos local (sim).
4. Verificador concreto e campos de aceite: ficam fora, como GAP de identidade de máquina e contrato sucessor (sim).
5. Inverter `…preserves_numeric_values` em `test_commercial_c6.py` (sim; o teste pertence ao fluxo C6 da W2-MZO-GP? confirmar se é de outra janela).

Nada de DMN, migração, DL, `PLANS.md`, CODEOWNERS, workflows ou contrato. Arquivos de contagem: nenhum. Começo 05r-a em ramo local `sf-w2/mzo-gp/esf05r-a`, sem push até a CD.

## 2026-11-18 ~13:50Z — PR E-SF-05r (a) aberto (CD-162/163)

- PR maezo-operadora #1327, ramo `sf-w2/mzo-gp/esf05r-a`, head `b158b334db1b591194dc0b312ef7ad3b5e7b6fd9`, base `bef44a63`. CI em curso.
- Arquivos: `src/maezo/gateway/commercial_public_fences.py` (novo), `src/maezo/gateway/commercial_admission.py` (só `compose_public_catalog_gateway` e o import), `tests/unit/gateway/test_esf05r_public_fences.py` (novo, 18 testes), `tests/unit/test_commercial_c6.py`, `tests/unit/test_commercial_admission_a1.py`, `tests/unit/test_commercial_admission_a2.py`, `tests/unit/gateway/test_esf20_collective_hold.py`.
- O corpo lista cada teste invertido (o que afirmava, o que passa a afirmar). Nenhum passou de recusa para aceite. Também registra as três lacunas (raiz de produção existe e deixa as rotas em 503; A4 inválida ignorada sem A2; cobertura `unknown` sem `unknownReason`).
- Achado de forma: o wire de fonte traz os dois códigos de registro dentro de `ansRegistry`; não há `operatorAnsRegistry`. A cerca segue o wire.
- Gates do diff: `make lint type validate-artifacts` verde; comerciais + gateway 7591 passed (1 falha que também ocorre na main); sec+processes+portal 3498 passed (2 falhas, sem Postgres); ci 3403 passed, 28 falhas e 141 erros = lista conhecida (os nomes de partição de shard falham igual na main limpa). Mutação: 4 violações reais vermelhas. Uma primeira mutação de "reescrita" sobreviveu por ser equivalente (o wire já canoniza) e foi trocada por filtro real.
- 1 merge atrás: #1321 (`98b99c80`), disjunto, não trazido (CD-155); `merge-tree` limpo.
- Falha de processo: na 1ª versão, a cerca lia um campo que o wire de fonte não tem (`operatorAnsRegistry`) e usava `assert` em src. Os testes pegaram os dois antes do commit.

## 2026-11-18 ~14:15Z — CD-170 aplicada ao #1327

- `git diff origin/main...HEAD`: nenhum `# noqa`, `# type: ignore` ou `# pragma` novo. `ruff check` e `ruff format --check` limpos nos 7 arquivos do diff.
- Testes de `tests/unit/ci` que varrem a árvore (seleção por noqa, import, fence, count, shard, partition, census, inventory, hygiene, scan, boundary, perspective, chokepoint), rodados na main limpa (`bef44a63`, clone isolado) e no ramo, comparando a linha de resumo com a mensagem: nenhuma falha aparece só no ramo, e as comuns têm mensagem idêntica. A main isolada tem erros a mais só nos testes de ledger, que dependem do histórico git do clone.
- CI do #1327 agora: 35 success, 2 skipped, 4 em curso, 0 vermelho.

## 2026-11-18 ~14:25Z — CORREÇÃO da entrada anterior (CD-170 no #1327)

- A entrada das ~14:15Z dizia que as falhas comuns tinham mensagem idêntica, mas a rodada paralela do ramo não tinha produzido o arquivo de falhas (0 linhas contra 16 da main). Afirmei sem conferir a contagem. Erro meu.
- Refeito, em série: a seleção dos testes de `tests/unit/ci` que varrem a árvore dá `1 failed, 805 passed, 15 errors` na main limpa (`bef44a63`, clone isolado) e no ramo. As 16 linhas de resumo, com mensagem e caminhos normalizados, são idênticas nos dois lados: 0 só no ramo, 0 só na main.
- Também não procede o que eu disse sobre "erros a mais na main nos testes de ledger": a diferença vinha da rodada defeituosa. Os 15 erros de ledger aparecem igual nos dois lados.
