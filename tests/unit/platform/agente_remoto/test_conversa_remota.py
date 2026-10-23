"""O fio do agente de entrada remoto (ADR-0067 fase 4b): codec em ida-e-volta exata e assinatura HMAC."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from maezo.contratos import conversa_remota as fio

CHAVE = fio.derivar_chave("chave-phi-de-teste")


@pytest.mark.parametrize(
    "valor",
    [
        ("a", 1, None),
        (),
        {"x", "y"},
        frozenset({1, 2}),
        set(),
        datetime(2026, 10, 23, 12, 30, 1, 123456, tzinfo=UTC),
        datetime(2026, 10, 23, 9, 0, tzinfo=timezone(timedelta(hours=-3))),
        datetime(2026, 10, 23, 9, 0),
        date(2026, 10, 23),
        b"\x00\xffbytes",
        {1: "um", (2, 3): "par"},
        {"__maezo__": "colide", "outra": 1},
        [("a",), [("b", {"c"})]],
        {
            "aninhado": {
                "tupla": ("x", ("y",)),
                "lista": ["x", ["y"]],
                "quando": datetime(2026, 1, 1, tzinfo=UTC),
            }
        },
        {"texto": "acentuação é çã", "n": 1.5, "b": True, "nada": None},
    ],
)
def test_codec_ida_e_volta_exata(valor: Any) -> None:
    volta = fio.decodificar(fio.codificar(valor))
    assert volta == valor
    assert type(volta) is type(valor)


def test_codec_distingue_tupla_de_lista_e_set_de_frozenset() -> None:
    volta = fio.decodificar(fio.codificar({"t": ("a",), "l": ["a"], "s": {"a"}, "f": frozenset({"a"})}))
    assert isinstance(volta["t"], tuple)
    assert isinstance(volta["l"], list)
    assert type(volta["s"]) is set
    assert type(volta["f"]) is frozenset


def test_codec_preserva_o_fuso_do_datetime() -> None:
    quando = datetime(2026, 10, 23, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    volta = fio.decodificar(fio.codificar(quando))
    assert volta.utcoffset() == timedelta(hours=-3)
    naive = fio.decodificar(fio.codificar(datetime(2026, 10, 23, 9, 0)))
    assert naive.tzinfo is None


def test_codec_recusa_tipo_desconhecido_em_vez_de_virar_texto() -> None:
    class Qualquer:
        pass

    with pytest.raises(TypeError):
        fio.codificar({"x": Qualquer()})
    with pytest.raises(ValueError):
        fio.codificar(float("nan"))


def test_codec_recusa_etiqueta_desconhecida() -> None:
    with pytest.raises(ValueError):
        fio.decodificar(b'{"__maezo__": "inventada", "v": 1}')


def test_chave_derivada_separa_o_uso_da_phi_hmac_key() -> None:
    esperado = hmac.new(b"chave-phi-de-teste", b"maezo-agente-remoto-v1", hashlib.sha256).digest()
    assert esperado == CHAVE
    assert CHAVE != b"chave-phi-de-teste"
    with pytest.raises(ValueError):
        fio.derivar_chave("")


def test_assinatura_valida() -> None:
    corpo = fio.codificar({"a": 1})
    assert fio.assinatura_valida(CHAVE, corpo, fio.assinar(CHAVE, corpo))


def test_assinatura_de_corpo_adulterado_e_recusada() -> None:
    corpo = fio.codificar({"a": 1})
    assinatura = fio.assinar(CHAVE, corpo)
    assert not fio.assinatura_valida(CHAVE, fio.codificar({"a": 2}), assinatura)
    assert not fio.assinatura_valida(fio.derivar_chave("outra"), corpo, assinatura)
    adulterada = ("0" if assinatura[0] != "0" else "1") + assinatura[1:]
    assert not fio.assinatura_valida(CHAVE, corpo, adulterada)


@pytest.mark.parametrize("assinatura", [None, "", "nao-hex", "ção"])
def test_assinatura_ausente_ou_malformada_e_recusada(assinatura: str | None) -> None:
    assert not fio.assinatura_valida(CHAVE, fio.codificar({"a": 1}), assinatura)


def test_estado_adiado_so_dos_tipos_conhecidos() -> None:
    assert fio.estado_adiado(fio.ADIADO_INICIAL, {"x": 1}) == {
        fio.ESTADO_ADIADO: "inicial",
        "campos": {"x": 1},
    }
    with pytest.raises(ValueError):
        fio.estado_adiado("outro")
