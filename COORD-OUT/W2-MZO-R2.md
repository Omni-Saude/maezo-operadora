# COORD-OUT — W2-MZO-R2 (funil, ondas 2 e 3)

Fila: E-SF-34r, E-SF-12r, E-SF-06r, E-SF-32r. Base: main `b86b3d8`.

## 2026-11-20 22:51Z — BLOQUEIO de leitura do pacote

`add_repo` (omni_sales_platform, leitura) negado pelo modo automático ("[Unauthorized Access]"). Não tentei outro caminho. Material recebido do coordenador por mensagem. Os seis prefixos sha256 foram conferidos pelo orquestrador às 22:52Z no clone dele, não por mim.

## 2026-11-20 22:56Z — CAMINHO E-SF-34r

Ordem da fila: 34r primeiro (só arquivo novo, sem colisão). 12r, 32r e 06r depois, cada uma com CAMINHO próprio e pontos de parada, enviado antes à coordenação do programa.

**Ramo:** `sf-w2/mzo-r2/esf34r` a partir da main.

**Arquivos novos (só estes):**
1. `scripts/omni_matrix_drift.py`: detector de deriva da matriz de interfaces do portal (CORE-12c).
   - Entrada: duas versões da matriz, base e alvo. Em produção são lidas de um clone do portal só com `git cat-file` (sem checkout, sem rede). Nos testes são lidas de arquivos sintéticos.
   - Saída: relatório determinístico com `base_sha` e `target_sha` (40 hex; qualquer outra coisa é recusada) e, por linha, `id`, `estado` (`igual`, `alterada`, `ausente`, `nova`) e os sha256 da forma canônica da linha na base e no alvo.
   - O relatório não copia conteúdo de linha, só ids e hashes, para respeitar a guarda de proveniência `scripts/docs/omni_provenance.py`.
   - Escreve só com `--out` e só dentro de `docs/delivery/commercial-platform/portal-mirror/`. Sem `--out`, imprime na saída padrão.
2. `tests/governance/test_omni_matrix_drift.py`. Cobre:
   - `test_drift_report_pins_target_sha` e `test_drift_never_promotes_rows`;
   - o estado fechado do sucessor;
   - recusa de sha que não tenha 40 caracteres;
   - recusa de escrita fora do diretório de relatório;
   - relatório sem conteúdo de linha;
   - determinismo (duas execuções, mesmos bytes).

**Arquivos alterados:** nenhum.

**O que fica fechado (codificado como teste):**
- O detector nunca promove linha: o estado de qualificação de cada linha (`NOT_RUN` / `PLANNING_ONLY_UNQUALIFIED`) não é lido como entrada de decisão nem escrito na saída como novo valor. O relatório traz `promove_linhas: false` constante.
- Sucessor por janela de admissão: a função existe e recusa sempre (`REGISTRO_DE_ADMISSAO_AUSENTE`); nenhum registro de admissão existe.
- Nenhuma flag, nenhuma ingestão, nenhum envio, nenhuma medição.
- Nada de spec/processes, deploy/, spec/policies, workflows, CODEOWNERS, PLANS.md, ledger nem contagens.

**Disjunção com as seis árvores da W2-MZO-GP:** nenhum arquivo em comum. As delas estão em `src/maezo/.../auth.py`, `denial_term.py`, BPMN REEMBOLSO-001, `cancelamento.py`/`politica.py` e testes em `tests/unit/{sec,spec,processes,portal}` e `tests/unit/test_commercial_admission_a9_fallback.py`. Nenhum teste de lista fechada nem contagem que elas tocam. O único teste que enumera `scripts/*.py` (`tests/unit/agents/test_helena_sem_camadas_antigas.py`) só procura imports de módulos removidos.

**GAPs:**
- G1 — O arquivo `docs/delivery/commercial-platform/portal-mirror/MATRIX-DRIFT-<sha>.json` NÃO é gerado nesta linha.
  - Esta sessão não tem acesso ao repositório do portal (`add_repo` negado).
  - O maezo guarda só os hashes do arquivo da matriz em `config/provenance/omni-sales-b3de4b4.blobs.json`: `maezo-interface-matrix-p1-successor.json`, sha256 `268ee476…5219` no commit `b3de4b4`. Não guarda o conteúdo, e a guarda de proveniência proíbe copiá-lo.
  - Quem tem o clone executa `python -m scripts.omni_matrix_drift --omni-git-dir <clone> --base <sha40> --target <sha40> --out …`.
- G2 — Forma da linha da matriz desconhecida para mim (51 interfaces + 8 lacunas, campo identificador). Procurado no maezo em: `portal-mirror/README.md`, `PORTAL-MIRROR-PIN-C6-4.json`, `GAP-CATALOG-A1-A10.{md,json}` e no manifesto de proveniência. Só há contagens e o nome do arquivo. Ver SQ-34r-1.

**Subpergunta com recuo:**
- SQ-34r-1 (sem item): qual a chave das linhas e onde ficam as listas de interfaces e de lacunas dentro de `maezo-interface-matrix-p1-successor.json`?
  - Recuo conservador: o detector exige `id` textual único por linha em listas declaradas por parâmetro.
  - Forma desconhecida, id ausente ou duplicado é recusado (`FORMA_DA_MATRIZ_DESCONHECIDA`), nunca adivinhado.

**Verificação planejada:**
- mutação dos testes novos;
- revisão às cegas por subagente e reparo por terceiro;
- `tests/unit` inteiro, main × ramo;
- `make lint type validate-artifacts`;
- varredura de `noqa` e `type: ignore` novos.

## 2026-11-20T23:06Z — E-SF-34r: CD-218 recebida; construção e revisão

- Ramo `sf-w2/mzo-r2/esf34r` @ `9d487f605308fb79e0f4b9e03cf4299b6ee85ca8`, sem PR. Dois arquivos novos, nenhum alterado.
- Forma da matriz fixada em constantes (CD-218); SQ-34r-1 encerrada.
- Mutação antes da revisão: 25/25 mortos (2 casos reforçados).
- Revisão às cegas: FAIL com 2 bloqueantes (symlink no destino; clone parcial poderia buscar blob na rede) e 6 não bloqueantes.
- Reparo por terceiro: todos tratados; 78 testes; um mutante sobrevivente equivalente (id vazio no denominador, já recusado pela checagem seguinte).
- Reconferência independente do reparo: em curso.
- GAP G1 mantido: o relatório de deriva não é gerado nesta linha.

## 2026-11-20T23:47Z — E-SF-34r pronta para PR; em espera

- Head `e169e1428fe2ecaf2952bb2392d1187245f21a9b`, sobre a main `b86b3d822fd8020858eeee288472322509dba519`, 0 merges atrás, `git merge-tree` limpo.
- Reparo da segunda rodada por terceiro; reconferência independente PASS (9/9 mutantes mortos). 88 testes no arquivo novo.
- `tests/unit` inteiro, main limpa × ramo (worktrees destacadas, `-n 4`): os dois com 122 failed, 359 errors, 43934 passed. Conjunto de falhas idêntico; zero falha só no ramo.
- `tests/governance`: ramo 177 passed; main 89 passed.
- `make lint type validate-artifacts` rc=0. Nenhum `noqa` nem `type: ignore` novo.
- PR não aberto: já há PR do funil aberto (#1343). Abro quando ele fechar, sem trazer a main se o diff continuar disjunto.

## 2026-11-20T23:48Z — CD-219: parada

- 34r registrada. PR só quando a coordenação definir a posição na fila do funil; ramo fica como está, sem novas baterias.
- `febe41a` fica como está (sem reescrita).
- 12r não começa até a decisão do dono sobre o acesso. Janela parada.

## 2026-11-20T23:49Z — CD-220: PR #1344 aberto

- Head `e169e1428fe2ecaf2952bb2392d1187245f21a9b`; aberto em paralelo ao #1343 (teto de dois, CD-220).
- Aguardando CI; PRONTO à coordenação do programa quando verde.

## 2026-11-21T00:35Z — PRONTO E-SF-34r

- PR #1344, head `e169e1428fe2ecaf2952bb2392d1187245f21a9b`.
- CI 37 success / 2 skipped / 0 falhos; mergeable clean.
- 2 commits atrás da main `0e8b0bed56bb2bb0924901041cf1fd3ccacef489` (merge do #1343, disjunto); `merge-tree` limpo.
- PRONTO enviado à coordenação do programa, com cópia ao orquestrador. Aguardo merge para a LIMPEZA #1.

## 2026-11-21T00:36Z — LIMPEZA #1 (E-SF-34r, PR #1344 mesclado)

- Main `faea2f74` (merge do #1344); `e169e1428fe2ecaf2952bb2392d1187245f21a9b` é ancestral da main.
- Ramos:
  - local `sf-w2/mzo-r2/esf34r` apagado com `git branch -d` (era `e169e142`);
  - remoto `sf-w2/mzo-r2/esf34r` @ `e169e142` mantido (apagar ramo remoto é recusado);
  - `sf-w2/mzo-r2/coord` mantido (canal).
- PRs: nenhum PR órfão desta janela. Abertos do funil: só o #1345 (W2-MZO-GP).
- Worktrees: `main-limpa` e `ramo-34r` removidas sem `--force`. Resta só a do canal `coord`.
- Temporários: scripts de mutação, saídas das baterias e cópias dos revisores apagados do scratchpad.
- Clone principal em `main`, limpo.
- Estado: 12r, 06r e 32r paradas à espera da decisão do dono sobre o acesso (CD-219).

## 2026-11-21T02:44Z — E-SF-12r: CAMINHO enviado por mensagem (ponto de parada)

- Acesso de leitura ao pacote aprovado pelo dono nesta conversa. Os seis prefixos sha256 foram conferidos por mim: batem.
- CAMINHO da 12r enviado por mensagem à coordenação do programa e ao orquestrador. Ponto de parada: a linha, como escrita, mexe nas duas listas de chaves de processo, que um teste de lista fechada fixa.
- Recuo: nada de `src/`. Proposta de um arquivo novo de teste do estado fechado e um test-spec documental. Aguardo a CD.

## 2026-11-21T03:37Z — E-SF-12r no PR #1352; 06r e 32r em curso; desvio registrado

**E-SF-12r** (variante a; decisão por mensagem da coordenação do programa)
- PR #1352, head `166f41ef6fabb14374c7941864aba8193f5d9153`. Dois arquivos novos: teste do estado fechado e test-spec "NÃO PROMOVIDO / DRAFT".
- Mutação antes da revisão: 13/13 mortos, em worktree com `PYTHONPATH` próprio.
- Revisão às cegas: FAIL com 2 bloqueantes (registro A6 procurado por nome; recusa por pertinência não afirmada). Reparo por terceiro. Reconferência independente: PASS (7/7 pedidos mortos, mais os próprios).
- `tests/unit` main `3757ca3d` × ramo: 100 F / 359 E nos dois, conjunto idêntico; zero falha só no ramo.
- `make lint type validate-artifacts` rc=0. `merge-tree` sobre `7ae9092b` limpo.
- Aguardando CI.

**E-SF-06r** (variante a; sem módulo de parâmetro)
- Ramo `sf-w2/mzo-r2/esf06r` @ `5854c292`, um teste novo.
- Revisão às cegas: FAIL. Reparo por terceiro. Reconferência: PASS.
- Exceção declarada no teste: TTL de 30 dias da custódia do telefone do lead, pinado como exceção exata.
- Suíte main `7ae9092b` × ramo em curso.

**E-SF-32r** (variante a; nada em `spec/processes`, nenhum texto novo de norma)
- Ramo `sf-w2/mzo-r2/esf32r` @ `5b8d1ea4`, um teste novo. Mutação: 8/8 mortos. Revisão às cegas em curso.

**Desvio:** o subagente de reconferência da 06r usou `git reset --hard` e `git clean -fd` dentro da própria worktree descartável e destacada (`chk06`), entre mutantes. Sem efeito fora dela; removida sem `--force`. Daqui em diante todo prompt de subagente traz a proibição literal (CD-232).

## 2026-11-21T04:26Z — LIMPEZA #2 (E-SF-12r, PR #1352 mesclado); PR #1357 (E-SF-06r) aberto

- Main `fb748b0c` contém `166f41ef`.
- Ramo local `sf-w2/mzo-r2/esf12r` apagado com `-d`; o remoto fica em `166f41ef`.
- Worktrees de teste da 12r removidas sem `--force`. Pastas `--basetemp` das baterias apagadas por caminho explícito, depois que o disco encheu.
- Clone principal em `main`, limpo. Nenhum PR órfão.
- E-SF-06r: PR #1357, head `5854c292dacc852d9ae2cd20acb1b6e661df9be7`, 15 commits atrás da main, diff disjunto.
- E-SF-32r: bateria main × ramo refeita, em curso.
