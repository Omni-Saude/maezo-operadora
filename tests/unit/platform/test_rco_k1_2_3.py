"""K1 `formato_manifesto` 2.3.0 (DL-0183): leitor, gate, runtime e a composicao `mais_restritiva`.

Forma acordada com a trilha rco (contrato-k1.md §3, §4 item 8, §6, §9 e §11 item 16; PROG-COORD-26,
PROG-RCO-33): `composicoes[].regra` em {`primeira_com_idRegra`, `mais_restritiva`}; `vocabulario.json`
com `ordem_restricao` (permutacao EXATA de `decisao`) e `sinalAuditoria` (vocabulario fechado, com o
vazio); saida opcional `sinalAuditoria`, fora de `saidas_obrigatorias`; `avisos` no K6 com as chaves
exatas `{filas, prazo_parametro}`. Cada recusa tem o seu codigo; gate e runtime recusam pela MESMA
checagem. Na composicao: a decisao mais restritiva vence, empate e da primeira em `decisoes`, sem
desempate por sinal; o resultado leva a uniao dos sinais e o veredicto de todos os membros; membro sem
linha, com linha a mais, `decisao` fora da ordem, sinal fora do vocabulario, resultado invalido ou falha
do motor levam o grupo inteiro a `revisao_humana`, nunca a uma negativa. Pacote 100% sintetico.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import structlog
from scripts.ci.verify_rco_rules_pin import main, verify_lock

from maezo.platform import observability, rco_composicao
from maezo.platform import rco_manifesto as k1
from maezo.platform.rco_manifesto import ComposicaoK1
from maezo.tools.workers import rco_pin
from maezo.tools.workers.dmn_transport import DmnHitPolicyViolationError, DmnVersion
from tests.unit.platform import rco_k1_sintetico as sint

# --- leitor, gate e runtime ---------------------------------------------------------------------------


@pytest.fixture
def pacote_23(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    return tmp_path, sint.com_formato_2_3(tmp_path, sint.montar(tmp_path))


def _ler(raiz: Path) -> k1.ManifestoK1:
    return k1.ler_manifesto((raiz / sint.K1_DIR / "manifesto.json").read_bytes())


def _verificar(raiz: Path) -> list[str]:
    return k1.verificar_pacote(_ler(raiz), k1_dir=raiz / sint.K1_DIR, rules_dir=raiz / sint.RULES_DIR)


def _pin(raiz: Path) -> rco_pin.RcoRulesPin:
    return rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)


def test_23_le_confere_e_expoe(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote_23
    lido = _ler(raiz)
    assert lido.formato_manifesto == "2.3.0"
    assert lido.major_minor == (2, 3)
    grupo = lido.composicao_de("rco_sint_d")
    assert grupo == ComposicaoK1(
        "grupo_restritivo", ("rco_sint_d", "rco_sint_c"), k1.REGRA_MAIS_RESTRITIVA, "rco_sint_c"
    )
    assert _verificar(raiz) == []
    vocab = k1.carregar_vocabulario(lido, k1_dir=raiz / sint.K1_DIR)
    assert vocab.ordem_restricao == tuple(sint.ORDEM_RESTRICAO)
    assert vocab.sinais_auditoria == ("", sint.SINAL)
    assert "sinalAuditoria" not in vocab.saidas_obrigatorias
    avisos = k1.carregar_avisos_k6(lido, vocab, k1_dir=raiz / sint.K1_DIR)
    assert dict(avisos) == {
        sint.SINAL: k1.AvisoK6(sint.SINAL, ("auditoria_medica", "cadastro"), "SINT-0001:prazos.0")
    }
    assert verify_lock(sint.lock(raiz), repo_root=raiz) == []
    assert main(["--repo-root", str(raiz)]) == 0
    pin = _pin(raiz)
    assert pin.ordem_restricao == tuple(sint.ORDEM_RESTRICAO)
    assert pin.sinais_auditoria == ("", sint.SINAL)
    assert dict(pin.avisos) == dict(avisos)
    assert pin.composicoes == lido.composicoes


def test_22_continua_aceito_sem_os_blocos_novos(tmp_path: Path) -> None:
    sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    lido = _ler(tmp_path)
    assert lido.formato_manifesto == "2.2.0"
    vocab = k1.carregar_vocabulario(lido, k1_dir=tmp_path / sint.K1_DIR)
    # Abaixo de 2.3.0 o vocabulario do sinal e so o vazio (nunca `()`, que recusaria o vazio).
    assert (vocab.ordem_restricao, vocab.sinais_auditoria) == ((), k1.SINAIS_SO_VAZIO)
    assert k1.carregar_avisos_k6(lido, vocab, k1_dir=tmp_path / sint.K1_DIR) == {}
    pin = _pin(tmp_path)
    assert (pin.ordem_restricao, pin.sinais_auditoria, dict(pin.avisos)) == ((), ("",), {})


def test_compor_com_o_vocabulario_de_um_pin_22_da_resultado_normal(tmp_path: Path) -> None:
    # Revisao independente (MAJOR): com o pin 2.2 o vocabulario do sinal tem o vazio, entao `compor` com os
    # dados do pin decide normalmente (antes: todo grupo ia a revisao_humana CMP12_SINAL).
    sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    pin = _pin(tmp_path)
    (grupo,) = pin.composicoes
    linhas = {
        "rco_sint_a": [
            _l("negar", "SINT-A-0002", motivoTab38="3171", fila="cadastro", classeAdverso="administrativo")
        ],
        "rco_sint_b": [_l("informar", "SINT-B-0001", fila="cadastro")],
    }
    kw: dict[str, Any] = {
        "ordem_restricao": pin.ordem_restricao,
        "sinais_auditoria": pin.sinais_auditoria,
        "adversas": pin.adversas,
        "adversa_exige_preenchidos": pin.adversa_exige_preenchidos,
    }
    r = rco_composicao.compor(grupo, _Fake(linhas), {"tipoSintetico": "B"}, **kw, modo="efeito")
    assert (r.desfecho, r.decisao_vencedora, r.sinais) == ("escolhido", "rco_sint_a", ())
    sem_sinal = {"rco_sint_a": [_l("liberar", "")], "rco_sint_b": [_l("informar", "SINT-B-0001")]}
    r = rco_composicao.compor(grupo, _Fake(sem_sinal), {"tipoSintetico": "B"}, **kw, modo="efeito")
    assert (r.desfecho, r.decisao_vencedora, r.pelo_padrao) == ("escolhido", "rco_sint_b", True)
    # `mais_restritiva` nao existe no 2.2 (o leitor recusa); com o vocabulario do pin 2.2 e a ordem do
    # contrato, a regra tambem decide normalmente.
    gm = ComposicaoK1("gm", ("rco_sint_a", "rco_sint_b"), k1.REGRA_MAIS_RESTRITIVA, "rco_sint_b")
    r = rco_composicao.compor(
        gm, _Fake(linhas), {}, **{**kw, "ordem_restricao": tuple(_ORDEM)}, modo="efeito"
    )
    assert (r.desfecho, r.decisao_vencedora) == ("escolhido", "rco_sint_a")


def test_vocabulario_real_22_tem_o_sinal_vazio() -> None:
    from tests.unit.platform import rco_k1_2_2_recorte as rec22

    vocab = k1.carregar_vocabulario(rec22.manifesto(), k1_dir=rec22.RECORTE)
    assert vocab.sinais_auditoria == ("",)
    linhas = {d: [_l("liberar", "")] for d in rec22.ORDEM}
    r = rco_composicao.compor(
        rec22.composicao(), _Fake(linhas), {}, sinais_auditoria=vocab.sinais_auditoria, modo="efeito"
    )
    assert (r.desfecho, r.decisao_vencedora) == ("escolhido", "rco_mex_ortop")


def test_k6_sem_avisos_aceito_quando_nao_ha_sinal_nao_vazio(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote_23
    linhas = copy.deepcopy(_D_OK)
    linhas[0][5] = '""'
    _trocar_tabela_d(raiz, manifesto, linhas)
    sint.trocar_vocabulario(raiz, manifesto, sint.canonico(sint.vocabulario_2_3(sinalAuditoria=[""])))
    sint.trocar_k6(raiz, manifesto, sint.canonico(sint.k6_2_3(avisos=None)))
    assert _verificar(raiz) == []
    pin = _pin(raiz)
    assert (pin.sinais_auditoria, dict(pin.avisos)) == (("",), {})
    # Com o bloco vazio tambem; e um aviso sem sinal no vocabulario continua recusado.
    sint.trocar_k6(raiz, manifesto, sint.canonico(sint.k6_2_3(avisos={})))
    assert _verificar(raiz) == []
    sint.trocar_k6(raiz, manifesto, sint.canonico(sint.k6_2_3()))
    assert any("avisos-k6-invalido" in p for p in _verificar(raiz))


def test_23_campo_desconhecido_continua_recusado(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote_23
    manifesto["campo_novo"] = 1
    sint.regravar(raiz, manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError, match="desconhecidos \\['campo_novo'\\]"):
        _ler(raiz)


def test_24_continua_recusado(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote_23
    manifesto["formato_manifesto"] = "2.4.0"
    sint.regravar(raiz, manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError, match="formato_manifesto 2.4.0 nao suportado"):
        _ler(raiz)


def test_mais_restritiva_no_22_e_recusada(tmp_path: Path) -> None:
    manifesto = sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    sint.com_tabela_c(tmp_path, manifesto)
    manifesto["composicoes"] = [
        {
            "decisoes": ["rco_sint_a", "rco_sint_c"],
            "nome": "g",
            "padrao": "rco_sint_c",
            "regra": "mais_restritiva",
        }
    ]
    sint.regravar(tmp_path, manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError, match="composicao-regra-fora-do-formato"):
        _ler(tmp_path)
    violacoes = verify_lock(sint.lock(tmp_path), repo_root=tmp_path)
    assert {v.code for v in violacoes} == {"manifesto-invalido"}
    with pytest.raises(rco_pin.RcoRulesPinError, match="composicao-regra-fora-do-formato"):
        _pin(tmp_path)


def test_regra_fora_do_vocabulario_no_23_continua_desconhecida(
    pacote_23: tuple[Path, dict[str, Any]],
) -> None:
    raiz, manifesto = pacote_23
    manifesto["composicoes"][1]["regra"] = "mais_restritiva_por_sinal"
    sint.regravar(raiz, manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError, match="composicao-regra-desconhecida"):
        _ler(raiz)


def _sem(lista: list[str], valor: str) -> list[str]:
    return [v for v in lista if v != valor]


_ORDEM = sint.ORDEM_RESTRICAO


@pytest.mark.parametrize(
    ("trocas", "codigo"),
    [
        ({"ordem_restricao": None}, "ordem-restricao-invalida"),
        ({"ordem_restricao": "negar"}, "ordem-restricao-invalida"),
        ({"ordem_restricao": _sem(_ORDEM, "liberar")}, "ordem-restricao-invalida"),
        ({"ordem_restricao": [*_ORDEM, "pagar_tudo"]}, "ordem-restricao-invalida"),
        ({"ordem_restricao": [*_ORDEM, "negar"]}, "ordem-restricao-invalida"),
        ({"ordem_restricao": [*_sem(_ORDEM, "liberar"), "negar"]}, "ordem-restricao-invalida"),
        ({"ordem_restricao": [*_ORDEM[:-1], 7]}, "ordem-restricao-invalida"),
        ({"decisao": None}, "ordem-restricao-invalida"),
        ({"decisao": [*sint.DECISOES, "liberar"]}, "ordem-restricao-invalida"),
        ({"decisao": [*sint.DECISOES, "pagar_tudo"]}, "ordem-restricao-invalida"),
        ({"sinalAuditoria": None}, "sinal-auditoria-invalido"),
        ({"sinalAuditoria": [sint.SINAL]}, "sinal-auditoria-invalido"),
        ({"sinalAuditoria": ["", sint.SINAL, sint.SINAL]}, "sinal-auditoria-invalido"),
        ({"sinalAuditoria": ["", "Aviso Maiusculo"]}, "sinal-auditoria-invalido"),
        ({"sinalAuditoria": ["", None]}, "sinal-auditoria-invalido"),
        ({"sinalAuditoria": ""}, "sinal-auditoria-invalido"),
        (
            {"saidas_obrigatorias": [*sint.SAIDAS_OBRIGATORIAS, "sinalAuditoria"]},
            "sinal-auditoria-obrigatorio",
        ),
    ],
)
def test_vocabulario_23_invalido_recusa_o_pin(
    pacote_23: tuple[Path, dict[str, Any]], trocas: dict[str, Any], codigo: str
) -> None:
    raiz, manifesto = pacote_23
    sint.trocar_vocabulario(raiz, manifesto, sint.canonico(sint.vocabulario_2_3(**trocas)))
    assert any(codigo in p for p in _verificar(raiz)), _verificar(raiz)
    with pytest.raises(k1.ManifestoK1InvalidoError, match=codigo):
        k1.carregar_vocabulario(_ler(raiz), k1_dir=raiz / sint.K1_DIR)
    violacoes = verify_lock(sint.lock(raiz), repo_root=raiz)
    assert any(v.code == "pacote-k1" and codigo in v.detail for v in violacoes)
    with pytest.raises(rco_pin.RcoRulesPinError, match=codigo):
        _pin(raiz)


def test_ordem_e_permutacao_qualquer(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    # A ordem e dado do pacote: outra permutacao exata passa (o leitor nao fixa a ordem no codigo).
    raiz, manifesto = pacote_23
    invertida = list(reversed(_ORDEM))
    sint.trocar_vocabulario(raiz, manifesto, sint.canonico(sint.vocabulario_2_3(ordem_restricao=invertida)))
    assert _verificar(raiz) == []
    assert _pin(raiz).ordem_restricao == tuple(invertida)


def _aviso(**trocas: Any) -> dict[str, Any]:
    item: dict[str, Any] = copy.deepcopy(sint.AVISOS[sint.SINAL])
    for chave, valor in trocas.items():
        if valor is None:
            item.pop(chave)
        else:
            item[chave] = valor
    return {sint.SINAL: item}


@pytest.mark.parametrize(
    "k6",
    [
        sint.k6_2_3(avisos=None),
        sint.k6_2_3(avisos=[]),
        sint.k6_2_3(avisos={}),
        sint.k6_2_3(avisos={**sint.AVISOS, "aviso_desconhecido": sint.AVISOS[sint.SINAL]}),
        sint.k6_2_3(avisos={**sint.AVISOS, "": sint.AVISOS[sint.SINAL]}),
        sint.k6_2_3(avisos=_aviso(extra=1)),
        sint.k6_2_3(avisos=_aviso(prazo_parametro=None)),
        sint.k6_2_3(avisos=_aviso(filas=None)),
        sint.k6_2_3(avisos=_aviso(filas=[])),
        sint.k6_2_3(avisos=_aviso(filas=["cadastro", "cadastro"])),
        sint.k6_2_3(avisos=_aviso(filas=["fila_inexistente"])),
        sint.k6_2_3(avisos=_aviso(filas="cadastro")),
        sint.k6_2_3(avisos=_aviso(prazo_parametro=" ")),
        sint.k6_2_3(avisos=_aviso(prazo_parametro=3)),
        sint.k6_2_3(filas=None),
        sint.k6_2_3(filas=["cadastro"]),
        {"avisos": sint.AVISOS},
    ],
)
def test_avisos_k6_invalido_recusa_o_pin(pacote_23: tuple[Path, dict[str, Any]], k6: dict[str, Any]) -> None:
    raiz, manifesto = pacote_23
    sint.trocar_k6(raiz, manifesto, sint.canonico(k6))
    codigo = "avisos-k6-invalido"
    assert any(codigo in p for p in _verificar(raiz)), _verificar(raiz)
    violacoes = verify_lock(sint.lock(raiz), repo_root=raiz)
    assert any(v.code == "pacote-k1" and codigo in v.detail for v in violacoes)
    assert main(["--repo-root", str(raiz)]) == 1
    with pytest.raises(rco_pin.RcoRulesPinError, match=codigo):
        _pin(raiz)


def test_k6_nao_objeto_recusa(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote_23
    sint.trocar_k6(raiz, manifesto, b"[]")
    assert any("avisos-k6-invalido" in p for p in _verificar(raiz))


def test_destinos_dos_avisos_sao_dado(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    # Trocar a fila de destino nao muda o formato: o leitor so confere que ela existe no K6.
    raiz, manifesto = pacote_23
    sint.trocar_k6(raiz, manifesto, sint.canonico(sint.k6_2_3(avisos=_aviso(filas=["regulacao"]))))
    assert _verificar(raiz) == []
    assert _pin(raiz).avisos[sint.SINAL].filas == ("regulacao",)


def _trocar_tabela_d(raiz: Path, manifesto: dict[str, Any], linhas: list[list[str]]) -> None:
    saidas, _, ids = sint._TABELA_D
    sint.com_tabela(raiz, manifesto, "sint_d", (saidas, linhas, ids))


_D_OK: list[list[str]] = [list(linha) for linha in sint._TABELA_D[1]]


@pytest.mark.parametrize(
    ("celula", "aceita"),
    [
        ('""', True),
        ("null", True),
        ("", True),
        (f'"{sint.SINAL}"', True),
        ('"aviso_inventado"', False),
        ('"AVISO_SINTETICO"', False),
        (f'if true then "{sint.SINAL}" else ""', False),
    ],
)
def test_celula_de_sinal_no_vocabulario(
    pacote_23: tuple[Path, dict[str, Any]], celula: str, aceita: bool
) -> None:
    raiz, manifesto = pacote_23
    linhas = copy.deepcopy(_D_OK)
    linhas[1][5] = celula
    _trocar_tabela_d(raiz, manifesto, linhas)
    problemas = _verificar(raiz)
    if aceita:
        assert problemas == []
        return
    assert any("sinal-auditoria-fora-do-vocabulario" in p for p in problemas), problemas
    with pytest.raises(rco_pin.RcoRulesPinError, match="sinal-auditoria-fora-do-vocabulario"):
        _pin(raiz)


def test_sinal_antes_de_23_e_recusado(tmp_path: Path) -> None:
    manifesto = sint.com_composicoes(tmp_path, sint.montar(tmp_path))
    sint.com_tabela(tmp_path, manifesto, "sint_d", sint._TABELA_D)
    problemas = _verificar(tmp_path)
    assert any("sinal-auditoria-antes-de-2.3" in p for p in problemas), problemas
    with pytest.raises(rco_pin.RcoRulesPinError, match="sinal-auditoria-antes-de-2.3"):
        _pin(tmp_path)


def test_decisao_fora_da_ordem_em_grupo_mais_restritiva_recusa(
    pacote_23: tuple[Path, dict[str, Any]],
) -> None:
    raiz, manifesto = pacote_23
    linhas = copy.deepcopy(_D_OK)
    linhas[0][0] = '"pagar_tudo"'
    _trocar_tabela_d(raiz, manifesto, linhas)
    problemas = _verificar(raiz)
    assert any("composicao-decisao-fora-da-ordem" in p for p in problemas), problemas
    violacoes = verify_lock(sint.lock(raiz), repo_root=raiz)
    assert any("composicao-decisao-fora-da-ordem" in v.detail for v in violacoes)
    with pytest.raises(rco_pin.RcoRulesPinError, match="composicao-decisao-fora-da-ordem"):
        _pin(raiz)


def test_tabela_fora_de_grupo_mais_restritiva_nao_confere_a_ordem(
    pacote_23: tuple[Path, dict[str, Any]],
) -> None:
    # Fora do grupo `mais_restritiva`, a `decisao` fora do vocabulario e do lint de saida, nao desta checagem.
    raiz, manifesto = pacote_23
    manifesto["composicoes"] = [sint.COMPOSICAO]
    sint.regravar(raiz, manifesto)
    linhas = copy.deepcopy(_D_OK)
    linhas[0][0] = '"pagar_tudo"'
    _trocar_tabela_d(raiz, manifesto, linhas)
    assert not any("composicao-decisao-fora-da-ordem" in p for p in _verificar(raiz))


@pytest.mark.parametrize("decisao", ["rco_sint_d", "rco_sint_c"])
def test_guarda_vale_para_cada_membro_de_mais_restritiva(
    pacote_23: tuple[Path, dict[str, Any]], decisao: str
) -> None:
    raiz, _ = pacote_23
    pin = _pin(raiz)
    assert rco_composicao.membro_de_composicao(pin, decisao)
    papel = "padrao" if decisao == "rco_sint_c" else "membro"
    with pytest.raises(rco_composicao.DecisaoSoEmComposicaoError, match=f"e {papel} da composicao"):
        rco_composicao.exigir_avaliacao_isolada(pin, decisao)


# --- semantica `mais_restritiva` ---------------------------------------------------------------------

_M1, _M2, _M3 = "rco_m1", "rco_m2", "rco_m3"
_GM = ComposicaoK1("gm", (_M1, _M2, _M3), k1.REGRA_MAIS_RESTRITIVA, _M3)
_GM2 = ComposicaoK1("gm2", (_M1, _M2), k1.REGRA_MAIS_RESTRITIVA, _M2)
_ORDEM_T = tuple(_ORDEM)
_SINAIS = frozenset(sint.SINAIS)
_ENTRADAS: dict[str, Any] = {"caraterAtendimento": "2", "lista": [1, 2]}


def _l(decisao: str, id_regra: str = "", sinal: str | None = None, **extra: Any) -> dict[str, Any]:
    linha: dict[str, Any] = {
        "decisao": decisao,
        "idRegra": id_regra,
        "motivoTab38": extra.pop("motivoTab38", ""),
        "fila": extra.pop("fila", ""),
        "classeAdverso": extra.pop("classeAdverso", ""),
    }
    if sinal is not None:
        linha["sinalAuditoria"] = sinal
    linha.update(extra)
    return linha


class _Fake:
    def __init__(
        self, respostas: Mapping[str, Any], falha_em: str | None = None, erro: Exception | None = None
    ):
        self.respostas = respostas
        self.falha_em = falha_em
        self.erro = erro or ConnectionError("motor fora")
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, decisao: str, entradas: Mapping[str, Any]) -> Any:
        self.chamadas.append((decisao, copy.deepcopy(dict(entradas))))
        if decisao == self.falha_em:
            raise self.erro
        return self.respostas[decisao]


def _compor(
    grupo: ComposicaoK1, respostas: Mapping[str, Any], **kw: Any
) -> rco_composicao.ResultadoComposicao:
    kw.setdefault("ordem_restricao", _ORDEM_T)
    kw.setdefault("sinais_auditoria", _SINAIS)
    return rco_composicao.compor(grupo, _Fake(respostas), _ENTRADAS, **kw, modo="efeito")


@pytest.fixture
def contados(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    registro: list[str] = []

    def contar(motivo: str, *, modo: str) -> None:
        assert modo == rco_composicao.MODO_EFEITO  # estes testes compoem em modo efeito
        registro.append(motivo)

    monkeypatch.setattr(rco_composicao, "record_rco_composicao_revisao", contar)
    return registro


@pytest.mark.parametrize(("i", "j"), list(itertools.combinations(range(len(_ORDEM)), 2)))
def test_vence_a_mais_restritiva_em_qualquer_posicao(i: int, j: int) -> None:
    mais, menos = _ORDEM[i], _ORDEM[j]
    for primeira, segunda in ((mais, menos), (menos, mais)):
        r = _compor(_GM2, {_M1: [_l(primeira, "R-1")], _M2: [_l(segunda, "R-2")]})
        assert r.desfecho == rco_composicao.DESFECHO_ESCOLHIDO
        assert r.linhas[0]["decisao"] == mais
        assert r.decisao_vencedora == (_M1 if primeira == mais else _M2)
        assert r.pelo_padrao is False


@pytest.mark.parametrize("decisao", _ORDEM)
def test_empate_e_da_primeira_em_decisoes(decisao: str) -> None:
    r = _compor(_GM, {_M1: [_l(decisao, "R-1")], _M2: [_l(decisao, "R-2")], _M3: [_l(decisao, "R-3")]})
    assert (r.decisao_vencedora, r.linhas[0]["idRegra"]) == (_M1, "R-1")


def test_sem_desempate_por_sinal() -> None:
    # Empate de `decisao`: o sinal da segunda NAO a faz vencer; o sinal vai junto pela uniao.
    r = _compor(_GM2, {_M1: [_l("liberar", "R-1")], _M2: [_l("liberar", "R-2", sint.SINAL)]})
    assert (r.decisao_vencedora, r.sinais) == (_M1, (sint.SINAL,))


def test_resultado_leva_os_campos_da_vencedora_e_o_veredicto_de_todas() -> None:
    v1 = DmnVersion(key=_M1, id="def-1", version=1, deployment_id="dep-1")
    v2 = DmnVersion(key=_M2, id="def-2", version=4, deployment_id="dep-2")
    linha_cpt = _l("revisao_humana", "OMNI-GERAL-CAR-0004", fila="regulacao")
    linha_car = _l("liberar", "OMNI-GERAL-CAR-0003", sint.SINAL)
    r = rco_composicao.escolher(
        _GM2,
        {_M1: ([linha_car], v1), _M2: ([linha_cpt], v2)},
        ordem_restricao=_ORDEM_T,
        sinais_auditoria=_SINAIS,
        modo="efeito",
    )
    assert r.decisao_vencedora == _M2
    assert dict(r.linhas[0]) == linha_cpt
    assert r.sinais == (sint.SINAL,)  # a carencia libera com aviso; a CPT manda a regulacao: aviso junto
    assert {d: [dict(x) for x in ls] for d, ls in r.veredictos.items()} == {
        _M1: [linha_car],
        _M2: [linha_cpt],
    }
    assert dict(r.versoes) == {_M1: v1, _M2: v2}
    assert r.avaliadas == (_M1, _M2)
    r.linhas[0]["decisao"] = "negar"  # type: ignore[index]
    assert linha_cpt["decisao"] == "revisao_humana"


def test_uniao_dos_sinais_na_ordem_do_grupo_sem_repeticao() -> None:
    respostas = {
        _M1: [_l("informar", "R-1", sint.SINAL)],
        _M2: [_l("liberar", "R-2", "")],
        _M3: [_l("glosar", "R-3", sint.SINAL)],
    }
    r = _compor(_GM, respostas)
    assert (r.decisao_vencedora, r.sinais) == (_M3, (sint.SINAL,))
    r = _compor(_GM, {_M1: [_l("liberar")], _M2: [_l("liberar")], _M3: [_l("liberar")]})
    assert r.sinais == ()


@pytest.mark.parametrize("vazio", [None, "", "  ", '""'])
def test_sinal_vazio_nao_entra_na_uniao(vazio: Any) -> None:
    r = _compor(_GM2, {_M1: [_l("liberar", sinal=vazio)], _M2: [_l("liberar")]})
    assert (r.desfecho, r.sinais) == (rco_composicao.DESFECHO_ESCOLHIDO, ())


def test_entradas_iguais_para_todas_e_todas_avaliadas() -> None:
    fake = _Fake({_M1: [_l("negar", "R-1")], _M2: [_l("liberar")], _M3: [_l("liberar")]})
    r = rco_composicao.compor(
        _GM, fake, _ENTRADAS, ordem_restricao=_ORDEM_T, sinais_auditoria=_SINAIS, modo="efeito"
    )
    assert [d for d, _ in fake.chamadas] == [_M1, _M2, _M3]
    assert all(e == _ENTRADAS for _, e in fake.chamadas)
    assert r.avaliadas == (_M1, _M2, _M3)


def _revisao(r: rco_composicao.ResultadoComposicao, motivo: str) -> None:
    assert r.desfecho == rco_composicao.DESFECHO_REVISAO_HUMANA
    assert r.revisao_humana
    assert r.motivo == motivo
    assert r.linhas == ()
    assert r.decisao_vencedora is None


@pytest.mark.parametrize(
    ("resposta_m2", "motivo", "codigo"),
    [
        ([], "violacao_prova_rco", "CMP10_LACUNA"),
        ([_l("liberar"), _l("negar", "R")], "violacao_prova_rco", "CMP08_UNIQUE"),
        ([_l("pagar_tudo")], "violacao_prova_rco", "CMP11_FORA_DA_ORDEM"),
        ([_l("")], "violacao_prova_rco", "CMP11_FORA_DA_ORDEM"),
        ([{"idRegra": "R"}], "violacao_prova_rco", "CMP11_FORA_DA_ORDEM"),
        ([{"decisao": None}], "violacao_prova_rco", "CMP11_FORA_DA_ORDEM"),
        ([{"decisao": 3}], "violacao_prova_rco", "CMP11_FORA_DA_ORDEM"),
        ([_l("liberar", sinal="aviso_inventado")], "violacao_prova_rco", "CMP12_SINAL"),
        ([_l("liberar", sinal=7)], "resultado_invalido", "resultado_invalido"),
        ([_l("liberar", id_regra=7)], "resultado_invalido", "resultado_invalido"),
        ("nao e lista", "resultado_invalido", "resultado_invalido"),
        ([["nao", "e", "objeto"]], "resultado_invalido", "resultado_invalido"),
        (None, "resultado_invalido", "resultado_invalido"),
    ],
)
def test_membro_com_falha_leva_o_grupo_a_revisao_humana(
    contados: list[str], resposta_m2: Any, motivo: str, codigo: str
) -> None:
    # A primeira decisao NEGA: mesmo assim o grupo nunca sai `negar` quando um membro falha.
    respostas = {
        _M1: [_l("negar", "R-1", sint.SINAL, motivoTab38="3171")],
        _M2: resposta_m2,
        _M3: [_l("liberar")],
    }
    with structlog.testing.capture_logs() as logs:
        r = _compor(_GM, respostas)
    _revisao(r, motivo)
    assert contados == [motivo]
    assert [(e["codigo"], e["decisoes"]) for e in logs if "codigo" in e] == [(codigo, [_M2])]
    assert r.sinais == (sint.SINAL,)  # o aviso valido de outro membro nao se perde
    assert _M1 in r.veredictos


def test_falha_do_motor_em_qualquer_membro_vai_a_revisao_humana(contados: list[str]) -> None:
    respostas = {_M1: [_l("liberar", "R-1", sint.SINAL)], _M2: [_l("negar", "R-2")], _M3: [_l("liberar")]}
    for falha in (_M1, _M2, _M3):
        fake = _Fake(respostas, falha_em=falha)
        r = rco_composicao.compor(
            _GM, fake, _ENTRADAS, ordem_restricao=_ORDEM_T, sinais_auditoria=_SINAIS, modo="efeito"
        )
        _revisao(r, "avaliador_falhou")
        assert [d for d, _ in fake.chamadas][-1] == falha  # nao avalia as seguintes
        assert r.avaliadas == _GM.decisoes[: _GM.decisoes.index(falha)]
        assert r.sinais == (() if falha == _M1 else (sint.SINAL,))
    assert contados == ["avaliador_falhou"] * 3


def test_hit_policy_violation_do_motor_e_cmp08(contados: list[str]) -> None:
    fake = _Fake({_M1: [_l("liberar")]}, falha_em=_M2, erro=DmnHitPolicyViolationError(_M2))
    r = rco_composicao.compor(
        _GM, fake, _ENTRADAS, ordem_restricao=_ORDEM_T, sinais_auditoria=_SINAIS, modo="efeito"
    )
    _revisao(r, "violacao_prova_rco")
    assert contados == ["violacao_prova_rco"]


def test_resultado_faltando_vai_a_revisao_humana(contados: list[str]) -> None:
    r = rco_composicao.escolher(
        _GM, {_M1: [_l("liberar")], _M3: [_l("liberar")]}, ordem_restricao=_ORDEM_T, modo="efeito"
    )
    _revisao(r, "avaliador_falhou")


@pytest.mark.parametrize("ordem", [(), ("negar", "negar", "liberar"), ("negar", ""), "negar"])
def test_ordem_ausente_ou_invalida_vai_a_revisao_humana(contados: list[str], ordem: Any) -> None:
    with structlog.testing.capture_logs() as logs:
        r = _compor(_GM2, {_M1: [_l("liberar")], _M2: [_l("liberar")]}, ordem_restricao=ordem)
    _revisao(r, "resultado_invalido")
    assert [e["codigo"] for e in logs if "codigo" in e] == ["ordem_restricao_invalida"]


def test_sinal_sem_vocabulario_do_pin_vai_a_revisao_humana(contados: list[str]) -> None:
    # Sem o vocabulario do pin, so o vazio e conhecido: sinal nao vazio nunca e aceito as cegas.
    r = rco_composicao.compor(
        _GM2,
        _Fake({_M1: [_l("liberar", sinal=sint.SINAL)], _M2: [_l("liberar")]}),
        _ENTRADAS,
        ordem_restricao=_ORDEM_T,
        modo="efeito",
    )
    _revisao(r, "violacao_prova_rco")


def test_compor_async_mesma_regra(contados: list[str]) -> None:
    respostas = {
        _M1: [_l("liberar", "R-1", sint.SINAL)],
        _M2: [_l("exigir_documento", "R-2")],
        _M3: [_l("liberar")],
    }

    async def ok(decisao: str, entradas: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return list(respostas[decisao])

    async def falha(decisao: str, entradas: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        if decisao == _M3:
            raise TimeoutError("motor")
        return list(respostas[decisao])

    r = asyncio.run(
        rco_composicao.compor_async(
            _GM, ok, _ENTRADAS, ordem_restricao=_ORDEM_T, sinais_auditoria=_SINAIS, modo="efeito"
        )
    )
    assert (r.decisao_vencedora, r.linhas[0]["decisao"], r.sinais) == (_M2, "exigir_documento", (sint.SINAL,))
    r = asyncio.run(
        rco_composicao.compor_async(
            _GM, falha, _ENTRADAS, ordem_restricao=_ORDEM_T, sinais_auditoria=_SINAIS, modo="efeito"
        )
    )
    _revisao(r, "avaliador_falhou")
    assert r.sinais == (sint.SINAL,)
    r = asyncio.run(
        rco_composicao.compor_async(
            _GM, ok, _ENTRADAS, ordem_restricao=(), sinais_auditoria=_SINAIS, modo="efeito"
        )
    )
    _revisao(r, "resultado_invalido")
    assert contados == ["avaliador_falhou", "resultado_invalido"]


def test_metrica_real_conta_a_lacuna() -> None:
    registro = observability.get_metrics_collector().registry
    rotulos = {"motivo": "violacao_prova_rco", "modo": "efeito"}
    antes = registro.get_sample_value("maezo_rco_composicao_revisao_humana_total", rotulos) or 0.0
    _compor(_GM2, {_M1: [], _M2: [_l("liberar")]})
    depois = registro.get_sample_value("maezo_rco_composicao_revisao_humana_total", rotulos)
    assert depois == antes + 1


def test_motivos_continuam_fechados() -> None:
    assert rco_composicao.MOTIVOS == observability.RCO_COMPOSICAO_MOTIVOS


# --- `primeira_com_idRegra` inalterada; sinal tambem unido ------------------------------------------

_P1, _PADRAO = "rco_p1", "rco_padrao"
_GP = ComposicaoK1("gp", (_P1, _PADRAO), k1.REGRA_PRIMEIRA_COM_ID_REGRA, _PADRAO)


def test_primeira_com_id_regra_ignora_a_ordem_de_restricao() -> None:
    # A anterior com idRegra vence mesmo menos restritiva: a regra antiga nao muda.
    r = _compor(_GP, {_P1: [_l("liberar", "R-1")], _PADRAO: [_l("negar", "R-2")]})
    assert (r.decisao_vencedora, r.linhas[0]["decisao"], r.pelo_padrao) == (_P1, "liberar", False)
    r = _compor(_GP, {_P1: [], _PADRAO: [_l("negar", "R-2")]})
    assert (r.decisao_vencedora, r.pelo_padrao) == (_PADRAO, True)
    assert dict(r.veredictos) == {_P1: (), _PADRAO: (_l("negar", "R-2"),)}


def test_primeira_com_id_regra_sem_linha_no_padrao_continua_valendo() -> None:
    r = rco_composicao.compor(_GP, _Fake({_P1: [_l("glosar", "")], _PADRAO: []}), _ENTRADAS, modo="efeito")
    assert (r.desfecho, r.decisao_vencedora, r.linhas, r.sinais) == ("escolhido", _PADRAO, (), ())


def test_primeira_com_id_regra_tambem_une_sinais_e_recusa_o_desconhecido(contados: list[str]) -> None:
    r = _compor(_GP, {_P1: [_l("liberar", "", sint.SINAL)], _PADRAO: [_l("liberar", "R-2")]})
    assert (r.decisao_vencedora, r.sinais) == (_PADRAO, (sint.SINAL,))
    r = _compor(_GP, {_P1: [_l("liberar", "", "aviso_inventado")], _PADRAO: [_l("liberar", "R-2")]})
    _revisao(r, "violacao_prova_rco")


def test_compor_com_o_pin_23(pacote_23: tuple[Path, dict[str, Any]]) -> None:
    # Ponta a ponta sem motor: o grupo `mais_restritiva` do pin, com a ordem e os sinais do pin.
    raiz, _ = pacote_23
    pin = _pin(raiz)
    grupo = next(c for c in pin.composicoes if c.regra == k1.REGRA_MAIS_RESTRITIVA)
    linhas: dict[str, list[dict[str, Any]]] = {
        "rco_sint_d": [_l("liberar", "SINT-D-0001", sint.SINAL)],
        "rco_sint_c": [
            _l("glosar", "SINT-C-0001", fila="cadastro", classeAdverso="administrativo", motivoTab38="1801")
        ],
    }
    avaliador: Callable[[str, Mapping[str, Any]], Any] = _Fake(linhas)
    r = rco_composicao.compor(
        grupo,
        avaliador,
        {"tipoSintetico": "A"},
        ordem_restricao=pin.ordem_restricao,
        sinais_auditoria=pin.sinais_auditoria,
        modo="efeito",
    )
    assert (r.decisao_vencedora, r.linhas[0]["idRegra"], r.sinais) == (
        "rco_sint_c",
        "SINT-C-0001",
        (sint.SINAL,),
    )
    assert set(pin.avisos) == set(r.sinais)


# --- revisao independente: vencedora adversa incompleta e sinal em linha a mais ---------------------

_EXIGE = ("classeAdverso", "fila", "idRegra", "motivoTab38")
_ADVERSAS = frozenset({"negar", "glosar", "recomendar_negativa"})
_COMPLETA: dict[str, Any] = {"motivoTab38": "3171", "fila": "cadastro", "classeAdverso": "administrativo"}


@pytest.mark.parametrize("faltando", ["idRegra", "motivoTab38", "fila", "classeAdverso"])
@pytest.mark.parametrize("vazio", [None, "", "  ", '""'])
def test_vencedora_adversa_incompleta_vai_a_revisao_nas_duas_regras(
    contados: list[str], faltando: str, vazio: Any
) -> None:
    adversa = _l("negar", "R-1", **_COMPLETA)
    adversa[faltando] = vazio
    kw: dict[str, Any] = {"adversas": _ADVERSAS, "adversa_exige_preenchidos": _EXIGE}
    with structlog.testing.capture_logs() as logs:
        r = _compor(_GM2, {_M1: [adversa], _M2: [_l("liberar", "R-2")]}, **kw)
    _revisao(r, "resultado_invalido")
    assert [e["codigo"] for e in logs if "codigo" in e] == ["adversa_incompleta"]
    # primeira_com_idRegra: o padrao adverso vence (nenhuma anterior com idRegra) e esta incompleto.
    r = _compor(_GP, {_P1: [_l("liberar", "")], _PADRAO: [adversa]}, **kw)
    _revisao(r, "resultado_invalido")


def test_vencedora_adversa_completa_e_escolhida() -> None:
    kw: dict[str, Any] = {"adversas": _ADVERSAS, "adversa_exige_preenchidos": _EXIGE}
    r = _compor(_GM2, {_M1: [_l("glosar", "R-1", **_COMPLETA)], _M2: [_l("liberar", "")]}, **kw)
    assert (r.desfecho, r.decisao_vencedora) == ("escolhido", _M1)
    # Nao adversa sem idRegra segue valendo (o vazio canonico so e proibido em linha adversa).
    r = _compor(_GM2, {_M1: [_l("liberar", "")], _M2: [_l("informar", "")]}, **kw)
    assert (r.desfecho, r.decisao_vencedora) == ("escolhido", _M2)


def test_piso_sem_o_combinacao_do_pin_exige_id_regra(contados: list[str]) -> None:
    r = _compor(_GM2, {_M1: [_l("recomendar_negativa", "")], _M2: [_l("liberar", "R-2")]})
    _revisao(r, "resultado_invalido")
    r = _compor(_GM2, {_M1: [_l("recomendar_negativa", "R-1")], _M2: [_l("liberar", "R-2")]})
    assert r.decisao_vencedora == _M1


def test_sinal_em_linha_a_mais_nao_se_perde(contados: list[str]) -> None:
    respostas = {_M1: [_l("liberar"), _l("revisao_humana", "R", sint.SINAL)], _M2: [_l("liberar")]}
    with structlog.testing.capture_logs() as logs:
        r = _compor(_GM2, respostas)
    _revisao(r, "violacao_prova_rco")
    assert [e["codigo"] for e in logs if "codigo" in e] == ["CMP08_UNIQUE"]
    assert r.sinais == (sint.SINAL,)
    # Sinal desconhecido em qualquer linha continua CMP12 (e nao entra na uniao).
    respostas = {
        _M1: [_l("liberar", sinal=sint.SINAL), _l("liberar", sinal="aviso_inventado")],
        _M2: [_l("liberar")],
    }
    with structlog.testing.capture_logs() as logs:
        r = _compor(_GM2, respostas)
    _revisao(r, "violacao_prova_rco")
    assert [e["codigo"] for e in logs if "codigo" in e] == ["CMP12_SINAL"]
    assert r.sinais == (sint.SINAL,)


def test_sinal_em_linha_a_mais_tambem_na_falha_do_motor(contados: list[str]) -> None:
    fake = _Fake({_M1: [_l("liberar"), _l("liberar", sinal=sint.SINAL)]}, falha_em=_M2)
    r = rco_composicao.compor(
        _GM2, fake, _ENTRADAS, ordem_restricao=_ORDEM_T, sinais_auditoria=_SINAIS, modo="efeito"
    )
    _revisao(r, "avaliador_falhou")
    assert r.sinais == (sint.SINAL,)
