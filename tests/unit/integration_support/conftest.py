"""Engine-lane identity guard for the three machine-local D7 packet modules.

POR QUE ESTE CONTEST EXISTE (run 35468141372, validador de identidades JUnit do job de
engine do PR #434): o job de engine coleta `tests` com `-m integration` e valida o JUnit
contra o manifesto de identidades pino de `main`. Um `pytest.skip(..., allow_module_level=
True)` dispara NA COLECAO, antes da deselecao por marker — cada modulo saltado emite uma
entrada JUnit propria (classname vazio, name = caminho pontilhado do modulo). `main` tem
ZERO entradas de modulo no manifesto (28 skips, todos de teste); a porta D7 acrescentava
3 (+3 entradas de modulo, 720 vs 717 testcases, 31 vs 28 skips) → divergencia de
identidade/conjunto → rc 1. `collect_ignore` remove o modulo DA COLECAO quando o pacote
esta' ausente: nenhuma entrada JUnit, nem skip, nem divergencia.

Os caminhos abaixo sao COPIA VERBATIM do `PACKET` de cada modulo (leia junto: o modulo
continua com o proprio guard de skip como defesa em profundidade para invocacao direta de
arquivo, que nao e' o caminho do job de engine). Se o `PACKET` de um modulo mudar sem
mudar aqui, a checagem de drift abaixo RECUSA a colecao (fail-closed) em vez de voltar a
coletar modulo com pacote ausente silenciosamente.
"""

from __future__ import annotations

from pathlib import Path

_DIRETORIO = Path(__file__).resolve().parent

#: raiz do pacote machine-local de CADA modulo — verbatim do `PACKET` de cada um
#: (`test_d7_startup_observation.py:17`, `test_d7_proof_budget.py:11`,
#: `test_d7_actual_proof_preflight.py:18`; dois pacotes distintos, por design da porta).
_PACOTES_POR_MODULO = {
    "test_d7_startup_observation.py": Path(
        "/Users/familia/code/maezo-completion-evidence/strategy-cycle1-20260910/"
        "d7-actual-proof-preflight-repair"
    ),
    "test_d7_proof_budget.py": Path(
        "/Users/familia/code/maezo-completion-evidence/strategy-cycle1-20260910/"
        "d7-actual-proof-preflight-repair"
    ),
    "test_d7_actual_proof_preflight.py": Path(
        "/Users/familia/code/maezo-completion-evidence/strategy-cycle1-20260910/"
        "d7-preflight-final-deadline-repair/toolkit"
    ),
}

#: Drift entre esta copia e o `PACKET` do modulo e' recusado, nunca ignorado: com drift, o
#: modulo voltaria a ser coletado com o pacote ausente e a divergencia de manifesto
#: retornaria silenciosamente em CI.
for _modulo, _raiz in _PACOTES_POR_MODULO.items():
    if str(_raiz) not in (_DIRETORIO / _modulo).read_text(encoding="utf-8"):
        raise AssertionError(
            f"conftest: raiz de pacote de {_modulo} divergiu do PACKET declarado no proprio "
            f"modulo ({_raiz}) — atualize a copia neste conftest junto do modulo"
        )

#: Pacote presente → ignora ninguem (colecao cheia, toda assercao roda).
#: Pacote ausente → o modulo NAO e' coletado (nada de entrada JUnit de modulo; o guard de
#: skip do modulo permanece como defesa em profundidade para invocacao direta).
collect_ignore = [_modulo for _modulo, _raiz in _PACOTES_POR_MODULO.items() if not _raiz.is_dir()]
