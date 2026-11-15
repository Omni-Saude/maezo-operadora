"""HEL-2: a resolucao de procedimento da habilidade `cobertura` (DL-0167). Pura, sem rede e sem modelo.

O catalogo aqui e' SINTETICO e pequeno: os codigos `99xxxxxx` sao FICTICIOS (nao existem na TUSS) e as
descricoes so' imitam a forma do catalogo da rota `/procedimentos`. Nenhum dado pessoal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from maezo.agents.helena.habilidades.cobertura.procedimento import (
    MAX_OPCOES_LISTADAS,
    PERGUNTA_PARTE_DO_CORPO,
    PERGUNTA_QUAL_EXAME,
    Resolucao,
    Sinonimos,
    SinonimosInvalidosError,
    carregar_sinonimos,
    resolver,
    validar_sinonimos,
)
from maezo.ports.tina import ProcedimentoCatalogo


def _p(codigo: str, descricao: str, grupo: str | None = None) -> ProcedimentoCatalogo:
    return ProcedimentoCatalogo(codigo=codigo, descricao=descricao, tuss=None, grupo=grupo)


# Codigos FICTICIOS (prefixo 99), so' para o teste.
RM_JOELHO = _p("99000001", "Ressonância magnética de joelho")
RM_CRANIO = _p("99000002", "Ressonância magnética de crânio")
RM_COLUNA = _p("99000003", "RM - Coluna lombar")
RM_OMBRO = _p("99000004", "Ressonância magnética de ombro")
ECO = _p("99000010", "Ecodopplercardiograma transtorácico")
US_ABDOME = _p("99000011", "Ultrassonografia de abdome total")
RX_TORAX = _p("99000020", "RX - Tórax PA e perfil")
RX_JOELHO = _p("99000021", "RX - Joelho")
EDA = _p("99000030", "Endoscopia digestiva alta")
COLONO = _p("99000031", "Colonoscopia")
ECG = _p("99000040", "Eletrocardiograma convencional")
EEG = _p("99000041", "Eletroencefalograma de rotina")
TOMO_CRANIO = _p("99000050", "Tomografia computadorizada de crânio")
FACECTOMIA = _p("99000060", "Facectomia com implante de lente intraocular")
GLICOSE = _p("99000070", "Glicose")

CATALOGO: tuple[ProcedimentoCatalogo, ...] = (
    RM_JOELHO,
    RM_CRANIO,
    RM_COLUNA,
    RM_OMBRO,
    ECO,
    US_ABDOME,
    RX_TORAX,
    RX_JOELHO,
    EDA,
    COLONO,
    ECG,
    EEG,
    TOMO_CRANIO,
    FACECTOMIA,
    GLICOSE,
)
SO_TRES_RM: tuple[ProcedimentoCatalogo, ...] = (RM_JOELHO, RM_CRANIO, RM_COLUNA, ECG)


@pytest.fixture(scope="module")
def sinonimos() -> Sinonimos:
    return carregar_sinonimos()  # o YAML do repositorio, pelo caminho padrao


def _codigos(r: Resolucao) -> set[str]:
    return {c.codigo for c in r.candidatos}


# --- unico -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("O plano cobre ressonância do joelho?", RM_JOELHO),
        ("RESSONANCIA MAGNÉTICA DE CRÂNIO", RM_CRANIO),
        ("preciso de uma ressonância da coluna", RM_COLUNA),
        ("raio-x do tórax", RX_TORAX),
        ("rx do joelho", RX_JOELHO),
        ("chapa do peito, preciso fazer o exame", RX_TORAX),
        ("endoscopia", EDA),
        ("colonoscopia", COLONO),
        ("vou fazer uma colono", COLONO),
        ("ecg", ECG),
        ("eletro do coração", ECG),
        ("eeg", EEG),
        ("eco do coração", ECO),
        ("ultrassom", US_ABDOME),
        ("quero fazer o exame de tomo da cabeça", TOMO_CRANIO),
        ("cirurgia de catarata", FACECTOMIA),
        ("exame de glicose", GLICOSE),
    ],
)
def test_unico(sinonimos: Sinonimos, texto: str, esperado: ProcedimentoCatalogo) -> None:
    r = resolver(texto, catalogo=CATALOGO, sinonimos=sinonimos)
    assert r == Resolucao(desfecho="unico", candidatos=(esperado,))


def test_endoscopia_e_colonoscopia_nao_se_confundem(sinonimos: Sinonimos) -> None:
    assert _codigos(resolver("endoscopia", catalogo=CATALOGO, sinonimos=sinonimos)) == {EDA.codigo}
    assert _codigos(resolver("colonoscopia", catalogo=CATALOGO, sinonimos=sinonimos)) == {COLONO.codigo}
    ambos = resolver("endoscopia e colonoscopia", catalogo=CATALOGO, sinonimos=sinonimos)
    assert ambos.desfecho == "esclarecer"
    assert _codigos(ambos) == {EDA.codigo, COLONO.codigo}


# --- esclarecer ------------------------------------------------------------------------------------
def test_ressonancia_sem_parte_do_corpo_com_mais_de_tres_opcoes(sinonimos: Sinonimos) -> None:
    r = resolver("o plano cobre ressonância?", catalogo=CATALOGO, sinonimos=sinonimos)
    assert r.desfecho == "esclarecer"
    assert _codigos(r) == {RM_JOELHO.codigo, RM_CRANIO.codigo, RM_COLUNA.codigo, RM_OMBRO.codigo}
    assert len(r.candidatos) > MAX_OPCOES_LISTADAS
    assert r.pergunta == PERGUNTA_PARTE_DO_CORPO  # sem listar


def test_ressonancia_com_tres_opcoes_lista_as_tres(sinonimos: Sinonimos) -> None:
    r = resolver("ressonancia", catalogo=SO_TRES_RM, sinonimos=sinonimos)
    assert r.desfecho == "esclarecer"
    assert r.pergunta is not None
    for item in (RM_JOELHO, RM_CRANIO, RM_COLUNA):
        assert item.descricao in r.pergunta
    assert r.pergunta.count("?") == 1  # UMA pergunta


def test_eco_ecocardiograma_ou_ultrassom_com_sinonimo_do_k2(sinonimos: Sinonimos) -> None:
    extra = {"eco": ["ecodopplercardiograma", "ultrassonografia"]}
    r = resolver("eco", catalogo=CATALOGO, sinonimos=sinonimos, sinonimos_extra=extra)
    assert r.desfecho == "esclarecer"
    assert _codigos(r) == {ECO.codigo, US_ABDOME.codigo}
    assert r.pergunta is not None and ECO.descricao in r.pergunta and US_ABDOME.descricao in r.pergunta
    # Sem o K2, "eco" sozinho nao e' forma popular de nada: transfere, nunca chuta.
    assert resolver("eco", catalogo=CATALOGO, sinonimos=sinonimos).desfecho == "sem_candidato"


def test_raio_x_torax_ou_joelho(sinonimos: Sinonimos) -> None:
    r = resolver("raio x", catalogo=CATALOGO, sinonimos=sinonimos)
    assert r.desfecho == "esclarecer"
    assert _codigos(r) == {RX_TORAX.codigo, RX_JOELHO.codigo}


def test_eletro_ecg_ou_eletroencefalograma_quando_o_k2_diz_que_e_ambiguo(sinonimos: Sinonimos) -> None:
    # O YAML diz "eletro" = eletrocardiograma; com o K2 marcando o duplo sentido, a Helena pergunta.
    assert resolver("eletro", catalogo=CATALOGO, sinonimos=sinonimos).candidatos == (ECG,)
    extra = {"eletro": ["eletroencefalograma"]}
    r = resolver("eletro", catalogo=CATALOGO, sinonimos=sinonimos, sinonimos_extra=extra)
    assert r.desfecho == "esclarecer"
    assert _codigos(r) == {ECG.codigo, EEG.codigo}
    # "eletro da cabeca": o ECG nao tem o qualificador; sem o K2 nao ha' EEG -> transfere, nunca o ECG.
    assert resolver("eletro da cabeça", catalogo=CATALOGO, sinonimos=sinonimos).desfecho == "sem_candidato"


@pytest.mark.parametrize(
    ("anteriores", "atual", "esperado"),
    [
        (("o plano cobre ressonância?",), "a do joelho", RM_JOELHO),
        (("oi", "ressonância", "tudo bem"), "de crânio", RM_CRANIO),
        (("raio x",), "do tórax", RX_TORAX),
    ],
)
def test_esclarecimento_resolvido_pela_fala_seguinte(
    sinonimos: Sinonimos, anteriores: tuple[str, ...], atual: str, esperado: ProcedimentoCatalogo
) -> None:
    assert resolver(atual, catalogo=CATALOGO, sinonimos=sinonimos).desfecho == "sem_candidato"
    r = resolver(atual, catalogo=CATALOGO, sinonimos=sinonimos, falas_anteriores=anteriores)
    assert r == Resolucao(desfecho="unico", candidatos=(esperado,))


def test_esclarecimento_sem_qualificador_nao_herda_o_termo(sinonimos: Sinonimos) -> None:
    r = resolver("e a carência?", catalogo=CATALOGO, sinonimos=sinonimos, falas_anteriores=("ressonância",))
    assert r.desfecho == "sem_candidato"


def test_esclarecimento_com_qualificador_que_nao_existe(sinonimos: Sinonimos) -> None:
    r = resolver("a do tornozelo", catalogo=CATALOGO, sinonimos=sinonimos, falas_anteriores=("ressonância",))
    assert r.desfecho == "sem_candidato"


# --- sinonimos -------------------------------------------------------------------------------------
def test_sinonimo_do_yaml(sinonimos: Sinonimos) -> None:
    catalogo = (_p("99000080", "Teste ergométrico computadorizado"), ECG)
    r = resolver("teste da esteira", catalogo=catalogo, sinonimos=sinonimos)
    assert r.desfecho == "unico" and r.candidatos[0].codigo == "99000080"


def test_sinonimo_extra_do_k2(sinonimos: Sinonimos) -> None:
    catalogo = (_p("99000090", "Videolaringoscopia"), ECG)
    assert resolver("exame da garganta", catalogo=catalogo, sinonimos=sinonimos).desfecho == "sem_candidato"
    r = resolver(
        "exame da garganta",
        catalogo=catalogo,
        sinonimos=sinonimos,
        sinonimos_extra={"exame da garganta": ["videolaringoscopia"]},
    )
    assert r.desfecho == "unico" and r.candidatos[0].codigo == "99000090"


def test_sinonimo_extra_fora_do_formato_e_ignorado(sinonimos: Sinonimos) -> None:
    catalogo = (_p("99000090", "Videolaringoscopia"),)
    extra: dict[str, object] = {"garganta": "videolaringoscopia", "": ["videolaringoscopia"], "laringe": [3]}
    for texto in ("garganta", "laringe"):
        r = resolver(texto, catalogo=catalogo, sinonimos=sinonimos, sinonimos_extra=extra)  # type: ignore[arg-type]
        assert r.desfecho == "sem_candidato"


# --- codigo TUSS -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "desfecho", "codigos"),
    [
        ("o 99000002 tem cobertura?", "unico", {RM_CRANIO.codigo}),
        ("99000001 ou 99000004", "esclarecer", {RM_JOELHO.codigo, RM_OMBRO.codigo}),
        ("o codigo e' 12345678", "sem_candidato", set()),  # fora do catalogo: nao cai para as palavras
        ("ressonância 12345678", "sem_candidato", set()),
        ("1234567", "sem_candidato", set()),  # 7 digitos nao e' codigo
    ],
)
def test_codigo_tuss(sinonimos: Sinonimos, texto: str, desfecho: str, codigos: set[str]) -> None:
    r = resolver(texto, catalogo=CATALOGO, sinonimos=sinonimos)
    assert r.desfecho == desfecho
    assert _codigos(r) == codigos


# --- sem candidato, nunca por aproximacao ----------------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    [
        "ressonancai do joelho",  # erro de digitacao: nunca "o mais parecido"
        "resonancia",
        "endoscopa",
        "colonoscopio",
        "eletrocardiogram",
        "o plano cobre?",
        "qual a carência do meu plano?",
        "estou com dor de cabeça e febre",  # sintoma: nao vira tomografia/ressonancia de cranio
        "tenho dor no joelho",
        "tomo remédio para pressão, cobre?",  # "tomo" verbo, nao tomografia
        "tenho catarata",  # doenca sem marcador de procedimento
        "tomo remédio pra pressão, preciso de autorização?",  # revisao: "autorizacao" nao e' marcador
        "estou com pedra na vesícula, preciso de autorização?",  # doenca nao vira cirurgia
        "minha glicose está alta",
        "ressonância do tornozelo",  # o qualificador restringe: nunca outra ressonancia
        "",
    ],
)
def test_sem_candidato(sinonimos: Sinonimos, texto: str) -> None:
    assert resolver(texto, catalogo=CATALOGO, sinonimos=sinonimos) == Resolucao(desfecho="sem_candidato")


def test_qualificador_restringe_mesmo_com_um_so_candidato(sinonimos: Sinonimos) -> None:
    so_cranio = (RM_CRANIO,)
    assert resolver("ressonância", catalogo=so_cranio, sinonimos=sinonimos).candidatos == (RM_CRANIO,)
    assert (
        resolver("ressonância do joelho", catalogo=so_cranio, sinonimos=sinonimos).desfecho == "sem_candidato"
    )


# --- empate nao se desempata --------------------------------------------------------------------------
def test_empate_nao_se_desempata_por_ordem_nem_tamanho(sinonimos: Sinonimos) -> None:
    curto = _p("99000100", "RM - Joelho")
    longo = _p("99000101", "Ressonância magnética de joelho com contraste")
    for catalogo in ((curto, longo), (longo, curto)):
        r = resolver("ressonancia magnetica de joelho com contraste", catalogo=catalogo, sinonimos=sinonimos)
        assert r.desfecho == "esclarecer"
        assert _codigos(r) == {curto.codigo, longo.codigo}


def test_codigo_repetido_no_catalogo_conta_uma_vez(sinonimos: Sinonimos) -> None:
    r = resolver("endoscopia", catalogo=(EDA, EDA), sinonimos=sinonimos)
    assert r == Resolucao(desfecho="unico", candidatos=(EDA,))


# --- a pergunta -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "extra"),
    [("ressonancia", {}), ("raio x", {}), ("eco", {"eco": ["ecodopplercardiograma", "ultrassonografia"]})],
)
def test_pergunta_sem_link_sem_promessa_sem_o_texto(
    sinonimos: Sinonimos, texto: str, extra: dict[str, list[str]]
) -> None:
    for catalogo in (CATALOGO, SO_TRES_RM):
        r = resolver(texto, catalogo=catalogo, sinonimos=sinonimos, sinonimos_extra=extra)
        if r.desfecho != "esclarecer":
            continue
        assert r.pergunta is not None
        baixa = r.pergunta.lower()
        for proibido in ("http", "www", "cobre", "cobertura", "consta no rol", " rol"):
            assert proibido not in baixa


def test_descricao_com_link_nao_vai_a_pergunta(sinonimos: Sinonimos) -> None:
    catalogo = (
        _p("99000110", "Colonoscopia (ver www.exemplo.invalid)"),
        _p("99000111", "Colonoscopia com biópsia"),
    )
    r = resolver("colonoscopia", catalogo=catalogo, sinonimos=sinonimos)
    assert r.desfecho == "esclarecer"
    assert r.pergunta == PERGUNTA_QUAL_EXAME


def test_so_esclarecer_tem_pergunta(sinonimos: Sinonimos) -> None:
    assert resolver("endoscopia", catalogo=CATALOGO, sinonimos=sinonimos).pergunta is None
    assert resolver("bom dia", catalogo=CATALOGO, sinonimos=sinonimos).pergunta is None


def test_o_texto_da_pessoa_nao_vai_a_log(sinonimos: Sinonimos) -> None:
    marca = "ressonância MARCA-UNICA-DO-TEXTO"
    with structlog.testing.capture_logs() as eventos:
        resolver(marca, catalogo=CATALOGO, sinonimos=sinonimos, falas_anteriores=(marca,))
    assert "MARCA-UNICA" not in repr(eventos)


# --- os sinonimos: falha fechada -------------------------------------------------------------------
def test_o_yaml_do_repositorio_carrega(sinonimos: Sinonimos) -> None:
    assert sinonimos.versao == 1
    assert sinonimos.entradas
    assert all(e.fonte for e in sinonimos.entradas)


_ENTRADA_OK = {"populares": ["colono"], "oficial": "colonoscopia", "fonte": "TUSS"}


@pytest.mark.parametrize(
    "bruto",
    [
        None,
        [],
        "versao: 1",
        {"sinonimos": [_ENTRADA_OK]},  # sem versao
        {"versao": 2, "sinonimos": [_ENTRADA_OK]},  # versao desconhecida
        {"versao": True, "sinonimos": [_ENTRADA_OK]},
        {"versao": 1, "sinonimos": []},
        {"versao": 1, "sinonimos": [_ENTRADA_OK], "extra": 1},
        {"versao": 1, "sinonimos": [{"populares": ["colono"], "oficial": "colonoscopia"}]},  # sem fonte
        {"versao": 1, "sinonimos": [{**_ENTRADA_OK, "fonte": " "}]},
        {"versao": 1, "sinonimos": [{**_ENTRADA_OK, "populares": []}]},
        {"versao": 1, "sinonimos": [{**_ENTRADA_OK, "populares": "colono"}]},
        {"versao": 1, "sinonimos": [{**_ENTRADA_OK, "populares": ["de"]}]},  # so' palavra de ligacao
        {"versao": 1, "sinonimos": [{**_ENTRADA_OK, "oficial": ""}]},
        {"versao": 1, "sinonimos": [{**_ENTRADA_OK, "marca": "x"}]},
    ],
)
def test_validacao_falha_fechada(bruto: object) -> None:
    with pytest.raises(SinonimosInvalidosError):
        validar_sinonimos(bruto)


def test_arquivo_ausente_ou_ilegivel_falha_fechado(tmp_path: Path) -> None:
    with pytest.raises(SinonimosInvalidosError):
        carregar_sinonimos(tmp_path / "nao_existe.yaml")
    quebrado = tmp_path / "quebrado.yaml"
    quebrado.write_text("versao: [1\n", encoding="utf-8")
    with pytest.raises(SinonimosInvalidosError):
        carregar_sinonimos(quebrado)
    valido = tmp_path / "valido.yaml"
    valido.write_text(
        "versao: 1\nsinonimos:\n  - populares: [colono]\n    oficial: colonoscopia\n    fonte: TUSS\n",
        encoding="utf-8",
    )
    assert carregar_sinonimos(valido).entradas[0].oficial == "colonoscopia"
