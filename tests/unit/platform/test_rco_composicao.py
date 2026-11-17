"""`composicoes[]` do manifesto K1 (formato 2.2.0) e a composicao `primeira_com_idRegra` (DL-0174).

Leitor (`rco_manifesto`): forma estrita, recusas codificadas `composicao-*`, 2.1.0 sem o campo = lista vazia,
o campo em 2.1.0 = desconhecido. Gate (`verify_rco_rules_pin`) e runtime (`rco_pin`) recusam o MESMO grupo
malformado. Semantica (`rco_composicao`): todas as decisoes, mesmas entradas, a primeira com `idRegra`
vence, senao o padrao; duas anteriores com `idRegra`, linha a mais, resultado invalido ou falha do
avaliador viram `revisao_humana` (nunca glosa). Pacote 100% sintetico (`rco_k1_sintetico`).
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import structlog
from scripts.ci.verify_rco_rules_pin import K1_MANIFEST_PATH, main, verify_lock

from maezo.platform import observability, rco_composicao
from maezo.platform import rco_manifesto as k1
from maezo.platform.rco_manifesto import ComposicaoK1
from maezo.tools.workers import rco_pin
from tests.unit.platform import rco_k1_sintetico as sint

# --- leitor ------------------------------------------------------------------------------------------


@pytest.fixture
def pacote_22(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    manifesto = sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    return tmp_path, manifesto


def _ler(raiz: Path) -> k1.ManifestoK1:
    return k1.ler_manifesto((raiz / sint.K1_DIR / "manifesto.json").read_bytes())


def test_22_com_composicoes_le_e_confere(pacote_22: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote_22
    lido = _ler(raiz)
    assert lido.formato_manifesto == "2.2.0"
    assert lido.composicoes == (
        ComposicaoK1("grupo_sint", ("rco_sint_a", "rco_sint_b"), "primeira_com_idRegra", "rco_sint_b"),
    )
    assert lido.composicoes[0].anteriores == ("rco_sint_a",)
    assert lido.composicao_de("rco_sint_b") == lido.composicoes[0]
    assert lido.composicao_de("rco_nada") is None
    assert k1.verificar_pacote(lido, k1_dir=raiz / sint.K1_DIR, rules_dir=raiz / sint.RULES_DIR) == []
    assert k1.provas_ausentes_no_pin(lido) == []  # a regra de #1264 continua valendo no 2.2.0


def test_22_sem_o_campo_vale_lista_vazia(tmp_path: Path) -> None:
    sint.com_composicoes(tmp_path, sint.montar(tmp_path), campo=False)
    lido = _ler(tmp_path)
    assert (lido.formato_manifesto, lido.composicoes) == ("2.2.0", ())


def test_22_com_lista_vazia_vale_lista_vazia(tmp_path: Path) -> None:
    sint.com_composicoes(tmp_path, sint.montar(tmp_path), [])
    assert _ler(tmp_path).composicoes == ()


def test_21_sem_o_campo_segue_aceito_com_lista_vazia(tmp_path: Path) -> None:
    sint.montar(tmp_path)
    lido = _ler(tmp_path)
    assert (lido.formato_manifesto, lido.composicoes) == ("2.1.0", ())


def test_campo_no_21_e_desconhecido_e_recusado(tmp_path: Path) -> None:
    manifesto = sint.montar(tmp_path)
    manifesto["composicoes"] = [copy.deepcopy(sint.COMPOSICAO)]
    with pytest.raises(k1.ManifestoK1InvalidoError, match=r"desconhecidos \['composicoes'\]"):
        k1.ler_manifesto(sint.canonico(manifesto))


def test_22_sem_provas_e_recusado(tmp_path: Path) -> None:
    manifesto = sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    del manifesto["provas"]
    with pytest.raises(k1.ManifestoK1InvalidoError, match=r"faltam \['provas'\]"):
        k1.ler_manifesto(sint.canonico(manifesto))


def _grupo(**mudar: Any) -> dict[str, Any]:
    grupo = copy.deepcopy(sint.COMPOSICAO)
    for chave, valor in mudar.items():
        if valor is _REMOVER:
            del grupo[chave]
        else:
            grupo[chave] = valor
    return grupo


_REMOVER = object()
_A, _B = "rco_sint_a", "rco_sint_b"


def _sem_saida_id_regra(manifesto: dict[str, Any]) -> None:
    decisao = next(d for d in manifesto["decisoes"] if d["id"] == _B)
    decisao["saidas"] = [s for s in decisao["saidas"] if s["nome"] != "idRegra"]


@pytest.mark.parametrize(
    ("composicoes", "mutar", "codigo"),
    [
        (
            [_grupo(decisoes=[_A, "rco_fora"], padrao="rco_fora")],
            None,
            "composicao-decisao-fora-do-manifesto",
        ),
        (
            [_grupo(), _grupo(nome="outro", decisoes=[_B, _A], padrao=_A)],
            None,
            "composicao-decisao-em-dois-grupos",
        ),
        ([_grupo(padrao=_A)], None, "composicao-padrao-nao-e-ultima"),
        ([_grupo(regra="maioria")], None, "composicao-regra-desconhecida"),
        ([_grupo(regra="Primeira_com_idRegra")], None, "composicao-regra-desconhecida"),
        ([_grupo(), _grupo(decisoes=[_B, _A], padrao=_A)], None, "composicao-nome-repetido"),
        ([_grupo(decisoes=[_B], padrao=_B)], None, "composicao-poucas-decisoes"),
        ([_grupo(decisoes=[_B, _B], padrao=_B)], None, "composicao-decisao-repetida"),
        ([_grupo(decisoes=[_A, _B, _B], padrao=_B)], None, "composicao-decisao-repetida"),
        ([_grupo(descricao="texto")], None, "composicao-forma"),  # chave fora do formato acordado
        ([_grupo(regra=_REMOVER)], None, "composicao-forma"),
        ([_grupo(nome="")], None, "composicao-forma"),
        ([_grupo(nome="a b")], None, "composicao-forma"),
        ([_grupo(decisoes=_A)], None, "composicao-forma"),
        ([_grupo(decisoes=[_A, 1])], None, "composicao-forma"),
        ([_grupo(padrao=None)], None, "composicao-forma"),
        (["grupo"], None, "composicao-forma"),
        ({"nome": "x"}, None, "composicao-forma"),  # o campo tem de ser lista
        ([_grupo()], _sem_saida_id_regra, "composicao-decisao-sem-idRegra"),
    ],
)
def test_grupo_malformado_e_recusado_com_codigo(
    tmp_path: Path,
    composicoes: Any,
    mutar: Callable[[dict[str, Any]], None] | None,
    codigo: str,
) -> None:
    manifesto = sint.com_composicoes(tmp_path, sint.montar(tmp_path), [])
    manifesto["composicoes"] = composicoes
    if mutar is not None:
        mutar(manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError) as excinfo:
        k1.ler_manifesto(sint.canonico(manifesto))
    assert any(codigo in p for p in excinfo.value.problemas), excinfo.value.problemas


# --- gate e runtime: mesma checagem ------------------------------------------------------------------


def test_gate_aceita_22_com_grupos(pacote_22: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote_22
    assert verify_lock(sint.lock(raiz), repo_root=raiz) == []
    assert main(["--repo-root", str(raiz)]) == 0


def test_runtime_aceita_22_com_grupos_e_expoe(pacote_22: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote_22
    pin = rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)
    assert [c.nome for c in pin.composicoes] == ["grupo_sint"]
    assert set(pin.tables) == {_A, _B}


def test_runtime_sem_pacote_nao_tem_grupos() -> None:
    assert rco_pin.RcoRulesPin(estado="sem_pacote", tables={}).composicoes == ()


@pytest.mark.parametrize(
    ("grupo", "codigo"),
    [
        (_grupo(padrao=_A), "composicao-padrao-nao-e-ultima"),
        (_grupo(decisoes=[_A, "rco_fora"], padrao="rco_fora"), "composicao-decisao-fora-do-manifesto"),
        (_grupo(regra="maioria"), "composicao-regra-desconhecida"),
    ],
)
def test_gate_e_runtime_recusam_o_mesmo_grupo_malformado(
    pacote_22: tuple[Path, dict[str, Any]], grupo: dict[str, Any], codigo: str
) -> None:
    raiz, manifesto = pacote_22
    manifesto["composicoes"] = [grupo]
    sint.regravar(raiz, manifesto)
    violacoes = verify_lock(sint.lock(raiz), repo_root=raiz)
    assert {v.code for v in violacoes} == {"manifesto-invalido"}
    assert any(codigo in v.detail for v in violacoes)
    assert main(["--repo-root", str(raiz)]) == 1
    with pytest.raises(rco_pin.RcoRulesPinError, match=codigo):
        rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)


def test_gate_recusa_campo_no_21(tmp_path: Path) -> None:
    sint.montar(tmp_path)
    manifesto = json.loads((tmp_path / K1_MANIFEST_PATH).read_bytes())
    manifesto["composicoes"] = [copy.deepcopy(sint.COMPOSICAO)]
    sint.regravar(tmp_path, manifesto)
    assert {v.code for v in verify_lock(sint.lock(tmp_path), repo_root=tmp_path)} == {"manifesto-invalido"}
    with pytest.raises(rco_pin.RcoRulesPinError, match="composicoes"):
        rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)


# --- guarda: membro nao padrao nunca avaliado sozinho -----------------------------------------------


def test_guarda_recusa_avaliar_membro_nao_padrao_sozinho(pacote_22: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote_22
    pin = rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)
    assert rco_composicao.membro_nao_padrao(pin, _A) is True
    assert rco_composicao.membro_nao_padrao(pin, _B) is False  # o padrao
    assert rco_composicao.membro_nao_padrao(pin, "rco_fora_de_grupo") is False
    with pytest.raises(rco_composicao.DecisaoSoEmComposicaoError, match="grupo_sint"):
        rco_composicao.exigir_avaliacao_isolada(pin, _A)
    rco_composicao.exigir_avaliacao_isolada(pin, _B)
    rco_composicao.exigir_avaliacao_isolada(pin, "rco_fora_de_grupo")
    # O manifesto serve de fonte igual ao pin.
    assert pin.manifesto is not None
    assert rco_composicao.membro_nao_padrao(pin.manifesto, _A) is True


# --- semantica `primeira_com_idRegra` -----------------------------------------------------------------

_P1, _P2, _PADRAO = "rco_p1", "rco_p2", "rco_padrao"
_G = ComposicaoK1("g", (_P1, _P2, _PADRAO), k1.REGRA_PRIMEIRA_COM_ID_REGRA, _PADRAO)
_ENTRADAS: dict[str, Any] = {"codigoPrincipal": "X", "lista": [1, 2]}


class _Fake:
    """Avaliador: devolve as linhas registradas por decisao e grava cada chamada (id, entradas)."""

    def __init__(self, respostas: Mapping[str, Any], falha_em: str | None = None) -> None:
        self.respostas = respostas
        self.falha_em = falha_em
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, decisao: str, entradas: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        self.chamadas.append((decisao, copy.deepcopy(dict(entradas))))
        if decisao == self.falha_em:
            raise ConnectionError("motor fora")
        resposta: Sequence[Mapping[str, Any]] = self.respostas[decisao]
        return resposta


def _linha(id_regra: Any, decisao: str = "glosar") -> dict[str, Any]:
    return {"decisao": decisao, "idRegra": id_regra}


@pytest.fixture
def contados(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    registro: list[str] = []
    monkeypatch.setattr(rco_composicao, "record_rco_composicao_revisao", registro.append)
    return registro


def test_avalia_todas_na_ordem_com_as_mesmas_entradas() -> None:
    fake = _Fake({_P1: [_linha("A")], _P2: [], _PADRAO: [_linha("D", "liberar")]})
    rco_composicao.compor(_G, fake, _ENTRADAS)
    assert [d for d, _ in fake.chamadas] == [_P1, _P2, _PADRAO]
    assert all(e == _ENTRADAS for _, e in fake.chamadas)


def test_avaliador_que_muda_as_entradas_nao_muda_as_da_seguinte() -> None:
    vistas: list[dict[str, Any]] = []

    def avaliador(decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]:
        vistas.append(copy.deepcopy(dict(entradas)))
        assert isinstance(entradas, dict)
        entradas["codigoPrincipal"] = "MUDADO"
        entradas["lista"].append(99)
        return []

    rco_composicao.compor(_G, avaliador, _ENTRADAS)
    assert vistas == [_ENTRADAS, _ENTRADAS, _ENTRADAS]
    assert _ENTRADAS == {"codigoPrincipal": "X", "lista": [1, 2]}


def _primeira_vence(escolher: Callable[[ComposicaoK1, Mapping[str, Any]], Any]) -> None:
    """A anterior e o padrao trazem `idRegra`: vale a anterior (a prova da ordem)."""
    resultados = {_P1: [_linha("A")], _P2: [_linha(None)], _PADRAO: [_linha("D", "liberar")]}
    r = escolher(_G, resultados)
    assert r.decisao_vencedora == _P1
    assert r.linhas == (_linha("A"),)
    assert r.pelo_padrao is False


def test_ordem_invertida_falha() -> None:
    """Teste que REPROVA se a ordem de avaliacao for invertida (padrao antes das anteriores)."""
    _primeira_vence(rco_composicao.escolher)
    fake = _Fake({_P1: [_linha("A")], _P2: [_linha(None)], _PADRAO: [_linha("D", "liberar")]})
    r = rco_composicao.compor(_G, fake, _ENTRADAS)
    assert (r.decisao_vencedora, r.linhas) == (_P1, (_linha("A"),))

    # Mutacao: a mesma regra aplicada na ordem inversa. O teste acima tem de pegar o mutante.
    def escolher_invertido(g: ComposicaoK1, resultados: Mapping[str, Any]) -> Any:
        invertido = ComposicaoK1(g.nome, tuple(reversed(g.decisoes)), g.regra, g.decisoes[0])
        return rco_composicao.escolher(invertido, resultados)

    with pytest.raises(AssertionError):
        _primeira_vence(escolher_invertido)


def test_primeira_vence() -> None:
    r = rco_composicao.compor(
        _G, _Fake({_P1: [_linha("A")], _P2: [], _PADRAO: [_linha("", "liberar")]}), _ENTRADAS
    )
    assert (r.desfecho, r.decisao_vencedora, r.pelo_padrao) == ("escolhido", _P1, False)
    assert r.revisao_humana is False and r.motivo is None
    assert r.avaliadas == (_P1, _P2, _PADRAO)


def test_segunda_vence() -> None:
    r = rco_composicao.compor(
        _G, _Fake({_P1: [], _P2: [_linha("B")], _PADRAO: [_linha("D", "liberar")]}), _ENTRADAS
    )
    assert (r.decisao_vencedora, r.linhas, r.pelo_padrao) == (_P2, (_linha("B"),), False)


def test_nenhuma_anterior_vale_o_padrao_com_id_regra() -> None:
    r = rco_composicao.compor(
        _G, _Fake({_P1: [], _P2: [_linha(None)], _PADRAO: [_linha("D", "liberar")]}), _ENTRADAS
    )
    assert (r.decisao_vencedora, r.linhas, r.pelo_padrao) == (_PADRAO, (_linha("D", "liberar"),), True)


def test_nenhuma_anterior_vale_o_padrao_sem_id_regra() -> None:
    padrao = [{"decisao": "liberar", "idRegra": ""}]
    r = rco_composicao.compor(_G, _Fake({_P1: [], _P2: [], _PADRAO: padrao}), _ENTRADAS)
    assert (r.desfecho, r.decisao_vencedora, r.linhas, r.pelo_padrao) == (
        "escolhido",
        _PADRAO,
        tuple(padrao),
        True,
    )


def test_padrao_sem_linha_vale_sem_linha() -> None:
    r = rco_composicao.compor(_G, _Fake({_P1: [], _P2: [], _PADRAO: []}), _ENTRADAS)
    assert (r.desfecho, r.decisao_vencedora, r.linhas, r.pelo_padrao) == ("escolhido", _PADRAO, (), True)


@pytest.mark.parametrize(
    "linha_anterior",
    [{"decisao": "liberar"}, _linha(None), _linha("")],
    ids=["sem-chave", "null", "vazio"],
)
def test_id_regra_nulo_ou_vazio_conta_como_ausente(linha_anterior: dict[str, Any]) -> None:
    r = rco_composicao.compor(
        _G,
        _Fake({_P1: [linha_anterior], _P2: [linha_anterior], _PADRAO: [_linha("D", "liberar")]}),
        _ENTRADAS,
    )
    assert (r.decisao_vencedora, r.pelo_padrao) == (_PADRAO, True)


def test_duas_anteriores_com_id_regra_vao_a_revisao_humana(contados: list[str]) -> None:
    with structlog.testing.capture_logs() as logs:
        r = rco_composicao.compor(
            _G, _Fake({_P1: [_linha("A")], _P2: [_linha("B")], _PADRAO: [_linha("D")]}), _ENTRADAS
        )
    assert (r.desfecho, r.motivo, r.linhas, r.decisao_vencedora) == (
        "revisao_humana",
        "violacao_prova_rco",
        (),
        None,
    )
    assert r.revisao_humana is True
    assert logs == [
        {
            "event": "rco_composicao_violacao_prova",
            "log_level": "warning",
            "decisoes": [_P1, _P2],
            "codigo": "CMP06_EXCLUSIVIDADE",
        }
    ]
    assert contados == ["violacao_prova_rco"]


def test_mais_de_uma_linha_vai_a_revisao_humana(contados: list[str]) -> None:
    with structlog.testing.capture_logs() as logs:
        r = rco_composicao.compor(
            _G, _Fake({_P1: [], _P2: [_linha("B"), _linha("C")], _PADRAO: []}), _ENTRADAS
        )
    assert (r.desfecho, r.motivo) == ("revisao_humana", "violacao_prova_rco")
    assert [(e["decisoes"], e["codigo"]) for e in logs] == [([_P2], "CMP08_UNIQUE")]
    assert contados == ["violacao_prova_rco"]


@pytest.mark.parametrize("resultado", [None, {"idRegra": "A"}, "linha", [None], [_linha(7)]], ids=str)
def test_resultado_invalido_vai_a_revisao_humana(resultado: Any, contados: list[str]) -> None:
    r = rco_composicao.compor(_G, _Fake({_P1: resultado, _P2: [], _PADRAO: []}), _ENTRADAS)
    assert (r.desfecho, r.motivo, r.linhas) == ("revisao_humana", "resultado_invalido", ())
    assert contados == ["resultado_invalido"]


@pytest.mark.parametrize("falha_em", [_P1, _P2, _PADRAO])
def test_falha_do_avaliador_vai_a_revisao_humana_sem_resultado_parcial(
    falha_em: str, contados: list[str]
) -> None:
    fake = _Fake({_P1: [_linha("A")], _P2: [], _PADRAO: [_linha("D")]}, falha_em=falha_em)
    with structlog.testing.capture_logs() as logs:
        r = rco_composicao.compor(_G, fake, _ENTRADAS)
    assert (r.desfecho, r.motivo, r.linhas, r.decisao_vencedora) == (
        "revisao_humana",
        "avaliador_falhou",
        (),
        None,
    )
    ordem = list(_G.decisoes)
    assert [d for d, _ in fake.chamadas] == ordem[: ordem.index(falha_em) + 1]
    assert logs == [
        {
            "event": "rco_composicao_avaliador_falhou",
            "log_level": "warning",
            "decisoes": [falha_em],
            "codigo": "ConnectionError",
        }
    ]
    assert contados == ["avaliador_falhou"]


def test_escolher_sem_o_resultado_de_uma_decisao_vai_a_revisao_humana(contados: list[str]) -> None:
    r = rco_composicao.escolher(_G, {_P1: [_linha("A")], _PADRAO: []})
    assert (r.desfecho, r.motivo) == ("revisao_humana", "avaliador_falhou")
    assert contados == ["avaliador_falhou"]


def test_compor_async_mesma_regra_e_falha(contados: list[str]) -> None:
    async def ok(decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]:
        return {_P1: [], _P2: [_linha("B")], _PADRAO: [_linha("D")]}[decisao]

    async def falha(decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]:
        raise TimeoutError

    r = asyncio.run(rco_composicao.compor_async(_G, ok, _ENTRADAS))
    assert (r.decisao_vencedora, r.pelo_padrao) == (_P2, False)
    r = asyncio.run(rco_composicao.compor_async(_G, falha, _ENTRADAS))
    assert (r.desfecho, r.motivo) == ("revisao_humana", "avaliador_falhou")
    assert contados == ["avaliador_falhou"]


def test_metrica_real_conta_a_violacao() -> None:
    registro = observability.get_metrics_collector().registry
    rotulos = {"motivo": "violacao_prova_rco"}
    antes = registro.get_sample_value("maezo_rco_composicao_revisao_humana_total", rotulos) or 0.0
    rco_composicao.compor(_G, _Fake({_P1: [_linha("A")], _P2: [_linha("B")], _PADRAO: []}), _ENTRADAS)
    depois = registro.get_sample_value("maezo_rco_composicao_revisao_humana_total", rotulos)
    assert depois == antes + 1


def test_motivos_fechados_em_paridade_com_a_metrica() -> None:
    assert rco_composicao.MOTIVOS == observability.RCO_COMPOSICAO_MOTIVOS
