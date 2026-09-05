"""Todo falso de inferencia em `tests/` acompanha o Protocol real (CC-12 x integracao lote3).

DE ONDE VEM ESTE ARQUIVO (fleet audit, WP lote3-integration-fakes-task-kind, 04/09/2026)

O job CI `integration tests (real engine)` do PR #319 (run 33927690960, branch
`fleet/train-w3-lote3`) deu `5 failed, 499 passed`:
`TypeError: _FakeInference.generate() got an unexpected keyword argument 'task_kind'`
em `tests/integration/agents/test_helena_escalation.py` (dois falsos, linhas 96 e 323),
`test_marina_contas_dossier.py` (linha 55) e `test_rafael_auth_dossier.py` (linha 54).

Causa-raiz: CC-12 acrescentou `task_kind` ao Protocol da facade externa
(`runtime/inference::InferenceProvider.generate`, hoje
`(self, prompt, *, phi=False, agent_id=None, tenant_id=None, task_kind=None)`) e toda chamada
REAL `self._llm.generate(...)` dentro de um `graph.py` passou a declarar
`task_kind=` (cerca irma `test_llm_calls_declare_task_kind.py`). Os falsos acima foram escritos
ANTES de CC-12 e nunca atualizados -- exatamente a mesma especie do defeito `f1bc87f`
(`tests/unit/agents/test_gather_notes_no_raw_exception.py::_RecordingInference`, corrigido no
lane UNITARIO pelo integration-engineer-lote3b, que registrou como risco nao ter auditado "os
demais falsos" do repo). Esta cerca fecha esse risco: varre `tests/` inteiro por AST, nao so'
`tests/integration/`.

O QUE ESTA CERCA COBRE E O QUE NAO COBRE

`InferenceProvider` (a FACADE externa que os grafos chamam via `self._llm`/`self._inference`) e'
UM protocolo. Mas `runtime/inference/providers.py::BaseInferenceProvider` (o `_impl` que a
facade delega DEPOIS de resolver `phi`/`task_kind` -- `InferenceProvider.generate` linha ~624,
`return await self._impl.generate(prompt, agent_id=agent_id, tenant_id=tenant_id)`) e' OUTRO
protocolo, deliberadamente mais estreito: seu `generate` nunca recebe `phi` nem `task_kind`
(ambos sao resolvidos e consumidos pela propria facade ANTES de delegar -- ver o comentario
"ORDER IS THE INVARIANT (I-6)" no modulo). `tests/unit/runtime/test_inference_capabilities.py`
tem varios falsos desse segundo protocolo (`_ProbeChild`, `_ProbeSiblingModule`,
`WellFormedProvider`) que corretamente NAO declaram `phi` nem `task_kind` -- exigir os dois ali
seria falso-positivo, cercando o protocolo ERRADO.

O discriminador usado aqui e' a presenca de `phi` nos kwonly de `generate`: e' o marcador da
facade externa (o `_impl` categoricamente nunca o recebe, por construcao do modulo -- ver acima),
e um controle ADR-0006 dificil de remover por acidente. So' esse discriminador e' fixo; O QUE E'
EXIGIDO de cada falso identificado como facade e' lido de `inspect.signature(
InferenceProvider.generate)` em tempo de execucao -- nao hardcoded -- entao um kwonly NOVO que o
dono acrescente amanha ao Protocol real (alem de `task_kind`) quebra esta cerca imediatamente
para todo falso que nao o aceitar, sem precisar editar este arquivo.

Um falso com `**kwargs` (`async def generate(self, prompt, **_kwargs)`) sempre passa --
absorve qualquer kwonly futuro por construcao, entao nao ha' o que exigir dele.

DECISAO DE ESCOPO (autorizada pelo brief do WP): a varredura aponta falsos em `tests/unit/` que
hoje PASSAM (nenhum no atualmente exercitado por eles chama `.generate(task_kind=...)`) mas
ficariam quebrados no dia em que passassem a chamar -- mesma deriva do `f1bc87f`. Foram
corrigidos mecanicamente (kwarg `task_kind: str | None = None` acrescentado, nenhuma asserção
de comportamento alterada) em vez de deixados como debito, porque o proprio ponto desta cerca e'
fechar essa classe de defeito de uma vez, nao so' os 4 do CI. Ver `docs/evidence-ledger.md`
(linha `LOTE3-INTEGRATION-FAKES-TASK-KIND`) para a contagem exata e a lista arquivo:linha.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from maezo.runtime.inference import InferenceProvider

_RAIZ = Path(__file__).resolve().parents[3]
_TESTS_ROOT = _RAIZ / "tests"

#: Marcador de que um `generate()` de teste esta' duck-typing a FACADE externa
#: (`InferenceProvider`), nao o `_impl` interno -- ver docstring do modulo acima.
_MARCADOR_FACADE = "phi"


def _protocol_kwonly_names() -> tuple[str, ...]:
    """Nomes dos parametros keyword-only do Protocol REAL, lidos por `inspect.signature` --
    nunca hardcoded, para que um kwonly novo amanha quebre esta cerca sem editar o arquivo."""
    sig = inspect.signature(InferenceProvider.generate)
    return tuple(
        nome
        for nome, param in sig.parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    )


_PROTOCOL_KWONLY = _protocol_kwonly_names()


def _kwonly_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {a.arg for a in fn.args.kwonlyargs}


def _achados_em_fonte(fonte: str, rotulo: str) -> list[str]:
    """Toda classe com um metodo `generate` que declara `phi` (marcador da facade) e NAO cobre
    todo `_PROTOCOL_KWONLY` (nem via `**kwargs`) -- devolve uma linha de achado por ocorrencia."""
    try:
        arvore = ast.parse(fonte)
    except SyntaxError:
        return []
    achados: list[str] = []
    for classe in (n for n in ast.walk(arvore) if isinstance(n, ast.ClassDef)):
        for item in classe.body:
            if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if item.name != "generate":
                continue
            kwonly = _kwonly_names(item)
            if _MARCADOR_FACADE not in kwonly:
                continue  # nao e' um duplo da facade (ex.: `_impl`/`BaseInferenceProvider`) -- fora do escopo
            tem_kwargs_catchall = item.args.kwarg is not None
            faltando = sorted(set(_PROTOCOL_KWONLY) - kwonly)
            if faltando and not tem_kwargs_catchall:
                achados.append(
                    f"{rotulo}:{item.lineno} — classe `{classe.name}` aceita {sorted(kwonly)} "
                    f"mas falta {faltando} do Protocol real "
                    "(`runtime/inference::InferenceProvider.generate`)"
                )
    return achados


def _achados_em_arquivo(caminho: Path) -> list[str]:
    return _achados_em_fonte(
        caminho.read_text(encoding="utf-8"), str(caminho.relative_to(_RAIZ))
    )


# =================================================================================================
# (A) Fence estrutural — AST sobre tests/ inteiro
# =================================================================================================


def test_falsos_de_inferencia_em_tests_aceitam_todo_kwonly_do_protocol_real() -> None:
    """Nenhum falso que duck-typa a facade `InferenceProvider` pode ficar sem um kwonly do
    Protocol real -- e' a mesma especie do defeito `f1bc87f`: uma chamada real
    `self._llm.generate(..., task_kind=...)` levanta `TypeError` contra um falso desatualizado,
    o `except Exception` do no engole, e o sintoma vira "resposta vazia"/"prompt vazio" em vez de
    um erro de teste claro (ver `INTEGRATION-LOTE3B.md` §"Defeito de INTEGRACAO encontrado").
    """
    arquivos = sorted(_TESTS_ROOT.rglob("*.py"))
    assert arquivos, f"nenhum arquivo em {_TESTS_ROOT} — cerca sem alvo"

    ofensores: list[str] = []
    for caminho in arquivos:
        ofensores.extend(_achados_em_arquivo(caminho))

    assert not ofensores, (
        "falso de inferencia em tests/ desalinhado do Protocol real "
        "(`runtime/inference::InferenceProvider.generate`) — mesma especie do defeito f1bc87f:\n"
        + "\n".join(ofensores)
    )


# =================================================================================================
# (B) Controles do proprio discriminador — prova de que a cerca faz o que afirma
# =================================================================================================


def test_protocolo_real_tem_phi_e_task_kind_hoje() -> None:
    """Premissa: o Protocol real declara `phi` (o marcador usado para identificar um falso da
    facade) e `task_kind` (o kwonly cuja falta causou o defeito do PR #319) — se algum dia
    deixar de declarar um dos dois, esta premissa quebra ANTES da cerca principal, apontando
    direto para a causa (Protocol mudou), nao para "todo falso do repo esta errado"."""
    assert "phi" in _PROTOCOL_KWONLY
    assert "task_kind" in _PROTOCOL_KWONLY


def test_achados_recusa_falso_sem_task_kind_da_facade() -> None:
    """Sondagem direta do helper (sem depender de nenhum arquivo real de tests/ continuar
    quebrado no futuro): um falso com `phi` mas sem `task_kind` E' apontado."""
    fonte = (
        "class F:\n"
        "    async def generate(self, prompt, *, phi=False, agent_id=None, tenant_id=None):\n"
        "        return ''\n"
    )
    achados = _achados_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "sintetico.py:2" in achados[0]
    assert "task_kind" in achados[0]


def test_achados_aceita_falso_com_task_kind_nomeado_ou_kwargs_catchall() -> None:
    """As duas formas aceitas pelo brief: o kwarg `task_kind` nomeado, OU um `**kwargs`
    catch-all (o padrao de `tests/unit/gateway/seams/test_live_dispatch_wiring.py`)."""
    fonte_nomeado = (
        "class F:\n"
        "    async def generate(self, prompt, *, phi=False, agent_id=None, tenant_id=None,"
        " task_kind=None):\n"
        "        return ''\n"
    )
    fonte_kwargs = (
        "class F:\n"
        "    async def generate(self, prompt, *, phi=False, **_kwargs):\n"
        "        return ''\n"
    )
    assert _achados_em_fonte(fonte_nomeado, "sintetico.py") == []
    assert _achados_em_fonte(fonte_kwargs, "sintetico.py") == []


def test_achados_ignora_falso_do_impl_layer_sem_phi() -> None:
    """NEGATIVO: um falso do protocolo `_impl` (`BaseInferenceProvider`-style, sem `phi` nem
    `task_kind` — ex.: `test_inference_capabilities.py::_ProbeChild`) NAO e' exigido a declarar
    `task_kind`, porque nao e' a facade externa. Sem este controle, a cerca principal
    marcaria falso-positivo sobre um protocolo diferente por design (ver docstring do modulo)."""
    fonte = (
        "class ImplFalso:\n"
        "    async def generate(self, prompt, *, agent_id=None, tenant_id=None):\n"
        "        return ''\n"
    )
    assert _achados_em_fonte(fonte, "sintetico.py") == []
