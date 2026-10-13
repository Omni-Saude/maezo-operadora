"""Cerca `scripts/ci/check_qa_isolada.py`: nenhum codigo de producao importa `maezo.qa`."""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_qa_isolada import executar, main

_RAIZ = Path(__file__).resolve().parents[3]


def _arvore(tmp_path: Path, arquivos: dict[str, str]) -> Path:
    src = tmp_path / "src" / "maezo"
    for relativo, texto in {"qa/__init__.py": '"""qa."""\n', "__init__.py": "", **arquivos}.items():
        caminho = src / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
    return src


def test_a_arvore_real_passa_e_nao_e_vacua() -> None:
    achados, lidos = executar(_RAIZ / "src" / "maezo")
    assert achados == []
    assert lidos > 100


def test_import_absoluto_relativo_e_dinamico_reprovam(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {
            "platform/__init__.py": "",
            "platform/a.py": "import maezo.qa.simulador\n",
            "platform/b.py": "from maezo.qa import simulador\n",
            "platform/c.py": "from ..qa import simulador\n",
            "platform/d.py": "import importlib\nimportlib.import_module('maezo.qa.simulador')\n",
            "platform/e.py": "from .. import qa\n",
        },
    )
    achados, _ = executar(src)
    for nome in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        assert any(nome in a for a in achados), nome


def test_docstring_e_o_proprio_qa_nao_contam(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {
            "platform/__init__.py": "",
            "platform/ok.py": '"""Reusado por `maezo.qa.simulador` (o sentido inverso)."""\nX = 1\n',
            "platform/doc.py": '"""maezo.qa"""\n',
            "qa/simulador.py": "from maezo.platform import ok\nNOME = 'maezo.qa.simulador'\n",
        },
    )
    assert executar(src) == ([], 4)


def test_sem_pacote_qa_e_arquivo_que_nao_parseia_reprovam(tmp_path: Path) -> None:
    src = tmp_path / "src" / "maezo"
    src.mkdir(parents=True)
    (src / "x.py").write_text("x = 1\n", encoding="utf-8")
    achados, _ = executar(src)
    assert achados and "ausente" in achados[0]
    src = _arvore(tmp_path / "b", {"platform/quebrado.py": "def (:\n"})
    achados, _ = executar(src)
    assert achados and "nao parseia" in achados[0]


def test_main_codigo_de_saida(tmp_path: Path) -> None:
    assert main(["--src-dir", str(_arvore(tmp_path, {}))]) == 0
    assert main(["--src-dir", str(_arvore(tmp_path / "r", {"m.py": "import maezo.qa\n"}))]) == 1
