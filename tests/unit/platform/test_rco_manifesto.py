"""Leitor do manifesto K1 do rco (`maezo.platform.rco_manifesto`, MZO-2a; contrato-k1.md §11 itens 1-6).

Contra o pacote SINTETICO de `rco_k1_sintetico.py` (nenhum byte real do rco): caminho feliz e cada
recusa fail-closed — digest cru, formato MAJOR/MINOR, `origem.sujo`, campo desconhecido ou ausente,
bytes nao canonicos, prefixo `rco_`, caminho nao plano, anexo adulterado, coluna de saida fora do
`saidas[]` e saida obrigatoria ausente.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_manifesto as k1
from tests.unit.platform import rco_k1_sintetico as sint


@pytest.fixture
def pacote(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    return tmp_path, sint.montar(tmp_path)


def _ler(raiz: Path) -> k1.ManifestoK1:
    return k1.ler_manifesto((raiz / sint.K1_DIR / "manifesto.json").read_bytes())


def _verificar(raiz: Path) -> list[str]:
    return k1.verificar_pacote(_ler(raiz), k1_dir=raiz / sint.K1_DIR, rules_dir=raiz / sint.RULES_DIR)


def test_constantes_batem_com_o_gate() -> None:
    from scripts.ci import verify_rco_rules_pin as gate

    assert k1.K1_DIR == gate.K1_DIR
    assert k1.K1_MANIFESTO_PATH == gate.K1_MANIFEST_PATH
    assert k1.RULES_DIR == gate.RULES_DIR


def test_caminho_feliz_expoe_o_que_a_auditoria_usa(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    lido = _ler(raiz)
    assert lido.formato_manifesto == "2.0.0"
    assert (lido.repo, lido.commit_sha) == (sint.REPO, sint.COMMIT)
    assert set(lido.decisoes) == {"rco_sint_a", "rco_sint_b"}
    a = lido.decisoes["rco_sint_a"]
    assert a.caminho_consumidor == f"{sint.RULES_DIR}/sint_a.dmn"
    assert a.sha256 == manifesto["decisoes"][0]["sha256"]  # = shaDmn
    assert a.regras["SINT-A-0002"].versao_regra == "1.0.1"
    assert a.regras["SINT-A-0002"].sha256_yaml.startswith("sha256:")
    assert lido.decisoes["rco_sint_b"].saidas[-1] == "acaoSintetica"
    # MZO-2b: o montador de entradas escolhe o mapeamento pelo dominio e monta as entradas declaradas.
    assert (a.dominio, a.entradas) == ("sintetico", ("tipoSintetico",))
    assert a.entradas_declaradas == (("tipoSintetico", "fora_tiss", None),)
    assert lido.dicionario_entradas_versao == "1.0.0"
    assert lido.dicionario_entradas_sha256 == manifesto["dicionario_entradas"]["sha256"]
    assert {p: x.nome for p, x in lido.anexos.items()} == {
        "vocabulario": "vocabulario.json",
        "entradas": "entradas.json",
        "k6": "k6-filas.json",
    }
    assert lido.exemplos == ("exemplos/rco_sint_a.json",)  # listado, nunca vendorizado
    assert _verificar(raiz) == []


def _mutacao(caminho: list[Any], valor: Any) -> Callable[[dict[str, Any]], None]:
    def aplicar(m: dict[str, Any]) -> None:
        alvo: Any = m
        for chave in caminho[:-1]:
            alvo = alvo[chave]
        if valor is _REMOVER:
            del alvo[caminho[-1]]
        else:
            alvo[caminho[-1]] = valor

    return aplicar


_REMOVER = object()
_HEX_CRU = "a" * 64


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mutacao(["origem", "sujo"], True), "origem.sujo"),
        (_mutacao(["origem", "sujo"], False), "origem.sujo"),  # a chave, presente, ja recusa
        (_mutacao(["formato_manifesto"], "3.0.0"), "formato_manifesto 3.0.0"),
        (_mutacao(["formato_manifesto"], "2.1.0"), "formato_manifesto 2.1.0"),
        (_mutacao(["formato_manifesto"], "1.1.0"), "formato_manifesto 1.1.0"),
        (_mutacao(["formato_manifesto"], "1"), "formato_manifesto"),
        (_mutacao(["campo_novo"], 1), "desconhecidos ['campo_novo']"),
        (_mutacao(["k6"], _REMOVER), "faltam ['k6']"),
        (_mutacao(["decisoes", 0, "extra"], 1), "desconhecidos ['extra']"),
        (_mutacao(["decisoes", 0, "regras", 0, "nova"], 1), "desconhecidos ['nova']"),
        (_mutacao(["decisoes", 0, "regras", 0, "sha256_yaml"], _REMOVER), "faltam ['sha256_yaml']"),
        (_mutacao(["decisoes", 0, "saidas", 0, "novo"], 1), "desconhecidos ['novo']"),
        (_mutacao(["vocabulario", "sha256"], _HEX_CRU), "vocabulario.sha256"),
        (_mutacao(["decisoes", 0, "sha256"], _HEX_CRU), "decisoes[0].sha256"),
        (_mutacao(["decisoes", 0, "regras", 0, "sha256_yaml"], _HEX_CRU), "sha256_yaml"),
        (_mutacao(["decisoes", 0, "regras", 0, "versaoRegra"], "v1"), "versaoRegra"),
        (_mutacao(["decisoes", 0, "regras", 0, "idRegra"], "regra 1"), "idRegra"),
        (_mutacao(["decisoes", 0, "id"], "sint_a"), "'rco_' + id_original"),
        (_mutacao(["prefixo_decisao"], "xyz_"), "prefixo_decisao"),
        (_mutacao(["decisoes", 0, "arquivo"], "dmn/sub/sint_a.dmn"), "sem subpasta"),
        (_mutacao(["decisoes", 0, "caminho_consumidor"], "spec/processes/dmn/rco/sub/sint_a.dmn"), "plano"),
        (_mutacao(["vocabulario", "path"], "../vocabulario.json"), "plano"),
        (_mutacao(["origem", "commit_sha"], "abc"), "commit_sha"),
        (_mutacao(["decisoes"], []), "lista nao vazia"),
        (_mutacao(["decisoes", 0, "dominio"], "a/b"), "decisoes[0].dominio"),
        (_mutacao(["decisoes", 0, "entradas", 0, "nome"], ""), "entradas[0].nome"),
    ],
)
def test_forma_invalida_reprova(
    pacote: tuple[Path, dict[str, Any]], mutar: Callable[[dict[str, Any]], None], trecho: str
) -> None:
    raiz, manifesto = pacote
    alterado = copy.deepcopy(manifesto)
    mutar(alterado)
    with pytest.raises(k1.ManifestoK1InvalidoError) as excinfo:
        k1.ler_manifesto(sint.canonico(alterado))
    assert trecho in str(excinfo.value)


def test_entrada_repetida_na_decisao_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    _, manifesto = pacote
    entradas = manifesto["decisoes"][0]["entradas"]
    entradas.append(copy.deepcopy(entradas[0]))
    with pytest.raises(k1.ManifestoK1InvalidoError, match=r"entradas\[1\]: nome 'tipoSintetico' repetido"):
        k1.ler_manifesto(sint.canonico(manifesto))


def test_decisao_repetida_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    _, manifesto = pacote
    manifesto["decisoes"].append(copy.deepcopy(manifesto["decisoes"][0]))
    with pytest.raises(k1.ManifestoK1InvalidoError, match="repetido"):
        k1.ler_manifesto(sint.canonico(manifesto))


@pytest.mark.parametrize(
    "raw",
    [
        b"{nao e json",
        b'{"a":1,"a":2}',
        "﻿".encode() + b"{}",
    ],
)
def test_json_ilegivel_ou_chave_repetida_reprova(raw: bytes) -> None:
    with pytest.raises(k1.ManifestoK1InvalidoError):
        k1.ler_manifesto(raw)


def test_bytes_nao_canonicos_reprovam(pacote: tuple[Path, dict[str, Any]]) -> None:
    _, manifesto = pacote
    with pytest.raises(k1.ManifestoK1InvalidoError, match="canonicos"):
        k1.ler_manifesto(sint.canonico(manifesto) + b"\n")


@pytest.mark.parametrize("anexo", ["vocabulario.json", "entradas.json", "k6-filas.json"])
def test_anexo_adulterado_ou_ausente_reprova(pacote: tuple[Path, dict[str, Any]], anexo: str) -> None:
    raiz, _ = pacote
    caminho = raiz / sint.K1_DIR / anexo
    caminho.write_bytes(caminho.read_bytes() + b" ")
    assert any(anexo in p and "sha256" in p for p in _verificar(raiz))
    caminho.unlink()
    assert any(anexo in p and "ausente" in p for p in _verificar(raiz))


def test_dmn_adulterado_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote
    alvo = raiz / sint.RULES_DIR / "sint_a.dmn"
    alvo.write_bytes(alvo.read_bytes().replace(b"liberar", b"informar"))
    assert any("sint_a.dmn: sha256 diverge" in p for p in _verificar(raiz))


def _trocar_dmn(raiz: Path, manifesto: dict[str, Any], indice: int, raw: bytes) -> None:
    decisao = manifesto["decisoes"][indice]
    (raiz / decisao["caminho_consumidor"]).write_bytes(raw)
    decisao["sha256"] = sint.sha(raw)
    sint.regravar(raiz, manifesto)


def test_saida_extra_listada_no_saidas_passa_e_nao_listada_reprova(
    pacote: tuple[Path, dict[str, Any]],
) -> None:
    raiz, manifesto = pacote
    assert _verificar(raiz) == []  # rco_sint_b ja tem a extra `acaoSintetica`, listada
    linhas = [
        ['"liberar"', '""', '"SINT-A-0001"', '""', '""', '"x"'],
        ['"informar"', '""', '"SINT-A-0002"', '"cadastro"', '""', '"y"'],
    ]
    _trocar_dmn(
        raiz, manifesto, 0, sint.dmn_bytes("rco_sint_a", [*sint.SAIDAS_OBRIGATORIAS, "colunaNova"], linhas)
    )
    assert any("['colunaNova'] fora do saidas[]" in p for p in _verificar(raiz))


def test_saida_obrigatoria_ausente_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    saidas = [s for s in sint.SAIDAS_OBRIGATORIAS if s != "motivoTab38"]
    linhas = [['"liberar"', '"SINT-A-0001"', '""', '""'], ['"informar"', '"SINT-A-0002"', '"cadastro"', '""']]
    manifesto["decisoes"][0]["saidas"] = [
        s for s in manifesto["decisoes"][0]["saidas"] if s["nome"] != "motivoTab38"
    ]
    _trocar_dmn(raiz, manifesto, 0, sint.dmn_bytes("rco_sint_a", saidas, linhas))
    assert any("faltam as saidas obrigatorias ['motivoTab38']" in p for p in _verificar(raiz))


def test_saidas_do_manifesto_que_a_tabela_nao_tem_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    manifesto["decisoes"][0]["saidas"].append(
        {"nome": "fantasma", "nome_snake": "fantasma", "typeRef": "string"}
    )
    sint.regravar(raiz, manifesto)
    assert any("['fantasma'], ausentes da tabela" in p for p in _verificar(raiz))


def test_id_da_decisao_no_dmn_diferente_do_manifesto_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    linhas = [
        ['"liberar"', '""', '"SINT-A-0001"', '""', '""'],
        ['"informar"', '""', '"SINT-A-0002"', '"cadastro"', '""'],
    ]
    _trocar_dmn(raiz, manifesto, 0, sint.dmn_bytes("sint_a", sint.SAIDAS_OBRIGATORIAS, linhas))
    assert any("exatamente a decisao 'rco_sint_a'" in p for p in _verificar(raiz))


# ---------------------------------------------------------------------------------------------
# formato_manifesto 2.0.0 (contrato-k1.md do rco em 75015304, PROG-RCO-8, §3 e §7): cada regra tem
# EXATAMENTE idRegra, versaoRegra, situacao, validacao_status, classe_adverso, ativacao, exige_aditivo
# e sha256_yaml; `vocabulario.json` traz `ativacao` = {valores, parametros}.
# ---------------------------------------------------------------------------------------------

_CAMPOS_REGRA_2 = {
    "idRegra",
    "versaoRegra",
    "situacao",
    "validacao_status",
    "classe_adverso",
    "ativacao",
    "exige_aditivo",
    "sha256_yaml",
}


def test_major2_campos_exatos_da_regra_sao_os_do_contrato() -> None:
    assert k1.CAMPOS_REGRA_POR_MAJOR[2] == _CAMPOS_REGRA_2
    assert {"plena", "conferida", "em_correcao"} == k1.ATIVACOES


def test_major2_le_ativacao_exige_aditivo_e_parametros(tmp_path: Path) -> None:
    """O LEITOR le todo o conjunto fechado; quem recusa o que ainda nao tem significado e
    `ativacao_nao_suportada` (gate e runtime)."""
    raiz = tmp_path
    sint.montar(raiz, ativacoes={"SINT-A-0002": ("conferida", True), "SINT-B-0001": ("em_correcao", False)})
    lido = _ler(raiz)
    assert (lido.formato_manifesto, lido.major) == ("2.0.0", 2)
    regras = lido.decisoes["rco_sint_a"].regras
    assert (regras["SINT-A-0001"].ativacao, regras["SINT-A-0001"].exige_aditivo) == ("plena", False)
    assert (regras["SINT-A-0002"].ativacao, regras["SINT-A-0002"].exige_aditivo) == ("conferida", True)
    vocab = k1.carregar_vocabulario(lido, k1_dir=raiz / sint.K1_DIR)
    assert vocab.ativacoes == k1.ATIVACOES
    assert vocab.parametros_ativacao == k1.ParametrosAtivacao(**sint.PARAMETROS_ATIVACAO)
    assert _verificar(raiz) == []
    assert lido.decisoes["rco_sint_b"].regras["SINT-B-0001"].ativacao == "em_correcao"
    assert len(k1.ativacao_nao_suportada(lido)) == 2


def test_major1_continua_lido_sem_campos_do_major2(tmp_path: Path) -> None:
    sint.montar(tmp_path, major=1)
    lido = _ler(tmp_path)
    regra = lido.decisoes["rco_sint_a"].regras["SINT-A-0001"]
    assert (lido.major, regra.ativacao, regra.exige_aditivo) == (1, None, None)
    vocab = k1.carregar_vocabulario(lido, k1_dir=tmp_path / sint.K1_DIR)
    assert (vocab.ativacoes, vocab.parametros_ativacao) == (None, None)
    assert _verificar(tmp_path) == []


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mutacao(["decisoes", 0, "regras", 0, "ativacao"], "automatica"), "ativacao"),
        (_mutacao(["decisoes", 0, "regras", 0, "ativacao"], "PLENA"), "ativacao"),
        (_mutacao(["decisoes", 0, "regras", 0, "ativacao"], None), "ativacao"),
        (_mutacao(["decisoes", 0, "regras", 0, "exige_aditivo"], "sim"), "exige_aditivo"),
        (_mutacao(["decisoes", 0, "regras", 0, "exige_aditivo"], 0), "exige_aditivo"),
        (_mutacao(["decisoes", 0, "regras", 0, "ativacao"], _REMOVER), "faltam ['ativacao']"),
        (_mutacao(["decisoes", 0, "regras", 0, "exige_aditivo"], _REMOVER), "faltam ['exige_aditivo']"),
        (
            _mutacao(["decisoes", 0, "regras", 0, "ratificavel_automatico"], False),
            "desconhecidos ['ratificavel_automatico']",
        ),
        (_mutacao(["formato_manifesto"], "1.0.0"), "desconhecidos ['ativacao', 'exige_aditivo']"),
    ],
)
def test_major2_regra_fora_do_contrato_reprova(
    pacote: tuple[Path, dict[str, Any]], mutar: Callable[[dict[str, Any]], None], trecho: str
) -> None:
    _, manifesto = pacote
    mutar(manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError) as excinfo:
        k1.ler_manifesto(sint.canonico(manifesto))
    assert trecho in str(excinfo.value)


_PARAMS = dict(sint.PARAMETROS_ATIVACAO)


@pytest.mark.parametrize(
    ("ativacao", "trecho"),
    [
        ({"valores": ["plena", "conferida"]}, "ativacao.valores"),
        ({"valores": ["plena", "conferida", "em_correcao", "nova"]}, "ativacao.valores"),
        ({"valores": ["plena", "conferida", "em_correcao", "plena"]}, "ativacao.valores"),
        ({"parametros": {k: v for k, v in _PARAMS.items() if k != "limiar_reversao_pct"}}, "faltam"),
        ({"parametros": {**_PARAMS, "parametro_novo": 1}}, "desconhecidos ['parametro_novo']"),
        ({"parametros": {**_PARAMS, "REVISAO_INICIAL_DECISOES": 30}}, "desconhecidos"),
        ({"parametros": {**_PARAMS, "amostra_posterior_pct": "5"}}, "amostra_posterior_pct"),
        ({"parametros": {**_PARAMS, "amostra_posterior_pct": True}}, "amostra_posterior_pct"),
        ({"parametros": {**_PARAMS, "limiar_reversao_pct": -1}}, "limiar_reversao_pct"),
        ({"parametros": {**_PARAMS, "limiar_reversao_pct": None}}, "limiar_reversao_pct"),
        ({"extra": 1}, "desconhecidos ['extra']"),
    ],
)
def test_major2_bloco_ativacao_do_vocabulario_invalido_reprova(
    pacote: tuple[Path, dict[str, Any]], ativacao: dict[str, Any], trecho: str
) -> None:
    raiz, manifesto = pacote
    sint.trocar_vocabulario(raiz, manifesto, sint.vocabulario_bytes(2, **ativacao))
    problemas = _verificar(raiz)
    assert any(trecho in p for p in problemas), problemas
    with pytest.raises(k1.ManifestoK1InvalidoError):
        k1.carregar_vocabulario(_ler(raiz), k1_dir=raiz / sint.K1_DIR)


def test_major2_vocabulario_sem_bloco_ativacao_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    sint.trocar_vocabulario(raiz, manifesto, sint.vocabulario_bytes(1))
    assert any("exige o bloco 'ativacao'" in p for p in _verificar(raiz))


# ---------------------------------------------------------------------------------------------
# Revisao de 2403c8ed: idRegra emitidos x regras[], linha adversa, JSON/bytes estritos, pastas
# ---------------------------------------------------------------------------------------------


def test_regras_do_manifesto_tem_de_ser_os_idregra_emitidos(pacote: tuple[Path, dict[str, Any]]) -> None:
    """Contrato §3: uma entrada de regras[] por idRegra emitido — nem a menos, nem a mais."""
    raiz, manifesto = pacote
    manifesto["decisoes"][0]["regras"].pop()  # SINT-A-0002 continua emitido pela tabela
    sint.regravar(raiz, manifesto)
    assert any("so na tabela ['SINT-A-0002']" in p for p in _verificar(raiz))

    sint.montar(raiz)
    manifesto = json.loads((raiz / sint.K1_DIR / "manifesto.json").read_bytes())
    extra = copy.deepcopy(manifesto["decisoes"][1]["regras"][0])
    extra["idRegra"] = "SINT-B-9999"
    manifesto["decisoes"][1]["regras"].append(extra)
    sint.regravar(raiz, manifesto)
    assert any("so no manifesto ['SINT-B-9999']" in p for p in _verificar(raiz))


def test_idregra_vazio_em_linha_nao_adversa_passa(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote  # rco_sint_b linha 1: liberar com idRegra "" (vazio canonico)
    assert _verificar(raiz) == []


@pytest.mark.parametrize(("coluna", "indice"), [("idRegra", 2), ("motivoTab38", 1), ("classeAdverso", 4)])
def test_linha_adversa_sem_campo_exigido_reprova(
    pacote: tuple[Path, dict[str, Any]], coluna: str, indice: int
) -> None:
    raiz, manifesto = pacote
    adversa = ['"negar"', '"3171"', '"SINT-A-0002"', '"cadastro"', '"administrativo"']
    adversa[indice] = '""'
    linhas = [['"liberar"', '""', '"SINT-A-0001"', '""', '""'], adversa]
    if coluna == "idRegra":  # sem o idRegra a linha tambem deixa de emitir SINT-A-0002
        manifesto["decisoes"][0]["regras"].pop()
    _trocar_dmn(raiz, manifesto, 0, sint.dmn_bytes("rco_sint_a", sint.SAIDAS_OBRIGATORIAS, linhas))
    problemas = _verificar(raiz)
    assert any("adversa (negar)" in p and coluna in p for p in problemas), problemas


def test_vocabulario_sem_combinacao_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    vocab = json.loads(sint.vocabulario_bytes(2))
    del vocab["combinacao"]
    sint.trocar_vocabulario(raiz, manifesto, sint.canonico(vocab))
    assert any("combinacao.adversas" in p for p in _verificar(raiz))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b'{"origem":"\\ud800"}',  # surrogate solitario: sem bytes canonicos UTF-8
    ],
)
def test_constante_nao_padrao_e_surrogate_reprovam_sem_traceback(raw: bytes) -> None:
    with pytest.raises(k1.ManifestoK1InvalidoError):
        k1.ler_manifesto(raw)


def test_surrogate_e_reportado_como_bytes_nao_canonicos() -> None:
    with pytest.raises(k1.ManifestoK1InvalidoError, match="canonicos"):
        k1.ler_manifesto(b'{"origem":"\\ud800"}')


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mutacao(["exemplos_sinteticos", 0, "path"], 7), "exemplos_sinteticos[0].path"),
        (_mutacao(["versao_pacote"], "١.0.0"), "versao_pacote"),  # digito arabe-indico
        (_mutacao(["decisoes", 0, "regras", 0, "versaoRegra"], "1.٢.0"), "versaoRegra"),
    ],
)
def test_nits_de_forma_reprovam(
    pacote: tuple[Path, dict[str, Any]], mutar: Callable[[dict[str, Any]], None], trecho: str
) -> None:
    _, manifesto = pacote
    mutar(manifesto)
    with pytest.raises(k1.ManifestoK1InvalidoError) as excinfo:
        k1.ler_manifesto(sint.canonico(manifesto))
    assert trecho in str(excinfo.value)


def test_ativacao_nao_suportada_so_aceita_plena_sem_aditivo(tmp_path: Path) -> None:
    sint.montar(tmp_path)
    assert k1.ativacao_nao_suportada(_ler(tmp_path)) == []
    sint.montar(tmp_path, ativacoes={"SINT-A-0001": ("plena", True)})
    (problema,) = k1.ativacao_nao_suportada(_ler(tmp_path))
    assert "SINT-A-0001" in problema and "Onda 2 item 5" in problema
    sint.montar(tmp_path, major=1)
    assert k1.ativacao_nao_suportada(_ler(tmp_path)) == []  # MAJOR 1 nao tem ativacao


def test_leitor_nao_importa_o_pacote_maezo() -> None:
    """O gate carrega este arquivo pelo caminho: nenhum import de `maezo` (so biblioteca padrao)."""
    fonte = Path(k1.__file__).read_text(encoding="utf-8")
    assert "import maezo" not in fonte and "from maezo" not in fonte


# ---------------------------------------------------------------------------------------------
# Re-revisao de e70d9b79: ativacao nao hasheavel e `decisao` nao literal
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ativacao", [[], {}, ["plena"], {"plena": True}])
def test_ativacao_nao_texto_reprova_sem_type_error(
    pacote: tuple[Path, dict[str, Any]], ativacao: object
) -> None:
    _, manifesto = pacote
    manifesto["decisoes"][0]["regras"][0]["ativacao"] = ativacao
    with pytest.raises(k1.ManifestoK1InvalidoError, match="ativacao"):
        k1.ler_manifesto(sint.canonico(manifesto))


def test_decisao_nao_literal_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    linhas = [
        ['"liberar"', '""', '"SINT-A-0001"', '""', '""'],
        ['if x then "negar" else "liberar"', '"3171"', '"SINT-A-0002"', '"cadastro"', '"administrativo"'],
    ]
    _trocar_dmn(raiz, manifesto, 0, sint.dmn_bytes("rco_sint_a", sint.SAIDAS_OBRIGATORIAS, linhas))
    assert any("decisao nao e literal string" in p for p in _verificar(raiz))
