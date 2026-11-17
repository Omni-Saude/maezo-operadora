"""Liga o validador de CI do registro de templates (`scripts/ci/check_whatsapp_templates.py`) ao lane normal.

O registro publicado passa (saida 0); um registro com classe de saude ou classe desconhecida reprova
(saida 1, com o token); arquivo ausente e' saida 2. Executado como subprocesso, do mesmo jeito que o CI o
executaria.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_RAIZ = Path(__file__).resolve().parents[3]
_SCRIPT = _RAIZ / "scripts" / "ci" / "check_whatsapp_templates.py"
_REGISTRO = _RAIZ / "spec" / "communications" / "whatsapp-templates.yaml"


def _rodar(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, cwd=_RAIZ, check=False
    )


def test_registro_publicado_passa() -> None:
    r = _rodar()
    assert r.returncode == 0, r.stderr
    assert "0 aprovado(s)" in r.stdout and "0 ratificada(s)" in r.stdout


def _quebrado(tmp_path: Path, classe: str) -> Path:
    doc = yaml.safe_load(_REGISTRO.read_text(encoding="utf-8"))
    doc["templates"][0]["variables"][0]["class"] = classe
    alvo = tmp_path / "registro.yaml"
    alvo.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return alvo


def test_classe_de_saude_reprova(tmp_path: Path) -> None:
    r = _rodar("--registro", str(_quebrado(tmp_path, "cpt")))
    assert r.returncode == 1
    assert "VARIABLE_CLASS_HEALTH:templates[0].variables[0]" in r.stderr


def test_classe_desconhecida_reprova(tmp_path: Path) -> None:
    r = _rodar("--registro", str(_quebrado(tmp_path, "inventada")))
    assert r.returncode == 1
    assert "VARIABLE_CLASS_UNKNOWN:templates[0].variables[0]" in r.stderr


def test_registro_ausente_e_saida_2(tmp_path: Path) -> None:
    assert _rodar("--registro", str(tmp_path / "nao.yaml")).returncode == 2
