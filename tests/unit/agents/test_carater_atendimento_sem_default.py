"""DL-0170 — `carater_atendimento` sem default no Rafael (decisao do dono de 17/11/2026).

Tres provas, nesta ordem:

1. COMPORTAMENTO NO GRAFO. Caso sem carater (chave ausente, `None`, vazio, so' espacos) LEVANTA
   `CaraterAtendimentoAusenteError` no no' `receive`, antes de qualquer avaliacao de DMN, chamada
   de LLM ou start do SP-OP-AUTH-001. Em particular, um carater nulo/vazio NUNCA chega a
   `auth_sla` — a tabela devolveria a linha r4 (P5D) para ele, nao o prazo de urgencia.
2. COMPORTAMENTO NA DELEGACAO A2A. `state_from_envelope` recusa o envelope sem carater, e o
   dispatcher real devolve a quem originou uma rejeicao TERMINAL (`handler_error`) cujo `detail`
   nomeia a classe — o pedido do dado que falta — sem iniciar processo nenhum.
3. CERCA ESTRUTURAL (AST, com autoteste). Nenhum arquivo de `src/` le `carater_atendimento` com
   um valor padrao: `.get(chave, x)`, `.pop(chave, x)`, `.setdefault(chave, x)`,
   `getattr(obj, chave, x)`, `.get(chave) or x`, parametro ou campo de classe com default literal.
   O criterio de ausencia do Rafael e' o mesmo da rota de ingresso (`ingress.py`), e isso tambem
   e' verificado aqui.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from maezo.a2a import RejectionReason
from maezo.agents.helena.delegation import build_auth_analysis_envelope
from maezo.agents.rafael.delegation import make_rafael_handler, state_from_envelope
from maezo.agents.rafael.graph import (
    CAMPO_CARATER_ATENDIMENTO,
    CaraterAtendimentoAusenteError,
    build,
    carater_atendimento_exigido,
    new_rafael_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import build_test_dispatcher, make_card

_SRC = Path(__file__).resolve().parents[3] / "src"

#: As formas de "carater ausente" que a rota de ingresso tambem recusa. `"__ausente__"` = chave
#: inexistente no mapeamento (nao um valor).
_AUSENTES: tuple[Any, ...] = ("__ausente__", None, "", "   ")


class _FakeInference:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append(prompt)
        return "dossie factual sintetico"


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("auth_admissibility", [{"resultado": "SEGUE_ANALISE", "motivo": "test"}])
    dmn.register("auth_sla", [{"sla_analise": "PT2H", "sla_alerta": "PT1H"}])
    dmn.register("auth_auto_approval", [{"recomendacao": "ANALISE_HUMANA", "motivo": "test"}])
    return dmn


def _caso(carater: Any) -> dict[str, Any]:
    caso: dict[str, Any] = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-CARATER-1",
        "beneficiario_pseudo_id": "pseudo-carater-1",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "requer_autorizacao": True,
        "documentacao_completa": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
    }
    if carater != "__ausente__":
        caso[CAMPO_CARATER_ATENDIMENTO] = carater
    return caso


# --- 1. Grafo -------------------------------------------------------------------------------


@pytest.mark.parametrize("carater", _AUSENTES)
async def test_grafo_sem_carater_recusa_antes_de_dmn_llm_e_start(carater: Any) -> None:
    dmn, inference, audit_sink = _dmn(), _FakeInference(), FakeStartAuditSink()
    graph = build(
        {"inference": inference, "dmn": dmn, "cibseven": FakeCibSevenTransport(), "audit_sink": audit_sink}
    ).compile()

    with pytest.raises(CaraterAtendimentoAusenteError):
        await graph.ainvoke(new_rafael_state(_caso(carater)))

    assert dmn.calls == [], "carater ausente chegou a uma DMN (auth_sla/auth_auto_approval)"
    assert inference.calls == []
    # O start do SP-OP-AUTH-001 e' precedido do audit duravel (T-C2): sem audit, sem start.
    assert audit_sink.calls == []


@pytest.mark.parametrize("carater", ["urgencia", "eletivo"])
async def test_grafo_repassa_o_carater_informado_sem_trocar(carater: str) -> None:
    """Controle positivo: o carater informado chega INTACTO a `auth_sla` e ao start."""
    dmn, audit_sink = _dmn(), FakeStartAuditSink()
    cibseven = FakeCibSevenTransport()
    graph = build(
        {"inference": _FakeInference(), "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink}
    ).compile()

    result = await graph.ainvoke(new_rafael_state(_caso(f"  {carater} ")))

    sla_inputs = [variaveis for tabela, variaveis in dmn.calls if tabela == "auth_sla"]
    assert sla_inputs == [{"carater_atendimento": carater, "categoria_procedimento": "consulta"}]
    auto_inputs = [variaveis for tabela, variaveis in dmn.calls if tabela == "auth_auto_approval"]
    assert [v["carater_atendimento"] for v in auto_inputs] == [carater]
    assert result["process_started"] is True
    assert result["dossier"]["fatos"]["carater_atendimento"] == carater


# --- 2. Delegacao A2A -----------------------------------------------------------------------


def _envelope(carater: Any) -> Any:
    case_meta = {k: v for k, v in _caso(carater).items() if k not in {"tenant_id", "numero_guia_tiss"}}
    return build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-CARATER-A2A",
        coverage_ref="fhir://Coverage/carater-1",
        case_meta=case_meta,
    )


@pytest.mark.parametrize("carater", _AUSENTES)
def test_state_from_envelope_sem_carater_recusa_sem_presumir(carater: Any) -> None:
    with pytest.raises(CaraterAtendimentoAusenteError):
        state_from_envelope(_envelope(carater))


@pytest.mark.parametrize("carater", ["urgencia", "eletivo"])
def test_state_from_envelope_preserva_o_carater_informado(carater: str) -> None:
    assert state_from_envelope(_envelope(carater))["carater_atendimento"] == carater


@pytest.mark.parametrize("carater", _AUSENTES)
async def test_dispatcher_devolve_rejeicao_terminal_que_nomeia_o_dado_faltante(carater: Any) -> None:
    dmn, audit_sink = _dmn(), FakeStartAuditSink()
    inference: Any = _FakeInference()  # fake estrutural; o tipo nominal e' a fachada real
    handler = make_rafael_handler(inference, dmn=dmn, cibseven=FakeCibSevenTransport(), audit_sink=audit_sink)
    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher, _, _ = build_test_dispatcher(cards=[card], handlers={"rafael": handler})

    result = await dispatcher.delegate(_envelope(carater))

    assert result.success is False
    assert result.rejection_reason is RejectionReason.HANDLER_ERROR
    assert "CaraterAtendimentoAusenteError" in (result.detail or "")
    assert dmn.calls == []
    assert audit_sink.calls == []  # nenhum SP-OP-AUTH-001 iniciado


# --- Mesmo criterio da rota de ingresso -----------------------------------------------------


@pytest.mark.parametrize("valor", [None, "", " ", "\t", 0, False, "urgencia", "eletivo", " x "])
def test_criterio_de_ausencia_e_o_mesmo_da_rota_de_ingresso(valor: Any) -> None:
    from maezo.runtime.agent_runtime.ingress import CAMPOS_OBRIGATORIOS

    assert CAMPO_CARATER_ATENDIMENTO in CAMPOS_OBRIGATORIOS
    # A expressao abaixo e' a de `ingress.py::receber_solicitacao` para campo obrigatorio.
    rota_recusa = not str(valor or "").strip()
    try:
        carater_atendimento_exigido(valor)
    except CaraterAtendimentoAusenteError:
        rafael_recusa = True
    else:
        rafael_recusa = False
    assert rafael_recusa is rota_recusa


# --- 3. Cerca estrutural --------------------------------------------------------------------

#: Chaves do carater de atendimento da autorizacao previa. `tipo_carater` (adequacao de rede,
#: `tools/workers/adequacao.py`) e' outro vocabulario, com fail-safe proprio (M-1), e fica fora.
_CHAVES_CARATER: frozenset[str] = frozenset({"carater_atendimento"})

#: Excecoes: (arquivo relativo a `src/`, linha). Vazia de proposito. Evidencia ou dado de teste que
#: AFIRMA "eletivo" (ex.: `platform/evidence/auth_instance.py`) e' literal de dicionario, nao
#: default, e a cerca nao o confunde com um.
_EXCECOES: frozenset[tuple[str, int]] = frozenset()


def _constantes_de_chave(arvores: list[ast.Module]) -> frozenset[str]:
    """Nomes de modulo cujo valor e' uma chave de carater (`CAMPO = "carater_atendimento"`)."""
    nomes: set[str] = set()
    for arvore in arvores:
        for no in arvore.body:
            alvo: ast.expr | None = None
            valor: ast.expr | None = None
            if isinstance(no, ast.Assign) and len(no.targets) == 1:
                alvo, valor = no.targets[0], no.value
            elif isinstance(no, ast.AnnAssign):
                alvo, valor = no.target, no.value
            if (
                isinstance(alvo, ast.Name)
                and isinstance(valor, ast.Constant)
                and valor.value in _CHAVES_CARATER
            ):
                nomes.add(alvo.id)
    return frozenset(nomes)


def _e_chave(no: ast.expr | None, constantes: frozenset[str]) -> bool:
    if isinstance(no, ast.Constant):
        return no.value in _CHAVES_CARATER
    if isinstance(no, ast.Name):
        return no.id in constantes
    if isinstance(no, ast.Attribute):
        return no.attr in constantes
    return False


def _e_none(no: ast.expr | None) -> bool:
    return isinstance(no, ast.Constant) and no.value is None


def _leitura_sem_default(no: ast.expr, constantes: frozenset[str]) -> bool:
    """`x.get(chave)` ou `x[chave]` — uma leitura do carater (para pegar `... or "eletivo"`)."""
    if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) and no.func.attr == "get":
        return bool(no.args) and _e_chave(no.args[0], constantes)
    if isinstance(no, ast.Subscript):
        return _e_chave(no.slice, constantes)
    return False


def defaults_de_carater(fonte: str, constantes: frozenset[str] = frozenset()) -> list[tuple[int, str]]:
    """Lista `(linha, forma)` de cada default de carater em `fonte`. Vazio = limpo."""
    arvore = ast.parse(fonte)
    constantes = constantes | _constantes_de_chave([arvore])
    achados: list[tuple[int, str]] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            func = no.func
            if isinstance(func, ast.Attribute) and func.attr in {"get", "pop", "setdefault"}:
                if no.args and _e_chave(no.args[0], constantes):
                    default = no.args[1] if len(no.args) > 1 else None
                    for kw in no.keywords:
                        if kw.arg == "default":
                            default = kw.value
                    if func.attr == "setdefault" or (default is not None and not _e_none(default)):
                        achados.append((no.lineno, f".{func.attr}(carater, <default>)"))
            elif (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(no.args) >= 3
                and _e_chave(no.args[1], constantes)
                and not _e_none(no.args[2])
            ):
                achados.append((no.lineno, "getattr(obj, carater, <default>)"))
        elif isinstance(no, ast.BoolOp) and isinstance(no.op, ast.Or):
            le_carater = any(_leitura_sem_default(v, constantes) for v in no.values[:-1])
            if le_carater and not _e_none(no.values[-1]):
                achados.append((no.lineno, "<leitura do carater> or <default>"))
        elif isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = no.args
            posicionais = args.posonlyargs + args.args
            com_default = posicionais[len(posicionais) - len(args.defaults) :]
            pares = list(zip(com_default, args.defaults, strict=True))
            pares += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is not None]
            for arg, default in pares:
                if arg.arg in _CHAVES_CARATER and not _e_none(default):
                    achados.append((default.lineno, f"parametro {arg.arg}=<default>"))
        elif isinstance(no, ast.ClassDef):
            for item in no.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id in _CHAVES_CARATER
                    and item.value is not None
                    and not _e_none(item.value)
                ):
                    achados.append((item.lineno, f"campo {item.target.id} = <default>"))
    return sorted(set(achados))


def test_autoteste_da_cerca_pega_cada_forma_de_default() -> None:
    positivos = {
        'def f(s):\n    return s.get("carater_atendimento", "eletivo")\n': 1,
        'def f(s):\n    return s.get("carater_atendimento", "")\n': 1,
        'def f(s):\n    return s.get("carater_atendimento", default="urgencia")\n': 1,
        'def f(s, X):\n    return s.get("carater_atendimento", X)\n': 1,
        'CAMPO = "carater_atendimento"\ndef f(s):\n    return s.get(CAMPO, "eletivo")\n': 1,
        'def f(s):\n    return s.pop("carater_atendimento", "eletivo")\n': 1,
        'def f(s):\n    s.setdefault("carater_atendimento", "eletivo")\n': 1,
        'def f(o):\n    return getattr(o, "carater_atendimento", "eletivo")\n': 1,
        'def f(s):\n    return s.get("carater_atendimento") or "eletivo"\n': 1,
        'def f(s):\n    return s["carater_atendimento"] or "eletivo"\n': 1,
        'def f(carater_atendimento="eletivo"):\n    return carater_atendimento\n': 1,
        'def f(*, carater_atendimento=""):\n    return carater_atendimento\n': 1,
        'class C:\n    carater_atendimento: str = "eletivo"\n': 1,
    }
    for fonte, esperado in positivos.items():
        assert len(defaults_de_carater(fonte)) == esperado, fonte
    # Constante importada de outro modulo tambem conta como chave.
    importada = 'from m import CAMPO\ndef f(s):\n    return s.get(CAMPO, "eletivo")\n'
    assert defaults_de_carater(importada) == []
    assert len(defaults_de_carater(importada, frozenset({"CAMPO"}))) == 1

    negativos = [
        'def f(s):\n    return s.get("carater_atendimento")\n',
        'def f(s):\n    return s.get("carater_atendimento", None)\n',
        'def f(s):\n    return s["carater_atendimento"]\n',
        'def f(s):\n    return s.get("categoria_procedimento", "")\n',
        'def f(s):\n    return s.get("tipo_carater", "")\n',
        # Evidencia/dado que AFIRMA o carater nao e' default.
        'CASO = {"carater_atendimento": "eletivo"}\n',
        'def f():\n    return {"carater_atendimento": "eletivo"}\n',
        "def f(carater_atendimento=None):\n    return carater_atendimento\n",
        "def f(carater_atendimento: str):\n    return carater_atendimento\n",
        "class C:\n    carater_atendimento: str\n",
    ]
    for fonte in negativos:
        assert defaults_de_carater(fonte) == [], fonte


def test_nenhum_default_de_carater_em_src() -> None:
    arquivos = sorted(_SRC.rglob("*.py"))
    assert len(arquivos) > 100, "a cerca nao achou src/ — caminho errado silenciaria a prova"
    arvores = {p: ast.parse(p.read_text(encoding="utf-8")) for p in arquivos}
    constantes = _constantes_de_chave(list(arvores.values()))
    assert "CAMPO_CARATER_ATENDIMENTO" in constantes

    violacoes: list[str] = []
    for caminho in arquivos:
        rel = caminho.relative_to(_SRC).as_posix()
        for linha, forma in defaults_de_carater(caminho.read_text(encoding="utf-8"), constantes):
            if (rel, linha) not in _EXCECOES:
                violacoes.append(f"{rel}:{linha}: {forma}")
    assert violacoes == [], (
        "default de carater_atendimento em src/ (DL-0170): carater ausente nao e' presumido; "
        "recuse com CaraterAtendimentoAusenteError ou leia sem default.\n" + "\n".join(violacoes)
    )
