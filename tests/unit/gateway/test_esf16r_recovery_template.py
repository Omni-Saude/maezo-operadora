"""E-SF-16r (GTM-E-53): modelo unico e neutro da recuperacao no registro publicado. Nada envia."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway.communications import finalidades_do_aceite as fa
from maezo.gateway.communications import whatsapp_templates as wt
from maezo.gateway.communications.outbound_gate import Motivo, OutboundGate, PedidoDeEnvio
from maezo.gateway.communications.whatsapp_templates import RegistroInvalidoError, validar_registro

_RAIZ = Path(wt.__file__).resolve().parents[4]
_REGISTRO = _RAIZ / "spec/communications/whatsapp-templates.yaml"
FINALIDADE = "recuperacao_jornada"
MODELO = "recuperacao_jornada_v1"
#: As unicas classes que um modelo de recuperacao pode usar (sem regiao, nome, idade, composicao ou saude).
CLASSES_PERMITIDAS = frozenset({"contagem", "data_publicacao_fonte", "link_opaco_jornada"})


def _doc() -> dict[str, Any]:
    dados = yaml.safe_load(_REGISTRO.read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def _entrada(doc: dict[str, Any]) -> dict[str, Any]:
    (entrada,) = [t for t in doc["templates"] if t["id"] == MODELO]
    assert isinstance(entrada, dict)
    return entrada


def _so_classes_permitidas(entrada: dict[str, Any]) -> bool:
    return all(v["class"] in CLASSES_PERMITIDAS for v in entrada["variables"])


def _pedido(tipo_titular: str = "lead") -> PedidoDeEnvio:
    return PedidoDeEnvio(
        template_id=MODELO,
        finalidade=FINALIDADE,
        tipo_titular=tipo_titular,
        titular_ref="titular-sintetico-1",
        canal_ref="canal-sintetico-1",
        variaveis=(),
        permissao=None,
        chave_idempotencia="k-esf16r",
    )


def test_finalidade_registrada_e_nao_ratificada() -> None:
    (finalidade,) = [p for p in _doc()["purposes"] if p["id"] == FINALIDADE]
    assert finalidade == {"id": FINALIDADE, "ratified": False, "ratification_ref": None}


def test_modelo_unico_neutro_para_lead() -> None:
    doc = _doc()
    assert [t["id"] for t in doc["templates"] if t["purpose_ref"] == FINALIDADE] == [MODELO]
    entrada = _entrada(doc)
    assert entrada["subject_kind"] == "lead"
    assert entrada["category"] == "marketing" and entrada["publicity"] is True
    assert entrada["meta_status"] == "not_submitted" and entrada["meta_template_ref"] is None
    assert entrada["content_review_ref"] is None
    assert entrada["body"] is None  # sem texto inventado: o corpo e' da revisao de conteudo
    assert not entrada["id"].startswith("ret.")


def test_recovery_templates_have_only_allowed_classes() -> None:
    entrada = _entrada(_doc())
    assert entrada["variables"] == []
    assert _so_classes_permitidas(entrada)
    # A verificacao nao e' vazia: qualquer classe pessoal, de regiao ou de saude reprova.
    for proibida in (
        "competencia_mes_ano",
        "valor_cobranca_reais",
        "data_vencimento_dia_mes",
        "regiao",
        "cid",
    ):
        assert not _so_classes_permitidas({"variables": [{"pos": 1, "class": proibida}]})


def test_nenhum_envio_com_o_registro_publicado() -> None:
    registro = wt.registro_de_producao()
    assert not registro.finalidade_ratificada(FINALIDADE)
    assert not registro.templates[MODELO].aprovado
    assert "lead" not in registro.oposicao_executavel_para


@pytest.mark.parametrize("tipo", ["lead", "prospect", "beneficiario"])
async def test_gate_de_producao_recusa_a_recuperacao(tipo: str) -> None:
    gate = OutboundGate.de_producao(supressao=None)
    decisao = await gate.avaliar(_pedido(tipo))
    assert not decisao.permitido and decisao.motivo is Motivo.PURPOSE_NOT_RATIFIED


async def test_recovery_template_without_content_review_is_refused() -> None:
    # Mesmo com a finalidade ratificada (so' neste teste), modelo sem revisao de conteudo recusa.
    doc = copy.deepcopy(_doc())
    for p in doc["purposes"]:
        if p["id"] == FINALIDADE:
            p.update(ratified=True, ratification_ref="TESTE-SINTETICO")
    gate = OutboundGate(validar_registro(doc), supressao=None)
    assert (await gate.avaliar(_pedido("lead"))).motivo is Motivo.TEMPLATE_NOT_APPROVED
    assert (await gate.avaliar(_pedido("prospect"))).motivo is Motivo.TEMPLATE_PURPOSE_MISMATCH
    # E o esquema nao deixa aprovar sem a referencia da revisao de conteudo.
    _entrada(doc).update(meta_status="approved", meta_template_ref="ref-sintetica", body="Texto sintetico.")
    with pytest.raises(RegistroInvalidoError):
        validar_registro(doc)


def test_finalidade_continua_sem_numero() -> None:
    # Numerar a finalidade e' ato do registro de finalidades (CD-144/S3): campanha para lead/prospect segue
    # recusada.
    assert FINALIDADE not in fa.NUMERO_DA_FINALIDADE
    assert dict(fa.NUMERO_DA_FINALIDADE) == {}
