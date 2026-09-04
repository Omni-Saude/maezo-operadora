"""CC-10 — nota de enriquecimento NUNCA carrega o texto cru de uma excecao.

## O vazamento que esta fence fecha

`gather` e' o no de enriquecimento best-effort de cada grafo: ele chama o leitor FHIR e, quando
a leitura falha, registra uma LACUNA em `gather_notes`. Essa lista nao morre no no — ela viaja
por dois caminhos, ambos fora da zona PHI:

1. para o PROMPT do dossie (`_build_dossier` monta `fatos["lacunas_enriquecimento"]` e renderiza
   os fatos no prompt do LLM); e
2. para a VARIAVEL DE PROCESSO `dossie_<agente>` (`_contract_variables`), que o engine sela na
   cadeia de custodia da ADR-0007 — zona geral, visivel no Cockpit.

O argumento de `read_patient`/`read_patient_summary`/`search_coverage` e' uma referencia de
paciente. A mensagem de um erro de cliente HTTP/FHIR tipicamente ecoa a URL ou o id que falhou
(`404 Not Found: .../Patient/<ref>`), de modo que `f"...: {exc}"` publica um identificador
resolvivel de beneficiario na zona geral. A postura correta e' a mesma que
`agents/beatriz/graph.py` (docstring do modulo, "FAIL-CLOSED FAILURE POSTURE") e
`agents/gustavo/graph.py::GustavoGraph.gather` ja praticam: a nota registra um TOKEN DE CLASSE
limitado (`type(exc).__name__`), e o trace completo vai para o log estruturado
(`exc_info=True`), que e' o canal de diagnostico — nao a nota selada.

Duas provas convivem neste arquivo:

- `test_no_agent_graph_interpolates_a_raw_exception_into_a_note` — fence ESTRUTURAL (AST) sobre
  `src/maezo/agents/*/graph.py`: nenhum `notes.append(...)` dentro de um `except ... as <nome>`
  pode interpolar `<nome>` cru. Impede a regressao em agentes futuros, nao so nos quatro
  corrigidos.
- `test_<agente>_gather_note_never_carries_the_exception_text` — prova COMPORTAMENTAL por
  agente: um leitor que levanta uma excecao cujo `str()` carrega um CPF sintetico, e a asserceao
  de que o CPF nao aparece em `gather_notes`, no prompt do dossie, no dossie, nem em
  `dossie_<agente>`/`_contract_variables`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.carolina.graph import CarolinaGraph, CarolinaState
from maezo.agents.marina.graph import MarinaGraph, MarinaState
from maezo.agents.rafael.graph import RafaelGraph, RafaelState
from maezo.agents.valentina.graph import ValentinaGraph, ValentinaState
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).resolve().parents[3] / "src" / "maezo" / "agents"

# CPF sintetico (nao pertence a ninguem; digitos verificadores validos para o algoritmo, o que o
# torna indistinguivel de um real para qualquer scanner de PHI — exatamente o ponto do teste).
_CPF_SINTETICO = "123.456.789-09"

# Formas do objeto de excecao que NUNCA podem entrar numa nota: o proprio nome ligado pelo
# `except ... as <nome>`, e as coercoes obvias para texto cru.
_ALLOWED_EXC_EXPRESSIONS = ("type({name}).__name__", "redact_error_message({name})")


# =================================================================================================
# (A) Fence estrutural — AST sobre os grafos vivos
# =================================================================================================


def _is_note_sink(node: ast.Call) -> bool:
    """`<algo>notes.append(...)` / `lacunas.append(...)` — os coletores de lacuna dos grafos."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "append":
        return False
    receiver = func.value
    if not isinstance(receiver, ast.Name):
        return False
    return receiver.id.endswith("notes") or receiver.id.endswith("lacunas")


def _raw_exception_uses(expr: ast.AST, exc_name: str) -> bool:
    """`expr` renderiza o texto CRU da excecao ligada a `exc_name`?

    Aceita as duas formas seguras (`type(exc).__name__`, `redact_error_message(exc)`); rejeita o
    nome nu e as coercoes para texto (`str(exc)`, `repr(exc)`, `exc.args`, ...).
    """
    rendered = ast.unparse(expr).strip()
    if rendered in tuple(form.format(name=exc_name) for form in _ALLOWED_EXC_EXPRESSIONS):
        return False
    return any(isinstance(inner, ast.Name) and inner.id == exc_name for inner in ast.walk(expr))


def _offending_sites(source: str, path: Path) -> list[str]:
    """Sitios `<nome>.append(...)` dentro de um `except ... as <exc>` que rendem `<exc>` cru."""
    offenders: list[str] = []
    for handler in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)):
        exc_name = handler.name
        if not exc_name:
            continue  # `except Exception:` sem binding — nada a interpolar (padrao da Beatriz).
        for call in (n for n in ast.walk(handler) if isinstance(n, ast.Call)):
            if not _is_note_sink(call):
                continue
            for arg in call.args:
                # f-string: cada `{...}` e' um `FormattedValue`; fora dela o argumento inteiro.
                candidates = (
                    [fv.value for fv in ast.walk(arg) if isinstance(fv, ast.FormattedValue)]
                    if isinstance(arg, ast.JoinedStr)
                    else [arg]
                )
                for candidate in candidates:
                    if _raw_exception_uses(candidate, exc_name):
                        offenders.append(
                            f"{path}:{call.lineno} — `{ast.unparse(candidate).strip()}` "
                            f"(excecao ligada como `{exc_name}`) numa nota de enriquecimento"
                        )
    return offenders


def test_no_agent_graph_interpolates_a_raw_exception_into_a_note() -> None:
    """CC-10: nenhuma nota de lacuna publica o texto cru de uma excecao na zona geral.

    Somente o TOKEN DE CLASSE (`type(exc).__name__`) ou o texto ja redigido por
    `tools/workers/phi_vars.py::redact_error_message` sao admitidos — o trace vai para o log
    estruturado, nunca para a nota que o engine sela em `dossie_<agente>`.
    """
    graphs = sorted(_AGENTS_ROOT.glob("*/graph.py"))
    assert graphs, f"nenhum grafo encontrado em {_AGENTS_ROOT} — fence sem alvo"

    offenders: list[str] = []
    for path in graphs:
        offenders.extend(_offending_sites(path.read_text(encoding="utf-8"), path))

    assert not offenders, "texto cru de excecao em nota de enriquecimento (CC-10):\n" + "\n".join(offenders)


# =================================================================================================
# (B) Provas comportamentais por agente — o CPF nao atravessa o `gather`
# =================================================================================================


class _LeakyReader:
    """Leitor FHIR cuja falha ecoa a referencia do paciente — a forma real do vazamento.

    Um cliente HTTP/FHIR real levanta com a URL/id que falhou no `str()` da excecao; aqui o id
    e' um CPF sintetico para tornar o vazamento detectavel por asserceao textual.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _raise(self, patient_id: str) -> None:
        self.calls.append(patient_id)
        raise RuntimeError(f"paciente {_CPF_SINTETICO} nao encontrado em /Patient/{patient_id}")

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self._raise(patient_id)
        raise AssertionError("inalcancavel")

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        self._raise(patient_id)
        raise AssertionError("inalcancavel")

    async def search_coverage(self, patient_id: str) -> Any:
        self._raise(patient_id)
        raise AssertionError("inalcancavel")


class _RecordingInference:
    """Provedor de inferencia falso que GUARDA o prompt — o prompt e' uma das duas saidas."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.task_kinds: list[str | None] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        self.prompts.append(prompt)
        return "narrativa factual sintetica"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _rafael() -> tuple[RafaelGraph, _RecordingInference, _LeakyReader, RafaelState]:
    inference, fhir = _RecordingInference(), _LeakyReader()
    graph = RafaelGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=fhir,
    )
    state: RafaelState = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-001",
        "beneficiario_pseudo_id": "pseudo-123",
        "prestador_id": "prestador-1",
        "canal": "portal_tiss",
        "codigo_procedimento_tuss": "40304361",
        "carater_atendimento": "eletivo",
        "valor_estimado_brl": 1200.0,
        "documentos_refs": [],
        "patient_ref": "patient-1",
        "route": "human_auditor",
    }
    return graph, inference, fhir, state


def _marina() -> tuple[MarinaGraph, _RecordingInference, _LeakyReader, MarinaState]:
    inference, fhir = _RecordingInference(), _LeakyReader()
    graph = MarinaGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=fhir,
    )
    state: MarinaState = {
        "tenant_id": "amh",
        "fluxo": "contas",
        "numero_conta": "CONTA-001",
        "prestador_id": "prestador-1",
        "canal": "portal",
        "valor_apresentado_brl": 900.0,
        "patient_summary_ref": "patient-1",
        "route": "human_review",
    }
    return graph, inference, fhir, state


def _carolina() -> tuple[CarolinaGraph, _RecordingInference, _LeakyReader, CarolinaState]:
    inference, fhir = _RecordingInference(), _LeakyReader()
    graph = CarolinaGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=fhir,
    )
    state: CarolinaState = {
        "tenant_id": "amh",
        "prestador_id": "prestador-1",
        "canal": "portal",
        "direcao": "credenciamento",
        "tipo_prestador": "clinica",
        "origem_solicitacao": "prestador",
        "data_solicitacao_iso": "2026-07-16",
        "documentos_refs": [],
        "patient_summary_ref": "patient-1",
        "route": "human_review",
    }
    return graph, inference, fhir, state


def _valentina() -> tuple[ValentinaGraph, _RecordingInference, _LeakyReader, ValentinaState]:
    inference, fhir = _RecordingInference(), _LeakyReader()
    graph = ValentinaGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=fhir,
    )
    state: ValentinaState = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-123",
        "programa": "cronicos",
        "canal": "app",
        "consentimento_ativo": True,
        "patient_summary_ref": "patient-1",
        "route": "human_review",
    }
    return graph, inference, fhir, state


async def _dossier_of(graph: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Chama o `_build_dossier` real do agente (assinaturas divergem por grafo)."""
    if isinstance(graph, RafaelGraph):
        return await graph._build_dossier(state)
    if isinstance(graph, ValentinaGraph):
        dossier, _ = await graph._build_dossier(state, route="human_review")
        return dossier
    return await graph._build_dossier(state, route="human_review")


@pytest.mark.parametrize(
    ("agente", "factory"),
    [
        ("rafael", _rafael),
        ("marina", _marina),
        ("carolina", _carolina),
        ("valentina", _valentina),
    ],
)
async def test_gather_note_never_carries_the_exception_text(agente: str, factory: Any) -> None:
    """CC-10 fim a fim: a falha do leitor vira token de classe, nunca o CPF da mensagem.

    Percorre exatamente a rota do vazamento: `gather` -> `gather_notes` -> prompt do dossie ->
    `dossier["fatos"]["lacunas_enriquecimento"]` -> `dossie_<agente>` em `_contract_variables`
    (a variavel de processo que o engine sela na zona geral).
    """
    graph, inference, fhir, state = factory()

    gathered = await graph.gather(state)
    notes = gathered["gather_notes"]
    assert fhir.calls, "o leitor precisa ter sido chamado — senao o teste nao prova nada"
    assert notes, "a lacuna tem de ser registrada (degradacao explicita, nunca silenciosa)"
    assert any("indisponivel" in note for note in notes)

    enriched = {**state, **gathered}
    dossier = await _dossier_of(graph, enriched)
    variables = graph._contract_variables({**enriched, "dossier": dossier})

    assert inference.prompts, "o dossie tem de ter chamado o LLM (o prompt e' uma das saidas)"
    surfaces = {
        "gather_notes": _dump(notes),
        "prompt_dossie": inference.prompts[0],
        "dossier": _dump(dossier),
        f"dossie_{agente}": _dump(variables[f"dossie_{agente}"]),
        "contract_variables": _dump(variables),
    }
    for nome, rendered in surfaces.items():
        assert _CPF_SINTETICO not in rendered, f"CPF vazou em {nome}"
        assert "nao encontrado" not in rendered, f"mensagem crua da excecao vazou em {nome}"

    # E o token de classe SOBREVIVE — a redacao nao pode custar o diagnostico.
    assert any("RuntimeError" in note for note in notes)
