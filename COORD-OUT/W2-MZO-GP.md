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
