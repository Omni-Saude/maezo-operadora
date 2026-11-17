"""DL-0170 — `carater_atendimento` sem default e em dominio fechado (decisao do dono de 17/11/2026).

Quatro provas, nesta ordem:

1. COMPORTAMENTO NO GRAFO. Caso sem carater (chave ausente, `None`, vazio, so' espacos) ou com
   carater fora de `{"urgencia", "eletivo"}` (`"emergencia"`, `"Urgencia"`, `"URGENCIA"`, `"2"`,
   `"urgência"`) LEVANTA no no' `receive`, antes de qualquer avaliacao de DMN, chamada de LLM ou
   start do SP-OP-AUTH-001. Em particular, um carater nulo, vazio ou desconhecido NUNCA chega a
   `auth_sla` — a tabela devolveria a linha r4 (P5D) para ele, nao o prazo de urgencia.
2. ORIGEM DA DELEGACAO A2A. `delegate_auth_analysis` (Helena) valida ANTES de montar o envelope:
   carater ruim levanta na origem e nenhuma tarefa nasce no dispatcher; uma chamada seguinte com
   carater valido passa. Isso importa porque o dispatcher SELA a rejeicao do Rafael no `task_id`
   `auth-{tenant}-{guia}`: um reenvio depois da rejeicao so' repetiria a rejeicao.
3. DESTINO DA DELEGACAO A2A (defesa em profundidade). `state_from_envelope` recusa o envelope ruim,
   e o dispatcher real devolve uma rejeicao TERMINAL (`handler_error`) que nomeia a classe, sem
   iniciar processo nenhum.
4. CERCA ESTRUTURAL (AST, com autoteste) — ver a secao 4 abaixo para o que ela pega e o que NAO
   pega. E' um ALARME (tripwire), nao uma prova: ela barra as formas conhecidas de default.

A rota de ingresso aplica o mesmo dominio: `tests/unit/runtime/test_agent_ingress.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from maezo.a2a import RejectionReason
from maezo.agents.helena.delegation import build_auth_analysis_envelope, delegate_auth_analysis
from maezo.agents.rafael.delegation import make_rafael_handler, state_from_envelope
from maezo.agents.rafael.graph import build, new_rafael_state
from maezo.domain.carater_atendimento import (
    CAMPO_CARATER_ATENDIMENTO,
    CARATERES_CANONICOS,
    CaraterAtendimentoAusenteError,
    CaraterAtendimentoInvalidoError,
    CaraterAtendimentoRecusadoError,
    carater_atendimento_exigido,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import build_test_dispatcher, make_card

_SRC = Path(__file__).resolve().parents[3] / "src"

#: As formas de "carater ausente" que a rota de ingresso tambem recusa. `"__ausente__"` = chave
#: inexistente no mapeamento (nao um valor).
_AUSENTES: tuple[Any, ...] = ("__ausente__", None, "", "   ")

#: Valores presentes mas fora do dominio. Cada um cairia na r4 da `auth_sla` (P5D). Nenhum e'
#: traduzido: nem o sinonimo regulatorio (`emergencia`), nem a caixa, nem o codigo TISS, nem o
#: acento.
_FORA_DO_DOMINIO: tuple[str, ...] = (
    "emergencia",
    "Urgencia",
    "URGENCIA",
    "Eletivo",
    "2",
    "1",
    "urgência",
    "x",
)

#: Toda forma recusada, com a classe esperada.
_RECUSADOS: tuple[tuple[Any, type[CaraterAtendimentoRecusadoError]], ...] = tuple(
    [(v, CaraterAtendimentoAusenteError) for v in _AUSENTES]
    + [(v, CaraterAtendimentoInvalidoError) for v in _FORA_DO_DOMINIO]
)


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


def test_dominio_fechado_e_o_do_contrato() -> None:
    assert frozenset({"urgencia", "eletivo"}) == CARATERES_CANONICOS


# --- 1. Grafo -------------------------------------------------------------------------------


@pytest.mark.parametrize(("carater", "erro"), _RECUSADOS)
async def test_grafo_sem_carater_valido_recusa_antes_de_dmn_llm_e_start(
    carater: Any, erro: type[CaraterAtendimentoRecusadoError]
) -> None:
    dmn, inference, audit_sink = _dmn(), _FakeInference(), FakeStartAuditSink()
    graph = build(
        {"inference": inference, "dmn": dmn, "cibseven": FakeCibSevenTransport(), "audit_sink": audit_sink}
    ).compile()

    with pytest.raises(erro):
        await graph.ainvoke(new_rafael_state(_caso(carater)))

    assert dmn.calls == [], "carater ausente ou fora do dominio chegou a uma DMN (auth_sla/...)"
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


# --- 2. Origem A2A (Helena) -----------------------------------------------------------------


def _case_meta(carater: Any) -> dict[str, Any]:
    return {k: v for k, v in _caso(carater).items() if k not in {"tenant_id", "numero_guia_tiss"}}


def _dispatcher_com_rafael() -> tuple[Any, FakeDmnTransport, FakeStartAuditSink, Any, Any]:
    dmn, audit_sink = _dmn(), FakeStartAuditSink()
    inference: Any = _FakeInference()  # fake estrutural; o tipo nominal e' a fachada real
    handler = make_rafael_handler(inference, dmn=dmn, cibseven=FakeCibSevenTransport(), audit_sink=audit_sink)
    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher, a2a_audit, kafka = build_test_dispatcher(cards=[card], handlers={"rafael": handler})
    return dispatcher, dmn, audit_sink, a2a_audit, kafka


@pytest.mark.parametrize(("carater", "erro"), _RECUSADOS)
async def test_origem_recusa_sem_criar_tarefa_e_a_chamada_corrigida_passa(
    carater: Any, erro: type[CaraterAtendimentoRecusadoError]
) -> None:
    dispatcher, dmn, audit_sink, a2a_audit, kafka = _dispatcher_com_rafael()
    guia = "GUIA-CARATER-ORIGEM"

    with pytest.raises(erro):
        await delegate_auth_analysis(
            dispatcher,
            tenant="amh",
            numero_guia_tiss=guia,
            coverage_ref="fhir://Coverage/carater-1",
            case_meta=_case_meta(carater),
        )
    # Nenhuma tarefa nasceu: nem linha de audit A2A, nem fato, nem DMN, nem start.
    assert a2a_audit.emitted == []
    assert kafka.sent == []
    assert dmn.calls == []
    assert audit_sink.calls == []

    # MESMA guia (mesmo task_id `auth-amh-<guia>`), carater corrigido: passa, e roda o handler.
    result = await delegate_auth_analysis(
        dispatcher,
        tenant="amh",
        numero_guia_tiss=guia,
        coverage_ref="fhir://Coverage/carater-1",
        case_meta=_case_meta("urgencia"),
    )
    assert result.success is True, result
    assert result.idempotent_replay is False
    sla_inputs = [variaveis for tabela, variaveis in dmn.calls if tabela == "auth_sla"]
    assert sla_inputs == [{"carater_atendimento": "urgencia", "categoria_procedimento": "consulta"}]


async def test_origem_envia_o_valor_validado_sem_espacos() -> None:
    dispatcher, dmn, _, _, _ = _dispatcher_com_rafael()
    result = await delegate_auth_analysis(
        dispatcher,
        tenant="amh",
        numero_guia_tiss="GUIA-CARATER-STRIP",
        coverage_ref="fhir://Coverage/carater-1",
        case_meta=_case_meta("  eletivo "),
    )
    assert result.success is True, result
    assert [v["carater_atendimento"] for t, v in dmn.calls if t == "auth_sla"] == ["eletivo"]


# --- 3. Destino A2A (Rafael, defesa em profundidade) -----------------------------------------


def _envelope(carater: Any) -> Any:
    # `build_auth_analysis_envelope` NAO valida (so' `delegate_auth_analysis` valida): e' o que
    # permite montar aqui o envelope ruim que um originador sem a checagem mandaria.
    return build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss="GUIA-CARATER-A2A",
        coverage_ref="fhir://Coverage/carater-1",
        case_meta=_case_meta(carater),
    )


@pytest.mark.parametrize(("carater", "erro"), _RECUSADOS)
def test_state_from_envelope_recusa_sem_presumir(
    carater: Any, erro: type[CaraterAtendimentoRecusadoError]
) -> None:
    with pytest.raises(erro):
        state_from_envelope(_envelope(carater))


@pytest.mark.parametrize("carater", ["urgencia", "eletivo"])
def test_state_from_envelope_preserva_o_carater_informado(carater: str) -> None:
    assert state_from_envelope(_envelope(carater))["carater_atendimento"] == carater


@pytest.mark.parametrize(("carater", "erro"), _RECUSADOS)
async def test_dispatcher_devolve_rejeicao_terminal_que_nomeia_a_recusa(
    carater: Any, erro: type[CaraterAtendimentoRecusadoError]
) -> None:
    dispatcher, dmn, audit_sink, _, _ = _dispatcher_com_rafael()

    result = await dispatcher.delegate(_envelope(carater))

    assert result.success is False
    assert result.rejection_reason is RejectionReason.HANDLER_ERROR
    assert erro.__name__ in (result.detail or "")
    assert dmn.calls == []
    assert audit_sink.calls == []  # nenhum SP-OP-AUTH-001 iniciado


# --- Mesmo criterio da rota de ingresso -----------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    [None, "", " ", "\t", 0, False, "urgencia", "eletivo", " urgencia ", "emergencia", "URGENCIA", "2"],
)
def test_criterio_e_o_mesmo_da_rota_de_ingresso(valor: Any) -> None:
    from maezo.runtime.agent_runtime.ingress import CAMPOS_DE_DOMINIO_FECHADO, CAMPOS_OBRIGATORIOS

    assert CAMPO_CARATER_ATENDIMENTO in CAMPOS_OBRIGATORIOS
    assert CAMPOS_DE_DOMINIO_FECHADO[CAMPO_CARATER_ATENDIMENTO] is CARATERES_CANONICOS
    # As duas expressoes abaixo sao as de `ingress.py::receber_solicitacao`.
    texto = str(valor or "").strip()
    rota_recusa = not texto or texto not in CAMPOS_DE_DOMINIO_FECHADO[CAMPO_CARATER_ATENDIMENTO]
    try:
        carater_atendimento_exigido(valor)
    except CaraterAtendimentoRecusadoError:
        rafael_recusa = True
    else:
        rafael_recusa = False
    assert rafael_recusa is rota_recusa


def test_mensagem_de_recusa_nao_promete_reenvio_nem_ecoa_o_valor() -> None:
    for erro in (CaraterAtendimentoAusenteError(), CaraterAtendimentoInvalidoError()):
        texto = str(erro)
        assert "reenvie" not in texto
        assert "terminal" in texto
    with pytest.raises(CaraterAtendimentoInvalidoError) as exc:
        carater_atendimento_exigido("valor-livre-qualquer")
    assert "valor-livre-qualquer" not in str(exc.value)


# --- 4. Cerca estrutural --------------------------------------------------------------------
#
# O QUE ELA PEGA (em `src/`, por AST, arquivo a arquivo):
#   * `.get(K, x)`, `.pop(K, x)`, `.setdefault(K, ...)`, `getattr(o, K, x)` com `x` nao-`None`;
#   * `<leitura> or x` e `x if <leitura> else y` (e `not`/`is None`/`is not None` no teste), onde
#     `<leitura>` e' `s.get(K)`, `s[K]`, `getattr(o, K)` OU um nome atribuido de uma leitura no
#     mesmo escopo (`v = s.get(K)` ... `v or "eletivo"`);
#   * atribuicao `s[K] = x` dentro de um `if` cujo teste menciona K ou uma leitura de K
#     (`if K not in s: s[K] = x`, `if not s.get(K): s[K] = x`);
#   * dicionario literal que poe K ANTES de um `**s` (`{K: x, **s}`) ou que e' o lado esquerdo de
#     `|` (`{K: x} | s`); `ChainMap(s, {K: x})`;
#   * argumento nomeado `carater_atendimento=<literal>` em chamada;
#   * parametro com default, campo anotado com default (inclusive `Field(default=x)`/`Field(x)`) e
#     atributo de classe sem anotacao (`carater_atendimento = x`).
#   K = o literal `"carater_atendimento"` ou um nome de modulo cujo valor e' esse literal
#   (`CAMPO_CARATER_ATENDIMENTO`), achado em qualquer arquivo de `src/`.
#
# O QUE ELA NAO PEGA (por isso e' alarme, nao prova): chave montada em runtime (f-string,
# concatenacao, `"carater_" + x`); alias que nao seja constante de modulo (atributo de instancia,
# retorno de funcao, chave vinda de configuracao); leitura atribuida em outra funcao e passada
# adiante; `dict(K=..., **s)` e outras fusoes nao listadas acima; default vindo de configuracao,
# YAML, JSON, SQL, BPMN, DMN ou qualquer codigo que nao seja Python em `src/`.

#: Chaves do carater de atendimento da autorizacao previa. `tipo_carater` (adequacao de rede,
#: `tools/workers/adequacao.py`) e' outro vocabulario, com fail-safe proprio (M-1), e fica fora.
_CHAVES_CARATER: frozenset[str] = frozenset({"carater_atendimento"})

#: Excecoes: (arquivo relativo a `src/`, linha). Vazia: a varredura de `src/` nao achou dado de
#: teste ou evidencia que use as formas acima. Evidencia que AFIRMA "eletivo" num dicionario
#: literal (ex.: `platform/evidence/auth_instance.py`) nao e' default e a cerca nao a confunde.
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


def _e_literal(no: ast.expr | None) -> bool:
    """Literal que poderia ser um carater: constante nao-`None`."""
    return isinstance(no, ast.Constant) and no.value is not None


class _Escopo:
    """Leituras do carater num escopo (modulo ou funcao) e os nomes que as guardam."""

    def __init__(self, raiz: ast.AST, constantes: frozenset[str]) -> None:
        self.constantes = constantes
        self.nomes: set[str] = set()
        for no in ast.walk(raiz):
            if isinstance(no, ast.Assign) and self.e_leitura(no.value):
                self.nomes.update(t.id for t in no.targets if isinstance(t, ast.Name))
            elif (
                isinstance(no, (ast.AnnAssign, ast.NamedExpr))
                and isinstance(no.target, ast.Name)
                and no.value is not None
                and self.e_leitura(no.value)
            ):
                self.nomes.add(no.target.id)

    def e_leitura(self, no: ast.expr | None) -> bool:
        """`s.get(K)`/`s.get(K, None)`, `s[K]`, `getattr(o, K)` — leitura sem default."""
        if isinstance(no, ast.Call):
            func = no.func
            if isinstance(func, ast.Attribute) and func.attr == "get" and no.args:
                return _e_chave(no.args[0], self.constantes) and (len(no.args) == 1 or _e_none(no.args[1]))
            if isinstance(func, ast.Name) and func.id == "getattr" and len(no.args) == 2:
                return _e_chave(no.args[1], self.constantes)
        if isinstance(no, ast.Subscript):
            return _e_chave(no.slice, self.constantes)
        return False

    def le_carater(self, no: ast.expr | None) -> bool:
        if isinstance(no, ast.Name):
            return no.id in self.nomes
        return self.e_leitura(no)

    def teste_le_carater(self, teste: ast.expr) -> bool:
        if isinstance(teste, ast.UnaryOp) and isinstance(teste.op, ast.Not):
            return self.teste_le_carater(teste.operand)
        if isinstance(teste, ast.NamedExpr):
            return self.le_carater(teste.value)
        if (
            isinstance(teste, ast.Compare)
            and len(teste.ops) == 1
            and isinstance(teste.ops[0], (ast.Is, ast.IsNot))
            and _e_none(teste.comparators[0])
        ):
            return self.le_carater(teste.left)
        return self.le_carater(teste)

    def menciona_carater(self, teste: ast.expr) -> bool:
        return any(
            _e_chave(n, self.constantes) or self.le_carater(n)
            for n in ast.walk(teste)
            if isinstance(n, ast.expr)
        )


def _dict_com_carater_literal(no: ast.expr | None, constantes: frozenset[str]) -> int | None:
    """Indice da chave K com valor nao-`None` num dicionario literal, ou `None`."""
    if not isinstance(no, ast.Dict):
        return None
    for i, (chave, valor) in enumerate(zip(no.keys, no.values, strict=True)):
        if chave is not None and _e_chave(chave, constantes) and not _e_none(valor):
            return i
    return None


def _default_de_campo(valor: ast.expr) -> ast.expr | None:
    """O default efetivo de um campo de classe: `Field(default=x)`/`Field(x)` -> x."""
    if isinstance(valor, ast.Call):
        for kw in valor.keywords:
            if kw.arg == "default":
                return kw.value
        return valor.args[0] if valor.args else None
    return valor


def _achados_no_escopo(raiz: ast.AST, constantes: frozenset[str]) -> set[tuple[int, str]]:
    escopo = _Escopo(raiz, constantes)
    achados: set[tuple[int, str]] = set()
    for no in ast.walk(raiz):
        if isinstance(no, ast.BoolOp) and isinstance(no.op, ast.Or):
            ultimo = no.values[-1]
            if (
                any(escopo.le_carater(v) for v in no.values[:-1])
                and not _e_none(ultimo)
                and not escopo.le_carater(ultimo)
            ):
                achados.add((no.lineno, "<leitura do carater> or <default>"))
        elif isinstance(no, ast.IfExp):
            if escopo.teste_le_carater(no.test) and (_e_literal(no.body) or _e_literal(no.orelse)):
                achados.add((no.lineno, "<x> if <leitura do carater> else <default>"))
        elif isinstance(no, ast.If) and escopo.menciona_carater(no.test):
            for filho in no.body:
                if (
                    isinstance(filho, ast.Assign)
                    and any(
                        isinstance(t, ast.Subscript) and _e_chave(t.slice, constantes) for t in filho.targets
                    )
                    and not _e_none(filho.value)
                ):
                    achados.add((filho.lineno, "if <sem carater>: s[carater] = <default>"))
    return achados


def defaults_de_carater(fonte: str, constantes: frozenset[str] = frozenset()) -> list[tuple[int, str]]:
    """Lista `(linha, forma)` de cada default de carater em `fonte`. Vazio = limpo."""
    arvore = ast.parse(fonte)
    constantes = constantes | _constantes_de_chave([arvore])
    achados: set[tuple[int, str]] = set()
    escopos: list[ast.AST] = [arvore]
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            escopos.append(no)
        if isinstance(no, ast.Call):
            func = no.func
            if isinstance(func, ast.Attribute) and func.attr in {"get", "pop", "setdefault"}:
                if no.args and _e_chave(no.args[0], constantes):
                    default = no.args[1] if len(no.args) > 1 else None
                    for kw in no.keywords:
                        if kw.arg == "default":
                            default = kw.value
                    if func.attr == "setdefault" or (default is not None and not _e_none(default)):
                        achados.add((no.lineno, f".{func.attr}(carater, <default>)"))
            elif (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(no.args) >= 3
                and _e_chave(no.args[1], constantes)
                and not _e_none(no.args[2])
            ):
                achados.add((no.lineno, "getattr(obj, carater, <default>)"))
            nome_func = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if nome_func == "ChainMap" and any(
                _dict_com_carater_literal(a, constantes) is not None for a in no.args[1:]
            ):
                achados.add((no.lineno, "ChainMap(s, {carater: <default>})"))
            for kw in no.keywords:
                if kw.arg in _CHAVES_CARATER and _e_literal(kw.value):
                    achados.add((no.lineno, f"chamada(..., {kw.arg}=<literal>)"))
        elif isinstance(no, ast.Dict):
            i = _dict_com_carater_literal(no, constantes)
            if i is not None and any(chave is None for chave in no.keys[i + 1 :]):
                achados.add((no.lineno, "{carater: <default>, **s}"))
        elif isinstance(no, ast.BinOp) and isinstance(no.op, ast.BitOr):
            if _dict_com_carater_literal(no.left, constantes) is not None:
                achados.add((no.lineno, "{carater: <default>} | s"))
        elif isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = no.args
            posicionais = args.posonlyargs + args.args
            com_default = posicionais[len(posicionais) - len(args.defaults) :]
            pares = list(zip(com_default, args.defaults, strict=True))
            pares += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is not None]
            for arg, default in pares:
                if arg.arg in _CHAVES_CARATER and not _e_none(default):
                    achados.add((default.lineno, f"parametro {arg.arg}=<default>"))
        if isinstance(no, ast.ClassDef):
            for item in no.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id in _CHAVES_CARATER
                    and item.value is not None
                ):
                    default = _default_de_campo(item.value)
                    if default is not None and not _e_none(default):
                        achados.add((item.lineno, f"campo {item.target.id} = <default>"))
                elif (
                    isinstance(item, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id in _CHAVES_CARATER for t in item.targets)
                    and not _e_none(item.value)
                ):
                    achados.add((item.lineno, "atributo de classe carater_atendimento = <default>"))
    for escopo in escopos:
        achados |= _achados_no_escopo(escopo, constantes)
    return sorted(achados)


_POSITIVOS: dict[str, str] = {
    # leitura com default
    "get": 'def f(s):\n    return s.get("carater_atendimento", "eletivo")\n',
    "get vazio": 'def f(s):\n    return s.get("carater_atendimento", "")\n',
    "get kw": 'def f(s):\n    return s.get("carater_atendimento", default="urgencia")\n',
    "get nome": 'def f(s, X):\n    return s.get("carater_atendimento", X)\n',
    "get constante": 'CAMPO = "carater_atendimento"\ndef f(s):\n    return s.get(CAMPO, "eletivo")\n',
    "pop": 'def f(s):\n    return s.pop("carater_atendimento", "eletivo")\n',
    "setdefault": 'def f(s):\n    s.setdefault("carater_atendimento", "eletivo")\n',
    "getattr": 'def f(o):\n    return getattr(o, "carater_atendimento", "eletivo")\n',
    # leitura e depois `or`/`if-else`
    "get or": 'def f(s):\n    return s.get("carater_atendimento") or "eletivo"\n',
    "sub or": 'def f(s):\n    return s["carater_atendimento"] or "eletivo"\n',
    "var or": 'def f(s):\n    c = s.get("carater_atendimento")\n    return c or "eletivo"\n',
    "var ifexp": 'def f(s):\n    c = s.get("carater_atendimento")\n    return c if c else "eletivo"\n',
    "var ifexp not": 'def f(s):\n    c = s["carater_atendimento"]\n    return "eletivo" if not c else c\n',
    "var ifexp none": (
        'def f(s):\n    c = s.get("carater_atendimento")\n    return "eletivo" if c is None else c\n'
    ),
    "walrus": 'def f(s):\n    return c if (c := s.get("carater_atendimento")) else "eletivo"\n',
    "getattr var or": 'def f(o):\n    c = getattr(o, "carater_atendimento")\n    return c or "eletivo"\n',
    # preenchimento condicional
    "if not in": (
        'def f(s):\n    if "carater_atendimento" not in s:\n        s["carater_atendimento"] = "eletivo"\n'
    ),
    "if not get": (
        'def f(s):\n    if not s.get("carater_atendimento"):\n        s["carater_atendimento"] = "eletivo"\n'
    ),
    "if constante": 'K = "carater_atendimento"\ndef f(s):\n    if K not in s:\n        s[K] = "eletivo"\n',
    # fusoes de dicionario
    "dict merge": 'def f(s):\n    return {"carater_atendimento": "eletivo", **s}\n',
    "dict or": 'def f(s):\n    return {"carater_atendimento": "eletivo"} | s\n',
    "chainmap": (
        "from collections import ChainMap\n"
        "def f(s):\n"
        '    return ChainMap(s, {"carater_atendimento": "eletivo"})\n'
    ),
    "chainmap attr": (
        'import collections\ndef f(s):\n    return collections.ChainMap(s, {"carater_atendimento": "x"})\n'
    ),
    # argumento nomeado
    "kwarg": 'def f(g):\n    return g(carater_atendimento="eletivo")\n',
    # parametros e classes
    "param": 'def f(carater_atendimento="eletivo"):\n    return carater_atendimento\n',
    "kwonly": 'def f(*, carater_atendimento=""):\n    return carater_atendimento\n',
    "campo": 'class C:\n    carater_atendimento: str = "eletivo"\n',
    "campo Field": 'class C:\n    carater_atendimento: str = Field(default="eletivo")\n',
    "campo Field pos": 'class C:\n    carater_atendimento: str = Field("eletivo")\n',
    "atributo": 'class C:\n    carater_atendimento = "eletivo"\n',
}

_NEGATIVOS: dict[str, str] = {
    "get": 'def f(s):\n    return s.get("carater_atendimento")\n',
    "get None": 'def f(s):\n    return s.get("carater_atendimento", None)\n',
    "sub": 'def f(s):\n    return s["carater_atendimento"]\n',
    "outra chave": 'def f(s):\n    return s.get("categoria_procedimento", "")\n',
    "tipo_carater": 'def f(s):\n    return s.get("tipo_carater", "")\n',
    # Evidencia/dado que AFIRMA o carater nao e' default.
    "dict evidencia": 'CASO = {"carater_atendimento": "eletivo"}\n',
    "dict retorno": 'def f():\n    return {"carater_atendimento": "eletivo"}\n',
    "dict sobrescreve": 'def f(s):\n    return {**s, "carater_atendimento": "eletivo"}\n',
    "atribuicao sem if": 'def f(s):\n    s["carater_atendimento"] = "eletivo"\n',
    "param None": "def f(carater_atendimento=None):\n    return carater_atendimento\n",
    "param sem default": "def f(carater_atendimento: str):\n    return carater_atendimento\n",
    "campo sem default": "class C:\n    carater_atendimento: str\n",
    "campo Field None": "class C:\n    carater_atendimento: str | None = Field(default=None)\n",
    "campo Field vazio": "class C:\n    carater_atendimento: str = Field(min_length=1)\n",
    "atributo None": "class C:\n    carater_atendimento = None\n",
    "kwarg variavel": "def f(g, c):\n    return g(carater_atendimento=c)\n",
    "kwarg None": "def f(g):\n    return g(carater_atendimento=None)\n",
    "or entre leituras": (
        'def f(a, b):\n    return a.get("carater_atendimento") or b.get("carater_atendimento")\n'
    ),
    "regra nao default": (
        'def f(s):\n    c = s["carater_atendimento"]\n    return "PT2H" if c == "urgencia" else "P5D"\n'
    ),
    "var de outra leitura": (
        'def f(s):\n    c = s.get("categoria_procedimento")\n    return c or "consulta"\n'
    ),
    "chainmap sem carater": 'from collections import ChainMap\ndef f(s):\n    return ChainMap(s, {"x": 1})\n',
}


@pytest.mark.parametrize("nome", sorted(_POSITIVOS))
def test_autoteste_a_cerca_pega_cada_forma(nome: str) -> None:
    assert len(defaults_de_carater(_POSITIVOS[nome])) == 1, (nome, defaults_de_carater(_POSITIVOS[nome]))


@pytest.mark.parametrize("nome", sorted(_NEGATIVOS))
def test_autoteste_a_cerca_nao_acusa_o_que_nao_e_default(nome: str) -> None:
    assert defaults_de_carater(_NEGATIVOS[nome]) == [], nome


def test_autoteste_constante_importada_conta_como_chave() -> None:
    importada = 'from m import CAMPO\ndef f(s):\n    return s.get(CAMPO, "eletivo")\n'
    assert defaults_de_carater(importada) == []
    assert len(defaults_de_carater(importada, frozenset({"CAMPO"}))) == 1


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
        "recuse com CaraterAtendimentoRecusadoError ou leia sem default.\n" + "\n".join(violacoes)
    )
