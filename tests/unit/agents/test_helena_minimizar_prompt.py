"""DL-0192: a mascara da rota global (`minimizacao.minimizar_prompt`, reexportada em `modelo_allowlist`).

Todo valor e' SINTETICO e gerado aqui (prefixo `FICT`/`Ficti`); nenhum CPF valido, nenhum dado real. Para
cada classe: o SENTINELA posto no texto nao sobrevive a mascara, e o texto de saude ao redor fica.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from maezo.agents.helena import modelo_allowlist as ma
from maezo.agents.helena.consultas_plano import NomesDoTurno
from maezo.agents.helena.habilidades.saude_original import fluxo
from maezo.agents.helena.minimizacao import SOBRENOMES_COMUNS
from maezo.runtime.inference.experimento_global import (
    MODELO_HAIKU_GLOBAL,
    ROTA_DE_RESIDENCIA,
    RespostaDaRotaGlobal,
)
from maezo.runtime.inference.rota_global import RotaGlobal

_REF = "psr-ficticio-0001"
_CONVERSA = "wa:amh:hk1_" + "c" * 64


def _nome(n: int) -> tuple[str, str]:
    """Um primeiro nome e um sobrenome sinteticos (nao sao nomes de pessoa)."""
    return f"Fictinome{chr(97 + n)}", f"Exemplonio{chr(97 + n)}"


def _email(n: int) -> str:
    return f"fict.pessoa{n}+teste@exemplo-{n}.invalid"


class _Transporte:
    """A rota global falsa: guarda o texto que SAIRIA."""

    rota = ROTA_DE_RESIDENCIA
    modelo = MODELO_HAIKU_GLOBAL

    def __init__(self, resposta: str = '{"ok": true}') -> None:
        self.resposta = resposta
        self.enviados: list[str] = []

    async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
        self.enviados.append(mensagens[0]["content"][0]["text"])
        return RespostaDaRotaGlobal(texto=self.resposta, parada="end_turn", entrada=1, saida=1, latencia_ms=1)


# --- Nome --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "molde",
    [
        "meu nome é {p} {s} e estou com febre desde ontem",
        "Meu nome completo: {p} de {s}. Estou com febre desde ontem",
        "oi, me chamo {p} e estou com febre desde ontem",
        "o nome do meu filho é {p} {s}, ele está com febre desde ontem",
        "MEU NOME É {p} e estou com febre desde ontem",
    ],
)
def test_nome_depois_de_apresentacao_nao_sobrevive(molde: str) -> None:
    primeiro, sobrenome = _nome(0)
    saida = ma.minimizar_prompt(molde.format(p=primeiro, s=sobrenome))
    assert primeiro not in saida and sobrenome not in saida
    assert ma.DADO_REMOVIDO in saida and "febre desde ontem" in saida


def test_apresentacao_mascara_so_o_nome_e_para_no_fim_da_linha() -> None:
    primeiro, _ = _nome(1)
    saida = ma.minimizar_prompt(f"Pessoa: me chamo {primeiro} e tenho dor no peito\nHelena: Entendi.")
    assert saida == f"Pessoa: me chamo {ma.DADO_REMOVIDO} e tenho dor no peito\nHelena: Entendi."


def test_nome_conhecido_sai_em_qualquer_lugar_sem_caixa_e_sem_acento() -> None:
    primeiro, sobrenome = _nome(2)
    conhecido = f"{primeiro} de {sobrenome}"
    texto = f"o {primeiro.upper()} piorou; {sobrenome.lower()} esta com febre. Fui com {primeiro}."
    saida = ma.minimizar_prompt(texto, nomes=[conhecido])
    assert primeiro.lower() not in saida.lower() and sobrenome.lower() not in saida.lower()
    assert "febre" in saida and " de " not in saida  # a particula do nome nao vira mascara


def test_nome_conhecido_casa_com_e_sem_acento() -> None:
    saida = ma.minimizar_prompt("a Fictícia tossiu e a Ficticia tem febre", nomes=["Ficticia Exemplonia"])
    assert "Fict" not in saida and saida.count(ma.DADO_REMOVIDO) == 2


def test_nome_fora_de_apresentacao_e_sem_lista_nao_e_coberto() -> None:
    """Limite documentado: sem a deixa e sem o nome conhecido, o nome passa (nao ha' heuristica ampla)."""
    primeiro, _ = _nome(3)
    texto = f"fui ao pronto-socorro com {primeiro}"
    assert ma.minimizar_prompt(texto) == texto


# --- E-mail ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n", range(3))
def test_email_nao_sobrevive(n: int) -> None:
    email = _email(n)
    saida = ma.minimizar_prompt(f"pode mandar para {email}? estou com tosse")
    assert email not in saida and "@" not in saida and "exemplo" not in saida
    assert saida == f"pode mandar para {ma.DADO_REMOVIDO}? estou com tosse"


# --- Identificador curto -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sentinela",
    [
        "FICT-1A2B-3C4D",  # forma dos protocolos do repo (REE-/CAN-XXXX-XXXX)
        "FICTA12345",  # codigo de letras e digitos (carteirinha alfanumerica)
        "hk1_" + "0a1b2c3d" * 8,  # pseudonimo keyed
        "555012",  # numero solto de 5 a 7 digitos (carteirinha parcial, protocolo curto)
        "4440123",
    ],
)
def test_identificador_curto_nao_sobrevive(sentinela: str) -> None:
    saida = ma.minimizar_prompt(f"meu protocolo e {sentinela} e estou com dor de cabeca")
    assert sentinela not in saida
    assert saida == f"meu protocolo e {ma.DADO_REMOVIDO} e estou com dor de cabeca"


@pytest.mark.parametrize(
    "texto",
    [
        "tomei 500mg de dipirona e 50000 UI de vitamina D",
        "pressao 120/80, febre 38.5, glicemia 250",
        "COVID19 ou H1N1? CID J45.9",
        "classify-v6 ADR-0012 DL-0155",
        "tenho 42 anos e 3 filhos",
    ],
)
def test_saude_dose_e_referencia_do_repo_ficam(texto: str) -> None:
    assert ma.minimizar_prompt(texto) == texto


# --- As classes de antes continuam -------------------------------------------------------------------------
def test_numero_longo_continua_mascarado() -> None:
    # Telefone e documento SINTETICOS (nao validos): 8+ digitos com separador.
    saida = ma.minimizar_prompt("telefone 11 90000 0000, documento 000.000.000-00, data 01/02/2030")
    assert "0000" not in saida and "2030" not in saida and saida.count(ma.DADO_REMOVIDO) == 3


def test_todas_as_classes_juntas() -> None:
    primeiro, sobrenome = _nome(4)
    texto = (
        f"meu nome é {primeiro} {sobrenome}, email {_email(9)}, carteirinha FICTB98765, "
        "telefone 11 90000 0001, protocolo 777001. Estou com falta de ar."
    )
    saida = ma.minimizar_prompt(texto)
    for sentinela in (primeiro, sobrenome, _email(9), "FICTB98765", "0001", "777001"):
        assert sentinela not in saida
    assert saida.endswith("Estou com falta de ar.")


# --- Fiacao: os nomes conhecidos chegam a mascara pelos dois chamadores -----------------------------------
def _grafo_falso(transporte: _Transporte, nomes: list[str]) -> SimpleNamespace:
    memoria = {_REF: (0.0, NomesDoTurno(dict(enumerate(nomes, start=1))), ())}
    return SimpleNamespace(
        _nomes_em_memoria=memoria,
        _rota_global=RotaGlobal(transporte=transporte, conversas=frozenset({_CONVERSA})),
        _llm=object(),
        _roteador_llm=object(),
        _modelo_allowlist=ma.MODO_HAIKU,
        _roteador_conversas=frozenset({_CONVERSA}),
        _roteador_prazo_s=6.0,
        _conversa_autorizada=lambda state: True,
    )


def _estado() -> dict[str, Any]:
    return {"conversation_id": _CONVERSA, "identidade_beneficiario": {"portable_subject_ref": _REF}}


def test_nomes_conhecidos_le_a_memoria_da_pessoa_da_conversa() -> None:
    primeiro, sobrenome = _nome(5)
    grafo = _grafo_falso(_Transporte(), [f"{primeiro} {sobrenome}"])
    assert ma.nomes_conhecidos(grafo, _estado()) == (f"{primeiro} {sobrenome}",)  # type: ignore[arg-type]
    assert ma.nomes_conhecidos(grafo, {"conversation_id": _CONVERSA}) == ()  # type: ignore[arg-type]
    assert ma.nomes_conhecidos(SimpleNamespace(), _estado()) == ()  # type: ignore[arg-type]


async def test_provedor_da_allowlist_mascara_o_nome_conhecido() -> None:
    primeiro, sobrenome = _nome(6)
    transporte = _Transporte()
    grafo = _grafo_falso(transporte, [f"{primeiro} {sobrenome}"])
    provedor = ma.provedor_para(grafo, _estado(), ma.PAPEL_CLASSIFY)  # type: ignore[arg-type]
    assert isinstance(provedor, ma.ProvedorHaikuGlobal)
    await provedor.generate(f"a {primeiro} esta com febre", phi=True)
    assert transporte.enviados == [f"a {ma.DADO_REMOVIDO} esta com febre"]


async def test_leitura_da_porta_unica_mascara_o_nome_conhecido_e_o_email() -> None:
    primeiro, sobrenome = _nome(7)
    transporte = _Transporte()
    grafo = _grafo_falso(transporte, [f"{primeiro} {sobrenome}"])
    chamar = fluxo._chamador(grafo, _estado())  # type: ignore[arg-type]
    await chamar("sistema", f"Pessoa: {sobrenome} tem febre, escreve para {_email(1)}", 50)
    assert transporte.enviados == [f"Pessoa: {ma.DADO_REMOVIDO} tem febre, escreve para {ma.DADO_REMOVIDO}"]


def test_nomes_do_turno_continuam_redigidos_no_repr() -> None:
    nomes = NomesDoTurno({1: "Fictinome Exemplonio"})
    assert "Fictinome" not in repr(nomes) and nomes.para_mascarar() == ("Fictinome Exemplonio",)


# --- Rodada 2 (revisao): nomes ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "nomes_na_saida"),
    [
        ("meu nome é Fict D'Ávila Exemplonio e tenho febre", ("D'Ávila", "Ávila", "Exemplonio", "Fict")),
        ("me chamo O'Fictneil, tenho febre", ("Neil", "O'")),
        ("meu nome é Ana-Fictclara Exemplonio, tenho febre", ("Ana", "clara", "Exemplonio")),
        ("meu nome é\nFictjoao Exemplonio, tenho febre", ("Fictjoao", "Exemplonio")),
        ("sou a Fictcia Exemplonia, tenho febre", ("Fictcia", "Exemplonia")),
        ("aqui é o Fictor Exemplonio, tenho febre", ("Fictor", "Exemplonio")),
        ("meu nome é Dr. Fictulano Exemplonio, tenho febre", ("Fictulano", "Exemplonio")),
        ("consultei a Dra. Fictana Exemplonia e tenho febre", ("Fictana", "Exemplonia")),
        ("o nome da minha mãe é Fictania Exemplonia, tenho febre", ("Fictania", "Exemplonia")),
    ],
)
def test_nomes_com_apostrofo_hifen_quebra_de_linha_deixa_fraca_e_tratamento(
    texto: str, nomes_na_saida: tuple[str, ...]
) -> None:
    saida = ma.minimizar_prompt(texto)
    assert all(n not in saida for n in nomes_na_saida), saida
    assert saida.endswith("tenho febre") and ma.DADO_REMOVIDO in saida


def test_tratamento_fica_e_so_o_nome_sai() -> None:
    assert ma.minimizar_prompt("falei com o Dr. Fictulano") == f"falei com o Dr. {ma.DADO_REMOVIDO}"


@pytest.mark.parametrize(
    "texto",
    [
        "sou a mãe dela e ela tem febre",  # deixa fraca sem maiuscula
        "aqui é o pai, ele tem febre",
        "o nome do remédio é Dipirona",  # "o nome do <X>" so' com parentesco
        "o nome do exame é Hemograma",
    ],
)
def test_deixa_fraca_sem_maiuscula_e_nome_de_coisa_ficam(texto: str) -> None:
    assert ma.minimizar_prompt(texto) == texto


@pytest.mark.parametrize("comum", sorted(SOBRENOMES_COMUNS))
def test_sobrenome_que_e_palavra_comum_so_sai_com_maiuscula(comum: str) -> None:
    primeiro, _ = _nome(8)
    conhecido = f"{primeiro} {comum.capitalize()}"
    minusculo = f"tem 3 {comum} e febre"
    assert ma.minimizar_prompt(minusculo, nomes=[conhecido]) == minusculo
    maiusculo = f"o {comum.capitalize()} tem febre"
    assert ma.minimizar_prompt(maiusculo, nomes=[conhecido]) == f"o {ma.DADO_REMOVIDO} tem febre"


def test_sobrenome_comum_e_exatamente_a_lista_combinada() -> None:
    assert (
        len(SOBRENOMES_COMUNS) == 27
        and {"dias", "leite", "rocha", "moura", "paz", "luz"} <= SOBRENOMES_COMUNS
    )


# --- Rodada 2: saude nao vira identificador ---------------------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    [
        "PA 140x90 mmHg",
        "PA 120x80",
        "pressao 140x90mmHg",
        "dose 2x500mg",
        "CA125 alto e CA19-9 normal",
        "tomo AAS100 e Glifage XR500",
        "25000 unidades de heparina",
        "15000 celulas por campo",
        "15000 células",
        "10000 passos por dia",
        "glicemia 120 mg/dl e 50000 UI de vitamina D",
        "frequencia 110 bpm",
    ],
)
def test_medida_marcador_e_remedio_com_dose_ficam(texto: str) -> None:
    assert ma.minimizar_prompt(texto) == texto


# --- Rodada 2: identificador curto e e-mail ----------------------------------------------------------------
@pytest.mark.parametrize("sentinela", ["ÁB12345", "_AB12345", "nº12345", "FICT-0000-0000", "hk1_" + "f" * 64])
def test_identificador_com_letra_nao_ascii_sublinhado_ou_formato_do_repo(sentinela: str) -> None:
    saida = ma.minimizar_prompt(f"o codigo e {sentinela}, estou com tosse")
    assert sentinela not in saida and saida == f"o codigo e {ma.DADO_REMOVIDO}, estou com tosse"


@pytest.mark.parametrize("email", ["fict@exemplo", "fict.pessoa@localhost", "(fict@exemplo.invalid)"])
def test_email_sem_dominio_de_topo_e_entre_parenteses(email: str) -> None:
    saida = ma.minimizar_prompt(f"escreve para {email} hoje")
    assert "fict" not in saida and "@" not in saida


# --- Rodada 2: tempo linear -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "adversaria",
    [
        "a" * 100_000,
        "111" + "a" * 100_000,
        "a." * 50_000,
        "a-" * 50_000,
        "1." * 50_000,
        "1 " * 50_000,
        "a" * 100_000 + "@",
        "(" * 100_000 + "@x",
        "x@" + "." * 100_000,
        "a@" * 50_000,
        "meu nome é " + "A" * 100_000,
        "Dr. " + "Aa " * 33_000,
        "_" * 100_000 + "1",
    ],
    ids=lambda t: f"{t[:6]!r}x{len(t)}",
)
def test_entrada_adversarial_de_100_mil_caracteres_em_menos_de_1s(adversaria: str) -> None:
    inicio = time.perf_counter()
    ma.minimizar_prompt(adversaria, nomes=["Fictinome Dias"])
    assert time.perf_counter() - inicio < 1.0


@pytest.mark.parametrize("deixa", ["me chamo", "meu nome é", "Meu nome completo:"])
def test_sobrenome_depois_de_nome_conhecido_na_apresentacao_tambem_sai(deixa: str) -> None:
    primeiro, sobrenome = _nome(9)
    saida = ma.minimizar_prompt(
        f"{deixa} {primeiro} {sobrenome}, tenho febre", nomes=[f"{primeiro} Dias Leite"]
    )
    assert primeiro not in saida and sobrenome not in saida
    assert saida == f"{deixa} {ma.DADO_REMOVIDO}, tenho febre"
