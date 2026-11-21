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

## 2026-11-18 ~14:35Z — PRONTO #1327 (E-SF-05r parte a)

- **PRONTO** enviado ao ROOT: #1327, head `b158b334db1b591194dc0b312ef7ad3b5e7b6fd9`. 40 success, 2 skipped, 0 vermelho; `live-pytest] PASS`: cred-ans 129, cancel-contas 138, recurso-nip 128, reembolso 53, restante 965. mergeable=true, clean.
- Gates do diff e comparação da CD-170: ver as entradas das ~14:15Z (corrigida) e ~14:25Z.
- 4 merges atrás (#1321, #1323, #1325, #1324), sem arquivo em comum e sem uso do catálogo público, não trazidos. Simulação de merge real sobre `c297f507`, em worktree temporária destacada e já removida: comerciais + gateway 7655 passed (1 falha de ambiente, igual na main).
- Falha de processo: a primeira simulação deste PR foi feita num clone `--shared`, cujo `origin/main` apontava para o `main` local antigo (fast-forward, não valia). Refeita no sha real da main.
- Depois do merge: LIMPEZA #1327, depois 05r-b (CAMINHO já aceito na CD-162).

## 2026-11-18 ~14:55Z — #1327 mesclado (main `5cf8a3fb`); LIMPEZA #1327 (CD-149)

- #1327 mesclado; a main atual (`ab35f086`) contém o head `b158b334db1b591194dc0b312ef7ad3b5e7b6fd9`.
- **LIMPEZA #1327**
  - Ramos para o dono apagar:
    - `sf-w2/mzo-gp/esf05r-a` @ `b158b334db1b591194dc0b312ef7ad3b5e7b6fd9`, no remoto.
    - O mesmo ramo no LOCAL: apagar com `git branch -d` (ramo já mesclado, sem forçar) foi NEGADO pelo modo automático desta sessão. Não contornei; fica no meu disco, só registrado.
  - Ramos locais nunca enviados: nenhum novo. `sf-w2/mzo-gp/esf26-parse-ca` @ `e1822aa2` segue só registrado.
  - PR órfão ou substituído: nenhum. Nenhum PR meu aberto no maezo.
  - Título e corpo do #1327 descrevem o que entrou.
  - Disco: temporários desta linha apagados. A worktree destacada `mainu` (main limpa `bef44a63`) segue ativa só para a comparação da CD-173/174 e sai sem `--force` ao fim.
- CD-173/174: a rodada do resto de `tests/unit` sobre o head mesclado acabou (partes 1–4 com falhas, parte 5 limpa). A comparação nome a nome com a main limpa está em curso; o resultado vem na próxima entrada.

## 2026-11-18 ~15:05Z — CD-173/174: resto de tests/unit para a 05r-a; nenhuma falha causada

`tests/unit` fora do que a rodada do #1327 cobria, sobre o head mesclado `b158b334` × main limpa `bef44a63` (worktree destacada, já removida sem `--force`), com o mesmo comando (`-n 4 -rfE`) e as linhas de resumo comparadas com mensagem e caminhos normalizados:

| parte | diretórios | resultado (ramo = main) | só no ramo | só na main | comuns |
|---|---|---|---|---|---|
| 1 | a2a, adapters, agents, commercial, deploy | 46 failed, 10151 passed, 114 errors | 0 | 0 | 160 |
| 2 | dev, dmn, docs, domain, evals | 3 failed, 1167 passed, 104 errors | 0 | 0 | 107 |
| 3 | integration_support, load, ops, platform, ports | 15 failed, 7790 passed | 0 | 0 | 15 |
| 4 | qa, runtime, spec, staff_install, staff_materials, support, tools | 14 failed, 9570 passed, 5 errors | 0 | 0 | 19 |
| 5 | arquivos soltos de tests/unit (fora `test_commercial_*`) | 10 passed | — | — | — |

- As contagens de linhas batem com os totais de cada parte (a comparação viu todas as falhas).
- `tests/unit/adapters/amh/test_no_foreign_exception_escapes.py::…::test_corpus_is_not_vacuous` falha só com `-n 4` e passa sozinho (153 passed). Falha igual na main nas duas rodadas.
- Com as rodadas anteriores (comerciais, gateway, sec, processes, portal, ci), a 05r-a passou por `tests/unit` inteiro. Nenhuma falha é dela.

## 2026-11-18 ~15:40Z — 05r-b pronta no ramo local; tests/unit inteiro × main limpa; pedido de push

Ramo local `sf-w2/mzo-gp/esf05r-b` (base `ab35f086`), commit `182d7b33`, SEM push. Arquivos:
- `src/maezo/gateway/commercial_admission.py`:
  - `CoverageGatedCodec`: sem A4, cobertura `unknown` sem `unknownReason` é recusada como fato vindo da fonte (lacuna 3);
  - `compose_commercial_gateway`: as checagens de sha de A1, A3, A4 e A5 sobem para antes do retorno antecipado e recusam também sem A2 (lacuna 2). O CAMINHO falava só da A4; A1, A3 e A5 tinham o mesmo defeito e entram, só recusando mais.
- `tests/unit/test_commercial_admission_a2.py`: `test_concrete_verifier_with_stale_connection_refuses_before_body` (vencido, alheio e os dois) e um caso de controle.
- `tests/unit/test_commercial_admission_a4.py`: `test_never_issued_to_active`, `test_unknown_without_reason_is_refused`, `test_admitted_a4_passes_the_source_rendering_unchanged` e `test_off_contract_admission_is_refused_even_without_a2` (quatro dimensões).
- `tests/unit/gateway/test_esf05r_acceptance.py` (novo): `test_declarations_never_in_acceptance_payload` (accept e decline, seis chaves, no payload e no envelope), controle e a ausência de slot de declaração e de assinatura no payload congelado.

ACHADO e teste invertido: `test_commercial_admission_a4.py::test_source_asserted_coverage_stays_gated_even_with_issued_contract` era VÁCUO. O helper `issued_journey_result` embrulhava o snapshot duas vezes, o wire ficava inválido e a resposta era 202 até com A4 admitida (provado por sonda). Troquei os 5 usos por `journey_with_coverage(..., issued=True)` e removi o helper. O teste antigo agora recusa pelo gate, e o mesmo snapshot com A4 dá 200.

Verificação:
- `tests/unit` inteiro em 6 partes, ramo × main limpa `ab35f086` (worktree destacada, já removida), linhas de resumo com mensagem comparadas: partes 1–6 com 160, 107, 15, 19, 18 e 169 linhas, **0 só no ramo e 0 só na main** em todas. A parte 5 tem 25 testes a mais no ramo (os novos).
- Mutação em worktree isolada (removida): sem a regra do `unknownReason` → 1 vermelho; checagens de sha devolvidas para depois do retorno → 5 vermelhos; sem `valid_until` → 1; sem `connection_ref` → 1. Duas tentativas não aplicaram (o texto aparece duas vezes no arquivo); refeitas mirando a linha exata, sem contar o "passou" vazio.
- 2 merges atrás: #1328 (`b558963c`) e #1330 (`ff86fb4f`). Nenhum arquivo em comum, nenhum uso dos símbolos tocados; `merge-tree` limpo. O teste novo segue no shard `gateway-platform`; o pronto vai contar os 4 shards de integração da CD-176.
- Falta antes do push: `make lint type validate-artifacts` e o `ruff`/noqa da CD-170 (rodo agora).

**Pedido:** liberação do push da 05r-b (toca `commercial_admission.py` e `test_commercial_admission_a2/_a4`, como a 05r-a).

## 2026-11-18 ~16:10Z — Revisão independente da 05r-b: PASS (CD-178)

- Revisor separado, só com o diff e o objetivo, sem minhas conclusões. **VEREDITO: PASS**, sem bloqueio. Rodou a1–a10, c5, c6, `test_commercial_http.py` e `tests/unit/gateway`; 5 mutações, todas vermelhas. Confirmou que nenhum caminho passou a recusar menos e que o teste antigo do a4 agora testa o gate.
- Achados baixos, tratados antes do push (commit novo, só testes):
  1. a recusa da cobertura `unknown` sem motivo não tinha teste no caminho de recuperação `operation.get` → `test_recovered_unknown_without_reason_is_refused`; o mutante que restringe a regra ao `journey.get` ficou vermelho;
  2. eu tinha escrito "5 usos" do helper vácuo; na base eram 2, os outros 3 eram dos meus testes novos → corrigido na descrição;
  3. variável mal nomeada no teste de aceite → renomeada.
- Informativos sem mudança: a ordem das mensagens quando A2 e A1 estão ambas fora do contrato; `test_never_issued_to_active` sobrepõe um teste existente (fica como a guarda nomeada).
- Push a seguir.

## 2026-11-18 ~16:20Z — PR E-SF-05r (b) aberto (CD-178)

- PR maezo-operadora #1333, ramo `sf-w2/mzo-gp/esf05r-b`, head `1c46c31605f2bb49e22006ea3a73941f10596a7d`, base `ab35f086`. CI em curso.
- Arquivos: `src/maezo/gateway/commercial_admission.py`, `tests/unit/test_commercial_admission_a2.py`, `tests/unit/test_commercial_admission_a4.py`, `tests/unit/gateway/test_esf05r_acceptance.py` (novo).
- O corpo lista as quatro dimensões da checagem de sha sem A2, cada uma com o teste, e o teste antigo vácuo do a4 entre os alterados. Também traz a revisão independente (PASS) e o `tests/unit` inteiro contra a main limpa.
- 2 merges atrás (#1328, #1330), disjuntos, não trazidos. O pronto vai direto à coordenação do programa, com os nomes novos dos checks (CD-176/178).

## 2026-11-18 ~16:50Z — PRONTO #1333 (E-SF-05r parte b)

- **PRONTO** enviado direto à coordenação do programa (CD-178): #1333, head `1c46c31605f2bb49e22006ea3a73941f10596a7d`. 37 success, 2 skipped, 0 vermelho.
  - Unit: ledger-consumer, ledger-resto, ci, ci-pesado, gateway-platform, dev, restante.
  - Integração (`live-pytest] PASS`): recurso-nip 181, cancel-contas 138, cred-ans 129, restante 965.
  - mergeable=true, clean.
- 4 merges atrás (#1328, #1330, #1329, #1332), sem arquivo em comum nem uso dos símbolos tocados, não trazidos. Simulação de merge real sobre `6c12804c` (worktree destacada, removida): comerciais + gateway 7783 passed, 1 falha de ambiente.
- Depois do merge: LIMPEZA #1333, depois a 05r-c (CAMINHO aceito na CD-162: `production.py` não muda, as rotas seguem 503, com o teste que prova).

## 2026-11-18 ~17:00Z — #1333 mesclado (main `587a759b`); LIMPEZA #1333 (CD-149)

- #1333 mesclado; a main `587a759b` contém o head `1c46c31605f2bb49e22006ea3a73941f10596a7d`.
- **LIMPEZA #1333**
  - Ramos para o dono apagar:
    - `sf-w2/mzo-gp/esf05r-b` @ `1c46c31605f2bb49e22006ea3a73941f10596a7d`, no remoto.
    - O mesmo ramo no LOCAL: não tentei apagar, porque `git branch -d` foi negado nesta sessão na LIMPEZA #1327. Fica no meu disco, junto com `sf-w2/mzo-gp/esf05r-a`.
  - Ramos locais nunca enviados: nenhum novo (`esf26-parse-ca` @ `e1822aa2` segue só registrado).
  - PR órfão ou substituído: nenhum. Nenhum PR meu aberto no maezo.
  - Título e corpo do #1333 descrevem o que entrou.
  - Disco: worktrees de mutação, comparação e simulação removidas sem `--force`; scratchpad sem temporários; `git status` limpo. Worktrees: só o clone principal (em `main` `587a759b`) e o canal.
- Próxima: 05r-c sobre `587a759b`. `production.py` não muda (CD-163; ROOT reforçou que mudança nele passa por ele).

## 2026-11-18 ~17:50Z — 05r-c: revisão independente FAIL → corrigido → PASS

- Ramo local `sf-w2/mzo-gp/esf05r-c` (base `587a759b`), SEM push. Arquivos: `src/maezo/gateway/commercial_record_resolver.py` (novo), `src/maezo/portal/api/app.py`, `src/maezo/portal/api/commercial.py` (CAMINHO da CD-162), `tests/unit/gateway/test_esf05r_record_resolver.py` (novo), `tests/unit/test_commercial_admission_a1.py` (o teste nomeado). `production.py` intocado.
- 1ª revisão (`7dfe6cac`): **FAIL**. Bloqueante: `test_resolved_gateway_passes_through_the_legitimacy_posture` era VÁCUO; rodava com a flag desligada e passou mesmo sem a aplicação da postura em `app.py` (mutação do revisor). Não bloqueantes: postura inválida dava 503 com corpo genérico, não o fixo; leitura do registro bloqueava o loop.
- Correção (`a33abda2`):
  - o teste liga a flag com a fábrica da shadow e afirma `type(...) is LegitimacyScreenShadow`, e `CommercialGateway` com a flag desligada;
  - o invólucro foi para dentro do fail-closed do resolvedor, com teste de postura inválida → 503 de corpo fixo;
  - as rotas leem o registro via `asyncio.to_thread`.
  - Mutações: sem a postura em `app.py` → 2 vermelhos; invólucro fora do `try` → 1 vermelho.
- 2ª revisão (`a33abda2`): **PASS**. Bloqueio fechado; nenhum caminho menos restritivo; `asyncio.to_thread` seguro (estado lido no loop, resolvedor imutável, exceções contidas). Achados não bloqueantes:
  1. as funções de composição agora rodam numa thread → registro como contrato na docstring (feito depois da bateria);
  2. a checagem de tipo pós-invólucro é redundante (fica como defesa);
  3. a leitura fora do loop não tem teste (propriedade de desempenho);
  4. acoplamento de import entre módulos de teste.
- Lição: o mesmo padrão de teste vácuo da parte b (afirmar algo que o caminho padrão também satisfaz). Passo a mutar cada teste novo de postura ou invólucro antes da revisão.
- `make lint type validate-artifacts` verde; `tests/unit` × main limpa em curso (parte 1 idêntica, 160 linhas).

## 2026-11-20 ~15:05Z — retomada após o limite de uso (CD-185); 05r-c no remoto

- Disco conferido: nada sumiu. Ramo local `sf-w2/mzo-gp/esf05r-c` @ `a33abda2f3edd3661337e42f794dd299049d7da3` (revisão PASS) intacto, `git status` limpo; worktrees: clone principal e canal (a da main antiga saiu sem `--force`).
- **Push do ramo feito, sem PR** (regra da CD-185): `origin/sf-w2/mzo-gp/esf05r-c` = `a33abda2`.
- A comparação de `tests/unit` de 18/11 parou na parte 6, lado da main; as partes 1–5 tinham dado idênticas contra `587a759b`. Refaço tudo sobre a main `41e364de`: a main andou 2 merges, sem arquivo em comum com o diff; `merge-tree` limpo. Comparo o MERGE simulado (`41e364de` + `a33abda2` = `80f6b19b`) × `41e364de` limpa, nas 6 partes, mais `make lint type validate-artifacts` no merge.
- Pendente antes do PR: a docstring do contrato de thread no resolvedor (achado não bloqueante da revisão).

## 2026-11-20 ~16:15Z — PR E-SF-05r (c) aberto

- PR maezo-operadora #1341, ramo `sf-w2/mzo-gp/esf05r-c`, head `9cfd8798b5f361a34ce44865cad12764de53a708`, base `587a759b`. CI em curso.
- Arquivos:
  - `src/maezo/gateway/commercial_record_resolver.py` (novo);
  - `src/maezo/portal/api/app.py`;
  - `src/maezo/portal/api/commercial.py`;
  - `tests/unit/gateway/test_esf05r_record_resolver.py` (novo);
  - `tests/unit/test_commercial_admission_a1.py` (o teste nomeado).
  - `production.py` intocado.
- Gates do diff:
  - `tests/unit` inteiro (CD-173), merge simulado (`41e364de` + `a33abda2`) × main limpa `41e364de`: 0 falha só no merge em todas as 6 partes (160, 107, 15, 19, 18 e 169 linhas comuns);
  - `make lint type validate-artifacts` verde no merge. Na 1ª tentativa o `make` saiu com 2 porque a worktree não tinha `.venv` (o Makefile usa caminho relativo); refeito com link temporário, verde.
  - Revisão FAIL → PASS (entrada das ~17:50Z de 18/11).
  - A docstring do contrato de thread entrou depois (`9cfd8798`), só documentação; os 11 testes da parte c passam sobre ela.
- 2 merges atrás (#1334, #1335), disjuntos, não trazidos. Worktrees temporárias removidas sem `--force`; scratchpad vazio.

## 2026-11-20 ~16:55Z — PRONTO #1341 (E-SF-05r parte c)

- **PRONTO** enviado direto à coordenação do programa: #1341, head `9cfd8798b5f361a34ce44865cad12764de53a708`. 37 success, 2 skipped, 0 vermelho; `live-pytest] PASS`: recurso-nip 181, cancel-contas 138, cred-ans 129, restante 965. mergeable=true, clean.
- 3 merges atrás (#1334, #1335, #1340), sem arquivo em comum e sem uso dos símbolos tocados, não trazidos. Simulação de merge real sobre `e4d65db1` (worktree removida): comerciais + gateway + portal, 11025 passed, 1 falha de ambiente.
- Com esta, a E-SF-05r fecha as três partes (a #1327, b #1333, c #1341). Ficam fechados e registrados:
  - `presential_offer`, `signature_mode` e os recibos do aceite (exigem contrato sucessor);
  - o verificador concreto (não há fonte de identidade de máquina);
  - a ligação da raiz de produção (registro assinado e transporte do registro).
- Depois do merge: LIMPEZA #1341, depois E-SF-24 (CAMINHO antes).

## 2026-11-20 ~17:05Z — #1341 mesclado (main `bf9c84df`); LIMPEZA #1341 (CD-149)

- #1341 mesclado; a main `bf9c84df` contém o head `9cfd8798b5f361a34ce44865cad12764de53a708`. A E-SF-05r está completa (#1327, #1333, #1341).
- **LIMPEZA #1341**
  - Ramos para o dono apagar:
    - `sf-w2/mzo-gp/esf05r-c` @ `9cfd8798b5f361a34ce44865cad12764de53a708`, no remoto.
    - O mesmo ramo no LOCAL: não tentei apagar (`git branch -d` negado nesta sessão). Ficam no meu disco `esf05r-a`, `esf05r-b` e `esf05r-c`, todos mesclados.
  - Ramos locais nunca enviados: nenhum novo (`esf26-parse-ca` @ `e1822aa2` segue só registrado).
  - PR órfão ou substituído: nenhum. Nenhum PR meu aberto no maezo.
  - Título e corpo do #1341 descrevem o que entrou.
  - Disco: worktrees temporárias removidas sem `--force`; scratchpad vazio; `git status` limpo; clone principal em `main` `bf9c84df`.
- Próxima da fila: E-SF-24 → E-SF-29 → RT-02 → E-SF-11 → E-SF-35. CAMINHO da E-SF-24 antes de qualquer código.

## 2026-11-20 ~17:10Z — pendência registrada (ROOT, sobre o #1341)

- LACUNA para a linha que um dia ligar a produção, não para agora: `CommercialRecordResolver` compõe um gateway POR CHAMADA. Com transporte real (cliente HTTP), quem monta `compose_public`/`compose_commercial` tem de reutilizar o cliente ou fechá-lo; do contrário vaza uma conexão por requisição. Na próxima vez que eu tocar `src/maezo/gateway/commercial_record_resolver.py`, isso entra no contrato da docstring, com teste (por exemplo, composição que conta clientes abertos e não fechados ao fim de N chamadas).
- LIMPEZA #1341 e aviso ao funil já feitos (entrada das ~17:05Z). E-SF-24: levantamento em curso, CAMINHO a seguir.

## 2026-11-20 ~17:30Z — CAMINHO E-SF-24 (EP-PROC-07a, termo de negativa do art. 14 da RN 623)

Base: main `bf9c84df`. Ramo: `sf-w2/mzo-gp/esf24` (construo localmente enquanto espero; sem PR até a resposta).

- **Hoje**: o `SendDenialNoticeWorker` (`src/maezo/tools/workers/auth.py`) compõe o registro da negativa e fica em `entrega_manual_pendente`, sem envio. `_REQUIRED_DENIAL_FIELDS` exige justificativa clínica, CID-10 e fundamentação DUT/ROL, mas não a **cláusula contratual ou dispositivo legal** que o art. 14 da RN 623 pede. Não há render do termo nem template versionado.
- **Arquivos**:
  1. `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`: formField novo `clausula_contratual_ou_dispositivo_legal` (`requiredIf decisao_auditor == NEGAR`, `enforcedBy ... ERR_AUTH_DENIAL_INCOMPLETE`) nas 3 UTs de decisão (`UT_AnaliseMedicoAuditor`, `UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta`). O BPMN não tem entrada na baseline dos gates. Nenhum elemento, fluxo ou tópico novo.
  2. `src/maezo/tools/workers/auth.py`: `_REQUIRED_DENIAL_FIELDS` + o campo novo (o comentário passa de 9 para 12 anotações, 4 campos). Função pura `render_denial_term(record, template) -> (texto, sha256)`, chamada SÓ com `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` ligada (já existe, default False). Recusas tipadas `ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO` e `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE`. O estado continua `entrega_manual_pendente`; o registro guarda só versão e sha256 do template e o sha256 do render, nunca o texto.
  3. Template novo `spec/templates/auth/termo-negativa.v0.txt` + manifesto (versão, sha256). Sem prosa própria: só os campos do registro humano e o verbatim do art. 16 da RN 623 (reanálise pela Ouvidoria), conferido contra `docs/compliance/legal/textos-base/RN-623-2024.md`.
  4. `tests/unit/sec/test_documento_negativa_fence.py` (novo), E1–E4 com os nomes do plano: exige cláusula/dispositivo; nenhum gerador sem template versionado (allowlist módulo→template com versão + sha256); registro sem texto autoral nem "entregue"/"delivered"; render puro, determinístico (2 renders = mesmo sha256) e completo.
  5. Ajustes de fence que o campo novo obriga: `tests/unit/tools/workers/test_auth_denial_guard.py` (conjunto de formFields por UT), `phi_vars`/`test_validation_phi_completeness.py` (AUTH-001 `bpmn_declared_input` 10 → 11; **arquivo de contagem, CD-80**) e `engine_schemas` se o gerado cobrir formFields.
  6. `docs/processes/contracts/SP-OP-AUTH-001.md`: o campo novo e o render atrás da flag; seguem proibidos entregue/delivered/notice_sent.
- **Fica fechado**:
  - a flag segue desligada (D5 B) — teste: com a flag em False, nenhum render é chamado;
  - envio, PDF/PDF-A (07b, sem dependência nem licença no pyproject) e Carta (07c, J29) ficam fora;
  - `authorization_denial` (hard) intocado; nenhuma DMN, nenhum DL, nenhuma migração;
  - os gates do BPMN seguem UNVERIFIED.
- **Subperguntas (com o padrão conservador que adoto se não houver resposta)**:
  - (a) Tocar o BPMN (formField nas 3 UTs) ou completar só no worker? Padrão: tocar, porque o plano pede o espelho BPMN↔`_REQUIRED_DENIAL_FIELDS` e o próprio comentário do worker diz que o BPMN é a fonte.
  - (b) Formato do render: texto UTF-8 determinístico com sha256, sem PDF. Padrão: texto.
  - (c) `/spec/templates/` não tem dono no CODEOWNERS. Padrão: acrescentar `/spec/templates/  @rodaquino-OMNI @Omni-Saude/security-team`, uma linha. Se a coordenação preferir não tocar o CODEOWNERS, ponho o template num diretório que já tem dono.
  - (d) O campo novo NÃO é PHI (é referência de cláusula ou norma). Padrão: classificá-lo como não PHI, com a justificativa no comentário.
- Mutação: E1–E4 e o teste da flag desligada, em worktree isolada, antes da revisão.

## 2026-11-20 ~17:50Z — ADENDO ao CAMINHO E-SF-24: o campo novo não é local

O levantamento do impacto do formField novo mudou o tamanho da E-SF-24. Correções ao CAMINHO das ~17:30Z:

- **O BPMN não propaga sozinho.** Hoje o campo existe em três listas mantidas à mão, e um gate de runtime em `src/maezo/gateway/human/decision_binding.py` (~:802) exige `formFields do BPMN == allowed_inputs ∪ {auditor_id}`. Se só o BPMN mudar, a ligação do portal quebra para as 3 UTs. O campo novo obriga:
  - `src/maezo/portal/contracts/models.py` (`AllowedInput`, `AuthDecisionInputs`, `AuthJuntaInputs` e os validadores, `_INPUTS_BY_FORM`);
  - `PortalReadCommand.java` (mapa `COMPILED`) e 3 testes Java;
  - 3 GERADOS: `openapi.json`, `generated/api.ts` e `taskReadBindings.ts` (CD-80);
  - o corpus de PHI: `DISPOSITIONS`, `docs/review-queue.md`, `CORPUS_DELTA_LOG` e os pinos de linha e de contagem em `test_validation_phi_completeness.py` (27 → 30 ocorrências). **Correção**: o `bpmn_declared_input` = 10 NÃO muda, porque ele conta o rol da documentação, não os formFields;
  - cerca de 15 testes do portal e de gateway/human, mais 5 payloads NEGAR da integração.
  Na prática é uma mudança de contrato do formulário do portal, maior que a E-SF-24 inteira.
- **Fronteira jurídica.** `docs/processes/contracts/SP-OP-AUTH-001.md` (~:764) já mapeia o art. 14 assim: motivo = `justificativa_clinica`; cláusula/dispositivo = `fundamentacao_dut`. O guard já exige os dois, e o mapeamento está marcado "a confirmar pelo jurídico". Decidir que `fundamentacao_dut` NÃO basta e que é preciso um campo próprio é exatamente essa confirmação, e ela é da classe jurídica da fronteira. A janela não decide isso.
- **Novo padrão conservador** (substitui a subpergunta (a)): dividir em duas partes.
  - **E-SF-24a (sigo com esta)**: sem campo novo, sem tocar BPMN nem portal. Template v0 versionado + render puro e determinístico, atrás da flag escura (que segue desligada). O registro, com a flag ligada, leva só a versão e o sha256 do template. Testes E2, E3 e E4 + E1 pinado no mapeamento VIGENTE do contrato: o render recusa `…_FUNDAMENTO_AUSENTE` quando falta motivo ou cláusula/dispositivo (= `fundamentacao_dut`); o nome do teste diz isso. O template cita o art. 16 da RN 623 verbatim, e os canais da Ouvidoria (§1º, II) são entrada obrigatória do render (recusa tipada se faltarem). Não há dado da operadora no repositório.
  - **E-SF-24b (ponto de parada)**: o campo próprio nas 3 UTs, com a cascata acima. Fica condicionado à confirmação jurídica do mapeamento e, se confirmada, vira uma linha própria da fila com o seu próprio CAMINHO.
- As subperguntas (b), (c) e (d) seguem como estavam; a (d) fica sem objeto na 24a, que não tem campo novo.

## 2026-11-20 ~17:15Z — CD-192/193 aplicadas; PONTO DE PARADA E-SF-24b; estado da 24a

- **PONTO DE PARADA — E-SF-24b (campo próprio de cláusula ou dispositivo legal). Não construída.** É da classe (i) da fronteira (jurídico); nenhum agente decide.
  - Trecho de `docs/processes/contracts/SP-OP-AUTH-001.md` (seção R-004): "**Mapeamento a confirmar pelo juridico (COUNSEL):** motivo = `justificativa_clinica`; clausula/dispositivo = `fundamentacao_dut` (o guard de completude ja exige os dois)."
  - A pergunta ao jurídico: `fundamentacao_dut` (fundamentação na DUT/ROL) atende à "cláusula contratual ou o dispositivo legal" que o art. 14 da RN 623 manda indicar?
  - Se a resposta for "não", a mudança arrasta:
    - BPMN AUTH-001 (3 formFields, nas UTs de análise, coordenação e junta);
    - `_REQUIRED_DENIAL_FIELDS`;
    - o contrato de formulário do portal (`src/maezo/portal/contracts/models.py`: `AllowedInput`, `AuthDecisionInputs`, `AuthJuntaInputs`, `_INPUTS_BY_FORM`) e `PortalReadCommand.java` (`COMPILED`), mais 3 testes Java;
    - os gerados `openapi.json`, `generated/api.ts` e `taskReadBindings.ts`;
    - o corpus de PHI (`DISPOSITIONS`, `docs/review-queue.md`, `CORPUS_DELTA_LOG` e os pinos de `test_validation_phi_completeness.py`);
    - cerca de 15 testes de portal e de gateway/human, mais 5 payloads NEGAR da integração.
  - Vai para a lista de pedidos de linha.
- **E-SF-24a — construída localmente, sem commit nem push** (o modo automático negou o commit; aguardo a liberação do dono). Ajustes aplicados:
  - **CODEOWNERS intocado.** O template mudou para `spec/policies/ans/templates/termo-negativa.v0.txt`, porque `/spec/policies/ans/` já tem dono (security-team) e já é empacotado. **PEDIDO DE LINHA:** "dono próprio para `/spec/templates/` (templates de documento ao beneficiário)", à coordenação do programa.
  - A 1ª linha do template é um metadado (`# `, entra no sha256, não entra no termo) e diz que o mapeamento do art. 14 NÃO está confirmado pelo jurídico.
  - Verbatim byte a byte: o caput do art. 16 e cada trecho citado nos rótulos (art. 14 e art. 16, § 1º, II e III) são comparados em teste com `docs/compliance/legal/textos-base/RN-623-2024.md`. O pacote do funil NÃO tem snapshot próprio da RN 623, então não há divergência a registrar. Corrigi um rótulo do § 1º, III que não era verbatim.
  - Negativa não decidida por código: o render só compõe para NEGAR com proveniência humana (o mesmo critério do guard: `auditor_id` não vazio ou `human_approved is True`); fora disso recusa com `ERR_DENIAL_NOT_HUMAN`. Teste de que nem o render nem o worker produzem ou alteram `decisao_auditor`.
  - Flag: testes de que nenhum código de `src` chama o render nem liga a flag (AST, com controles). O teste do estado fechado declara a confirmação jurídica como pré-condição para ligar. O contrato também diz isso.
  - Fora: BPMN, portal, gerados, corpus de PHI e arquivos de contagem. Sem DL, sem migração.
- Gates locais: `make lint type validate-artifacts` rc=0; CD-170 limpo; 40 testes novos; 22 de 22 mutações mortas (worktree isolada, restaurada). Em curso: `tests/unit` inteiro × main e a revisão independente.

## 2026-11-20 ~18:00Z — E-SF-24a: revisão PASS, tests/unit × main, correções não bloqueantes

- **Revisão independente: PASS**, sem bloqueantes (sobre a árvore de ~17:10Z). Das 7 observações não bloqueantes, 4 foram corrigidas na árvore:
  1. O termo v0 NÃO traz o elemento I do art. 16, § 1º (segunda instância na Ouvidoria). A docstring e o contrato dizem isso agora, e a pré-condição para ligar a flag passou a incluir a completude do § 1º, com teste.
  2. "Mesmo critério do guard": o render é MAIS estrito (`auditor_id` só de largura zero conta como vazio). O texto foi corrigido no módulo e no contrato.
  3. Uma asserção vácua do E3 (`texto not in repr(...)`) foi trocada por uma conferência contra os valores do registro.
  4. Canal da Ouvidoria com quebra de linha (forjaria itens da lista) passa a ser recusado, com teste.
- Ficam como ACHADO no PR, não corrigidos:
  - a varredura E2 só confere a convenção de nome (`compose_vendor_notice` e `compose_provider_notices` existem fora dela);
  - o detector da flag não vê o kwarg `denial_written_notice=True` (coberto em `tests/unit/runtime/test_worker_runtime_service.py:524`);
  - o template mora em `spec/policies/ans/` (empacotado), mas o caminho de carga em runtime não foi verificado, porque ninguém chama o render.
- **tests/unit inteiro × main limpa `bf9c84df`** (6 partes; a parte `ci` foi refeita em 4 blocos por timeout nas duas árvores): 0 falha só na main, 2 falhas só no ramo, nenhuma do código novo:
  - `tests/unit/dev/test_engine_integration_runner.py::test_discovery_rejects_untracked_test_outside_integration_directory` exige checkout sem alterações tracked e cai porque a árvore ainda não tem commit. Some com o commit; confiro de novo depois dele;
  - `tests/unit/dev/test_engine_runner_final_repair.py::test_teardown_is_non_green_until_completion_and_interruptions_are_durable[normal]` é flake sob `-n auto` (pid vazio); isolado no ramo, 13 de 13 em 2 rodadas.
  - A parte com `tests/unit/sec` coletou +40 no ramo (os testes novos), todos verdes.
- Depois das 4 correções: 42 testes novos; 24 de 24 mutações mortas (duas novas: canal com quebra; rótulo vazando no registro); `make lint type validate-artifacts` rc=0; `tests/unit/sec` + `tests/unit/tools/workers` + os testes que leem o contrato do AUTH-001: 5909 passed, 27 skipped. CD-170 limpo.
- **Bloqueio:** commit e push. O modo automático negou o commit no ramo `sf-w2/mzo-gp/esf24`, e o pedido de liberação está com o dono. Não contorno.

## 2026-11-20 ~19:10Z — CAMINHO E-SF-29 (EP-PROC-06a, relógio legal de pagamento do reembolso)

Base: main `bf9c84df`. Construção em worktree própria a partir da main, sem misturar com a árvore da 24a, e só depois da CD.

**O que o levantamento mudou no tamanho.** O plano dá a 06a como M. No head ela toca:
- `SP-OP-REEMBOLSO-001` (subprocesso envolvendo análise, pendência, auditor, coordenação e registro do pagamento + BRT + 2 boundaries não-interruptivos + publish + UT);
- uma DMN nova;
- 6 arquivos com a contagem de DMN 71 → 72 (`README.md`, `test_engine_deploy.py`, `test_engine_deploy_cli.py`, `test_shadow_candidates_common.py`, `test_validation_vocabulario_saida.py`, `test_validation_phi_completeness.py`), o corpus de PHI (`CORPUS_DELTA_LOG`) e cerca de 17 pinos de linha do BPMN (o aninhamento no subprocesso desloca as linhas; a cerca de PHI e `phi_completeness.py` citam linhas);
- o catálogo fechado de 5 UTs (`staff_material_reembolso_catalogo`, e o portal se a UT for exposta);
- `aprovacoes-de-regras.yaml`, `test_dmn_dead_inputs_fence.py`, `_catalogo_prazos.py`, o contrato, o test-spec, `catalog.md` e o teste de engine.

Além disso, tocar o BPMN tira dele a isenção da baseline dos gates: as 4 entradas G4 `no-gateway-join-fork` deste arquivo (`GW_DecisaoReembolso`, `GW_DesfechoPagamento`, `GW_AvisoAprovado`, `GW_AguardarDocs`) viram NOVAS e o `bpmn-gates` reprova (o mesmo que derrubou o #1318).

**Proposta: três PRs em série** (um aberto por vez):
- **29-1 (pequeno, sem arquivo de contagem):** `tests/unit/spec/test_sp_op_reembolso_001_prazo_legal_fence.py` com C1, C2 e C3 do plano, verdes hoje (o arquivo não existe; o plano o dava como existente).
  - C1: prazo do reembolso é timer BPMN parametrizado por DMN.
  - C2: nenhum prazo de reembolso em Python ou prompt.
  - C3: nenhum caminho de timer paga ou aprova.
  - Mutados antes da revisão.
- **29-2 (refator de gates, só BPMN + baseline):** corrigir os 4 G4 com junções explícitas, sem mudar comportamento: as instâncias seguem o mesmo caminho, o que o teste de engine existente prova. Remove as 4 entradas de `scripts/bpmn-gates/baseline.json` e baixa o teto `("4","sp-op") 18 → 14` em `tests/processes/test_bpmn_gates_harness.py`. Sem isso, a 29-3 nasce vermelha.
- **29-3 (o relógio):** o desenho da 06a, com C4 (vermelho hoje) como aceite, a DMN só com catch-all DRAFT e os defaults conservadores (`P30D`, corridos, sem reinício na pendência, âncora logo após a validação).
  - O vencimento publica `agents.events.reembolso.prazo_legal_vencido` com payload só `tenant_id, protocolo_reembolso, tipo_reembolso` (via `operadora.events.publish`, sem worker novo) e cria `UT_TratarPrazoLegalVencido` (`coordenacao-reembolso`, sem campo de pagar ou negar).
  - Nada paga, nega, reduz, avisa o beneficiário ou exibe ETA. A DMN entra em `duracoes_draft_internas` (nunca visível ao comprador). Gates de engine UNVERIFIED. O deploy é OWNER-DEPLOY.

**PONTOS DE PARADA e subperguntas** (para cada uma, o padrão conservador que adoto se a CD não disser outro):
- (a) **DMN nova** é ponto de parada desta janela. Alternativa sem DMN nova: o `funnel_deadline_catalog.dmn` já tem a linha `Lei9656-art12-VI` (DRAFT; âncora `documentacao_adequada_em`; quantidade, calendário, suspensão), e um BRT com `deadline_ref="Lei9656-art12-VI"` pode alimentar o timer. Mas o catálogo não tem duração ISO nem valor de alerta, e o contrato `:139` diz que o relógio parte de `documentacao_adequada_em`, nunca da submissão. **Padrão: DMN nova `reembolso_prazo_pagamento.dmn`**, só catch-all DRAFT, que DECIDE apenas a duração ISO do vencimento (parâmetro com a fonte citada: Lei 9.656 art. 12, VI, verbatim da baseline, "DRAFT/verify jurídico"). Nenhuma linha de "o quê" (cobertura, alcance, unidade), conforme a CD-55 §5. CODEOWNERS: a janela não toca (CD-192 c). **Pedido de linha:** dono para `reembolso_prazo_pagamento.dmn`.
- (b) **Valor do alerta**: o plano o põe como pré-requisito "definido pelo dono de operações", e não há valor no pacote. **Padrão: SEM timer de alerta na 29-3**; só o vencimento. O alerta entra quando o dono de operações der o valor (mudança de dado + um boundary).
- (c) **Âncora**: os defaults do plano dizem "logo após a validação"; o contrato diz `documentacao_adequada_em`. As duas leituras divergem quando houver SOLICITAR_INFO (J1, jurídico). **Padrão:** a âncora mais cedo (entrada no subprocesso, logo após a validação), sem reinício, que nunca alerta tarde (o racional do plano). O contrato é ajustado para dizer que a âncora é provisória até J1, e o C4 pina isso.
- (d) **UT nova e portal**: **padrão:** a UT entra no catálogo de material de staff (o teste fecha o conjunto de UTs com o BPMN), mas NÃO no contrato do portal (Literal, Java, TS, openapi, `portal-admissao.json` sob `/deploy/`). Fica pedido de linha. Sem deploy, nada opera.
- (e) **Arquivos de contagem** (DMN 71 → 72 e o corpus de PHI) na 29-3: preciso da serialização da coordenação do programa (CD-80/CD-192 §4) antes de abrir.
- (f) Sequência 29-1 → 29-2 → 29-3 ou só a 29-3 com o `bpmn-gates` vermelho como exceção conhecida? **Padrão:** a série.

## 2026-11-20 ~19:30Z — E-SF-29: 29-1 montada; lista da 29-2 para a T-MZO-A; desenho da 29-3 (duração nula)

**29-1** (CD-197 item 2):
- Montada em worktree própria a partir da main `bf9c84df`, ramo local `sf-w2/mzo-gp/esf29-1`. Um arquivo novo: `tests/unit/spec/test_sp_op_reembolso_001_prazo_legal_fence.py`, com C1, C2, C3 e controles de não-vacuidade.
- 7 de 7 verdes na main; 11 de 11 mutações mortas; `make lint type validate-artifacts` rc=0; CD-170 limpo.
- **ACHADO no C1:** o timer `TD_PrazoPendencia` (`ICE_PrazoPendencia`, P5D, "DRAFT/verify") tem duração literal no BPMN, não vinda de DMN. Fica numa allowlist que só encolhe, com o motivo: é a espera operacional pela documentação, não o prazo legal, e ao vencer decide um humano (`UT_DecidirPendenciaExpirada`). O teste falha se a entrada deixar de ser literal sem sair da lista.
- **C2** tem uma allowlist do mesmo tipo para duas durações de `reembolso_turno.py`: a janela de 24h da conversa e a validade da consulta do protocolo. Nenhuma das duas é prazo do processo.
- Em curso: revisão independente e `tests/unit` inteiro × main. Commit, push e PR quando o dono liberar.

**29-2 — lista de arquivos para a T-MZO-A** (CD-198 item 2.d; não escrevo nada antes do "sem colisão"):
- `CAMINHO spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn` — junções explícitas nos 4 gateways G4 (`GW_DecisaoReembolso`, `GW_DesfechoPagamento`, `GW_AvisoAprovado`, `GW_AguardarDocs`).
  - Gateways novos e os fluxos novos entram no FIM do `bpmn:process` (depois do bloco dormente da faixa B, como o comentário de `:556-559` já faz), e o DI entra no fim do diagrama. Os fluxos existentes só trocam `targetRef` na própria linha.
  - Assim nenhuma linha anterior a ~616 desloca e os pinos de linha da cerca de PHI não mudam. Se algum mudar, eu listo.
  - Nenhum id renomeado; nenhum listener, timer, variável ou condição tocado.
- `CAMINHO scripts/bpmn-gates/baseline.json` — só PERDE as 4 entradas G4 deste arquivo.
- `CAMINHO tests/processes/test_bpmn_gates_harness.py` — teto `("4","sp-op")` 18 → 14.
- Prova de comportamento idêntico: os testes de processo e de integração do reembolso sem nenhuma mudança de expectativa; `recurso-nip` no PR.

**29-3 — desenho na variante de DURAÇÃO NULA** (CD-198 item 4, CD-199). SEM código.
- **O que muda em relação ao CAMINHO anterior:**
  - NÃO há DMN nova do Maezo. Nenhum `P30D` nem outro número de prazo em DMN, BPMN, Python ou teste do Maezo.
  - A duração e a âncora vêm da regra `OMNI-GERAL-PRZ-0009` pela interface de regras (K1).
  - A âncora é a da regra, "pedido de reembolso"; o contrato e o plano não a redefinem.
  - Os itens 4(a) e 4(c) da CD-197 caem.
- **Dados:** o processo guarda os dois instantes como DADO, `data_solicitacao` (o pedido; já existe no contrato `:80`) e `documentacao_adequada_em` (a documentação adequada; a âncora da lei, para o ponto jurídico informativo no repositório de regras). Nenhum dos dois decide nada no Maezo.
- **Consumo (a forma é da coordenação do programa):** um `businessRuleTask` sobre a tabela publicada pelo K1 (pelo padrão do repositório: `spec/processes/dmn/rco/`, pinada em `config/integrations/rco/rules.lock.json`). Ele devolve `prazo_legal.vencimento_em` (instante já calculado pela regra a partir da âncora da regra) ou NULO.
  - Se o K1 só publicar quantidade e unidade, o cálculo âncora + duração fica na expressão FEEL da tabela publicada, nunca no Maezo.
  - Hoje o K1 do repositório só traz as tabelas `rco_*` de contas/AUTH. Se a PRZ-0009 não estiver lá, a 29-3 inteira espera.
- **Estrutura no BPMN** (sem nenhuma duração própria):
  - Subprocesso embutido `SP_JanelaPrazoLegalPagamento` em volta de análise, pendência, auditor, coordenação e registro do pagamento, com um ramo paralelo "vigia".
  - O vigia passa por `GW_PrazoPublicado`: com `prazo_legal.vencimento_em` nulo, vai direto a um fim sem efeito (falha fechada: nenhum timer armado, nenhum vencimento calculado, nada exibido). Senão, um `intermediateCatchEvent` de timer `timeDate` = `${prazo_legal.vencimento_em}` → `ST_PublishPrazoLegalVencido` (`agents.events.reembolso.prazo_legal_vencido`, payload só `tenant_id, protocolo_reembolso, tipo_reembolso`, via `operadora.events.publish`) → `UT_TratarPrazoLegalVencido` (`coordenacao-reembolso`, sem campo de pagar, negar ou reduzir).
  - Os fins do ramo principal dentro do subprocesso são `terminate` do escopo do subprocesso, então o pagamento (ou qualquer desfecho) encerra o vigia: "pagamento cancela o relógio".
  - Sem timer de alerta (CD-197 4(b)). Sem `boundaryEvent` de duração, porque um boundary com duração nula não arma.
- **Arquivos previstos** (quando liberada):
  - o BPMN;
  - o contrato e o test-spec do REEMBOLSO-001 (âncora = a da regra; os dois instantes como dado; vencimento só pelo publicado);
  - `docs/processes/catalog.md`;
  - `scripts/ops/staff_material_reembolso_catalogo.py` + o teste (UT nova, fora do portal, CD-197 4(d));
  - o teste de engine (`tests/integration/processes/test_sp_op_reembolso_001.py`);
  - o corpus de PHI e os pinos de linha, se o aninhamento deslocar.
  - SEM contagem de DMN (71 continua 71) e SEM `aprovacoes-de-regras.yaml`.
- **C4 (aceite) e fronteira**, como teste:
  - sem prazo publicado não há job de timer, nem variável de vencimento, nem tarefa;
  - nenhum caminho do vigia paga, nega, reduz, avisa o beneficiário ou exibe prazo;
  - o evento não leva dado de saúde nem valor;
  - o pagamento encerra o vigia;
  - nenhum número de prazo no Maezo (C1/C2 estendidos: timer de prazo legal só por `timeDate` vindo de tabela K1).
  - O caso "prazo publicado" fica para quando o K1 trouxer a PRZ-0009, com o valor lido da tabela pinada, nunca escrito no teste.
- **Dívida a retirar depois** (CD-199 item 3; não mexo agora): a linha DRAFT `Lei9656-art12-VI` do `spec/processes/dmn/funnel_deadline_catalog.dmn`, o mesmo número fora do lugar. Sai quando o K1 trouxer a PRZ-0009.
- **Subperguntas:** (i) a forma do consumo K1 (tabela `rco/` pinada × outra interface); (ii) o terminate no escopo do subprocesso encerra também a `UT_TratarPrazoLegalVencido` se ela já existir quando o pagamento for registrado. Padrão: aceitar, porque o pagamento resolve o vencimento; a alternativa é a UT fora do subprocesso, correlacionada por mensagem.

## 2026-11-20 ~20:10Z — E-SF-29: 29-1 com PASS; 29-2 montada (re-revisão em curso); 29-3 ajustada à CD-201

**29-1** (`tests/unit/spec/test_sp_op_reembolso_001_prazo_legal_fence.py`): revisão independente FAIL → PASS em três rodadas. Bloqueantes corrigidos:
- **B1:** o C3 não entrava em subprocesso. Agora a busca entra pelo início do subprocesso e sai pela saída e pelos boundaries dele. A regra virou allowlist: depois de um timer, até uma tarefa humana, só `notify_sla_risk` e o evento `sla_breached`; qualquer outra coisa reprova, inclusive fim com mensagem.
- **B2:** o C2 não via o texto do beneficiário. Agora lê os textos do Lucas em `domain/reembolso/pedido.py` e os blocos de texto inteiros (frase em várias linhas, "30 (trinta) dias", horas, meses).
- **B3:** o C2 dependia do caminho do checkout; agora usa o caminho relativo à raiz.
- **B4:** o C3 dava falso positivo com o alerta não-interruptivo terminando dentro de um subprocesso, o desenho natural da 29-3. Corrigido; o `terminate` continua saindo do escopo, como deve.
- A dívida do P5D está pinada pelo valor.
- Resultado: 14 testes, 16 de 16 mutações mortas, ruff limpo.
- **Limites aceitos e documentados no teste:** número solto em comparação (`d > 30`), número dentro de f-string, script de catálogo fora de `src`, proximidade de 160 caracteres em texto de agente.

**29-2** (junções exclusivas):
- O BPMN ganha 4 `exclusiveGateway` de junção no fim do process, mais o DI. Só o `targetRef` de 10 fluxos muda, na própria linha. As entradas dos 4 gateways foram trocadas no lugar, com as linhas restantes viradas comentário XML, sem deslocamento nenhum; nenhum pino de linha muda.
- `baseline.json` perde só as 4 entradas; o teto vai de 18 para 14.
- Teste novo, `tests/unit/spec/test_sp_op_reembolso_001_juncoes_g4.py`: junção só exclusiva, as entradas certas, as saídas e o default de cada gateway, e o texto exato de cada condição.
- Gates `--touched`: novos=0, obsoletos=0. bpmnlint antes × depois: só somem os 4 `no-gateway-join-fork`, nenhum aviso novo. `make lint type validate-artifacts check-aprovacao-de-regras check-bpmn-error-allowlist` rc=0. 6 de 6 mutações mortas.
- A revisão confirmou o comportamento idêntico no motor: o único ramo concorrente (`BT_AlertaSla`) não chega a nenhuma junção. Ela deu FAIL só pelo DI (2 junções sobrepostas e setas terminando no gateway antigo). Corrigi e a re-revisão está em curso.
- `recurso-nip` e a integração do reembolso só rodam no CI do PR.

**29-3** — desenho ajustado à **CD-201** (revoga o item 2 da CD-199). Sem código até a regra corrigida estar na main do repositório de regras e pinada no Maezo:
- **Dois relógios**, cada um definido pela fonte por CHAVE (duração, unidade e âncora; a âncora vem de vocabulário fechado): RN 566 art. 10, contado da solicitação; Lei 9.656 art. 12, VI, contado da entrega da documentação adequada.
- No BPMN, o "vigia" vira **um vigia por relógio publicado**. Uma lista de chaves vinda da fonte alimenta um multi-instance paralelo de vigias, ou dois vigias fixos por chave; a forma fica para o código.
- Cada vigia só arma quando o instante da sua âncora existe. O segundo relógio começa por revisão humana: a `documentacao_adequada_em` é registrada por uma pessoa (campo de UT), nunca inferida.
- Os dois instantes são DADO; nenhum número de prazo no Maezo; falha fechada com chave sem publicação.
- C4 por relógio: sem publicação não há job; sem âncora registrada não há job; o pagamento encerra todos os vigias.
- Os testes C1 e C3 da 29-1 já servem para os vigias dentro do subprocesso. A allowlist do C3 ganha `agents.events.reembolso.prazo_legal_vencido` quando a 29-3 entrar.

**Ordem de PRs:** 29-1 → 29-2. A 24a está pronta antes delas. Todas esperam a liberação de commit pelo dono. Depois: RT-02 (CAMINHO antes).

## 2026-11-20 ~20:40Z — 29-2: revisão PASS

- A revisão independente da 29-2 passou de FAIL a PASS em três rodadas, só por DI:
  - duas junções sobrepostas;
  - setas que terminavam no gateway antigo;
  - uma seta que atravessava a própria origem;
  - uma seta colinear à saída da junção.
- Confirmado pela revisão:
  - processo idêntico, salvo o `targetRef` de 10 fluxos;
  - junções só XOR;
  - nenhum ramo concorrente chega a uma junção (comportamento igual no motor);
  - baseline −4 entradas e teto 18 → 14;
  - nenhum pino de linha deslocado;
  - bpmnlint só perde os 4 `no-gateway-join-fork`, sem aviso novo;
  - o DI novo ainda removeu 4 cruzamentos e 5 sobreposições que já existiam;
  - gates `--touched` novos=0.
- Faltam no ramo: `tests/unit` inteiro × main e a integração do reembolso + `recurso-nip` no CI do PR.
- As três (24a, 29-1, 29-2) aguardam a liberação de commit pelo dono. Sigo para o CAMINHO da RT-02.

## 2026-11-20 ~21:00Z — CAMINHO E-SF-RT-02 (motivo de cancelamento em lista fechada, sem saúde; E35 = B)

Base: main `bf9c84df`. Construção local em worktree própria, sem PR, até a resposta.

**O que existe em main** (levantamento):
- A E-SF-09 / EP-REG-27b está em `src/maezo/platform/saida_a_pedido/cancelamento.py` (DL-0178, PR #1282) como modelo de domínio puro, com a política `spec/policies/saida-a-pedido/pedido-cancelamento.yaml` em `NAO_RATIFICADO`. O contrato diz "sem efeito no motor; sem intake vivo da página; emissão de protocolo pendente" (`SP-OP-CANCEL-001.md:376-390`).
- Já estão prontos:
  - `Elemento(tipo="pergunta_motivo")`;
  - a guarda `GUARD_REQUIRES_REASON` (pergunta antes do protocolo ou obrigatória);
  - o passo `pos_protocolo`, vazio (`passos_pos_protocolo=()`);
  - `submissao_de_payload`, que já recusa a chave `motivo`;
  - o evento catalogado `ret.cancelamento.motivo_registrado` (só `codigo_motivo`, emissor `nenhum`);
  - `legal_bases_matrix`, que recusa `vinculo_ao_titular` para `motivo_cancelamento`.
- O `motivo_informado` (texto livre) do contrato `:81` / `cancel.py:111` não é escrito por nenhum código do cancelamento.

**Proposta (menor caminho, nada ligado):**
- `src/maezo/platform/saida_a_pedido/cancelamento.py`:
  - enum fechado `CodigoMotivoCancelamento` com os 6 códigos do RT-05 item 5 (`valor_alto`, `mudanca_de_cidade`, `outro_vinculo`, `rede_nao_atende`, `atendimento_servico`, `prefiro_nao_dizer`);
  - `passos_pos_protocolo` passa a ter um `pergunta_motivo` opcional, com pular de um toque;
  - `registrar_motivo(codigo) -> CodigoMotivoCancelamento` recusa, com código fixo e sem eco, qualquer valor fora do enum, texto livre incluído;
  - `ContagemDeMotivos`, um contador PURO em memória (dict código → inteiro), sem nenhum campo de pessoa, contrato, pedido ou protocolo, e sem emissor.
- `spec/policies/saida-a-pedido/pedido-cancelamento.yaml`: os textos da pergunta e das 6 opções, em `NAO_RATIFICADO` como o resto. Se o empacotamento travar arquivo novo, os textos ficam no mesmo arquivo.
- Testes novos, `tests/unit/processes/test_e_sf_rt_02_motivo.py`, com os três nomeados pelo plano e controles:
  - `test_motivo_nao_bloqueia_protocolo`: o protocolo e as variáveis de início do CANCEL-001 são idênticos com e sem resposta; a pergunta só aparece depois do protocolo; pular é aceito;
  - `test_motivo_texto_livre_recusado`: qualquer string fora do enum é recusada sem eco;
  - `test_motivo_sem_codigo_saude`: enum e textos passam pelo `contem_dado_de_saude` da E-SF-13, mais uma lista de termos de saúde;
  - um teste estrutural: o código nunca entra em `variaveis_de_inicio_cancel_001`, `SubmissaoCancelamento`, nem em nenhum registro com chave de pessoa ou contrato; `ContagemDeMotivos` não tem atributo de pessoa (AST e dataclass).
- `docs/processes/contracts/SP-OP-CANCEL-001.md`, na seção E-SF-09: o motivo fica fora do motor, como contagem agregada só por código. O `motivo_informado` (texto livre) segue sem produtor; recomendo retirá-lo do contrato numa linha própria. Fica como ACHADO, sem mexer agora.

**Fica fechado** (com o teste que prova):
- vínculo ao titular (RT-D1, DPO) — teste estrutural;
- **nenhuma métrica emitida**: sem `Counter` em `runtime/metrics.py`, sem evento (`motivo_registrado` segue com emissor `nenhum`), sem tabela nem migração. Medir é ligar, e nada é ligado;
- BPMN do CANCEL-001 intocado, sem variável de processo nova. O plano listava uma "variável opcional"; recuo conservador porque ela viraria dado do motor ligado à instância;
- WhatsApp/Lucas intocado (DL-0098, sem retenção; o Lucas não mostra protocolo na confirmação);
- `spec/communications/` intocado (é o registro de templates proativos, FECHADO);
- `phi_vars`/corpus de PHI e arquivos de contagem não mudam.

**Subperguntas** (com o padrão que adoto se não houver resposta):
- (a) A contagem agregada só como modelo puro, sem emissor? Padrão: sim. O emissor (métrica ou evento) é decisão da E-SF-35r e da coordenação.
- (b) Os rótulos dos 6 códigos usam o texto do RT-05 item 5, verbatim? Padrão: sim.
- (c) O `motivo_informado` no contrato: padrão, ACHADO e pedido de linha, sem editar.

## 2026-11-20 ~21:40Z — 29-1 com `tests/unit` × main limpo; RT-02 montada com revisão PASS (CD-202)

- **29-1**, `tests/unit` inteiro × main limpa `bf9c84df` (6 partes; a parte `ci` em 4 blocos): 0 falha só no ramo e 0 só na main, com as mesmas falhas da main em todas as partes. +14 passed na parte com `tests/unit/spec` (o arquivo novo).
- **RT-02**, montada em worktree própria (ramo local `sf-w2/mzo-gp/rt02`), com as condições da CD-202:
  - enum `CodigoMotivoCancelamento` com os 6 códigos e rótulos VERBATIM do RT-05 item 5 (minúsculos, como no texto);
  - pergunta opcional no passo `pos_protocolo` do modelo E-SF-09, com pular;
  - `codigo_do_motivo` só aceita `str` exato da lista e devolve o código canônico; o resto é recusado com `reason_not_in_closed_list`, sem eco (`args` fixos);
  - `ContagemDeMotivos`, pura, só `por_codigo` (lista fechada, na ordem, inteiros ≥ 0, validados no `__post_init__`), sem pessoa, contrato, pedido, protocolo, tenant ou instante, e sem emissor;
  - teste de que nenhum outro módulo de `src/` lê os nomes do bloco (nenhum uso para retenção, oferta, segmentação ou mensagem);
  - teste de saúde em códigos e rótulos;
  - BPMN, Lucas, `spec/communications` e o corpus de PHI intocados.
  - A função foi renomeada de `motivo_informado` para `codigo_do_motivo`, para não colidir com a variável de texto livre do contrato.
- Revisão independente: **PASS**. As 5 recomendações não bloqueantes aplicadas:
  - subclasse de `str` não passa;
  - a contagem valida a lista fechada na construção;
  - o nome da função;
  - a pergunta diz "escolha" em vez de "conte";
  - os controles do teste foram reforçados.
  - Não aplicada: o loader exigir as chaves novas (mesmo padrão das antigas).
- Testes: `tests/unit/processes` 142 passed; 15 de 15 mutações mortas; `make lint type validate-artifacts` rc=0; CD-170 limpo (o único `noqa` do arquivo é anterior).
- **ACHADO (pedido de linha):** `motivo_informado` (texto livre pseudonimizado, contrato `SP-OP-CANCEL-001.md:81`, `cancel.py:111`, `phi_vars.py:347`) não tem produtor no cancelamento. Recomendo retirá-lo do contrato e do worker numa linha própria.
- Em curso: `tests/unit` inteiro × main para a 29-2 e a RT-02.
- **Ordem de PRs (CD-202):** 24a → 29-1 → 29-2 → RT-02, um por vez, quando o dono liberar o commit.

## 2026-11-20 ~22:30Z — tests/unit × main para 29-2 e RT-02; PONTO DE PARADA na 29-2 (admissão do portal em `/deploy/`)

- **RT-02:** `tests/unit` inteiro × main limpa: só 1 falha exclusiva, `tests/unit/dev/test_engine_integration_runner.py::test_discovery_rejects_untracked_test_outside_integration_directory`, que exige checkout sem alterações tracked ("checkout possui alterações tracked"). É artefato da árvore sem commit (o mesmo caso da 24a), e confiro de novo depois do commit. +27 passed na parte de `tests/unit/processes`.
- **29-2:** além do mesmo teste de checkout sujo, uma **regressão real**: `tests/unit/ci/test_check_portal_admissao.py::test_repo_real_passa`.
  - O portão `scripts/ci/check_portal_admissao.py` exige que o sha256 de todo BPMN com tarefa no portal esteja admitido ou declarado em `aguardando_admissao` no espelho `deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json`.
  - O espelho já declara o REEMBOLSO-001 aguardando admissão, com o digest `05c181b9…` da main (#1149 e DEC-30 D14, ainda não implantados). As junções mudam os bytes para `ef5d529c79b4c572e62dee63b82111331d3a5a63e4c1bda63e1993d373236b94`.
  - A forma que o próprio portão prevê para "mudança de BPMN antes do corte" é trocar o digest dessa entrada `aguardando_admissao` (e acrescentar a 29-2 ao `motivo`), sem tocar `admitidos`; o `deploy-processes` dela exige repinar a admissão (runbook `material-staff-na-aws.md`, passo 5).
  - **PONTO DE PARADA:** o arquivo está em `/deploy/` (dono no CODEOWNERS) e é o espelho da admissão de deploy. A janela não edita sem resposta.
  - **Proposta:** no PR da 29-2, trocar SÓ o `process_definition_digest` da entrada `aguardando_admissao` do REEMBOLSO-001 (`05c181b9…` → `ef5d529c79b4c572e62dee63b82111331d3a5a63e4c1bda63e1993d373236b94`) e acrescentar ao `motivo` "e a E-SF-29-2 (junções exclusivas G4, sem mudança de comportamento)". Nada em `admitidos`, nada implantado, e o repin segue ato do dono no corte.
  - Alternativa: a 29-2 espera até a admissão ser repinada pelo dono, ou é feita junto com o próximo corte.
- Sem o espelho atualizado, o PR da 29-2 fica vermelho nesse portão. A 29-2 fica parada até a resposta. A 29-1 e a RT-02 não tocam BPMN e seguem prontas.

## 2026-11-20 ~22:50Z — CAMINHO E-SF-11 (REG-21; só a EP-REG-21a)

**Escopo:** só a **EP-REG-21a** ("nenhum sucesso DSR fabricado, nos dois lados"). Ficam fora, como pontos de parada já declarados no plano:
- 21b: ato do encarregado (GAP-C3), DPO;
- 21c: F-2, AF-07, legal-hold e OWNER-DEPLOY;
- 21d: freeze sucessor do C6-4, A2 e OWNER-DEPLOY.
São das classes DPO/LGPD e deploy da fronteira.

**O que existe em main** (levantamento):
- **Lado worker:** `ExecuteRequestWorker.execute` (`src/maezo/tools/workers/lgpd.py:327-478`) não tem `return`; todo caminho levanta `WorkerFailureError(retries_left=0)` ou o `LegalHoldErasureDeferredError`. Isso já é pinado por `test_execute_request_nenhuma_entrada_completa_a_tarefa` (`tests/unit/tools/workers/test_lgpd_erasure.py:556`, produto cartesiano de tipos × decisões × `human_approved`) e pela integração `test_lgpd_execution_refusal.py`. O nome `test_execute_request_no_path_completes` do plano não existe.
- **Lado comprador:** NÃO existe superfície DSR no gateway comercial nem no C6-4 (sem `privacy.*`, sem enum de status). `PUBLISHED_DSR_REQUEST_KINDS` e `CONTRACT_PENDING` não existem. O único canal é o portal do titular, `POST /dsr` (`src/maezo/portal/api/dsr.py`, DARK), cujo recibo é `DsrReceipt.status: Literal["received"]` ("recibo de estágio, nunca desfecho"). O adapter "CONTRACT_PENDING" do plano exigiria código novo, e isso é a 21d (bloqueada).

**Proposta — um PR, só testes** (molde da E-SF-12, `test_e_sf_12_nao_contato_fence.py`): arquivo novo `tests/unit/portal/test_ep_reg_21a_dsr_sem_sucesso_fabricado.py` com:
1. `test_execute_request_no_path_completes`, o nome do plano. Reusa o produto cartesiano do `:556` e, além do `raises`, prova que nada devolve dict nem `status` terminal (`execution_completed`, `atendida`, `concluida`); inclui os caminhos de legal-hold.
2. `test_buyer_dsr_view_never_fulfilled_without_source`, na forma possível hoje, sem adapter:
   - o recibo do portal só tem `status == "received"`, e o schema (`DsrReceipt`, `openapi.json`, `generated/api.ts`) não tem campo nem valor de desfecho (`fulfill*`, `done`, `atendid*`, `concluid*`, `completed`);
   - nenhuma rota GET de status de DSR existe em `portal/api`;
   - o C6-4 não tem comando DSR (só leitura do JSON, sem editar);
   - nenhum literal de desfecho DSR em `src/maezo/{gateway,adapters,portal/api}`.
   - A semântica "adapter devolve CONTRACT_PENDING" fica para a 21d, e o teste diz isso na docstring.
3. Controles de não-vacuidade em cada varredura.

**Fica fechado:**
- `lgpd.py`, `erasure.py`, BPMN/DMN do LGPD-DSR, o portal (código), os conjuntos PHI, o C6-4 e os locks: intocados;
- nenhuma habilitação de direito;
- nada ligado (o canal segue DARK).

**Subperguntas** (com o padrão que adoto se não houver resposta):
- (a) Linha no `docs/evidence-ledger.md`: é ponto de parada desta janela. **Padrão:** mando o texto da linha no relatório final, sem editar.
- (b) Incluir o arquivo novo em `SECURITY_TESTS` do `Makefile`? **Padrão:** não (o Makefile é compartilhado e não é exigido); fica como pedido de linha.
- (c) A E-SF-11 conta no placar só com a 21a? **Padrão:** a 21a entra como parcial; 21b, 21c e 21d na lista de pontos de parada.

## 2026-11-20 ~23:30Z — 29-2 PASS no diff final (CD-205); E-SF-11 em re-revisão; CAMINHO E-SF-35 (EP-CORE-06F)

- **29-2:** a 4ª revisão (diff final, com o espelho do portal) deu **PASS**.
  - `portal-admissao.json` muda só nas linhas 56–57: o digest do REEMBOLSO-001 em `aguardando_admissao` passa a `ef5d529c…` (= sha256 do BPMN) e o `motivo` foi acrescentado, sem nada removido;
  - `admitidos` e as demais chaves estão idênticos byte a byte;
  - o portão sai 0, só com o aviso esperado; `test_check_portal_admissao.py` passa;
  - nenhum outro arquivo de `/deploy/` muda; o check de CODEOWNERS de `/deploy/` deve ficar vermelho (exceção do dono, CD-205);
  - ACHADO menor: `PLANS.md:58` ainda cita `05c181b9` como registro histórico; nenhum check o lê.
- **E-SF-11 (21a):** a revisão deu FAIL com dois bloqueantes no próprio arquivo de teste, os dois corrigidos:
  - **B1:** o teste do canal escuro não via o formato `name/value` do TF/ECS e do k8s. Agora falha em QUALQUER menção a `LGPD_DSR_CHANNEL` em `deploy/` ou `config/` (hoje não há nenhuma).
  - **B2:** o detector de desfecho não via snake_case/camelCase. Agora normaliza antes e o vocabulário foi ampliado.
  - 6 testes; 11 de 11 mutações mortas (inclusive o TF `name/value` e o `request_fulfilled`). Re-revisão em curso. O teste afirma o `POST /dsr` escuro pelo default de `PortalSettings`, sem ligar nada.

**CAMINHO E-SF-35 (só a variante EP-CORE-06F):**
- **Em main** (`src/maezo/gateway/commercial_analytics_admission.py`, testes em `tests/unit/test_commercial_admission_a9.py`):
  - o loader A9 só produz `refused` (`admitted` e `deferred` são recusados);
  - `refuse_measurement_payload` recusa envelope pessoal e stub com tenant/journey fora dos enums sintéticos (`synthetic-tenant-a|b`, `synthetic-journey-a|b`);
  - nenhum registro A9 é versionado no maezo (`test_no_a9_admission_record_ships_in_the_repository`);
  - nenhuma superfície de medição, agregador ou emissor. Não existe `measurement_not_admitted` nem `operationalBaseline`.
  - O registro real do OSP não existe (ato do DPO pendente; A-INDEX `pending`). A E-SF-47 está mesclada no omni (#54, #56), mas o walk real segue NOT_RUN por falta de staging.
- **Proposta: um PR, só testes, nada em `src/`, nenhum lock nem o C6-4.** Arquivo novo `tests/unit/test_commercial_admission_a9_fallback.py`, reaproveitando `a9_record()` e `_clean_stub_payload()` (registro sintético em memória, como a suíte atual):
  - `test_refused_record_keeps_measurement_dimension_closed`: o refused parseia com `admitted_commands == []`; com ele em mãos, `refuse_measurement_payload` segue recusando `journey_measurement_key`, `dedupe_key` e `variant_id` (reasons reais `*_fabricated`); nenhuma superfície de emissão (reusa a varredura existente);
  - `test_synthetic_walk_with_real_tenant_refuses`: tenant ou journey fora dos enums sintéticos (inclusive variações de caixa e espaço) → `measurement_stub_not_synthetic`;
  - `test_synthetic_walk_result_never_labelled_baseline`: qualquer rótulo de baseline (`operationalBaseline`, `baseline`, `meta`, inclusive `"UNMEASURED"`) no payload → `measurement_stub_shape_invalid` (o stub tem exatamente 14 campos).
  - Mutação de cada teste contra o código existente.
- **Fica fechado:** sem leitura do arquivo do OSP por caminho (não existe, e versionar um registro no maezo viola o teste `:538`); sem emissor; sem walk real; sem re-pin (06.2 ratificado). Classe DPO intocada.
- **Subperguntas** (com o padrão que adoto se não houver resposta):
  - (a) Reason tipado próprio para baseline (`measurement_baseline_labelled`, exige mudar `src` + nota de divergência no `CONTRACT-FREEZE-C6-4-REGISTER.json`)? **Padrão:** não; usar `measurement_stub_shape_invalid`.
  - (b) O nome `measurement_not_admitted` do plano não existe. **Padrão:** usar os reasons reais e registrar a deriva como ACHADO.

## 2026-11-21 ~00:10Z — ESTADO DAS SEIS ÁRVORES; PARADA (CD-208)

As seis linhas estão construídas e revisadas. O commit segue negado pelo modo automático, à espera da liberação do dono. Paro aqui, sem repetir baterias. Base de todas: main `bf9c84df`.

| Ordem | Linha | Árvore (ramo local) | Arquivos | Revisão | tests/unit × main |
|---|---|---|---|---|---|
| 1 | 24a | clone principal `/home/user/maezo-operadora` (`sf-w2/mzo-gp/esf24`) | `denial_term.py` (novo), `auth.py`, `spec/policies/ans/templates/termo-negativa.v0.txt` (novo), `tests/unit/sec/test_documento_negativa_fence.py` (novo), `test_worker_handler_purity.py`, contrato AUTH-001 | PASS (+4 não bloqueantes aplicados) | feito; só o teste de checkout sujo e um flake |
| 2 | 29-1 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/esf29` (`sf-w2/mzo-gp/esf29-1`) | `tests/unit/spec/test_sp_op_reembolso_001_prazo_legal_fence.py` (novo) | PASS (3 rodadas) | feito, limpo (a lacuna do terminate foi fechada depois, só no teste) |
| 3 | 29-2 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/esf29b` (`sf-w2/mzo-gp/esf29-2`) | BPMN REEMBOLSO-001, `baseline.json`, `test_bpmn_gates_harness.py`, `tests/unit/spec/test_sp_op_reembolso_001_juncoes_g4.py` (novo), `portal-admissao.json` (2 linhas, CD-205) | PASS (4 rodadas, a última sobre o diff final) | feito; a regressão de admissão foi resolvida pela CD-205 (conferido no portão e no teste); resta o de checkout sujo |
| 4 | RT-02 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/rt02` (`sf-w2/mzo-gp/rt02`) | `cancelamento.py`, `politica.py`, `pedido-cancelamento.yaml`, contrato CANCEL-001, `tests/unit/processes/test_e_sf_rt_02_motivo.py` (novo) | PASS (+5 não bloqueantes) | feito; só o de checkout sujo |
| 5 | E-SF-11 (21a) | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/esf11` (`sf-w2/mzo-gp/esf11`) | `tests/unit/portal/test_ep_reg_21a_dsr_sem_sucesso_fabricado.py` (novo) | PASS (2 rodadas) | a rodar no commit |
| 6 | E-SF-35 (06F) | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/esf35` (`sf-w2/mzo-gp/esf35`) | `tests/unit/test_commercial_admission_a9_fallback.py` (novo) | PASS (+ recomendações aplicadas) | a rodar no commit |

- **Todas:** `make lint type validate-artifacts` rc=0; CD-170 limpo; mutação dos testes novos (24a 24/24; 29-1 16/16; 29-2 6/6; RT-02 15/15; 11 11/11; 35 6/6).
- **Teste que exige checkout sem alterações:** `tests/unit/dev/test_engine_integration_runner.py::test_discovery_rejects_untracked_test_outside_integration_directory` falha em toda árvore sem commit. Será conferido de novo depois do commit de cada linha.
- **Ao liberar:** para cada linha, na ordem: commit (mensagem simples, com as linhas de atribuição), `tests/unit` × main para 11 e 35, push, PR, CI (`live-pytest` dos shards), simulação de merge, PRONTO. CD-155: trazer a main só se houver arquivo em comum.
- **Pontos de parada em aberto:**
  - 24b (jurídico: `fundamentacao_dut` × art. 14);
  - 29-3 (regra PRZ-0009 com dois relógios no repositório de regras + pin, semana que vem);
  - 21b, 21c e 21d (DPO, AF-07/legal-hold, deploy, freeze).
- **Pedidos de linha acumulados:**
  - dono para `/spec/templates/`;
  - dono para `reembolso_prazo_pagamento.dmn` (a 29-3 está hoje sem DMN);
  - retirar `motivo_informado` do CANCEL-001;
  - `SECURITY_TESTS` para o teste da 21a;
  - linha do evidence-ledger da 21a (texto no relatório final);
  - dívida `Lei9656-art12-VI` no `funnel_deadline_catalog.dmn`.
- **Limpeza:** `mut24` é a worktree de main limpa (usada como base das comparações); `esf29`, `esf29b`, `rt02`, `esf11` e `esf35` são as árvores das linhas. Ficam até os merges; serão removidas sem `--force` na LIMPEZA de cada uma.

## 2026-11-21 ~00:30Z — patches das seis linhas (pedido do funil e da coordenação do programa)

Gerados só com leitura, sem tocar no índice: `git diff bf9c84df` dos arquivos rastreados + `git diff --no-index /dev/null` de cada arquivo novo (`node_modules` excluído). Cada um passa em `git apply --check` sobre a main limpa `bf9c84df`. Nenhum commit, push ou envio para fora.

| Linha | Caminho | sha256 | Bytes | Arquivos |
|---|---|---|---|---|
| 24a | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/patches/24a.patch` | `8bb808fcec269044e0803cb527c21c5bf9c8b95c040124b133c371cdf7e6542a` | 34793 | 6 |
| 29-1 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/patches/29-1.patch` | `ec8be0a48357689777898ac66773ef76b5f5d0521c0998eeefce3f854c6d5125` | 28015 | 1 |
| 29-2 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/patches/29-2.patch` | `c14d00f011a1edf7ba289d815aaee45a71fab26835a86955aa1dc6cfc30fb2ab` | 30506 | 5 |
| rt02 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/patches/rt02.patch` | `f416282ed665300b624083e3835efe355ad8b29d6c697725d3b381ab8f4b397b` | 20448 | 5 |
| esf11 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/patches/esf11.patch` | `31299e1a658c9c8b8984f80df37884b3821bbae0ed08bb3871849162a4a6afbe` | 13761 | 1 |
| esf35 | `/tmp/claude-0/-home-user/5fe95728-62c1-5ca8-bc73-97817e534745/scratchpad/patches/esf35.patch` | `c04d275040516b86262c65fcc5567503c359fbac1bef7804a961625f4e239095` | 7024 | 1 |

- **Destino durável fora do repositório:** não tem. O scratchpad é da sessão e some quando o contêiner é reciclado. Não envio código para nenhum outro lugar.
- Volto a ficar parada.

## 2026-11-21 ~01:00Z — commit liberado; seis ramos no remoto; PR #1343 (24a) aberto

- O commit da 24a passou na tentativa única proposta pelo funil e pela coordenação do programa: mesmo comando, mesmo ramo e mesmo clone, só **sem a linha de coautoria com identificador de modelo** (regra do dono no brief). O link de sessão foi mantido; não é identificador de modelo.
- Commits e push (CD-185), todos com a árvore limpa depois:
  - `sf-w2/mzo-gp/esf24` @ `43462269fa204566594bf0f523b8e35051856b42`
  - `sf-w2/mzo-gp/esf29-1` @ `b15a3ecefef8b6bb720a074503089b686ec49189`
  - `sf-w2/mzo-gp/esf29-2` @ `ab611ff733badf6b1d6b7c6aeb76f88f643bfe6c`
  - `sf-w2/mzo-gp/rt02` @ `34b2803376126783802a184f3e852bbd7c26a0e6`
  - `sf-w2/mzo-gp/esf11` @ `73aedfaee5bb942f9d74dc405af4f64b9fb7a0f0`
  - `sf-w2/mzo-gp/esf35` @ `290594618a5d4fb4d4353252700182bd1fb07de0`
- **PR #1343 (E-SF-24a)** aberto. Um merge atrás (#1342, sem arquivo em comum; CD-155: não trazido). O teste de checkout limpo agora passa. Aguardo o CI para o PRONTO.
- Em curso: `tests/unit` × main para a 11 e a 35. Os PRs das demais saem um por vez, na ordem 29-1 → 29-2 → RT-02 → 11 → 35.

## 2026-11-21 ~00:05Z (UTC do contêiner: 2026-11-20 23:55Z) — PRONTO #1343 (E-SF-24a)

- **PRONTO:** #1343, head `43462269fa204566594bf0f523b8e35051856b42`.
  - CI verde, menos o `flip-path-review-gate`, esperado: o template está em `/spec/policies/ans/` (CODEOWNERS L238) e precisa da aprovação do security-team ou da exceção do dono, como no #1308. Está registrado no corpo do PR.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
  - Unit: ci, ci-pesado, ledger-resto, ledger-consumer, gateway-platform, dev e restante, todos verdes. `integration tests (real engine)` verde.
- **Simulação de merge real** numa worktree destacada no sha da main `b86b3d822fd8020858eeee288472322509dba519`: merge sem conflito; `make lint type validate-artifacts` rc=0 (com link temporário da `.venv`, removido); `tests/unit/{sec,tools/workers,spec,platform}` 14288 passed, 15 failed. As 15 são as mesmas falhas de ambiente local da main de base (`fail-main-4.txt`). Worktree removida sem `--force`.
- **1 merge atrás** (#1342, autonomy loaders): sem arquivo em comum, não trazido (CD-155).
- **Arquivos:** `src/maezo/tools/workers/denial_term.py` (novo), `src/maezo/tools/workers/auth.py`, `spec/policies/ans/templates/termo-negativa.v0.txt` (novo), `tests/unit/sec/test_documento_negativa_fence.py` (novo), `tests/unit/tools/workers/test_worker_handler_purity.py`, `docs/processes/contracts/SP-OP-AUTH-001.md`.
- **Próximo PR**, depois do merge deste: 29-1 (ramo já no remoto).

## 2026-11-20 ~23:55Z — #1343 mesclado (main `0e8b0bed`); LIMPEZA #1343; PR #1345 (29-1) aberto

- #1343 (E-SF-24a) mesclado; a main `0e8b0bed` contém o head `43462269`.
- **LIMPEZA #1343 (CD-149):**
  - **Ramo para o dono apagar:** `sf-w2/mzo-gp/esf24` @ `43462269fa204566594bf0f523b8e35051856b42`, no remoto. O mesmo ramo local fica no meu disco, só registrado (`git branch -d` negado nesta sessão).
  - Ramos locais nunca enviados: nenhum novo.
  - PR órfão: nenhum.
  - Título e corpo do #1343 descrevem o que entrou (o corpo registra o `flip-path-review-gate` esperado).
  - **Disco:** o clone principal está em `main` `0e8b0bed`, limpo. A worktree `sim24` foi removida. O check-in agendado foi cancelado.
- **PR #1345 (E-SF-29-1)** aberto: head `b15a3ecefef8b6bb720a074503089b686ec49189`, só o arquivo de teste novo; 2 merges atrás (#1342, #1343), sem arquivo em comum, não trazidos. Aguardo o CI.
- Fila: 29-2 → RT-02 → 11 → 35, um PR por vez.
- **Linha do evidence-ledger da E-SF-24a** (texto para quem escreve o ledger; a janela não edita): "E-SF-24a (EP-PROC-07a): termo da negativa (RN 623 art. 14/16) com template v0 versionado em `spec/policies/ans/templates/` e render puro atrás de `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` (DARK); registro só com versão e sha256 do template; mapeamento do art. 14 não confirmado pelo jurídico (pré-condição para ligar). PR #1343."

## 2026-11-21 ~00:45Z — PRONTO #1345 (E-SF-29-1)

- **PRONTO:** #1345, head `b15a3ecefef8b6bb720a074503089b686ec49189`.
  - 37 checks verdes (inclusive o `flip-path-review-gate`) e 2 skipped. `integration tests (real engine)` verde.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
  - Unit: ci, ci-pesado, ledger-resto, ledger-consumer, gateway-platform, dev e restante, todos verdes.
- **Simulação de merge real** numa worktree destacada no sha da main `faea2f74660b13449266f47b80a3018a626b901f`: sem conflito; `make lint type validate-artifacts` rc=0 (link temporário da `.venv`, removido); `tests/unit/spec` 866 passed. Worktree removida sem `--force`.
- **3 merges atrás** (#1342, #1343, #1344), sem arquivo em comum, não trazidos (CD-155).
- **Arquivo:** `tests/unit/spec/test_sp_op_reembolso_001_prazo_legal_fence.py` (novo; único).
- **Próximo**, depois do merge: 29-2 (ramo no remoto; condições da T-MZO-A e o digest em `aguardando_admissao`, CD-205).

## 2026-11-21 ~01:10Z — #1345 mesclado (main `4a420ba3`); LIMPEZA #1345; PR #1346 (29-2) aberto

- #1345 (E-SF-29-1) mesclado; a main `4a420ba3` contém o head `b15a3ece`.
- **LIMPEZA #1345 (CD-149):**
  - **Ramo para o dono apagar:** `sf-w2/mzo-gp/esf29-1` @ `b15a3ecefef8b6bb720a074503089b686ec49189`, no remoto. O ramo local fica só registrado.
  - Ramos locais nunca enviados: nenhum novo.
  - PR órfão: nenhum.
  - Título e corpo do #1345 fiéis.
  - **Disco:** worktree `esf29` removida sem `--force`; check-in agendado cancelado; clone principal em `main` `4a420ba3`.
- **PR #1346 (E-SF-29-2)** aberto: head `ab611ff733badf6b1d6b7c6aeb76f88f643bfe6c`.
  - 4 merges atrás (#1342–#1345), sem arquivo em comum, não trazidos (CD-155).
  - Corpo conforme a CD-205: só a linha do digest em `aguardando_admissao` (`05c181b9…` → `ef5d529c…`) mais uma frase no `motivo`; `admitidos` byte-idêntico; NÃO admite nada.
  - Checagem de CODEOWNERS em `/deploy/` esperada vermelha.
  - Condições da T-MZO-A: junções XOR; ids e condições intactos.
  - Aguardo o CI.
- Fila: RT-02 → 11 → 35, um PR por vez.

## 2026-11-21 ~01:45Z — PRONTO #1346 (E-SF-29-2)

- **PRONTO:** #1346, head `ab611ff733badf6b1d6b7c6aeb76f88f643bfe6c`.
  - 37 checks verdes e 2 skipped. `integration tests (real engine)` e `bpmn-gates (1-5 + engine pinada)` verdes.
  - Único vermelho: `flip-path-review-gate`, o esperado da CD-205. `deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json` pede aprovação do dono explícito do caminho ou de membro ativo do security-team no head atual.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
- **Simulação de merge real** numa worktree destacada no sha da main `4a420ba38ef78fd6e937a93d8e651c0caaa4af03`:
  - Merge sem conflito. `make lint type validate-artifacts` rc=0, com link temporário da `.venv`, removido.
  - `tests/unit/spec` + `tests/processes/test_bpmn_gates_harness.py`: 896 passed. A main tem 886; os 10 a mais são os testes novos.
  - Na primeira rodada, 8 testes de resolução de caminho falharam porque a instalação editável da `.venv` aponta para o `src` do clone principal. Com o `src` da simulação na frente do path, tudo passa. É artefato de ambiente, não do PR.
  - Worktree removida sem `--force`.
- **4 merges atrás** (#1342–#1345), sem arquivo em comum, não trazidos (CD-155).
- **Próximo PR**, depois do merge deste: RT-02 (ramo no remoto).

## 2026-11-21 ~01:50Z — #1346 mesclado (main `7bc00ce1`); LIMPEZA #1346; PR #1348 (RT-02) aberto

- #1346 (E-SF-29-2) mesclado; a main `7bc00ce1` contém o head `ab611ff7`.
- **LIMPEZA #1346 (CD-149):**
  - **Ramo para o dono apagar:** `sf-w2/mzo-gp/esf29-2` @ `ab611ff733badf6b1d6b7c6aeb76f88f643bfe6c`, no remoto. O ramo local fica só registrado.
  - Ramos locais nunca enviados: nenhum novo.
  - PR órfão: nenhum.
  - Título e corpo do #1346 fiéis.
  - **Disco:** worktree `esf29b` removida sem `--force`; check-in cancelado; clone principal em `main` `7bc00ce1`.
- **PR #1348 (E-SF-RT-02)** aberto: head `34b2803376126783802a184f3e852bbd7c26a0e6`.
  - 5 merges atrás (#1342–#1346), sem arquivo em comum, não trazidos (CD-155).
  - Nenhum caminho do PR tem dono no CODEOWNERS.
  - Aguardo o CI.
- Fila: 11 → 35, um PR por vez.

## 2026-11-21 ~02:45Z — PRONTO #1348 (E-SF-RT-02)

- **PRONTO:** #1348, head `34b2803376126783802a184f3e852bbd7c26a0e6`; `mergeable_state` = clean.
  - 37 checks verdes e 2 skipped, nenhum vermelho.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
- **Simulação de merge real** numa worktree destacada no sha da main `3757ca3d7b193b5be0d3f290f34b0651b7398f75`:
  - Merge sem conflito. `make lint type validate-artifacts` rc=0, com link temporário da `.venv`, removido.
  - `tests/unit/{processes,platform}`: 7714 passed, 15 failed. As 15 são as mesmas na main local, nos testes `test_a2a_outbox_relay_deployment` e `test_webhook_receiver_whatsapp_env` (falha de ambiente local).
  - Worktree removida sem `--force`.
- **6 merges atrás** (#1342–#1347), sem arquivo em comum, não trazidos (CD-155).
- **Próximo PR**, depois do merge deste: E-SF-11 (21a; ramo no remoto).

## 2026-11-21 ~03:10Z — #1348 mesclado (main `7ae9092b`); LIMPEZA #1348; PR #1351 (E-SF-11) aberto

- #1348 (E-SF-RT-02) mesclado; a main `7ae9092b` contém o head `34b28033`.
- **LIMPEZA #1348 (CD-149):**
  - **Ramo para o dono apagar:** `sf-w2/mzo-gp/rt02` @ `34b2803376126783802a184f3e852bbd7c26a0e6`, no remoto. O ramo local fica só registrado.
  - Ramos locais nunca enviados: nenhum novo.
  - PR órfão: nenhum.
  - Título e corpo do #1348 fiéis.
  - **Disco:** worktree `rt02` removida sem `--force`; check-in cancelado; clone principal em `main` `7ae9092b`.
- **PR #1351 (E-SF-11 / EP-REG-21a)** aberto: head `73aedfaee5bb942f9d74dc405af4f64b9fb7a0f0`, só o arquivo de teste novo.
  - 7 merges atrás (#1342–#1348), sem arquivo em comum, não trazidos (CD-155).
  - Aguardo o CI.
- Fila: 35 por último.

## 2026-11-21 ~03:35Z — CAMINHO E-SF-24b (parcial, CD-231)

Ramo `sf-w2/mzo-gp/esf24b`, a partir da main. O PR só abre depois do merge do #1351 (um PR por vez). Toca `spec/policies/ans/templates/` (CODEOWNERS), então o `flip-path-review-gate` deve ficar vermelho, como no #1343.

**(A) Template v1**
- Arquivo novo `spec/policies/ans/templates/termo-negativa.v1.txt`; o v0 fica byte-idêntico.
- O v1 mantém os rótulos do v0, todos tirados da própria norma, e acrescenta um bloco próprio com título: "RN 623/2024, art. 16, § 1° — elementos mínimos:".
- O bloco traz os incisos I, II e III verbatim. Logo abaixo do II vêm os canais da Ouvidoria, e logo abaixo do III o prazo, que são dados da operadora.
- Leio o pedido como "o § 1º, I, e com ele os três incisos". O I é o único elemento que faltava no v0.
- Nenhuma prosa própria. As linhas `# ` de metadado continuam, agora dizendo que o fundamento vem do catálogo e que o parecer jurídico está pendente.
- Teste novo de igualdade byte a byte: extrai do texto-base versionado (`docs/compliance/legal/textos-base/RN-623-2024.md`) as linhas "I - …", "II - …" e "III - …" do art. 16, § 1°, e exige cada uma idêntica no v1. Também exige o caput do art. 16 idêntico.
- `DENIAL_TERM_TEMPLATE` passa a ser v1 com o sha256 fixado, e a composição recusa o v0 (`…_TEMPLATE_NAO_VERSIONADO`).
- O registro da negativa (`SendDenialNoticeWorker`) continua levando só a versão e o sha256 do template, agora os do v1.
- A flag `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` continua desligada, e o teste que fixa o desligado fica intacto.

**(B) Porta do fundamento**
- Novo `FundamentoDoTermo` (dataclass fechada e frozen) com os campos `id_regra`, `versao_regra`, `motivo_texto`, `clausula_contratual`, `dispositivo_legal` e `hash_do_catalogo`.
  - `id_regra`, `versao_regra`, `motivo_texto` e `hash_do_catalogo` são obrigatórios e não podem ser vazios.
  - Pelo menos um de `clausula_contratual` e `dispositivo_legal` é obrigatório, porque o art. 14 diz "a cláusula contratual ou o dispositivo legal".
  - Nenhum campo aceita quebra de linha.
- O pino do catálogo, `CATALOGO_DE_FUNDAMENTOS_SHA256`, é `None` hoje: o catálogo não existe e vai vir do repositório de regras.
- `render_denial_term` ganha o parâmetro obrigatório `fundamento: FundamentoDoTermo`. A recusa é tipada:
  - `ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO` enquanto o pino é `None`;
  - `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO` para tipo errado, campo vazio ou hash diferente do pino.
- `fundamentacao_dut` deixa de ser lida pela composição. Mesmo preenchida, não substitui o fundamento: há teste para isso.
- O motivo no termo é `motivo_texto` seguido da `justificativa_clinica` do auditor como complemento, sem rótulo próprio. A justificativa continua obrigatória porque a negativa é humana.
- Testes:
  - a porta está fechada por construção: com o pino `None`, qualquer `FundamentoDoTermo`, mesmo perfeito, é recusado;
  - com um pino sintético só no teste (monkeypatch), o caminho positivo compõe o v1 e as recusas por campo valem.
- `_human_denial` e `ERR_DENIAL_NOT_HUMAN` não mudam, nem a decisão de negar.

**Também**
- `test_documento_negativa_fence.py` passa a usar o v1 e o fundamento.
- `SP-OP-AUTH-001.md` ganha a seção v1 e porta.
- Nada de dados do repositório de regras; nada ligado.
- Fica fora desta entrega e continua na pré-condição de ligar: o parecer jurídico e o catálogo revisado.

## 2026-11-21 ~04:00Z — PRONTO #1351 (E-SF-11 / EP-REG-21a)

- **PRONTO:** #1351, head `73aedfaee5bb942f9d74dc405af4f64b9fb7a0f0`; `mergeable_state` = clean.
  - 37 checks verdes e 2 skipped, nenhum vermelho.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
- **Simulação de merge real** numa worktree destacada no sha da main `90e7a841dcb707c35db953015f8d1c5819e07469`:
  - Merge sem conflito. `make lint type validate-artifacts` rc=0, com link temporário da `.venv`, removido.
  - `tests/unit/portal`: 3228 passed, 10 skipped.
  - Worktree removida sem `--force`.
- **9 merges atrás** (#1342–#1350), sem arquivo em comum, não trazidos (CD-155).
- **Próximo PR**, depois do merge deste: E-SF-24b (CD-231).
  - O ramo `sf-w2/mzo-gp/esf24b` vai ao remoto quando o `tests/unit` completo × main terminar.
  - Revisão independente: PASS na terceira rodada. As duas primeiras deram FAIL e foram corrigidas: o complemento do auditor podia forjar rótulo do art. 14.
  - Mutação: 17/17 e depois 7/7 + 2/2, todas mortas.

## 2026-11-21 ~04:05Z — #1351 mesclado (main `5950cc4d`); LIMPEZA #1351

- #1351 (E-SF-11 / EP-REG-21a) mesclado; a main `5950cc4d` contém o head `73aedfae`.
- **LIMPEZA #1351 (CD-149):**
  - **Ramo para o dono apagar:** `sf-w2/mzo-gp/esf11` @ `73aedfaee5bb942f9d74dc405af4f64b9fb7a0f0`, no remoto. O ramo local fica só registrado.
  - Ramos locais nunca enviados: nenhum novo (o `sf-w2/mzo-gp/esf24b` vai ao remoto antes do PR).
  - PR órfão: nenhum.
  - Título e corpo do #1351 fiéis.
  - **Disco:** worktree `esf11` removida sem `--force`; check-in cancelado; clone principal em `main` `5950cc4d`.
- **Próximo:** E-SF-24b. Antes do PR, aplico o ajuste do ROOT: o motivo do catálogo e o complemento do auditor são recusados, com erro tipado, se trouxerem qualquer rótulo do template ("RN 623/2024, art.") ou quebra de linha. O PR abre quando a suíte completa × main fechar. Depois: E-SF-35.

## 2026-11-21 ~04:10Z — PR #1355 (E-SF-35) aberto (CD-240)

- A LIMPEZA #1351 já está gravada na entrada anterior (commit `c6e5e0cb`).
- Usei a vaga do teto com o que estava pronto primeiro: **PR #1355 (E-SF-35 / EP-CORE-06F)**, head `290594618a5d4fb4d4353252700182bd1fb07de0`, só o arquivo de teste novo.
  - 10 merges atrás (#1342–#1351), sem arquivo em comum, não trazidos (CD-155).
  - Aguardo o CI.
- **E-SF-24b:** o ramo local `sf-w2/mzo-gp/esf24b` @ `8099ca98` ainda não foi ao remoto. O PR abre depois do merge do #1355.
  - Já aplicada a recusa de rótulo do template pedida pelo ROOT.
  - Aguardo a revisão independente dessa última mudança e o `tests/unit` completo × main.

## 2026-11-21 ~04:55Z — PRONTO #1355 (E-SF-35 / EP-CORE-06F)

- **PRONTO:** #1355, head `290594618a5d4fb4d4353252700182bd1fb07de0`; `mergeable_state` = clean.
  - 37 checks verdes e 2 skipped, nenhum vermelho.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
- **Simulação de merge real** numa worktree destacada no sha da main `fb748b0c892de99cf4c3db2cf96dab261ddc018b`:
  - Merge sem conflito. `make lint type validate-artifacts` rc=0, com link temporário da `.venv`, removido.
  - Os dois arquivos A9 + `tests/unit/gateway`: 6771 passed, 1 failed. A main dá 6603 passed com a mesma falha: `test_audit_anchor.py::test_worm_store_read_only_mode_is_not_integrity_against_the_owner`, que exige processo sem privilégio, e aqui o processo roda como root (ambiente local).
  - Worktree removida sem `--force`.
- **12 merges atrás** (#1342–#1354), sem arquivo em comum, não trazidos (CD-155).
- **Próximo PR**, depois do merge deste: E-SF-24b.
  - Revisão independente PASS em `b6f176d7`, já com a recusa de rótulo do template na forma visível (NFKC, sem invisíveis).
  - Sem arquivo em comum com a main `fb748b0c`.

## 2026-11-21 ~05:10Z — #1355 mesclado (main `10a6a602`); LIMPEZA #1355; PR #1359 (E-SF-24b) aberto

- #1355 (E-SF-35) mesclado; a main `10a6a602` contém o head `29059461`.
- **LIMPEZA #1355 (CD-149):**
  - **Ramo para o dono apagar:** `sf-w2/mzo-gp/esf35` @ `290594618a5d4fb4d4353252700182bd1fb07de0`, no remoto. O ramo local fica só registrado.
  - Ramos locais nunca enviados: nenhum.
  - PR órfão: nenhum.
  - Título e corpo do #1355 fiéis.
  - **Disco:** worktree `esf35` removida sem `--force`; check-in cancelado; clone principal em `main` `10a6a602`.
- **PR #1359 (E-SF-24b parcial, CD-231)** aberto: head `b6f176d7ff647400f2ccbbd1583e452b23aca9b2`.
  - 13 merges atrás, sem arquivo em comum, não trazidos (CD-155).
  - Sem DL.
  - O corpo registra: flag desligada, pino do catálogo `None`, nomes provisórios até o contrato-k2, receita do `hash_entrada` a fixar no k2, e o `flip-path-review-gate` vermelho esperado.
- Aguardo o CI. Esta é a última linha da fila; a 29-3 segue bloqueada.

## 2026-11-21 ~06:00Z — PRONTO #1359 (E-SF-24b parcial, CD-231)

- **PRONTO:** #1359, head `b6f176d7ff647400f2ccbbd1583e452b23aca9b2`.
  - 36 checks verdes e 2 skipped, todos completos; unit e CodeQL verdes.
  - Único vermelho: `flip-path-review-gate`, o esperado. `spec/policies/ans/templates/termo-negativa.v1.txt` (regra `/spec/policies/ans/`) pede aprovação de dono qualificado no head atual. Por isso o `mergeable_state` aparece como blocked.
  - `live-pytest] PASS`: recurso-nip passed=181; cancel-contas passed=138; cred-ans passed=129; restante passed=965.
- **Simulação de merge real** numa worktree destacada no sha da main `30960cd16055447fe3dc7471d00ad83b6a1d5f85` (já com o #1358):
  - Merge sem conflito. `make lint type validate-artifacts` rc=0, com link temporário da `.venv`, removido.
  - `tests/unit/sec` + `tests/unit/tools/workers`: 5940 passed.
  - Worktree removida sem `--force`.
- **`tests/unit` completo × main** (6 lotes): nenhuma falha só do ramo; as falhas comuns são de ambiente (sem `helm`, clone raso, `uv` offline, processo root).
- Sem arquivo em comum com a main; não trazida (CD-155). Sem novo push, conforme pedido do ROOT.
- **Pré-condições da unidade que pinar o catálogo** (pedidas pelo ROOT; vão no relatório final para a T-MZO-A): pertinência ao catálogo, cerca para o chamador honrar `modo`, e as lacunas restantes da recusa de rótulo.

## 2026-11-21 ~06:05Z — #1359 mesclado (main `dcfedd29`); LIMPEZA #1359; RELATÓRIO FINAL da fila

### LIMPEZA #1359 (CD-149)
- #1359 (E-SF-24b parcial) mesclado pela exceção do dono para o flip-path; a main `dcfedd29` contém o head `b6f176d7`.
- **Ramo para o dono apagar:** `sf-w2/mzo-gp/esf24b` @ `b6f176d7ff647400f2ccbbd1583e452b23aca9b2`. O ramo local fica só registrado.
- Ramos locais nunca enviados: nenhum. PR órfão: nenhum. Título e corpo do #1359 fiéis.
- **Disco:** worktree `esf24b` removida sem `--force`; nenhuma worktree temporária restante, só o clone principal (`main` `dcfedd29`) e a do canal; check-in cancelado.
- Run de push da main `dcfedd29`: em andamento; confirmo em seguida.

### Fila entregue nesta rodada
| Linha | PR | head |
|---|---|---|
| E-SF-24a | #1343 | `43462269` |
| E-SF-29-1 | #1345 | `b15a3ece` |
| E-SF-29-2 | #1346 | `ab611ff7` |
| E-SF-RT-02 | #1348 | `34b28033` |
| E-SF-11 (21a) | #1351 | `73aedfae` |
| E-SF-35 (06F) | #1355 | `29059461` |
| E-SF-24b parcial | #1359 | `b6f176d7` |

Todas foram mescladas. Bloqueada: **E-SF-29-3**, até a 42b do rco e o pin do K1; o funil acorda a janela.

### Ramos remotos desta janela para o dono apagar (todos mesclados)
- `sf-w2/mzo-gp/esf24` @ `43462269fa204566594bf0f523b8e35051856b42`
- `sf-w2/mzo-gp/esf29-1` @ `b15a3ecefef8b6bb720a074503089b686ec49189`
- `sf-w2/mzo-gp/esf29-2` @ `ab611ff733badf6b1d6b7c6aeb76f88f643bfe6c`
- `sf-w2/mzo-gp/rt02` @ `34b2803376126783802a184f3e852bbd7c26a0e6`
- `sf-w2/mzo-gp/esf11` @ `73aedfaee5bb942f9d74dc405af4f64b9fb7a0f0`
- `sf-w2/mzo-gp/esf35` @ `290594618a5d4fb4d4353252700182bd1fb07de0`
- `sf-w2/mzo-gp/esf24b` @ `b6f176d7ff647400f2ccbbd1583e452b23aca9b2`
- Também ainda no remoto, de rodadas anteriores já registradas nas LIMPEZAs acima:
  - `sf-w2/mzo-gp/esf05r-a` @ `b158b334db1b591194dc0b312ef7ad3b5e7b6fd9`
  - `sf-w2/mzo-gp/esf05r-b` @ `1c46c31605f2bb49e22006ea3a73941f10596a7d`
  - `sf-w2/mzo-gp/esf05r-c` @ `9cfd8798b5f361a34ce44865cad12764de53a708`
  - `sf-w2/mzo-gp/esf26` @ `ca609e7da24b7cd91ab5e6d7ef15cf0af90e7ff4`
- O ramo do canal `sf-w2/mzo-gp/coord` fica.

### DL
Nenhuma DL foi pedida nem é necessária nesta fila: todas as linhas saíram sem DL.

### Linhas para o evidence-ledger (texto; a janela não edita o ledger)
- E-SF-24a: já enviada na entrada de 2026-11-20 ~23:55Z.
- "E-SF-29-1 (reembolso, prazo legal): cercas C1–C3 de SP-OP-REEMBOLSO-001. Fixam o timer TD_PrazoPendencia em P5D numa allowlist que só encolhe, impedem prazo legal literal em BPMN e texto, e limitam o que roda depois do timer a avisar risco de SLA. PR #1345."
- "E-SF-29-2 (reembolso, G4): junções exclusivas antes de GW_DecisaoReembolso, GW_DesfechoPagamento, GW_AvisoAprovado e GW_AguardarDocs. Sem mudança de comportamento: ids, saídas, condições e default intactos. Baseline G4 −4; digest em aguardando_admissao atualizado sem admitir nada (CD-205). PR #1346."
- "E-SF-RT-02 (cancelamento, motivo): pergunta opcional depois do protocolo, com lista fechada de seis códigos sem saúde. A resposta só entra numa contagem agregada sem pessoa nem emissor; gravar o motivo ligado ao titular depende do DPO. PR #1348."
- "E-SF-11 / EP-REG-21a (LGPD, DSR): cercas de que nenhum sucesso de DSR é fabricado, nem no worker execute_request (sem caminho que complete) nem em schema, rota, OpenAPI ou contrato comercial. O canal POST /dsr segue escuro. PR #1351."
- "E-SF-35 / EP-CORE-06F (A9 recusada): walk sintético sobre o loader A9 e o registro em memória. A medição fica fechada, tenant ou jornada reais são recusados, e o resultado nunca é rotulado como baseline. PR #1355."
- "E-SF-24b parcial (EP-PROC-07a, CD-231): termo da negativa v1, com os incisos I–III do art. 16, § 1°, da RN 623 conferidos byte a byte contra o texto-base. Porta FundamentoDoTermo fechada, com pino do catálogo None; modos entrega e rascunho; nenhum valor forja rótulo do template. A flag MAEZO_AUTH_DENIAL_WRITTEN_NOTICE segue desligada. PR #1359."

### Pré-condições da unidade que pinar o catálogo de fundamentos (para a T-MZO-A)
1. **Pertinência ao catálogo, não só autoconsistência.** O `hash_da_entrada` é exportado e sem chave, e o `hash_do_catalogo` é fornecido pelo chamador. O leitor do catálogo pinado tem de ser o ÚNICO construtor de `FundamentoDoTermo`, ou a porta confere a entrada contra o catálogo carregado; com teste.
2. **O rascunho é textualmente igual ao termo de entrega.** É preciso uma cerca para o futuro chamador honrar `modo`: nunca enviar nem registrar como entregue um resultado `rascunho`.
3. **Lacunas da recusa de rótulo** (baixo impacto, só no meio da linha). Faltam os controles Cc, U+2800 como espaço, U+201A como vírgula e as variantes "artigo", "RN nº 623" e sem vírgula. Acrescentar Cc e U+2800 ao conjunto removido, e não afirmar "na forma que o leitor vê" além do que é coberto.
4. A receita do `hash_entrada` tem de ser fixada no contrato-k2: sha256 do JSON canônico com chaves ordenadas, UTF-8, sem espaços, sem os dois hashes. Os nomes de campo continuam provisórios até lá.

### Pedidos de linha
- Dono próprio para `spec/policies/ans/templates/` (hoje cai em `/spec/policies/ans/`, do security-team).
- Dono do DMN de prazo de pagamento do reembolso (`reembolso_prazo_pagamento`), para a 29-3.
- Retirar `motivo_informado` (texto livre, sem produtor) do contrato SP-OP-CANCEL-001 e do código ligado a ele.
- Incluir o teste da 21a em `SECURITY_TESTS` do Makefile (compartilhado; não editei).
- Dívida `Lei9656-art12-VI` no `funnel_deadline_catalog.dmn` (DRAFT), ligada à 29-3.
- Nit para um PR futuro: `ContagemDeMotivos.__post_init__` levanta `MotivoForaDaListaError` também para contagem negativa ou não inteira, e o nome engana.

### ACHADOs
- `PLANS.md:58` cita o digest antigo `05c181b9` de SP-OP-REEMBOLSO-001; nenhum check o lê.
- E-SF-35: deriva do plano. `measurement_not_admitted` e o "contador inalterado" não existem; as cercas usam os motivos reais.
- E-SF-24b, não bloqueantes:
  - letras de outros alfabetos que imitam latinas e quase-rótulos visivelmente diferentes não são barrados (barrar grego recusaria "β-bloqueador");
  - o teste do ponto único não vigia `texto`.
- Ambiente local (iguais na main, nada dos PRs): sem `helm`, clone raso, `uv` offline, processo root (`test_audit_anchor`). Uma worktree com a `.venv` editável do clone principal resolve `maezo` para o `src` dele e precisa de `PYTHONPATH` com o `src` da worktree na frente.
- Processo: na 24b, um commit (`1dc614a1`) entrou com o mypy vermelho por encadear comandos com `;`. Foi corrigido no commit seguinte, sem amend; nada saiu para o remoto antes do verde.

## 2026-11-21 ~06:20Z — NOVA FILA (CD-259); CAMINHO E-SF-ADD01-03

Fila: ADD01-03 → RT-03 completa → parte de agente da 17r; depois as prévias 37r, 09r, 04r e 33r (só arquivo novo e disjunto). A 29-3 segue esperando o aviso do pin.

### Deriva do head (main `dcfedd29`)
- A parte construível da ADD01-03 já está quase toda na main:
  - o modo `staff_assist` do agente `comercial` (E-SF-17, #1279) devolve só a rota de nota interna, com `envio_pelo_humano=True`, sem texto gerado e sem ação de envio;
  - sem LLM: `prompt_versions: {}`, e nenhum texto é gerado enquanto o SC-03.D1 não tiver resposta;
  - o `build()` recusa sem `comercial_ligado`;
  - `INPUT_FIELDS` é fechado: não há campo de entrada para texto nem para o snapshot do prospect;
  - a ação `staff_assist_draft` está em L1, sem parâmetros, sem tool e sem ser hard (#1349);
  - o console da ADD01-02 (#1303) já projeta a jornada sem campo clínico (`PHI_IN_COMMERCIAL_INPUT`).
- Ninguém em `src/` usa a ação ainda. O `agent.yaml` do `comercial` não a declara, e o teto fechado `_ACOES_COMERCIAIS` (`agent_def.py:341`) a recusaria.
- Dos três testes que o plano nomeia, só o equivalente de "nunca envia" existe (`test_staff_assist_nunca_envia_ao_prospect`).
- **O que o plano pede e fica fora:**
  - `L0-core.yaml`: a ação já existe, e a janela nunca edita `spec/policies/autonomy/` (CD-144/233);
  - `runtime/inference/`: sem provedor nem região (E38), sem resposta do DPO ao SC-03.D1, e a saída de LLM sem nome de autonomia instalado. O copiloto segue sem conteúdo gerado e determinístico.

### Proposta (A, padrão): ligar a ação como DADO, sem LLM, flag desligada
1. `src/maezo/agents/comercial/graph.py`:
   - constante `ACAO_RASCUNHO_ATENDENTE = "staff_assist_draft"`;
   - no modo `staff_assist`, o resultado ganha o campo de saída `acao_de_rascunho = ACAO_RASCUNHO_ATENDENTE`, e `acao_de_envio` continua `None`.
   O agente não avalia o PEP, porque não pode importar o gateway (ADR-0067). Ele devolve o nome da ação como dado, como já faz com `ACAO_ENVIO_PROSPECT`. Quem avalia é o gateway: L1 dá `REQUIRE_HUMAN`.
2. `spec/agents/comercial/agent.yaml`: `autonomy_actions` ganha `staff_assist_draft` (L1, só preparar).
3. `src/maezo/platform/validation/agent_def.py`: o teto fechado `_ACOES_COMERCIAIS` cresce em UM nome, `staff_assist_draft`. A ação não é hard, não é PHI e não tem tool. O resto do teto e as recusas (`COMMERCIAL_ACTION_NOT_ALLOWED`, `COMMERCIAL_PHI_ACTION_FORBIDDEN`) não mudam.
4. `tests/unit/platform/test_agent_yaml_tools_wired.py`: `staff_assist_draft` é classificado em `_ACTION_NOT_TOOL_SHAPED`, a lista fechada de ações sem tool.
5. Teste novo `tests/unit/agents/test_add01_03_staff_assist.py`:
   - `test_staff_assist_never_sends_without_human_click`: sem ação de envio, `envio_pelo_humano=True`, rota de nota interna, e nenhum tool chamado pelo grafo;
   - `test_staff_assist_output_has_no_price_or_commission_computation`: nenhuma saída numérica ou monetária, nem campo de preço, comissão, elegibilidade, legitimidade ou ranking, por varredura de chaves e valores;
   - `test_staff_assist_refuses_clinical_input`: a entrada é fechada, e qualquer campo fora de `INPUT_FIELDS` é recusa tipada, inclusive um texto clínico.
     - **Deriva:** o plano cita o código `PHI_IN_COMMERCIAL_INPUT`. O agente não pode importar o detector do gateway (ADR-0067), então a recusa é por construção. O código `PHI_IN_COMMERCIAL_INPUT` continua sendo o da projeção do console (ADD01-02), e o teste fixa as duas pontas.
   - também: o PEP real resolve `staff_assist_draft` como `REQUIRE_HUMAN`; o `build` desligado recusa; `_hard_frozen.yaml` fica byte-idêntico; nenhuma saída de LLM (`prompt_versions` vazio).

### Cercas lidas e declaração
O ramo foi lido contra estas cercas, e **nenhuma é contrariada nem contornada**:
- `test_esf17_comercial.py`:
  - o teto e o hash CD-13: a ação nova já está no conjunto excluído, e o hash das antigas não muda;
  - o sha256 do `_hard_frozen` e `test_staff_assist_nunca_envia_ao_prospect`.
- `test_l1_staff_assist_draft_policy.py`.
- `test_agent_yaml_tools_wired.py`: é a classificação do item 4.
- `test_agent_fleet_autonomy_fences.py`: nenhuma ação hard.
- `test_pep_policy_unification.py`.
- `test_l0_canonical_action_naming.py`: nenhuma saída de LLM.
- `check_fronteira_agentes.py`: só `MODULOS_DO_SDK`, sem importar o gateway.
- `test_turn_telemetry_comercial_fechada.py` e `test_terminal_nodes_emit_desfecho.py`: o vocabulário fechado não muda.
- `test_llm_sdk_only_in_runtime_inference.py`.
- `test_add01_02_specialist_console.py`.

A única cerca que muda de conteúdo é o teto fechado `_ACOES_COMERCIAIS`, que ganha um nome. Ele continua fechado e com teste, e por isso a mudança fica declarada aqui.

### Fronteira
- Nada é enviado pelo agente; nenhuma mensagem a beneficiário ou prospect.
- Nenhum dado de saúde como entrada.
- Nada decide rescisão, negativa ou valor.
- Sem DMN, migração, DL ou CODEOWNERS; sem edição de `spec/policies/autonomy/` nem de `_hard_frozen.yaml`.
- A flag fica desligada.
- Ativar depende do SC-03.D1 (DPO) e do SC-03.O1 (deploy do dono).

### Alternativa (B)
Se a coordenação não quiser que o teto `_ACOES_COMERCIAIS` cresça agora, a entrega é só o teste novo do item 5, sem os itens 1 a 4, fixando o estado fechado atual.

Sigo com (A) localmente, num ramo novo a partir da main. O PR só abre com o de acordo e uma vaga no teto.

## 2026-11-21 ~06:30Z — run de push da main `dcfedd29` (#1359) verde; de acordo à ADD01-03 (A)

- Push da main `dcfedd29`: CI (44873355696), CD, Security e Supply chain, todos `success`.
- ADD01-03: o de acordo expresso da coordenação do programa à variante (A) está registrado na CD-263.
  - Condições incorporadas aos testes:
    - o teto `_ACOES_COMERCIAIS` é igual a um conjunto exato;
    - o nome acrescentado é lido da pasta de autonomia;
    - REQUIRE_HUMAN do gateway;
    - nada enviado nesse modo;
    - `build` desligado e console sem rota montada;
    - sem LLM, provedor ou envio;
    - recusa clínica por construção, com o código do console intocado;
    - pasta de autonomia e `_hard_frozen` idênticos, com sha256 por arquivo;
    - `effect_classes` intocado.
  - O CAMINHO da RT-03 completa e o da 17r vêm antes de qualquer código delas.

## 2026-11-21 ~06:45Z — CAMINHO E-SF-RT-03 completa e CAMINHO da parte de agente da E-SF-17r

### RT-03 completa (rascunho do copiloto na retenção)
**Deriva do head:** nada de RT-03 está na main.
- O mais próximo é `gateway/human/reajuste_contestacao.py` (#1311, módulo puro, protocolo da custódia, prazo como GAP) e `portal/api/meu_plano.py` (#1319, fechado).
- A ação `retention_staff_assist_draft` está em L1 (#1349); ninguém a usa.
- A cédula E36 decidiu B por ora (fila humana). Se a pasta abrir: lista de campos com default VAZIO, sem CPT, sem inadimplência, só sintético.
- A RT-D3 (DPO) não tem resposta.
- O prazo de 7 dias úteis não tem linha no catálogo de prazos, e DMN nova é ponto de parada.

**Proposta (A), sem agente novo** (a frota e o ADR-0067 ficam intocados; não reusa a identidade `comercial`):
- **Arquivo novo** `src/maezo/gateway/human/retencao_relacionamento.py`, módulo puro e não montado:
  - O item da fila não clínica "Relacionamento e renovação" leva o protocolo vindo da custódia, como no #1311. O vencimento fica `None`, com GAP declarado: sem número literal, sem DMN.
  - O pedido de rascunho devolve, como DADO, a ação `retention_staff_assist_draft` (L1). Quem a avalia é o PEP do gateway, que dá REQUIRE_HUMAN.
  - O pedido leva os fatos-fonte por lista FECHADA de campos `CAMPOS_DO_RASCUNHO_RETENCAO = frozenset()`, vazia por default (E36). Os fatos passam por `render_fatos_para_prompt(permitidos=CAMPOS_DO_RASCUNHO_RETENCAO, agente=...)`, então hoje nada chega a um modelo. Não há LLM nem texto gerado.
- **Teste novo** `tests/unit/gateway/test_rt03_retencao_relacionamento.py`, com os testes do plano:
  - `test_assistente_retencao_sem_ferramenta_decisoria`: nenhum tool, e nenhuma ação de valor, elegibilidade, rescisão ou contestação;
  - `test_assistente_retencao_sem_zona_phi`: nenhum campo clínico ou de PHI passa a lista fechada; inclui CPT e inadimplência;
  - o reuso de `test_contract_termination_never_autonomous` (DENY).

**Pontos do ROOT:**
- **(1) Envio:** o rascunho é só dado. Não há chamada de envio no módulo (cerca por AST), e a entrega ao beneficiário continua ato humano.
- **(2) Campos ao modelo:** lista fechada vazia e `render_fatos_para_prompt` com `permitidos=` e `agente=`. Texto livre de terceiro não entra, porque não é campo da lista.
- **(3) Sem oferta e sem fricção no cancelamento:**
  - nenhuma oferta, desconto, prazo ou promessa gerados, com varredura de tokens no módulo e na saída;
  - o cancelamento continua sem fricção: o módulo não é chamado por `saida_a_pedido` (cerca de import), e a pergunta de motivo continua opcional e depois do protocolo (#1348);
  - o Lucas continua sem retenção.
- **(4) Nada ligado:** módulo não montado, por AST. Não há flag nova; se houver, desligada e com teste.

**Fora da entrega:** a pasta de autonomia, `action-approvals`, `_hard_frozen` e `effect_classes` são só lidos, com sha256 em teste. Também ficam fora:
- DMN, migração, DL e CODEOWNERS;
- o agente novo (B), que espera a RT-D3 e o provedor;
- o prazo de 7 dias úteis, GAP de catálogo.

**Alternativa (B):** só testes do estado fechado, sem módulo novo.

### Parte de agente da 17r
**Deriva:** o bloco da 17r cobre só o GTM-E-BPMN-06, BPMN e DMN, fora da janela.
- A parte de agente não tem entrada própria (CD-153/233).
- O que ela exerceria é `send_prospect_message` (L3) por um despachante, e a classe `comunicacao_prospect` está adiada: ela não está em `effect_classes` e não há despachante (CD-153). O #1340 entregou só a decisão de rota, com a chave em false.

**Proposta (A), só testes do estado fechado** em `tests/unit/agents/test_esf17r_agente_estado_fechado.py`:
- a ação de envio continua só DADO no modo prospect;
- `comunicacao_prospect` e `send_prospect_message` continuam ausentes de `effect_classes` (classes e OPERATIONS);
- a rota comercial do WhatsApp continua com a chave false e sem emissor;
- nenhum despachante importa o grafo `comercial`;
- `build` desligado.
Nenhum arquivo de `src/` muda.

**Alternativa (B):** declarar "nada a construir" e fechar a linha sem PR.

Ordem: ADD01-03 (PR quando a revisão der PASS) → RT-03 → 17r, um PR por vez e no máximo dois do funil abertos.
