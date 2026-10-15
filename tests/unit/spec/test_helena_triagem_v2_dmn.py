"""A tabela da NOVA TRIAGEM DA HELENA (`helena_triage_admissibility`, DL-0104) existe, casa com o codigo e
cada regra decide o que o catalogo do dono diz (com as divergencias declaradas).

O QUE ESTE ARQUIVO PROVA (a parte de engenharia):
  1. a chave da decisao e' EXATAMENTE `TRIAGEM_V2_DMN_KEY`, a politica e' FIRST, a versao e' v0.1.0 e o
     arquivo diz em texto que o conteudo clinico e' DRAFT, pendente do medico-auditor;
  2. as ENTRADAS sao as que `HelenaGraph._triagem_v2_decidir` monta (pelo `inputExpression`), as SAIDAS
     sao as quatro esperadas, e o vocabulario das saidas casa com o do codigo NAS DUAS DIRECOES;
  3. `motivo_categoria` so' usa valores do enum do contrato SP-OP-ESCALATION-001 quando abre caso, e "-"
     quando nao abre; toda regra cita o R-ID do catalogo;
  4. o limite de rodadas da tabela e' o do codigo (3);
  5. CADA REGRA, avaliada pelo leitor FIRST-hit do repositorio (`tests/support/dmn_first_hit.py`, a
     mesma gramatica fechada usada pelas tabelas do Lucas), decide o que se espera dela.

NAO PROVA o conteudo clinico (se 3 rodadas bastam, se a ficha deveria abrir caso): isso e' do
medico-auditor e do dono — decisoes D01, D02, D08, D09 do catalogo.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Final

import pytest

from maezo.agents.helena.triagem_v2 import (
    COLETA_MAX_RODADAS_TRIAGEM_V2,
    DECISOES,
    DECISOES_COM_TEXTO,
    DECISOES_QUE_ABREM_CASO,
    IDS_DE_TEXTO,
    MOTIVOS_DA_HELENA,
    NECESSIDADES,
    TRIAGEM_V2_DMN_KEY,
    SaidaInvalidaError,
    ler_linha,
)
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_DMN: Final = DMN_DIR / f"{TRIAGEM_V2_DMN_KEY}.dmn"
_NS: Final[dict[str, str]] = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
#: O enum do contrato SP-OP-ESCALATION-001 (`docs/processes/contracts/SP-OP-ESCALATION-001.md`).
_ENUM_DO_CONTRATO: Final[frozenset[str]] = frozenset(
    {
        "red_flag_clinico",
        "risco_psicossocial",
        "intencao_clinica",
        "solicitacao_humano",
        "falha_tecnica",
        "cobranca",
        "alerta_sla",
        "outro",
    }
)
#: A ordem em que `_triagem_v2_decidir` monta as entradas.
_ENTRADAS: Final[tuple[str, ...]] = (
    "necessidade",
    "sinal_alarme",
    "risco_psicossocial",
    "pedido_pessoa",
    "ficha_completa",
    "rodadas",
    "esclarecimento_pedido",
)
_SAIDAS: Final[tuple[str, ...]] = ("decisao", "motivo_categoria", "severidade", "texto")


def _tabela() -> Any:
    return read_live_table(_DMN)


def _entrada(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "necessidade": "S01",
        "sinal_alarme": False,
        "risco_psicossocial": False,
        "pedido_pessoa": False,
        "ficha_completa": False,
        "rodadas": 0,
        "esclarecimento_pedido": False,
    }
    base.update(campos)
    return base


def test_a_chave_a_politica_e_a_versao() -> None:
    raiz = ET.parse(_DMN).getroot()
    decisao = raiz.find(f".//d:decision[@id='{TRIAGEM_V2_DMN_KEY}']", _NS)
    assert decisao is not None, "a decisao tem de ter o id que o grafo consulta"
    tabela = decisao.find("d:decisionTable", _NS)
    assert tabela is not None and tabela.get("hitPolicy") == "FIRST"
    descricao = " ".join((raiz.findtext("d:description", default="", namespaces=_NS)).split())
    assert "helena_triage_admissibility v0.1.0" in descricao
    assert "CONTEUDO CLINICO: DRAFT — pendente de ratificacao do medico-auditor" in descricao
    assert "MAEZO_HELENA_TRIAGEM_V2" in descricao


def test_entradas_e_saidas_sao_as_do_codigo() -> None:
    tabela = _tabela()
    assert tabela.input_names == _ENTRADAS
    assert tabela.output_names == _SAIDAS


def test_toda_regra_cita_o_catalogo_e_tem_id_proprio() -> None:
    raiz = ET.parse(_DMN).getroot()
    regras = raiz.findall(".//d:rule", _NS)
    assert len(regras) == len(_tabela().rules) == 12
    for regra in regras:
        rid = regra.get("id") or ""
        assert rid.startswith("hta_r"), rid
        descricao = regra.findtext("d:description", default="", namespaces=_NS)
        assert re.search(r"Catalogo R\d{2}", descricao), f"{rid} nao cita o R-ID do catalogo"


def test_o_vocabulario_das_saidas_casa_com_o_codigo_nas_duas_direcoes() -> None:
    tabela = _tabela()
    decisoes = {regra.outputs[0] for regra in tabela.rules}
    textos = {regra.outputs[3] for regra in tabela.rules} - {"-"}
    assert decisoes == DECISOES
    assert textos == IDS_DE_TEXTO


def test_motivo_e_severidade_so_quando_abre_caso_e_do_enum_do_contrato() -> None:
    for regra in _tabela().rules:
        decisao, motivo, severidade, texto = regra.outputs
        if decisao in DECISOES_QUE_ABREM_CASO:
            assert motivo in _ENUM_DO_CONTRATO and motivo in MOTIVOS_DA_HELENA, regra.rule_id
            assert severidade in {"grave", "moderada", "leve"}, regra.rule_id
        else:
            assert (motivo, severidade) == ("-", "-"), regra.rule_id
        if decisao in DECISOES_COM_TEXTO:
            assert texto in IDS_DE_TEXTO, regra.rule_id
        # Toda linha passa pela leitura FECHADA do codigo.
        ler_linha(dict(zip(_SAIDAS, regra.outputs, strict=True)))


def test_as_necessidades_da_tabela_sao_as_do_codigo() -> None:
    tabela = _tabela()
    citadas: set[str] = set()
    for regra in tabela.rules:
        citadas.update(re.findall(r'"([^"]+)"', regra.inputs[0]))
    assert citadas <= NECESSIDADES
    # S06..S10/A05/COBRANCA tem linha propria; S01/S04/S05 dividem as linhas da ficha.
    assert citadas == NECESSIDADES


def test_o_limite_de_rodadas_da_tabela_e_o_do_codigo() -> None:
    regras = {regra.rule_id: regra for regra in _tabela().rules}
    assert regras["hta_r12_perguntar_mais"].inputs[5] == f"< {COLETA_MAX_RODADAS_TRIAGEM_V2}"
    assert regras["hta_r13_ficha_esgotada"].inputs[5] == f">= {COLETA_MAX_RODADAS_TRIAGEM_V2}"


def test_a_ultima_regra_e_o_catch_all_sem_caso() -> None:
    ultima = _tabela().rules[-1]
    assert ultima.rule_id == "hta_r24_catchall"
    assert set(ultima.inputs) == {"-"}
    assert ultima.outputs == ("RESPONDER_SEM_CASO", "-", "-", "OFERTA_PESSOA")


@pytest.mark.parametrize(
    ("entrada", "regra", "saidas"),
    [
        # R02 — risco psicossocial vence tudo (rede de seguranca).
        (
            _entrada(risco_psicossocial=True, sinal_alarme=True, pedido_pessoa=True),
            "hta_r02_risco_psicossocial",
            ("ABRIR_CASO_EMERGENCIA", "risco_psicossocial", "grave", "-"),
        ),
        # R03 — alarme vence pedido de pessoa e ficha.
        (
            _entrada(sinal_alarme=True, pedido_pessoa=True, ficha_completa=True),
            "hta_r03_sinal_alarme",
            ("ABRIR_CASO_EMERGENCIA", "red_flag_clinico", "grave", "-"),
        ),
        # R10 — pedido de pessoa e' sempre atendido, em qualquer necessidade.
        (
            _entrada(necessidade="A05", pedido_pessoa=True, esclarecimento_pedido=True),
            "hta_r10_pedido_pessoa",
            ("ABRIR_CASO", "solicitacao_humano", "leve", "-"),
        ),
        (
            _entrada(necessidade="S06", pedido_pessoa=True),
            "hta_r10_pedido_pessoa",
            ("ABRIR_CASO", "solicitacao_humano", "leve", "-"),
        ),
        # R08 — cobranca (reservada).
        (_entrada(necessidade="COBRANCA"), "hta_r08_cobranca", ("PASSAR_AO_LUCAS", "-", "-", "-")),
        # R12 — sintoma, ficha incompleta, 0..2 rodadas: pergunta mais.
        (_entrada(necessidade="S01", rodadas=0), "hta_r12_perguntar_mais", ("PERGUNTAR_MAIS", "-", "-", "-")),
        (_entrada(necessidade="S04", rodadas=1), "hta_r12_perguntar_mais", ("PERGUNTAR_MAIS", "-", "-", "-")),
        (_entrada(necessidade="S05", rodadas=2), "hta_r12_perguntar_mais", ("PERGUNTAR_MAIS", "-", "-", "-")),
        # R13 — ficha esgotada: caminho + oferta de pessoa, SEM caso (diverge do catalogo).
        (
            _entrada(necessidade="S01", rodadas=3),
            "hta_r13_ficha_esgotada",
            ("ORIENTAR_CAMINHO_SEM_CASO", "-", "-", "T05"),
        ),
        # R14 — ficha completa: caminho com o prazo da ANS, sem caso.
        (
            _entrada(necessidade="S05", ficha_completa=True, rodadas=1),
            "hta_r14_ficha_completa",
            ("ORIENTAR_CAMINHO_SEM_CASO", "-", "-", "T05"),
        ),
        # R15 — pergunta clinica sem sintoma: NAO abre caso.
        (_entrada(necessidade="S06"), "hta_r15_conduta", ("RESPONDER_SEM_CASO", "-", "-", "T08")),
        # R16 — exame / receita.
        (
            _entrada(necessidade="S08"),
            "hta_r16_exame_receita",
            ("ORIENTAR_CAMINHO_SEM_CASO", "-", "-", "T09"),
        ),
        (
            _entrada(necessidade="S09"),
            "hta_r16_exame_receita",
            ("ORIENTAR_CAMINHO_SEM_CASO", "-", "-", "T09"),
        ),
        # R18 — onde ir.
        (_entrada(necessidade="S10"), "hta_r18_navegacao", ("ORIENTAR_CAMINHO_SEM_CASO", "-", "-", "T12")),
        # R23 — necessidade nao clara pela primeira vez: UMA pergunta de esclarecimento.
        (_entrada(necessidade="A05"), "hta_r23_esclarecer", ("ESCLARECER_NECESSIDADE", "-", "-", "T11")),
        # R24 — continua sem clareza depois do esclarecimento: oferta de pessoa, SEM caso (diverge).
        (
            _entrada(necessidade="A05", esclarecimento_pedido=True),
            "hta_r24_catchall",
            ("RESPONDER_SEM_CASO", "-", "-", "OFERTA_PESSOA"),
        ),
        # Necessidade fora do vocabulario: catch-all, nunca caso.
        (
            _entrada(necessidade="XYZ"),
            "hta_r24_catchall",
            ("RESPONDER_SEM_CASO", "-", "-", "OFERTA_PESSOA"),
        ),
    ],
)
def test_cada_regra_decide_o_que_o_catalogo_diz(
    entrada: dict[str, Any], regra: str, saidas: tuple[str, str, str, str]
) -> None:
    veredito = evaluate(_tabela(), entrada)
    assert veredito.regra == regra
    assert tuple(veredito.saidas[nome] for nome in _SAIDAS) == saidas


@pytest.mark.parametrize(
    "linha",
    [
        {"decisao": "FECHAR_PLANO", "motivo_categoria": "-", "severidade": "-", "texto": "-"},
        {"decisao": "ABRIR_CASO", "motivo_categoria": "cobranca", "severidade": "leve", "texto": "-"},
        {"decisao": "ABRIR_CASO", "motivo_categoria": "solicitacao_humano", "severidade": "-", "texto": "-"},
        {"decisao": "RESPONDER_SEM_CASO", "motivo_categoria": "outro", "severidade": "-", "texto": "T08"},
        {"decisao": "RESPONDER_SEM_CASO", "motivo_categoria": "-", "severidade": "-", "texto": "T99"},
        {"decisao": "ORIENTAR_CAMINHO_SEM_CASO", "motivo_categoria": "-", "severidade": "-", "texto": "-"},
        {},
    ],
)
def test_linha_fora_do_vocabulario_e_recusada(linha: dict[str, Any]) -> None:
    with pytest.raises(SaidaInvalidaError):
        ler_linha(linha)


def test_a_leitura_aceita_o_formato_tipado_do_motor() -> None:
    """O motor devolve `{"value": ..., "type": ...}` por coluna; a leitura aceita as duas formas."""
    linha = {
        "decisao": {"value": "ABRIR_CASO", "type": "String"},
        "motivo_categoria": {"value": "solicitacao_humano", "type": "String"},
        "severidade": {"value": "leve", "type": "String"},
        "texto": {"value": "-", "type": "String"},
    }
    assert ler_linha(linha) == ("ABRIR_CASO", "solicitacao_humano", "leve", None)
