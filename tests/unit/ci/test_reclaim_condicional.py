"""L6 (custo de Actions): o reclaim de disco do ci.yml so' e' dispensado com cache uv quente E espaco livre
acima do limiar — o cenario do incidente de 20/09 (cache frio) sempre faz o reclaim de sempre.

Os testes executam o PROPRIO script do passo, lido do ci.yml, trocando so' o `rm -rf` (por um marcador) e a
leitura do `df` (por um valor fixo). Assim a cerca prova o comportamento do texto que vai para o runner,
nao de uma copia."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_CI = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
_JOBS = ("unit", "quality", "release-floor")
_DF = "df --output=avail -BG / | tail -1 | tr -dc '0-9'"


def _steps(job: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"][job]["steps"]
    return steps


def _reclaim(job: str) -> dict[str, Any]:
    return next(s for s in _steps(job) if str(s.get("name", "")).startswith("Reclaim runner disk headroom"))


@pytest.mark.parametrize("job", _JOBS)
def test_order_is_install_uv_then_decision_and_reclaim(job: str) -> None:
    """A decisao depende do `cache-hit` do `Install uv`: o passo de reclaim nao pode vir antes dele."""
    nomes = [str(s.get("name") or s.get("uses")) for s in _steps(job)]
    i_uv = nomes.index("Install uv")
    i_reclaim = next(i for i, n in enumerate(nomes) if n.startswith("Reclaim runner disk headroom"))
    assert i_uv < i_reclaim, f"{job}: o reclaim precisa vir DEPOIS do `Install uv`"
    assert _steps(job)[i_uv].get("id") == "uv"
    assert _reclaim(job)["env"]["CACHE_HIT"] == "${{ steps.uv.outputs.cache-hit }}"
    # Nada que use o disco da suite pode ficar entre o checkout e a decisao alem do setup.
    assert not any("Unit tests" in n or "floor gate" in n for n in nomes[:i_reclaim]), job


@pytest.mark.parametrize("job", _JOBS)
@pytest.mark.parametrize(
    ("cache_hit", "livre_gb", "reclaim_esperado"),
    [
        ("true", "86", False),  # cache quente e espaco de sobra: o unico caso que dispensa
        ("true", "30", True),  # cache quente mas abaixo do limiar (ex.: runner de disco menor)
        ("false", "86", True),  # cache frio: o cenario do incidente de 20/09
        ("", "86", True),  # saida ausente do setup-uv: na duvida, reclaim
    ],
)
def test_reclaim_decision_and_printed_numbers(
    job: str, cache_hit: str, livre_gb: str, reclaim_esperado: bool
) -> None:
    passo = _reclaim(job)
    script = passo["run"]
    assert _DF in script and "sudo rm -rf" in script, f"{job}: o passo mudou de forma; ajuste a cerca"
    script = script.replace(_DF, 'echo "$FAKE_LIVRE"').replace("sudo rm -rf", "echo RECLAIM_EXECUTADO")
    env = dict(os.environ, CACHE_HIT=cache_hit, LIMIAR_GB=str(passo["env"]["LIMIAR_GB"]), FAKE_LIVRE=livre_gb)
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert ("RECLAIM_EXECUTADO" in r.stdout) is reclaim_esperado
    # (a) da ROOT: toda run imprime a decisao e os numeros (cache, GB livres, limiar).
    decisao = next(linha for linha in r.stdout.splitlines() if linha.startswith("reclaim"))
    assert f"{livre_gb} GB livres" in decisao
    assert f"limiar {passo['env']['LIMIAR_GB']} GB" in decisao
    if reclaim_esperado:
        assert (cache_hit or "ausente") in decisao
