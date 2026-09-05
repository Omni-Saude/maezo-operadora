"""Toda chamada LLM dos grafos declara um `task_kind` catalogado (CC-12 / HEL-02 / BEA-01 / GUS-07).

DE ONDE VEM ESTE ARQUIVO (auditoria da frota, 04/09/2026)

`InferenceProvider.generate(..., task_kind: str | None = None)` (`runtime/inference/__init__.py`)
resolve o modelo por `_resolve_task_model` (`MODEL_TASK_KINDS = {task_default, reasoning,
batch}`). Hoje o efeito de OMITIR `task_kind` e' apenas TELEMETRIA (`record_llm_tier_resolution`,
`resolution="modelo_unico"` — um so' modelo serve todo mundo). O achado (CC-12/BEA-01) e' que no
dia em que o dono mapear tiers por `task_kind` em `spec/agents/<agente>/agent.yaml::model`, toda
chamada sem o kwarg passa a pegar o TIER ERRADO em silencio — sem esta cerca, esse dia chega sem
aviso.

Only `lucas/graph.py` ja' declarava `task_kind` nos seus tres sitios (`_build_message` x2 =
`task_default`, `_build_dossier` = `reasoning`) — o padrao que este WP estende aos outros 9
agentes que chamam um LLM.

ESTE ARQUIVO TRAVA DUAS COISAS:

1. A FENCE (`test_toda_chamada_generate_declara_task_kind_catalogado`): AST sobre
   `agents/*/graph.py` (e `delegation.py`/`adapters.py`, que hoje NAO chamam `.generate` — a
   fence os cobre de graca, para o dia em que passarem a chamar). O alvo e' todo `ast.Call` cujo
   `func` e' `<algo>.generate` e cujo receptor e' `self._llm` ou `self._inference` (os dois nomes
   de atributo usados pelos grafos reais — `_template/graph.py` usa `_inference` mas nao chama
   `generate` hoje). Exige um kwarg `task_kind=` cujo VALOR e' um literal de string presente em
   `MODEL_TASK_KINDS` — nao basta o kwarg existir com um valor computado ou fora do vocabulario
   ADR-0009 §2 (isso e' o que `_resolve_task_model` recusaria em runtime, e o que a fence recusa
   em CI).

2. O COMPORTAMENTO, por agente (em cada `test_<agente>.py`, reaproveitando os duplos de
   inferencia existentes la', estendidos de forma aditiva para gravar `task_kind`): um caso por
   NO chamado, provando que a TABELA agente->no->task_kind esta' coberta — nao so' a presenca do
   kwarg. Ver `docs/evidence-ledger.md` (linha CC-12) para a tabela completa dos 12 sitios (9
   agentes; `lucas` ja' conforme; `_template` sem sitio real).

O QUE ESTE ARQUIVO NAO E'. Nao decide qual tier cada `task_kind` usa — isso e' o mapa
`agent.yaml::model` (ADR-0044, decisao do dono) e `_resolve_task_model`, intocados aqui.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from maezo.runtime.inference import MODEL_TASK_KINDS

#: `MODEL_TASK_KINDS` inclui `"batch"` no runtime (ADR-0009 §2), mas nenhum sitio de grafo pode
#: DECLARAR `task_kind="batch"` ate' o dono ratificar o ADR-0044 (mapeamento de tiers por
#: task_kind) — sem essa ratificacao nao ha' definicao do que "batch" significa para um grafo
#: sincrono de agente. A constante em si continua existindo e servindo outros consumidores
#: (ex.: filas de processamento assincrono fora dos grafos); esta cerca so' restringe o
#: vocabulario ACEITO NOS SITIOS DE CHAMADA cobertos por este arquivo.
_TASK_KINDS_PERMITIDOS_NOS_GRAFOS = MODEL_TASK_KINDS - {"batch"}

_RAIZ = Path(__file__).resolve().parents[3]
_ARQUIVOS_AGENTES = sorted(
    {
        *(_RAIZ / "src" / "maezo" / "agents").glob("*/graph.py"),
        *(_RAIZ / "src" / "maezo" / "agents").glob("*/delegation.py"),
        *(_RAIZ / "src" / "maezo" / "agents").glob("*/adapters.py"),
    }
)

#: Os dois nomes de atributo sob os quais um grafo guarda seu seam de `InferenceProvider`
#: (`self._llm = inference` em 10/11 agentes; `self._inference = inference` no `_template`).
_ATRIBUTOS_DE_INFERENCIA: tuple[str, ...] = ("_llm", "_inference")


def _receptor_e_seam_de_inferencia(no: ast.expr) -> bool:
    """`no` e' `self._llm` / `self._inference` (ou `self.<algo>._llm`, para um dia em que um
    grafo delegue a um sub-objeto — a checagem e' pelo NOME do atributo terminal, nao pela
    cadeia inteira, entao cobre ambas as formas)."""
    return (
        isinstance(no, ast.Attribute)
        and no.attr in _ATRIBUTOS_DE_INFERENCIA
        and isinstance(no.value, ast.Name)
        and no.value.id == "self"
    )


def _achados_de_task_kind(fonte: str) -> list[str]:
    """Toda `Call` a `<seam>.generate(` sem um kwarg `task_kind` que seja um literal de string
    catalogado em `_TASK_KINDS_PERMITIDOS_NOS_GRAFOS` (ADR-0009 §2) — que e' `MODEL_TASK_KINDS`
    MENOS `"batch"`, ate' o dono ratificar o ADR-0044."""
    achados: list[str] = []
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        func = no.func
        if not (isinstance(func, ast.Attribute) and func.attr == "generate"):
            continue
        if not _receptor_e_seam_de_inferencia(func.value):
            continue

        kw = next((k for k in no.keywords if k.arg == "task_kind"), None)
        if kw is None:
            achados.append(f"linha {no.lineno}: chamada a .generate( sem kwarg task_kind")
            continue
        if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
            achados.append(
                f"linha {no.lineno}: task_kind nao e' um literal de string "
                "(precisa ser decidivel estaticamente, nao computado em runtime)"
            )
            continue
        if kw.value.value not in _TASK_KINDS_PERMITIDOS_NOS_GRAFOS:
            achados.append(
                f"linha {no.lineno}: task_kind={kw.value.value!r} fora de "
                f"{sorted(_TASK_KINDS_PERMITIDOS_NOS_GRAFOS)} (ADR-0009 §2; "
                '"batch" existe em MODEL_TASK_KINDS mas fica proibido nos grafos ate\' '
                "o dono ratificar o ADR-0044)"
            )
    return achados


@pytest.mark.parametrize("arquivo", _ARQUIVOS_AGENTES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_toda_chamada_generate_declara_task_kind_catalogado(arquivo: Path) -> None:
    """CC-12 / HEL-02 / BEA-01 / GUS-07: nenhuma chamada LLM de um grafo pode ficar de fora do
    vocabulario `task_kind` (ADR-0009 §2) — hoje o efeito e' so' telemetria
    (`record_llm_tier_resolution`), mas e' o unico mecanismo que existe para o dia em que o dono
    mapear tiers por `task_kind` (`spec/agents/<agente>/agent.yaml::model`).

    RED nos 12 sitios de 9 agentes antes da correcao (andre, beatriz, carolina, fernando x2,
    gustavo, helena x3, marina, rafael, valentina); `lucas` (3 sitios) e `_template` (sem sitio
    real) ja' passam.
    """
    achados = _achados_de_task_kind(arquivo.read_text(encoding="utf-8"))
    assert not achados, (
        f"{arquivo.relative_to(_RAIZ)}: {achados}. Toda chamada a `self._llm.generate(...)` "
        'precisa de `task_kind="task_default"` (classificacao/extracao/mensagem curta) ou '
        '`task_kind="reasoning"` (dossie/narrativa/resumo que o humano le antes de decidir), '
        'nunca "batch" (ADR-0044, decisao do dono). Siga o padrao de '
        "`lucas/graph.py` (kwarg `task_kind=` na mesma chamada, comentario curto "
        "'ADR-0009 §2 / CC-12')."
    )


def test_achados_de_task_kind_recusa_batch() -> None:
    """A2 (VERIFY-CC12): `"batch"` ESTA' em `MODEL_TASK_KINDS` (o runtime aceita) mas a fence
    recusa a DECLARACAO em um sitio de grafo ate' o dono ratificar o ADR-0044 — sondagem direta
    do helper (sem tocar nos 12 sitios reais), para nao depender de nenhum grafo em particular
    continuar chamando `.generate(task_kind="batch")` no futuro."""
    assert "batch" in MODEL_TASK_KINDS, "premissa: 'batch' e' um task_kind catalogado no runtime"

    fonte = (
        'class G:\n    async def no(self):\n        return await self._llm.generate("p", task_kind="batch")\n'
    )
    achados = _achados_de_task_kind(fonte)
    assert len(achados) == 1
    assert "task_kind='batch'" in achados[0]
    assert "batch" not in _TASK_KINDS_PERMITIDOS_NOS_GRAFOS

    fonte_ok = fonte.replace('"batch"', '"task_default"')
    assert _achados_de_task_kind(fonte_ok) == []
