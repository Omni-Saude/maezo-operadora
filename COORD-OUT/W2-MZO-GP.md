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
