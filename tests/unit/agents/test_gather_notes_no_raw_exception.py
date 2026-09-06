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
from maezo.agents.gustavo.graph import GustavoGraph, GustavoState
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


#: Metodos que ACRESCENTAM a uma lista. `append` sozinho era o ponto cego original: a auditoria
#: de garantia mutou `notes.append(...)` para `notes.extend([...])` no MESMO sitio, com o MESMO
#: vazamento, e esta cerca permaneceu VERDE (`$S/phase1/ASSURANCE-F1-A1.md`, mutacao M2).
_SINK_METHODS = frozenset({"append", "extend", "insert"})


def _is_note_sink(node: ast.Call) -> bool:
    """`<qualquer receptor>.append|extend|insert(...)` — os coletores de lacuna dos grafos.

    ENDURECIDA (CC-10-FENCE). A versao original exigia `ast.Attribute` com `attr == "append"` e
    receptor `ast.Name` terminado em `notes`/`lacunas`. Isso deixava passar, com o mesmo destino
    selado, `.extend`/`.insert`, receptor `self.notes` (`ast.Attribute`), `state["notes"]`
    (`ast.Subscript`) e qualquer nome de colecao fora das duas terminacoes (`observacoes`,
    `pendencias`). O nome do receptor era a parte fraca do teste: um sitio dentro de um
    `except ... as exc` que ACRESCENTA texto a uma lista e' um sink de nota por construcao,
    qualquer que seja o nome da lista.
    """
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in _SINK_METHODS


def _raw_exception_uses(expr: ast.AST, exc_name: str) -> bool:
    """`expr` renderiza o texto CRU da excecao ligada a `exc_name`?

    Aceita as duas formas seguras (`type(exc).__name__`, `redact_error_message(exc)`); rejeita o
    nome nu e as coercoes para texto (`str(exc)`, `repr(exc)`, `exc.args`, ...).
    """
    rendered = ast.unparse(expr).strip()
    if rendered in tuple(form.format(name=exc_name) for form in _ALLOWED_EXC_EXPRESSIONS):
        return False
    return any(isinstance(inner, ast.Name) and inner.id == exc_name for inner in ast.walk(expr))


def _candidates(expr: ast.AST) -> list[ast.AST]:
    """As sub-expressoes que RENDERIZAM: cada `{...}` de uma f-string, ou a expressao inteira."""
    if isinstance(expr, ast.JoinedStr):
        return [fv.value for fv in ast.walk(expr) if isinstance(fv, ast.FormattedValue)]
    return [expr]


def _sink_payloads(handler: ast.ExceptHandler) -> list[tuple[int, ast.AST]]:
    """Toda expressao que ENTRA num coletor de lacuna dentro deste handler.

    Duas formas, nao uma: a CHAMADA (`notes.append(x)` / `.extend([x])` / `.insert(0, x)`) e a
    ATRIBUICAO AUMENTADA (`notes += [x]`), que nao e' um `ast.Call` e por isso era invisivel para
    a versao anterior desta cerca.
    """
    payloads: list[tuple[int, ast.AST]] = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Call) and _is_note_sink(node):
            for arg in node.args:
                # `.extend([...])` / `.append([...])`: o payload real esta DENTRO da lista.
                inner = arg.elts if isinstance(arg, ast.List | ast.Tuple | ast.Set) else [arg]
                for item in inner:
                    payloads.extend((node.lineno, c) for c in _candidates(item))
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            alvo = ast.unparse(node.target).strip()
            if not (alvo.endswith("notes") or alvo.endswith("lacunas") or alvo.endswith('"]')):
                continue
            valor = node.value
            inner = valor.elts if isinstance(valor, ast.List | ast.Tuple | ast.Set) else [valor]
            for item in inner:
                payloads.extend((node.lineno, c) for c in _candidates(item))
    return payloads


def _offending_sites(source: str, path: Path) -> list[str]:
    """Sitios que ACRESCENTAM a uma lista dentro de um `except ... as <exc>` rendendo `<exc>` cru."""
    offenders: list[str] = []
    for handler in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)):
        exc_name = handler.name
        if not exc_name:
            continue  # `except Exception:` sem binding — nada a interpolar (padrao da Beatriz).
        for lineno, candidate in _sink_payloads(handler):
            if _raw_exception_uses(candidate, exc_name):
                offenders.append(
                    f"{path}:{lineno} — `{ast.unparse(candidate).strip()}` "
                    f"(excecao ligada como `{exc_name}`) numa nota de enriquecimento"
                )
    return offenders


def test_the_note_fence_sees_the_sink_shapes_it_used_to_be_blind_to() -> None:
    """Nao-vacuidade do ENDURECIMENTO: as formas medidas como cegas passam a ser acusadas.

    Cada linha abaixo saiu da tabela de pontos cegos da auditoria de garantia (mutacao M2 provou
    `.extend` viva no proprio `rafael/graph.py`). Sem esta prova, "endurecemos a cerca" seria uma
    afirmacao sem evidencia — a cerca fica verde com ou sem o endurecimento, porque a arvore atual
    ja' nao tem nenhum sitio cru.
    """
    cegas = [
        'notes.extend([f"x: {exc}"])',
        'notes += [f"x: {exc}"]',
        'self.notes.append(f"x: {exc}")',
        'state["notes"].append(f"x: {exc}")',
        'observacoes.append(f"x: {exc}")',
        'pendencias.append(f"x: {exc}")',
        'notes.insert(0, f"x: {exc}")',
        "notes.append(str(exc))",
        'lacunas.append(f"x: {exc}")',
    ]
    for forma in cegas:
        fonte = f"def f():\n    try:\n        g()\n    except Exception as exc:\n        {forma}\n"
        assert _offending_sites(fonte, Path("<sintetico>")), f"forma NAO acusada: {forma}"

    seguras = [
        'notes.append(f"x: {type(exc).__name__}")',
        'notes.extend([f"x: {redact_error_message(exc)}"])',
        'notes.append("constante sem excecao")',
        'notes += ["constante sem excecao"]',
    ]
    for forma in seguras:
        fonte = f"def f():\n    try:\n        g()\n    except Exception as exc:\n        {forma}\n"
        assert not _offending_sites(fonte, Path("<sintetico>")), f"falso positivo: {forma}"


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
        # INTEGRACAO lote2 x lote3: este falso nasceu com CC-10 antes de CC-12 acrescentar
        # `task_kind` ao Protocol (`runtime/inference::InferenceProvider.generate`). Sem o
        # parametro, a chamada real levantava `TypeError`, que o `except Exception` de
        # `_build_dossier` engolia -> `narrativa=""` e `self.prompts` VAZIO: a fence deixava de
        # provar o que afirma (o prompt e' uma das duas superficies auditadas) e a propria
        # asserceao de pre-condicao reprovava. A assinatura acompanha o Protocol de proposito.
        task_kind: str | None = None,
    ) -> str:
        self.prompts.append(prompt)
        self.task_kinds.append(task_kind)
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


def _gustavo() -> tuple[GustavoGraph, _RecordingInference, _LeakyReader, GustavoState]:
    inference, fhir = _RecordingInference(), _LeakyReader()
    graph = GustavoGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=fhir,
    )
    state: GustavoState = {
        "tenant_id": "amh",
        "fluxo": "nip",
        "canal": "portal",
        "numero_nip_ans": "NIP-001",
        "beneficiario_pseudo_id": "pseudo-123",
        "classificacao_nip": "assistencial",
        "tema_nip": "cobertura",
        "data_recebimento_nip_iso": "2026-07-16",
        "documentos_refs": [],
        "patient_ref": "patient-1",
        "route": "instruct_nip",
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
    if isinstance(graph, GustavoGraph):
        # Gustavo nao tem a rota `human_review`: o seu vocabulario de rota e' por FLUXO
        # (`review_submission` | `instruct_nip`), nunca por merito.
        return await graph._build_dossier(state, route="instruct_nip")
    return await graph._build_dossier(state, route="human_review")


#: Os agentes cobertos pela prova COMPORTAMENTAL. NAO e' uma lista de conveniencia: a cerca
#: `test_behavioural_coverage_matches_the_ast_walk` abaixo prova, por walk AST, que ela e'
#: EXATAMENTE o conjunto de agentes cujo `gather` registra uma nota a partir da EXCECAO ligada
#: por um `except ... as <nome>` — que e' onde o vazamento de CC-10 pode existir.
_FACTORIES: dict[str, Any] = {
    "rafael": _rafael,
    "marina": _marina,
    "carolina": _carolina,
    "valentina": _valentina,
    "gustavo": _gustavo,
}


def test_behavioural_coverage_matches_the_ast_walk() -> None:
    """Inventario FECHADO: a parametrizacao curada == o walk AST dos sitios de risco.

    O ponto cego #2 medido pela auditoria de garantia: a metade COMPORTAMENTAL de CC-10 cobria
    QUATRO agentes de nove, escolhidos a mao, sem nada que acusasse a deriva. `gustavo` tinha
    exatamente a mesma forma (`notes.append(f"...: {type(exc).__name__}")` dentro do `except` do
    leitor FHIR do `gather`) e estava de fora. Esta cerca torna a omissao impossivel de repetir:
    qualquer agente que ganhe (ou perca) um sink de nota alimentado pela excecao no `gather`
    quebra aqui ate a parametrizacao acompanhar.

    Agentes deliberadamente FORA, porque a nota deles nao interpola excecao nenhuma (constante
    literal): `andre` e `beatriz`. Sem sink de nota no `gather`: `_template`, `fernando`,
    `helena`, `lucas`.
    """
    do_ast: set[str] = set()
    for path in sorted(_AGENTS_ROOT.glob("*/graph.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        gathers = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "gather"
        ]
        for gather in gathers:
            for handler in (n for n in ast.walk(gather) if isinstance(n, ast.ExceptHandler)):
                nome = handler.name
                if not nome:
                    continue
                for lineno, candidate in _sink_payloads(handler):
                    del lineno
                    if any(isinstance(inner, ast.Name) and inner.id == nome for inner in ast.walk(candidate)):
                        do_ast.add(path.parent.name)

    assert do_ast == set(_FACTORIES), (
        f"walk AST de `gather` = {sorted(do_ast)} != parametrizacao curada "
        f"{sorted(_FACTORIES)} — a prova comportamental de CC-10 esta cobrindo um conjunto "
        "diferente do que o codigo realmente tem"
    )


@pytest.mark.parametrize(
    ("agente", "factory"),
    sorted(_FACTORIES.items()),
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
