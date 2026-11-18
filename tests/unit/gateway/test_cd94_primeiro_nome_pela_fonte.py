"""CD-94 — `primeiro_nome` e' RESOLVIDO PELA FONTE, nao validado por formato.

Uma palavra latina nao distingue nome de pessoa de nome de medicamento. O valor so' pode vir da custodia do
destinatario pela referencia opaca do titular; valor informado pelo chamador e' recusado. GAP: nao existe
fonte de primeiro nome (a custodia guarda so' telefone) -> todo modelo com essa classe e' recusado hoje.
Registro e pedidos SINTETICOS.
"""

from __future__ import annotations

import copy
import re

import pytest

from maezo.gateway.communications import outbound_gate as og
from maezo.gateway.communications import whatsapp_templates as wt
from maezo.gateway.communications.outbound_gate import (
    VALOR_PELA_FONTE,
    Motivo,
    OutboundGate,
    PedidoDeEnvio,
)
from maezo.gateway.communications.whatsapp_templates import (
    CAMINHO_PADRAO,
    CLASSES_RESOLVIDAS_PELA_FONTE,
    VALIDADORES_DE_CLASSE,
    RegistroInvalidoError,
    ler_registro,
    validar_registro,
    valor_valido_para_classe,
)
from tests.unit.esf13_apoio import (
    FINALIDADE_LEAD,
    ReservaEmMemoria,
    SupressaoFixa,
    TransporteGravador,
    aceite_os05_sintetico,
    documento_ratificado,
)


def _doc_com_primeiro_nome() -> dict:  # type: ignore[type-arg]
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].append({"id": "primeiro_nome", "tipo": "pessoal_contratual"})
    doc["templates"].append(
        {
            "id": "boas_vindas_teste_v1",
            "name": "boas_vindas_teste_v1",
            "language": "pt_BR",
            "category": "utility",
            "meta_status": "approved",
            "meta_template_ref": "TESTE-META-94",
            "purpose_ref": FINALIDADE_LEAD,
            "subject_kind": "lead",
            "content_review_ref": "TESTE-REV-94",
            "publicity": False,
            "body": "Ola {{1}}, tudo bem?",
            "variables": [{"pos": 1, "class": "primeiro_nome"}],
        }
    )
    return doc


def _pedido(valor: str) -> PedidoDeEnvio:
    return PedidoDeEnvio(
        template_id="boas_vindas_teste_v1",
        finalidade=FINALIDADE_LEAD,
        tipo_titular="lead",
        titular_ref="prospect-ref-0001",
        canal_ref="contact-ref-0001",
        variaveis=(valor,),
        permissao=aceite_os05_sintetico(),
        chave_idempotencia="chave-cd94",
    )


async def _enviar(gate: OutboundGate, valor: str) -> tuple[og.ResultadoDoGate, TransporteGravador]:
    transporte = TransporteGravador()

    async def destinatario() -> str | None:
        return "5511999990000"

    r = await gate.enviar(
        _pedido(valor), destinatario=destinatario, reserva=ReservaEmMemoria(), transporte=transporte
    )
    return r, transporte


@pytest.mark.parametrize("valor", ["Losartana", "Insulina", "Maria"])
async def test_nome_de_medicamento_recusado_pela_classe_sem_depender_da_lista(
    monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    gate = OutboundGate(validar_registro(_doc_com_primeiro_nome()), supressao=SupressaoFixa())
    monkeypatch.setattr(wt, "TERMOS_DE_SAUDE", ())
    monkeypatch.setattr(wt, "_CID10", re.compile(r"(?!x)x"))
    monkeypatch.setattr(wt, "_CID10_MAIN", re.compile(r"(?!x)x"))
    monkeypatch.setattr(og, "contem_dado_de_saude", lambda _s: False)
    assert not wt.contem_dado_de_saude(valor)  # a lista nao recusaria
    r, transporte = await _enviar(gate, valor)
    assert r.motivo is Motivo.VARIABLE_VALUE_FROM_CALLER
    assert not r.postado and transporte.posts == []


async def test_marcador_sem_fonte_recusa() -> None:
    gate = OutboundGate(validar_registro(_doc_com_primeiro_nome()), supressao=SupressaoFixa())
    r, transporte = await _enviar(gate, VALOR_PELA_FONTE)
    assert r.motivo is Motivo.VARIABLE_SOURCE_UNAVAILABLE
    assert not r.postado and transporte.posts == []


def test_carga_do_registro_aceita_classe_resolvida_pela_fonte() -> None:
    assert "primeiro_nome" in CLASSES_RESOLVIDAS_PELA_FONTE
    assert "primeiro_nome" not in VALIDADORES_DE_CLASSE
    assert "primeiro_nome" in validar_registro(_doc_com_primeiro_nome()).classes
    ler_registro(CAMINHO_PADRAO)  # o YAML publicado continua carregando
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].append({"id": "observacao_livre", "tipo": "nao_pessoal"})
    with pytest.raises(RegistroInvalidoError) as exc:
        validar_registro(doc)
    assert any(p.startswith("VARIABLE_CLASS_NOT_CLOSED:") for p in exc.value.problemas)


@pytest.mark.parametrize("valor", ["Maria", "Losartana", VALOR_PELA_FONTE, "", "J45", 1, None])
def test_valor_valido_para_classe_primeiro_nome_sempre_falso(valor: object) -> None:
    assert not valor_valido_para_classe("primeiro_nome", valor)


def test_estado_publicado_inalterado() -> None:
    registro = ler_registro(CAMINHO_PADRAO)
    assert not any(f.ratificada for f in registro.finalidades.values())
    assert not any(t.aprovado for t in registro.templates.values())
