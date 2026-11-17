"""Limites do historico de decisao do motor no leitor do K1 (DL-0183).

Cada limite de `LIMITES_HISTORICO_DMN` foi confirmado no CIB Seven 2.1.0 (Postgres, `history=full`) com um DMN
que viola so ele. Aqui: no limite passa, um caractere acima e recusado, com o campo e o dono na mensagem. O
`geral_carencia.dmn` do recorte 2.3.0 (rco main d9b8adfa) e o caso real: label de 278 caracteres que fazia
toda avaliacao falhar no motor.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from maezo.platform import rco_manifesto as k1
from tests.unit.platform import rco_k1_2_3_recorte as rec


def _dmn(o: Mapping[str, str]) -> bytes:
    def attr(nome: str, chave: str, padrao: str) -> str:
        return f' {nome}="{o.get(chave, padrao)}"'

    version_tag = f' camunda:versionTag="{o["versionTag"]}"' if "versionTag" in o else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" '
        'xmlns:camunda="http://camunda.org/schema/1.0/dmn" id="defs" name="n" namespace="ns">'
        f"<decision{attr('id', 'decId', 'rco_d')}{attr('name', 'decName', 'd')}{version_tag}>"
        '<decisionTable id="t" hitPolicy="UNIQUE">'
        f"<input{attr('id', 'inId', 'in1')}{attr('label', 'inLabel', 'a')}>"
        '<inputExpression id="ie" typeRef="string"><text>a</text></inputExpression></input>'
        f"<output{attr('id', 'outId', 'out1')}{attr('label', 'outLabel', 'o')}{attr('name', 'outName', 'o')}"
        ' typeRef="string"/>'
        f"<rule{attr('id', 'ruleId', 'r1')}>"
        '<inputEntry id="e1"><text>-</text></inputEntry>'
        f'<outputEntry id="e2"><text>{o.get("outVal", chr(34) + "ok" + chr(34))}</text></outputEntry>'
        "</rule></decisionTable></decision></definitions>"
    ).encode()


#: (chave no DMN de teste, campo em LIMITES_HISTORICO_DMN, prefixo para manter o valor valido)
_CASOS = [
    ("decId", "decision@id", "rco_"),
    ("decName", "decision@name", ""),
    ("versionTag", "decision@versionTag", ""),
    ("inId", "input@id", "i"),
    ("inLabel", "input@label", ""),
    ("outId", "output@id", "o"),
    ("outLabel", "output@label", ""),
    ("outName", "output@name", "o"),
    ("ruleId", "rule@id", "r"),
]


def test_dmn_minimo_cabe() -> None:
    assert k1.limites_do_historico_no_dmn(_dmn({}), "t.dmn") == []


@pytest.mark.parametrize(("chave", "campo", "prefixo"), _CASOS)
def test_no_limite_passa_um_acima_e_recusado(chave: str, campo: str, prefixo: str) -> None:
    limite = k1.LIMITES_HISTORICO_DMN[campo]
    no_limite = prefixo + "x" * (limite - len(prefixo))
    assert k1.limites_do_historico_no_dmn(_dmn({chave: no_limite}), "t.dmn") == []
    problemas = k1.limites_do_historico_no_dmn(_dmn({chave: no_limite + "x"}), "t.dmn")
    assert len(problemas) == 1
    assert f"{campo} de " in problemas[0]
    assert f"tem {limite + 1} caracteres, acima do limite de {limite}" in problemas[0]


def test_literal_de_saida_conta_o_valor_sem_aspas_e_expressao_nao_conta() -> None:
    limite = k1.LIMITES_HISTORICO_DMN["outputEntry literal"]
    assert k1.limites_do_historico_no_dmn(_dmn({"outVal": f'"{"x" * limite}"'}), "t.dmn") == []
    problemas = k1.limites_do_historico_no_dmn(_dmn({"outVal": f'"{"x" * (limite + 1)}"'}), "t.dmn")
    assert len(problemas) == 1 and "outputEntry literal de rco_d/rule[0]" in problemas[0]
    # Expressao (nao literal) nao tem tamanho estatico de valor: nao e conferida aqui.
    assert k1.limites_do_historico_no_dmn(_dmn({"outVal": "a" * (limite + 1)}), "t.dmn") == []


def test_conta_caracteres_e_nao_bytes() -> None:
    # Postgres `varchar(n)` conta caracteres: 255 caracteres acentuados (mais de 255 bytes) cabem.
    assert k1.limites_do_historico_no_dmn(_dmn({"inLabel": "ã" * 255}), "t.dmn") == []
    assert len(k1.limites_do_historico_no_dmn(_dmn({"inLabel": "ã" * 256}), "t.dmn")) == 1


def test_dmn_ilegivel_e_problema() -> None:
    assert k1.limites_do_historico_no_dmn(b"<nao-xml", "t.dmn") == ["t.dmn: nao e DMN legivel (ParseError)"]


def test_recorte_2_3_0_do_rco_tem_o_label_que_derrubava_o_motor() -> None:
    """O caso real: so o label de `urgenciaAposVinteQuatroHorasCobertura` (278) passa do limite."""
    raw = rec.dmn("rco_geral_carencia").read_bytes()
    carencia = k1.limites_do_historico_no_dmn(raw, "geral_carencia.dmn")
    assert len(carencia) == 1
    assert "input@label de rco_geral_carencia/input[" in carencia[0]
    assert "tem 278 caracteres, acima do limite de 255" in carencia[0]
    assert k1.limites_do_historico_no_dmn(rec.dmn("rco_geral_cpt").read_bytes(), "geral_cpt.dmn") == []
