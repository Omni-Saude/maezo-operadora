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
