"""E-SF-13 / GTM-E-42 — registro FECHADO de templates WhatsApp.

Arquivo: `spec/communications/whatsapp-templates.yaml`.

Guardas: `test_template_registry_rejects_health_variable`, `test_template_registry_rejects_unknown_class`,
o estado publicado fechado (zero ratificacao, zero aprovacao), templates `ret.*` registrados SEM aprovacao
e recusados pelo gate (ADDENDUM-01 §6, linha E-SF-13), e a migracao do template do lembrete do runbook.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.communications.outbound_gate import Motivo, OutboundGate, PedidoDeEnvio
from maezo.gateway.communications.whatsapp_templates import (
    CAMINHO_PADRAO,
    SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO,
    RegistroInvalidoError,
    registro_de_producao,
    validar_registro,
)
from maezo.platform.lembretes.texto import CORPO_TEMPLATE_META
from tests.unit.esf13_apoio import SupressaoFixa, aceite_os05_sintetico, documento_ratificado

_RAIZ = Path(__file__).resolve().parents[3]
_RET = (
    "ret.aviso_r0.v1",
    "ret.aviso_r1.v1",
    "ret.recibo_renovacao.v1",
    "ret.boas_vindas.v1",
    "ret.checkin_d7.v1",
)


def _doc_com_template(**campos: Any) -> dict[str, Any]:
    doc = copy.deepcopy(documento_ratificado())
    doc["templates"][1].update(campos)
    return doc


def _problemas(doc: dict[str, Any]) -> tuple[str, ...]:
    with pytest.raises(RegistroInvalidoError) as exc:
        validar_registro(doc)
    return exc.value.problemas


# ----------------------------------------------------------------------------- estado publicado
def test_registro_publicado_e_valido_e_fechado() -> None:
    assert CAMINHO_PADRAO == _RAIZ / "spec" / "communications" / "whatsapp-templates.yaml"
    r = registro_de_producao()
    assert r.templates, "o registro publicado tem de existir e ter entradas"
    assert [f.id for f in r.finalidades.values() if f.ratificada] == []
    assert [t.id for t in r.templates.values() if t.meta_status == "approved" or t.aprovado] == []
    assert r.textos_de_permissao == ()
    assert r.oposicao_executavel_para == frozenset()
    assert r.taxonomia_ratificada is False


def test_template_do_lembrete_migrado_do_runbook_e_identico_ao_texto_fixo() -> None:
    tpl = registro_de_producao().templates["lembrete_mensalidade_v1"]
    assert tpl.body == CORPO_TEMPLATE_META
    assert tpl.name == "lembrete_mensalidade_v1" and tpl.language == "pt_BR" and tpl.category == "utility"
    assert tpl.purpose_ref == "cobranca_lembrete_pre_vencimento" and tpl.subject_kind == "beneficiario"
    assert [v.pos for v in tpl.variables] == [1, 2, 3]
    assert tpl.meta_status != "approved"
    runbook = (_RAIZ / "docs/runbooks/lembrete-cobranca.md").read_text(encoding="utf-8")
    assert "spec/communications/whatsapp-templates.yaml" in runbook
    assert "Sua mensalidade de {{1}}" not in runbook  # o corpo vive SO' no registro


def test_templates_ret_registrados_sem_aprovacao() -> None:
    r = registro_de_producao()
    ret = {i: t for i, t in r.templates.items() if i.startswith("ret.")}
    assert sorted(ret) == sorted(_RET)
    for t in ret.values():
        assert t.meta_status != "approved" and t.content_review_ref is None and t.meta_template_ref is None
        assert t.subject_kind == "beneficiario"


@pytest.mark.parametrize("template_id", _RET)
async def test_gate_recusa_os_templates_ret(template_id: str) -> None:
    """Com o registro publicado: PURPOSE_NOT_RATIFIED. Mesmo com finalidades e oposicao abertas (registro
    sintetico + as entradas `ret.*` publicadas, inalteradas): TEMPLATE_NOT_APPROVED."""
    publicado = registro_de_producao()
    tpl = publicado.templates[template_id]
    pedido = PedidoDeEnvio(
        template_id=template_id,
        finalidade=tpl.purpose_ref,
        tipo_titular="beneficiario",
        titular_ref="ref-x",
        canal_ref="canal-x",
        variaveis=(),
        permissao=aceite_os05_sintetico(),
        chave_idempotencia="k",
    )
    d = await OutboundGate(publicado, supressao=SupressaoFixa()).avaliar(pedido)
    assert d.motivo is Motivo.PURPOSE_NOT_RATIFIED and d.autorizacao is None

    import yaml

    publicado_doc = yaml.safe_load(CAMINHO_PADRAO.read_text(encoding="utf-8"))
    doc = copy.deepcopy(documento_ratificado())
    doc["purposes"] += [
        {"id": p["id"], "ratified": True, "ratification_ref": "TESTE"}
        for p in publicado_doc["purposes"]
        if p["id"] != "cobranca_lembrete_pre_vencimento"
    ]
    doc["contact_permission_texts"][0]["purposes"].append(tpl.purpose_ref)
    doc["templates"] += [t for t in publicado_doc["templates"] if t["id"].startswith("ret.")]
    aberto = OutboundGate(validar_registro(doc), supressao=SupressaoFixa())
    d2 = await aberto.avaliar(pedido)
    assert d2.motivo is Motivo.TEMPLATE_NOT_APPROVED and d2.autorizacao is None


# ----------------------------------------------------------------------------- esquema: guardas
@pytest.mark.parametrize("classe", ["cpt", "dps_status", "cid10", "recusa_motivo", "utilizacao_assistencial"])
def test_template_registry_rejects_health_variable(classe: str) -> None:
    doc = _doc_com_template(variables=[{"pos": 1, "class": classe}])
    assert "VARIABLE_CLASS_HEALTH:templates[1].variables[0]" in _problemas(doc)


def test_template_registry_rejects_health_class_in_vocabulary() -> None:
    doc = copy.deepcopy(documento_ratificado())
    doc["variable_classes"].append({"id": "dps_status", "tipo": "nao_pessoal"})
    assert any(p.startswith("VARIABLE_CLASS_HEALTH:variable_classes") for p in _problemas(doc))


def test_template_registry_rejects_unknown_class() -> None:
    doc = _doc_com_template(variables=[{"pos": 1, "class": "classe_inventada"}])
    assert "VARIABLE_CLASS_UNKNOWN:templates[1].variables[0]" in _problemas(doc)


def test_template_registry_rejects_body_with_health_content() -> None:
    doc = _doc_com_template(body="Bem-vindo! Lembrete: sua CPT termina em {{1}}.")
    assert "BODY_HEALTH_CONTENT:templates[1]" in _problemas(doc)


@pytest.mark.parametrize(
    ("campos", "token"),
    [
        ({"content_review_ref": None}, "TEMPLATE_APPROVED_INCOMPLETE:templates[1]"),
        ({"meta_template_ref": None}, "TEMPLATE_APPROVED_INCOMPLETE:templates[1]"),
        ({"meta_status": "aprovado_pelo_agente"}, "TEMPLATE_META_STATUS_INVALID:templates[1]"),
        ({"purpose_ref": "finalidade_inexistente"}, "TEMPLATE_PURPOSE_UNKNOWN:templates[1]"),
        ({"body": "Sem placeholder."}, "TEMPLATE_PLACEHOLDERS_MISMATCH:templates[1]"),
        ({"name": "Nome Com Espaco"}, "TEMPLATE_NAME_INVALID:templates[1]"),
        ({"subject_kind": "qualquer"}, "TEMPLATE_SUBJECT_KIND_INVALID:templates[1]"),
        ({"campo_extra": 1}, "TEMPLATE_FIELDS_INVALID:templates[1]"),
    ],
)
def test_esquema_reprova_entrada_fora_do_contrato(campos: dict[str, Any], token: str) -> None:
    assert token in _problemas(_doc_com_template(**campos))


@pytest.mark.parametrize(
    ("version", "sha"),
    [("os05-v1", "WA"), ("wa-consent-v3", "0" * 64), ("wa-consent-v2", "WA")],
    ids=["mesmo-sha-outra-versao", "versao-wa-consent-outro-sha", "o-proprio"],
)
def test_registro_recusa_publicar_texto_wa_consent_sob_outra_versao(version: str, sha: str) -> None:
    """F1: o texto do consentimento de atendimento nunca entra como permissao de contato proativo."""
    doc = copy.deepcopy(documento_ratificado())
    doc["contact_permission_texts"].append(
        {
            "version": version,
            "sha256": SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO if sha == "WA" else sha,
            "purposes": ["cobranca_lembrete_pre_vencimento"],
            "channel": "whatsapp",
        }
    )
    assert "PERMISSION_TEXT_IS_SERVICE_CONSENT:contact_permission_texts[1]" in _problemas(doc)


def test_sha_do_consentimento_copiado_e_igual_ao_do_acesso() -> None:
    from maezo.platform.webhooks.whatsapp.acesso import SHA256_TEXTO_CONSENTIMENTO

    assert SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO == SHA256_TEXTO_CONSENTIMENTO


def test_finalidade_ratificada_sem_referencia_reprova() -> None:
    doc = copy.deepcopy(documento_ratificado())
    doc["purposes"][0]["ratification_ref"] = None
    assert "PURPOSE_RATIFIED_WITHOUT_REF:purposes[0]" in _problemas(doc)


def test_os_problemas_sao_tokens_sem_valor() -> None:
    doc = _doc_com_template(body="Telefone 5511977770001 e CPT em {{1}}.")
    for p in _problemas(doc):
        assert "5511" not in p and "CPT" not in p
