"""Os arquivos-fonte do GRAFO de cada agente, para as cercas que leem codigo por AST.

Ate' 23/10/2026 o grafo de cada agente era UM arquivo (`agents/<id>/graph.py`) e as cercas varriam
`agents/*/graph.py`. Desde a divisao da Helena por habilidade (ADR-0067 §5; Plano de ajustes da Helena v2,
etapa 6), os metodos do grafo dela moram tambem em `roteador.py`, `travas_entrada.py`, `travas_saida.py`,
`grafo.py` e
`habilidades/**`. Uma cerca que continuasse so' no `graph.py` ficaria VAZIA para o codigo
movido — exatamente o modo de falha que as cercas existem para impedir. Esta e' a lista unica.
"""

from __future__ import annotations

from pathlib import Path

#: Alem do `graph.py`, os modulos de grafo de um agente dividido por habilidade (ADR-0067 §5).
_MODULOS_DIVIDIDOS: tuple[str, ...] = ("grafo.py", "roteador.py", "travas_entrada.py", "travas_saida.py")


def arquivos_de_grafo(raiz_agentes: Path) -> list[Path]:
    """`<id>/graph.py` de todo agente + os modulos de grafo divididos (sem `__init__.py`)."""
    arquivos = [*raiz_agentes.glob("*/graph.py")]
    for nome in _MODULOS_DIVIDIDOS:
        arquivos += raiz_agentes.glob(f"*/{nome}")
    arquivos += [p for p in raiz_agentes.glob("*/habilidades/**/*.py") if p.name != "__init__.py"]
    return sorted(set(arquivos))


def fonte_do_agente(graph_path: Path) -> str:
    """O codigo do grafo INTEIRO de um agente (o `graph.py` e os modulos divididos dele), num texto so', para
    as cercas que resolvem nomes dentro do grafo (ex.: os `*_INPUT_FIELDS` do `graph.py` valem para os
    sitios de prompt movidos). Os `from __future__` dos modulos seguintes saem (so' valem no topo)."""
    raiz = graph_path.parent.parent
    agente = graph_path.parent.name
    partes = [graph_path.read_text(encoding="utf-8")]
    for p in arquivos_de_grafo(raiz):
        if p != graph_path and agente_do_arquivo(raiz, p) == agente:
            texto = p.read_text(encoding="utf-8")
            partes.append(
                "\n".join(x for x in texto.split("\n") if not x.startswith("from __future__ import"))
            )
    return "\n\n".join(partes)


def agente_do_arquivo(raiz_agentes: Path, arquivo: Path) -> str:
    """O id do agente dono de um arquivo de grafo (o primeiro diretorio abaixo de `agents/`)."""
    return arquivo.relative_to(raiz_agentes).parts[0]


def rotulo(raiz_agentes: Path, arquivo: Path) -> str:
    """Um id de teste legivel e unico: `helena`, `helena/habilidades/saude/informar`..."""
    rel = arquivo.relative_to(raiz_agentes).with_suffix("").as_posix()
    return rel.removesuffix("/graph")
