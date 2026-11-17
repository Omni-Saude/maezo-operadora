"""CD-87 — classes FECHADAS de variavel com validador de VALOR (a garantia de "zero dado de saude").

A lista de termos/CID de `contem_dado_de_saude` e' defesa em profundidade; a garantia vem da classe da
variavel + validador de valor, aplicado pelo gate. O registro e os pedidos aqui sao SINTETICOS.
"""

from __future__ import annotations

import copy
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.communications import outbound_gate as og
from maezo.gateway.communications import whatsapp_templates as wt
from maezo.gateway.communications.outbound_gate import Motivo, OutboundGate, PedidoDeEnvio
from maezo.gateway.communications.whatsapp_templates import (
    CAMINHO_PADRAO,
    VALIDADORES_DE_CLASSE,
    RegistroInvalidoError,
    contem_dado_de_saude,
    ler_registro,
    validador_produto_catalogo,
    validar_registro,
    valor_valido_para_classe,
)
from maezo.platform.lembretes.texto import compor_lembrete, compor_teste
from tests.unit.esf13_apoio import (
    FINALIDADE_LEAD,
    SupressaoFixa,
    aceite_os05_sintetico,
    documento_ratificado,
)

_RAIZ = Path(__file__).resolve().parents[3]


# ----------------------------------------------------------------------------- registro: classe fechada
@pytest.mark.parametrize("classe", ["observacao_livre", "texto", "mensagem_livre"])
def test_registro_com_classe_sem_validador_e_recusado_com_not_closed(classe: str) -> None:
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].append({"id": classe, "tipo": "nao_pessoal"})
    with pytest.raises(RegistroInvalidoError) as exc:
        validar_registro(doc)
    assert any(p.startswith("VARIABLE_CLASS_NOT_CLOSED:") for p in exc.value.problemas)


def test_chave_de_envio_nao_liga_com_classe_de_texto_livre() -> None:
    """Tudo aberto (finalidade ratificada, template aprovado, oposicao executavel, aceite), mas o template usa
    classe de texto livre: a carga recusa, logo nenhum gate e' construido nem autoriza."""
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].append({"id": "observacao_livre", "tipo": "nao_pessoal"})
    for t in doc["templates"]:
        if t["id"] == "recuperacao_teste_v1":
            t["variables"] = [{"pos": 1, "class": "observacao_livre"}]
    with pytest.raises(RegistroInvalidoError) as exc:
        validar_registro(doc)
    problemas = exc.value.problemas
    assert "VARIABLE_CLASS_NOT_CLOSED:variable_classes[4]" in problemas
    assert any(p.startswith("VARIABLE_CLASS_NOT_CLOSED:templates[1].variables[0]") for p in problemas) or any(
        p.startswith("VARIABLE_CLASS_UNKNOWN:templates[1]") for p in problemas
    )


def test_yaml_publicado_carrega_e_continua_fechado() -> None:
    registro = ler_registro(CAMINHO_PADRAO)
    assert set(registro.classes) <= set(VALIDADORES_DE_CLASSE)
    assert {
        "competencia_mes_ano",
        "valor_cobranca_reais",
        "data_vencimento_dia_mes",
        "contagem",
        "data_publicacao_fonte",
        "link_opaco_jornada",
    } == set(registro.classes)
    assert not any(f.ratificada for f in registro.finalidades.values())
    assert not any(t.aprovado for t in registro.templates.values())
    assert registro.textos_de_permissao == ()
    assert registro.oposicao_executavel_para == frozenset()


def test_mapa_de_validadores_e_somente_leitura_e_sem_texto_livre() -> None:
    with pytest.raises(TypeError):
        VALIDADORES_DE_CLASSE["texto"] = lambda _v: True  # type: ignore[index]
    assert set(VALIDADORES_DE_CLASSE) == {
        "competencia_mes_ano",
        "valor_cobranca_reais",
        "data_vencimento_dia_mes",
        "contagem",
        "data_publicacao_fonte",
        "link_opaco_jornada",
        "data",
        "valor_centavos_formatado",
        "protocolo_ref",
        "produto_catalogo",
        "primeiro_nome",
    }


# ----------------------------------------------------------------------------- validadores por classe
_TABELA: dict[str, tuple[list[str], list[str]]] = {
    "competencia_mes_ano": (
        ["outubro de 2026", "março de 2027"],
        [
            "outubro",
            "outubro 2026",
            "Outubro de 2026",
            " outubro de 2026",
            "mes de 2026",
        ],
    ),
    "valor_cobranca_reais": (
        ["320,25", "0,99", "1234,56"],
        ["320.25", "R$ 320,25", "320", "320,2", "01,00", "1.234,56", "320,25 "],
    ),
    "data_vencimento_dia_mes": (
        ["20/10", "29/02", "01/01"],
        ["20/13", "31/04", "32/01", "2/10", "20/10/2026", "20-10", " 20/10"],
    ),
    "contagem": (["0", "1", "42"], ["", "J45", "-1", "1.0", "01", "1 ", "um", "٣"]),
    "data_publicacao_fonte": (["01/10/2026", "29/02/2028"], ["29/02/2027", "2026-10-01", "01/10/26"]),
    "link_opaco_jornada": (
        ["https://app.exemplo.com.br/j/abc123", "ref_opaca-0001", "https://x.io/a?t=ab12"],
        [
            "abc",
            "https://x.io/ com espaco",
            "http://x.io/a",
            "retome sua jornada",
            "https://",
            "a b c d e f g h",
        ],
    ),
    "data": (
        ["01/10/2026", "29/02/2028"],
        ["29/02/2027", "1/10/2026", "01/10/26", "31/11/2026", " 01/10/2026"],
    ),
    "valor_centavos_formatado": (
        ["R$ 1.234,56", "R$ 0,99", "R$ 320,25"],
        ["1.234,56", "R$ 1234,56", "R$ 1.234,5", "R$ 1.23,56", "R$1.234,56", "R$ 1.234,56 "],
    ),
    "protocolo_ref": (
        ["AB12-CD34", "2026-10-0001", "abcd"],
        ["abc", "J 45", "ab_12", "protocolo é", "a" * 65, "AB12-CD34\n"],
    ),
    "produto_catalogo": ([], ["Plano Austa", "", "qualquer coisa"]),
    "primeiro_nome": (
        ["Maria", "José", "Ângela", "Li"],
        ["M", "gestante hipertensa", "Maria1", "Ana-Clara", "Maria ", "a" * 31, "J45", "Maria_"],
    ),
}


@pytest.mark.parametrize("classe", sorted(_TABELA))
def test_validador_por_classe_aceita_so_o_formato_fechado(classe: str) -> None:
    validos, invalidos = _TABELA[classe]
    for v in validos:
        assert valor_valido_para_classe(classe, v), (classe, v)
    for v in invalidos:
        assert not valor_valido_para_classe(classe, v), (classe, v)


@pytest.mark.parametrize("classe", sorted(VALIDADORES_DE_CLASSE))
def test_validador_exige_str_exata_e_classe_sem_validador_nunca_valida(classe: str) -> None:
    class Sub(str):
        pass

    assert not valor_valido_para_classe(classe, Sub("12"))
    assert not valor_valido_para_classe(classe, 12)
    assert not valor_valido_para_classe(classe, None)
    assert not valor_valido_para_classe("observacao_livre", "qualquer")


def test_produto_catalogo_so_vale_com_catalogo_injetado() -> None:
    assert not VALIDADORES_DE_CLASSE["produto_catalogo"]("Plano Austa")
    v = validador_produto_catalogo({"Plano Austa"})
    assert v("Plano Austa") and not v("Plano Austa ") and not v("Outro")


def test_valores_do_produtor_de_lembretes_passam_nas_classes_do_template() -> None:
    """Paridade: o que `platform/lembretes/texto.py` gera para `lembrete_mensalidade_v1` passa."""
    classes = ("competencia_mes_ano", "valor_cobranca_reais", "data_vencimento_dia_mes")
    for comp in ("2026-01", "2026-03", "2026-10", "2026-12"):
        for total in ("0.99", "320.25", "1234.56", "99999.00"):
            for venc in (date(2026, 1, 1), date(2026, 2, 28), date(2026, 10, 20), date(2028, 2, 29)):
                msg = compor_lembrete(competencia=comp, valor_total=total, vencimento=venc)
                for classe, valor in zip(classes, msg.parametros_template, strict=True):
                    assert valor_valido_para_classe(classe, valor), (classe, valor)
                assert not any(contem_dado_de_saude(v) for v in msg.parametros_template)


def test_valores_reais_do_lembrete_passam_e_fora_do_formato_recusa() -> None:
    """CD-91 item 3: os valores REAIS que o produtor de lembretes de cobranca gera hoje (mes por extenso,
    valor, data) passam nos validadores das classes do template; fora do formato, recusa."""
    msg = compor_lembrete(competencia="2026-10", valor_total="320.25", vencimento=date(2026, 10, 16))
    assert msg.parametros_template == ("outubro de 2026", "320,25", "16/10")
    classes = ("competencia_mes_ano", "valor_cobranca_reais", "data_vencimento_dia_mes")
    assert all(valor_valido_para_classe(c, v) for c, v in zip(classes, msg.parametros_template, strict=True))
    fora = {
        "competencia_mes_ano": ("2026-10", "Outubro de 2026", "outubro 2026", "asma de 2026"),
        "valor_cobranca_reais": ("320.25", "R$ 320,25", "320", "-320,25"),
        "data_vencimento_dia_mes": ("2026-10-16", "16/10/2026", "32/10", "16 de outubro"),
    }
    for classe, valores in fora.items():
        for valor in valores:
            assert not valor_valido_para_classe(classe, valor), (classe, valor)


def test_marca_de_teste_do_produtor_passa_na_classe_fechada() -> None:
    """`compor_teste` poe o literal `[TESTE] ` na variavel 1; a classe aceita SO' esse prefixo fixo."""
    msg = compor_teste(competencia="2026-10", valor_total="320.25", vencimento=date(2026, 10, 20))
    classes = ("competencia_mes_ano", "valor_cobranca_reais", "data_vencimento_dia_mes")
    assert all(valor_valido_para_classe(c, v) for c, v in zip(classes, msg.parametros_template, strict=True))
    assert not valor_valido_para_classe("competencia_mes_ano", "[TESTE] [TESTE] outubro de 2026")
    assert not valor_valido_para_classe("competencia_mes_ano", "[teste] outubro de 2026")


# ----------------------------------------------------------------------------- gate: a recusa vem da classe
_CLASSES_SINTETICAS = [
    {"id": "primeiro_nome", "tipo": "pessoal_contratual"},
    {"id": "protocolo_ref", "tipo": "nao_pessoal"},
    {"id": "data", "tipo": "nao_pessoal"},
]


def _gate_sintetico() -> OutboundGate:
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].extend(_CLASSES_SINTETICAS)
    doc["templates"].append(
        {
            "id": "classes_teste_v1",
            "name": "classes_teste_v1",
            "language": "pt_BR",
            "category": "utility",
            "meta_status": "approved",
            "meta_template_ref": "TESTE-META-9",
            "purpose_ref": FINALIDADE_LEAD,
            "subject_kind": "lead",
            "content_review_ref": "TESTE-REV-9",
            "publicity": False,
            "body": "Ola {{1}}, protocolo {{2}}, {{3}} itens, desde {{4}}.",
            "variables": [
                {"pos": 1, "class": "primeiro_nome"},
                {"pos": 2, "class": "protocolo_ref"},
                {"pos": 3, "class": "contagem"},
                {"pos": 4, "class": "data"},
            ],
        }
    )
    return OutboundGate(validar_registro(doc), supressao=SupressaoFixa())


_VALIDOS = ("Maria", "AB12-CD34", "3", "01/10/2026")


def _pedido(variaveis: tuple[str, ...]) -> PedidoDeEnvio:
    return PedidoDeEnvio(
        template_id="classes_teste_v1",
        finalidade=FINALIDADE_LEAD,
        tipo_titular="lead",
        titular_ref="prospect-ref-0001",
        canal_ref="contact-ref-0001",
        variaveis=variaveis,
        permissao=aceite_os05_sintetico(),
        chave_idempotencia="chave-cd87",
    )


async def test_gate_sintetico_autoriza_valores_dentro_das_classes() -> None:
    assert (await _gate_sintetico().avaliar(_pedido(_VALIDOS))).permitido


@pytest.mark.parametrize(
    ("pos", "valor"),
    [
        (0, "gestante hipertensa"),  # primeiro_nome: duas palavras
        (1, "J 45"),  # protocolo_ref: espaco
        (2, "J45"),  # contagem: nao e' digito
        (3, "losartana 50mg"),  # data: nao e' data
    ],
)
async def test_recusa_vem_da_classe_e_nao_da_lista(
    monkeypatch: pytest.MonkeyPatch, pos: int, valor: str
) -> None:
    """Detector DESLIGADO (termos esvaziados, CID desligado e a funcao do gate substituida): so' o validador
    de valor da classe pode recusar — e recusa."""
    gate = _gate_sintetico()  # o registro carrega com o detector real; so' depois o desligamos
    monkeypatch.setattr(wt, "TERMOS_DE_SAUDE", ())
    monkeypatch.setattr(wt, "_CID10", re.compile(r"(?!x)x"))
    monkeypatch.setattr(og, "contem_dado_de_saude", lambda _s: False)
    assert not contem_dado_de_saude(valor)  # a lista nao recusaria
    variaveis = list(_VALIDOS)
    variaveis[pos] = valor
    d = await gate.avaliar(_pedido(tuple(variaveis)))
    assert d.motivo is Motivo.VARIABLE_VALUE_INVALID and d.autorizacao is None


async def test_catalogo_injetado_e_o_unico_jeito_de_produto_catalogo_validar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].append({"id": "produto_catalogo", "tipo": "nao_pessoal"})
    for t in doc["templates"]:
        if t["id"] == "recuperacao_teste_v1":
            t["variables"] = [{"pos": 1, "class": "produto_catalogo"}]
    registro = validar_registro(doc)
    padrao = OutboundGate(registro, supressao=SupressaoFixa())
    assert (await padrao.avaliar(_pedido_lead("Plano Austa"))).motivo is Motivo.VARIABLE_VALUE_INVALID
    monkeypatch.setattr(
        og,
        "VALIDADORES_DE_CLASSE",
        {**VALIDADORES_DE_CLASSE, "produto_catalogo": validador_produto_catalogo({"Plano Austa"})},
    )
    com_catalogo = OutboundGate(registro, supressao=SupressaoFixa())
    assert (await com_catalogo.avaliar(_pedido_lead("Plano Austa"))).permitido


def _pedido_lead(valor: str) -> PedidoDeEnvio:
    base: dict[str, Any] = {
        "template_id": "recuperacao_teste_v1",
        "finalidade": FINALIDADE_LEAD,
        "tipo_titular": "lead",
        "titular_ref": "prospect-ref-0001",
        "canal_ref": "contact-ref-0001",
        "variaveis": (valor,),
        "permissao": aceite_os05_sintetico(),
        "chave_idempotencia": "chave-cd87b",
    }
    return PedidoDeEnvio(**base)


# ----------------------------------------------------------------------------- detector endurecido
@pytest.mark.parametrize(
    "texto",
    [
        "J 45",
        "j-45",
        "J.45",
        "J45.0",
        "cid  j45",
        "J_45",
        "J/45",
        "j 45 9",
        "J  45",
        "sua J45 esta",
        "K 35.8",
    ],
)
def test_detector_endurecido_recusa_cid_com_separador_e_espacos(texto: str) -> None:
    assert contem_dado_de_saude(texto)


@pytest.mark.parametrize(
    "texto",
    ["gestacao", "gravidez", "cancer", "neoplasia", "hipertensao", "diabetes", "medicamento", "remedio"],
)
def test_detector_recusa_termos_acrescentados(texto: str) -> None:
    assert contem_dado_de_saude(f"sobre {texto} hoje")


def test_detector_nao_perdeu_nenhum_termo_da_main() -> None:
    saida = subprocess.run(
        ["git", "show", "HEAD:src/maezo/gateway/communications/whatsapp_templates.py"],
        cwd=_RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    if saida.returncode != 0:
        pytest.skip("HEAD indisponivel")
    bloco = saida.stdout.split("TERMOS_DE_SAUDE: Final[tuple[str, ...]] = (")[1].split("\n)\n")[0]
    antigos = re.findall(r'"([^"]+)"', bloco)
    assert len(antigos) >= 18
    assert set(antigos) <= set(wt.TERMOS_DE_SAUDE)
    assert all(contem_dado_de_saude(f"x {t} y") for t in antigos)


def test_detector_continua_deixando_passar_o_texto_do_lembrete() -> None:
    from maezo.platform.lembretes.texto import CORPO_TEMPLATE_META

    assert not contem_dado_de_saude(CORPO_TEMPLATE_META)
