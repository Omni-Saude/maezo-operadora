"""E-SF-ADD01-02: console do especialista comercial fechado e compromisso nao ratificado."""

import ast
import typing
from pathlib import Path

import pytest

from maezo.portal.api import specialist_console as sc
from maezo.portal.api.specialist_console import (
    CAMPOS_DA_VISAO,
    CAMPOS_DO_SNAPSHOT,
    OPERACOES_DO_CONSOLE,
    OPERACOES_VEDADAS_AO_CONSOLE,
    PAPEL_CADASTRO,
    PAPEL_CONDUCAO,
    REGISTRO_DO_COMPROMISSO,
    AtorDoConsole,
    RegistroDoCompromisso,
    SpecialistConsoleError,
    compromisso_vigente,
    exigir_operacao,
    operacoes_permitidas,
    projetar_jornada,
    visao_do_compromisso,
)
from maezo.ports.commercial import CommercialOperation

ROOT = Path(sc.__file__).resolve().parents[2]
APP = ROOT / "portal" / "api" / "app.py"


def snapshot() -> dict[str, typing.Any]:
    return {
        "journey_ref": "jrn_0123456789abcdef",
        "revision": 3,
        "prospect_ref": "pro_fedcba9876543210",
        "account_ref": None,
        "party_ref": "123e4567-e89b-12d3-a456-426614174000",
        "payer_ref": "py1",
        "quote_contexts": [],
        "acceptance": None,
        "contract": None,
        "enrollment": None,
        "coverage": None,
        "documents": [{"title": "contrato", "ref": "d1"}],
        "pending_items": ["enviar documento"],
        "pending_operations": [],
        "next_actions": ["aguardar"],
        "support": None,
        "support_topics": [],
        "observed_at": "2026-01-01T00:00:00Z",
        "profile_ref": "pf1",
        "party_linkage": None,
        "commercial_acceptance": None,
        "receivable_refs": [],
        "payment_attempt_refs": [],
        "mandate_refs": [],
        "financial_action_refs": [],
    }


def test_specialist_console_has_no_acceptance_action() -> None:
    assert OPERACOES_DO_CONSOLE.isdisjoint({"acceptance.record", "enrollment.request"})
    assert all(o.startswith("work.") or o == "journey.get" for o in OPERACOES_DO_CONSOLE)
    assert (
        frozenset({"work.list", "work.claim", "work.assign", "work.handoff", "work.reconcile", "journey.get"})
        == OPERACOES_DO_CONSOLE
    )
    assert set(typing.get_args(CommercialOperation)) >= OPERACOES_DO_CONSOLE
    assert OPERACOES_DO_CONSOLE.isdisjoint(OPERACOES_VEDADAS_AO_CONSOLE)
    ator = AtorDoConsole("a1", frozenset({PAPEL_CONDUCAO}))
    with pytest.raises(SpecialistConsoleError) as e:
        exigir_operacao(ator, "acceptance.record")
    assert e.value.code == "console_operacao_vedada"
    exigir_operacao(ator, "work.claim")


def test_journey_snapshot_view_has_no_clinical_field() -> None:
    visao = projetar_jornada(snapshot())
    assert tuple(visao.__slots__) == CAMPOS_DA_VISAO
    assert visao.journey_ref == "jrn_0123456789abcdef" and visao.revision == 3 and visao.account_ref is None


def _com(mut: typing.Callable[[dict[str, typing.Any]], object]) -> dict[str, typing.Any]:
    s = snapshot()
    mut(s)
    return s


@pytest.mark.parametrize(
    ("mut", "termo"),
    [
        (lambda s: s["documents"][0].__setitem__("title", "declaracao de saude"), "declaracao"),
        (lambda s: s.__setitem__("pending_items", ["CID J45"]), "J45"),
        (lambda s: s.__setitem__("next_actions", ["doenca preexistente"]), "preexistente"),
        (lambda s: s["documents"][0].__setitem__("diagnostico", "x"), "diagnostico"),
    ],
)
def test_journey_snapshot_refuses_health_data(mut: typing.Any, termo: str) -> None:
    with pytest.raises(SpecialistConsoleError) as e:
        projetar_jornada(_com(mut))
    assert e.value.code == "PHI_IN_COMMERCIAL_INPUT"
    assert termo not in str(e.value)


@pytest.mark.parametrize("op", ["extra", "missing"])
def test_snapshot_key_mismatch(op: str) -> None:
    s = snapshot()
    if op == "extra":
        s["outra"] = 1
    else:
        del s["payer_ref"]
    with pytest.raises(SpecialistConsoleError) as e:
        projetar_jornada(s)
    assert e.value.code == "console_snapshot_contract_mismatch"
    assert set(snapshot()) == CAMPOS_DO_SNAPSHOT
    with pytest.raises(SpecialistConsoleError):
        projetar_jornada([1])


@pytest.mark.parametrize(
    "campo,valor", [("revision", True), ("revision", -1), ("observed_at", 5), ("journey_ref", "")]
)
def test_snapshot_bad_types(campo: str, valor: typing.Any) -> None:
    s = snapshot()
    s[campo] = valor
    with pytest.raises(SpecialistConsoleError):
        projetar_jornada(s)


def _reg(
    n: typing.Any = 30, ass: tuple[str, ...] = ("d1", "d2"), ato: str | None = "ato-1"
) -> RegistroDoCompromisso:
    return RegistroDoCompromisso(
        n_minutos=n, assinaturas_de_dono=tuple(ass), ato_ref=ato, estado="NAO_RATIFICADO"
    )


def test_first_response_commitment_requires_owner_signature() -> None:
    r = REGISTRO_DO_COMPROMISSO
    assert compromisso_vigente(r) is None
    assert r.estado == "NAO_RATIFICADO" and r.assinaturas_de_dono == () and r.ato_ref is None
    assert compromisso_vigente(_reg(ass=("d1",))) is None
    assert compromisso_vigente(_reg(ass=("d1", "d1"))) is None
    assert compromisso_vigente(_reg(ato=None)) is None
    for n in (True, 0, -5, None):
        assert compromisso_vigente(_reg(n=n)) is None
    assert compromisso_vigente(_reg()) == 30
    for reg in (r, _reg()):
        v = visao_do_compromisso(reg, estado_do_recibo="delivered")
        assert v == {"horario_de_atendimento": None, "estado_do_recibo": "delivered"}
        assert 30 not in v.values()
        assert not any(("n_" in k) or ("minutos" in k) or ("prazo" in k) for k in v)
    assert visao_do_compromisso(r, estado_do_recibo=None)["estado_do_recibo"] is None
    with pytest.raises(SpecialistConsoleError) as e:
        visao_do_compromisso(r, estado_do_recibo="sent")
    assert e.value.code == "console_recibo_estado_invalido"


def test_roles_distinct() -> None:
    assert str(PAPEL_CONDUCAO) != str(PAPEL_CADASTRO)
    assert operacoes_permitidas(AtorDoConsole("a", frozenset({PAPEL_CONDUCAO}))) == OPERACOES_DO_CONSOLE
    assert operacoes_permitidas(AtorDoConsole("a", frozenset({PAPEL_CADASTRO}))) == frozenset()
    with pytest.raises(SpecialistConsoleError) as e:
        operacoes_permitidas(AtorDoConsole("a", frozenset({PAPEL_CONDUCAO, PAPEL_CADASTRO})))
    assert e.value.code == "console_papel_acumulado"
    with pytest.raises(SpecialistConsoleError) as e:
        operacoes_permitidas(AtorDoConsole("a", frozenset({"outro"})))
    assert e.value.code == "console_papel_desconhecido"
    with pytest.raises(SpecialistConsoleError):
        exigir_operacao(AtorDoConsole("a", frozenset({PAPEL_CADASTRO})), "work.list")


def test_console_not_mounted() -> None:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            assert "specialist_console" not in (n.module or "")
            assert all(a.name != "specialist_console" for a in n.names)
        if isinstance(n, ast.Import):
            assert all("specialist_console" not in a.name for a in n.names)
    mod = ast.parse(Path(sc.__file__).read_text(encoding="utf-8"))
    nomes = {n.id for n in ast.walk(mod) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(mod) if isinstance(n, ast.Attribute)
    }
    assert "APIRouter" not in nomes and "FastAPI" not in nomes


@pytest.mark.parametrize(
    "valor", [b"declaracao de saude", {"diagnostico"}, frozenset({"cid"}), iter(["diagnostico"])]
)
def test_snapshot_opaque_types_fail_closed(valor: typing.Any) -> None:
    s = snapshot()
    s["pending_items"] = [valor]
    with pytest.raises(SpecialistConsoleError) as e:
        projetar_jornada(s)
    assert e.value.code in {"PHI_IN_COMMERCIAL_INPUT", "console_snapshot_contract_mismatch"}
    assert "diagnostico" not in str(e.value)


def test_snapshot_profundo_recusa_tipado() -> None:
    s = snapshot()
    no: list[typing.Any] = []
    s["pending_items"] = no
    for _ in range(100):
        filho: list[typing.Any] = []
        no.append(filho)
        no = filho
    with pytest.raises(SpecialistConsoleError) as e:
        projetar_jornada(s)
    assert e.value.code == "console_snapshot_contract_mismatch"


def test_padrao_cid_isolado_recusa() -> None:
    s = snapshot()
    s["pending_items"] = [{"code": "J45"}]
    with pytest.raises(SpecialistConsoleError) as e:
        projetar_jornada(s)
    assert e.value.code == "PHI_IN_COMMERCIAL_INPUT"


def test_repr_do_registro_nao_mostra_n() -> None:
    registro = RegistroDoCompromisso(
        n_minutos=30,
        assinaturas_de_dono=("dono-a", "dono-b"),
        ato_ref="ato-sintetico",
        estado="NAO_RATIFICADO",
    )
    assert "30" not in repr(registro)


# --------------------------------------------------------------- CD-94 item 2: VALORES em classe fechada
@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("journey_ref", "gestante_hipertensa"),  # termo clinico fora da lista, em referencia
        ("prospect_ref", "losartana"),  # medicamento
        ("party_ref", "pro_gravidez0001"),  # letras fora do alfabeto hex
        ("journey_ref", "gravidez_0123456789abcdef"),  # prefixo fora do conjunto fechado
        ("journey_ref", "j1"),  # curto demais
        ("account_ref", "0123456789abcdef 1"),  # espaco
        ("journey_ref", 1234567890123456),  # nao e' texto
        ("observed_at", "2026-01-01T00:00:00"),  # sem fuso
        ("observed_at", "ontem"),
        ("observed_at", "2026-13-01T00:00:00Z"),  # data invalida
        ("revision", -1),
        ("revision", True),
    ],
)
def test_valor_fora_da_classe_recusado_sem_depender_da_lista(
    monkeypatch: pytest.MonkeyPatch, campo: str, valor: typing.Any
) -> None:
    monkeypatch.setattr(sc, "contem_dado_de_saude", lambda _t: False)
    s = snapshot()
    s[campo] = valor
    with pytest.raises(SpecialistConsoleError) as e:
        projetar_jornada(s)
    assert e.value.code == "console_snapshot_value_class_refused"
    assert str(valor) not in str(e.value)


@pytest.mark.parametrize(
    "valor",
    ["0123456789abcdef", "jrn_0123456789abcdef", "123e4567-e89b-12d3-a456-426614174000", None],
)
def test_referencia_opaca_valida(valor: typing.Any) -> None:
    s = snapshot()
    s["account_ref"] = valor
    assert projetar_jornada(s).account_ref == valor


@pytest.mark.parametrize(
    "valor", ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.123+00:00", "2026-11-17T21:00:00-03:00"]
)
def test_instante_com_fuso_valido(valor: str) -> None:
    s = snapshot()
    s["observed_at"] = valor
    assert projetar_jornada(s).observed_at == valor
