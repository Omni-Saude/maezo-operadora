"""Cerca `scripts/ci/check_fronteira_agentes.py` (ADR-0067): catraca da fronteira plataforma x agentes."""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_fronteira_agentes import (
    BASELINE_PADRAO,
    catraca,
    coletar,
    escrever_baseline,
    ler_baseline,
    main,
    relatorio,
)

_RAIZ = Path(__file__).resolve().parents[3]


def _arvore(tmp_path: Path, arquivos: dict[str, str]) -> Path:
    src = tmp_path / "src" / "maezo"
    base = {
        "__init__.py": "",
        "agents/__init__.py": '"""framework."""\nclass AgentLoader: ...\n',
        "agents/helena/__init__.py": "",
        "agents/lucas/__init__.py": "",
        "qa/__init__.py": "",
    }
    for relativo, texto in {**base, **arquivos}.items():
        caminho = src / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
    return src


def _chaves(src: Path) -> set[str]:
    deps, erros, _ = coletar(src)
    assert erros == []
    return {f"{d.direcao} {d.chave()}" for d in deps}


def test_a_arvore_real_bate_com_a_baseline_e_nao_e_vacua() -> None:
    deps, erros, lidos = coletar(_RAIZ / "src" / "maezo")
    assert erros == []
    assert lidos > 100
    assert deps, "a divida inventariada em 23/10/2026 nao e' zero; baseline vazia seria vacua"
    assert catraca(deps, ler_baseline(BASELINE_PADRAO)) == ([], [])


def test_plataforma_importando_agente_conta_em_todas_as_formas(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {
            "platform/__init__.py": "",
            "platform/a.py": "import maezo.agents.helena.graph\n",
            "platform/b.py": "from maezo.agents.helena.graph import HelenaGraph\n",
            "platform/c.py": "from maezo.agents import helena\n",
            "platform/d.py": "from ..agents.lucas import fonte\n",
            "platform/e.py": "import importlib\nimportlib.import_module('maezo.agents.helena.graph')\n",
            "agents/helena/graph.py": "",
            "agents/lucas/fonte.py": "",
        },
    )
    assert _chaves(src) == {
        "plataforma->agente src/maezo/platform/a.py -> maezo.agents.helena.graph",
        "plataforma->agente src/maezo/platform/b.py -> maezo.agents.helena.graph",
        "plataforma->agente src/maezo/platform/c.py -> maezo.agents.helena",
        "plataforma->agente src/maezo/platform/d.py -> maezo.agents.lucas.fonte",
        "plataforma->agente src/maezo/platform/e.py -> string:maezo.agents.helena.graph",
    }


def test_framework_docstring_qa_e_import_interno_do_agente_nao_contam(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {
            "runtime/__init__.py": "",
            "runtime/ok.py": "from maezo.agents import AgentLoader\n",
            "runtime/doc.py": '"""Espelha `maezo.agents.helena.graph`."""\nX = 1\n',
            "runtime/frase.py": "MSG = 'veja maezo.agents.helena para detalhes'\n",
            "qa/sim.py": "from maezo.agents.helena.graph import HelenaGraph\n",
            "agents/helena/graph.py": "from maezo.agents.helena import prompts\nfrom . import prompts as p\n",
            "agents/helena/prompts.py": "",
        },
    )
    assert _chaves(src) == set()


def test_agente_importando_outro_agente_conta(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {
            "agents/lucas/graph.py": "from maezo.agents.helena.graph import HelenaState\n",
            "agents/lucas/rel.py": "from ..helena import graph\n",
            "agents/helena/graph.py": "",
        },
    )
    assert _chaves(src) == {
        "agente->agente src/maezo/agents/lucas/graph.py -> maezo.agents.helena.graph",
        "agente->agente src/maezo/agents/lucas/rel.py -> maezo.agents.helena.graph",
    }


def test_catraca_reprova_divida_nova_e_credito_velho(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {"platform/__init__.py": "", "platform/a.py": "from maezo.agents.helena import graph\n"},
    )
    baseline = tmp_path / "baseline.txt"
    argv = ["--src-dir", str(src), "--baseline", str(baseline)]

    assert main(argv) == 1, "baseline vazia: a dependencia existente e' nova"
    assert main([*argv, "--gerar-baseline"]) == 0
    assert main(argv) == 0

    (src / "platform" / "b.py").write_text("import maezo.agents.lucas\n", encoding="utf-8")
    assert main(argv) == 1, "dependencia nova fora da baseline"
    (src / "platform" / "b.py").unlink()

    (src / "platform" / "a.py").write_text("X = 1\n", encoding="utf-8")
    assert main(argv) == 1, "divida paga tem de sair da baseline, senao vira credito para reintroduzir"
    assert main([*argv, "--gerar-baseline"]) == 0
    assert main(argv) == 0
    assert ler_baseline(baseline) == set()


def test_baseline_ignora_comentarios_e_relatorio_agrupa_por_agente(tmp_path: Path) -> None:
    src = _arvore(
        tmp_path,
        {
            "platform/__init__.py": "",
            "platform/a.py": "from maezo.agents.helena.graph import X\nfrom maezo.agents.lucas.f import Y\n",
            "agents/helena/graph.py": "from maezo.runtime.inference import Z\n",
        },
    )
    deps, _, _ = coletar(src)
    baseline = tmp_path / "b.txt"
    escrever_baseline(baseline, deps)
    texto = baseline.read_text(encoding="utf-8")
    assert texto.startswith("#")
    assert ler_baseline(baseline) == {d.chave() for d in deps}
    md = relatorio(deps, src)
    assert "### `helena`" in md and "### `lucas`" in md
    assert "`maezo.runtime.inference` | 1" in md


def test_sem_agentes_ou_arquivo_que_nao_parseia_reprova(tmp_path: Path) -> None:
    vazio = tmp_path / "v" / "src" / "maezo"
    (vazio / "agents").mkdir(parents=True)
    _, erros, _ = coletar(vazio)
    assert erros and "vacua" in erros[0]

    src = _arvore(tmp_path, {"platform/quebrado.py": "def (:\n"})
    _, erros, _ = coletar(src)
    assert any("quebrado.py" in e and "nao parseia" in e for e in erros)
    assert main(["--src-dir", str(src), "--baseline", str(tmp_path / "x.txt")]) == 1
