"""E-SF-22r — boas-vindas: gatilho so' pela cobertura ativa da fonte; envio FECHADO; nada montado."""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from maezo.gateway.capabilities.journeys import boas_vindas as bv
from maezo.gateway.capabilities.journeys.boas_vindas import (
    DOCUMENTOS_DO_INDIVIDUAL,
    TEXTO_CONVITE,
    BoasVindasRecusada,
    FatoDeCobertura,
    IntencaoDeBoasVindas,
    RegistroDeIntencoes,
    avaliar_gatilho,
    convite_ao_programa,
    documentos_exigidos,
    enviar_boas_vindas,
    fato_de_mapeamento,
    marcar_entregue,
    processar_aceite_do_convite,
)
from maezo.gateway.communications.outbound_gate import OutboundGate
from maezo.gateway.communications.whatsapp_templates import contem_dado_de_saude, registro_de_producao
from tests.unit.esf13_apoio import SupressaoFixa

_RAIZ = Path(__file__).resolve().parents[3]
VIG = date(2026, 3, 1)
#: Dado de teste: o identificador vem do registro de modelos e e' injetado; o modulo nao o conhece.
MODELO = "ret.boas_vindas.v1"


def _fato(**kw: object) -> FatoDeCobertura:
    base: dict[str, object] = {
        "beneficiario_ref": "benef-0001",
        "contrato_ref": "contr-0001",
        "coverage_state": "active",
        "vigencia_inicio": VIG,
        "origem": "fonte_cobertura",
    }
    base.update(kw)
    return FatoDeCobertura(**base)  # type: ignore[arg-type]


def test_boas_vindas_sem_cobertura_fonte_recusa() -> None:
    for estado in ("unknown", "revoked", None, "pending"):
        assert avaliar_gatilho(_fato(coverage_state=estado)) is None
    assert avaliar_gatilho(_fato(vigencia_inicio=None)) is None
    for origem in ("pagamento", "clique", "outra"):
        with pytest.raises(BoasVindasRecusada) as e:
            avaliar_gatilho(_fato(origem=origem))
        assert e.value.code == "gatilho_fora_da_fonte_de_cobertura"


def test_boas_vindas_active_gera_exatamente_uma() -> None:
    reg = RegistroDeIntencoes()
    a = reg.registrar(avaliar_gatilho(_fato()))  # type: ignore[arg-type]
    b = reg.registrar(avaliar_gatilho(_fato()))  # type: ignore[arg-type]
    assert a is b
    assert len(reg) == 1
    outra = avaliar_gatilho(_fato(contrato_ref="contr-0002"))
    assert outra is not None and outra.chave_idempotencia != a.chave_idempotencia


def test_boas_vindas_sem_campo_clinico() -> None:
    ok = {
        "beneficiario_ref": "b1",
        "contrato_ref": "c1",
        "coverage_state": "active",
        "vigencia_inicio": VIG,
        "origem": "fonte_cobertura",
    }
    assert fato_de_mapeamento(ok).beneficiario_ref == "b1"
    for extra in ("cpt", "cid", "dlp", "declaracao_saude"):
        with pytest.raises(BoasVindasRecusada) as e:
            fato_de_mapeamento({**ok, extra: "x"})
        assert e.value.code == "esquema_fechado"
    with pytest.raises(BoasVindasRecusada) as e:
        fato_de_mapeamento({**ok, "contrato_ref": "diabetes"})
    assert e.value.code == "PHI_IN_COMMERCIAL_INPUT"
    with pytest.raises(BoasVindasRecusada) as e:
        fato_de_mapeamento({**ok, "contrato_ref": "com espaco"})
    assert e.value.code == "ref_invalida"


def test_vigencia_e_a_data_da_fonte() -> None:
    i = avaliar_gatilho(_fato(vigencia_inicio=date(2025, 7, 15)))
    assert i is not None and i.vigencia_inicio == date(2025, 7, 15)


def test_documento_nao_declarado_entregue_sem_recibo() -> None:
    for recibo in (None, "", "com espaco"):
        with pytest.raises(BoasVindasRecusada) as e:
            marcar_entregue("disponivel", recibo_ref=recibo)
        assert e.value.code == "entrega_sem_recibo"
    assert marcar_entregue("disponivel", recibo_ref="rec-1") == "entregue"
    assert documentos_exigidos("individual") == DOCUMENTOS_DO_INDIVIDUAL
    assert documentos_exigidos("familiar") == DOCUMENTOS_DO_INDIVIDUAL
    assert documentos_exigidos("coletivo_empresarial") == ("material_explicativo",)


def test_convite_sem_consentimento_nao_processa_phi() -> None:
    assert (
        processar_aceite_do_convite(consentimento_especifico_ref=None, sessao_declaracao_saude=False)
        == "sem_consentimento"
    )
    assert (
        processar_aceite_do_convite(consentimento_especifico_ref="cons-1", sessao_declaracao_saude=True)
        == "sem_consentimento"
    )
    assert (
        processar_aceite_do_convite(consentimento_especifico_ref="cons-1", sessao_declaracao_saude=False)
        == "ponte_nao_construida"
    )


def test_convite_publico_sem_selecao_por_saude() -> None:
    assert list(inspect.signature(convite_ao_programa).parameters) == ["intencao"]
    i1 = avaliar_gatilho(_fato())
    i2 = avaliar_gatilho(_fato(beneficiario_ref="benef-9", contrato_ref="c-9"))
    assert i1 and i2
    assert convite_ao_programa(i1) == convite_ao_programa(i2) == TEXTO_CONVITE
    assert {
        "doenca_preexistente",
        "cpt",
        "rescisao",
        "reajuste",
        "retencao",
    } <= bv.USOS_VEDADOS_DO_DADO_DO_PROGRAMA


def test_texto_do_convite_sem_saude_sem_pergunta_sem_prazo() -> None:
    assert not contem_dado_de_saude(TEXTO_CONVITE)
    assert "?" not in TEXTO_CONVITE
    assert not any(c.isdigit() for c in TEXTO_CONVITE)
    for termo in ("prazo", "dias", "hoje", "até", "urgente"):
        assert termo not in TEXTO_CONVITE.lower()


def test_envio_de_boas_vindas_fechado() -> None:
    intencao = avaliar_gatilho(_fato())
    assert intencao is not None
    gate = OutboundGate(registro_de_producao(), supressao=SupressaoFixa(frozenset()))
    decisao = asyncio.run(enviar_boas_vindas(intencao, gate=gate, template_id=MODELO))
    assert decisao.permitido is False and decisao.autorizacao is None
    assert decisao.motivo is not None and decisao.motivo.value == "PURPOSE_NOT_RATIFIED"
    assert gate.contagens["autorizado"] == 0 and gate.contagens["postado"] == 0
    assert not hasattr(bv, "transporte")


def test_nada_montado() -> None:
    src = _RAIZ / "src" / "maezo"
    importadores = [
        p
        for p in src.rglob("*.py")
        if p.name != "boas_vindas.py"
        and any(
            (
                isinstance(n, ast.ImportFrom)
                and "boas_vindas" in (n.module or "").split(".")[-1:] + [a.name for a in n.names]
            )
            or (isinstance(n, ast.Import) and any("boas_vindas" in a.name for a in n.names))
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        )
    ]
    assert importadores == []


def test_cerca_do_dado_do_programa_sem_importar_reajuste_rescisao_retencao() -> None:
    tree = ast.parse(Path(inspect.getfile(bv)).read_text(encoding="utf-8"))
    mods = [
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        for a in (n.names if isinstance(n, ast.Import | ast.ImportFrom) else [])
    ]
    mods += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    for m in mods:
        for vedado in ("reajuste", "rescisao", "legitimidade", "retencao"):
            assert vedado not in m


def test_active_exige_caixa_exata() -> None:
    for estado in ("Active", " active", "ACTIVE", "active "):
        assert avaliar_gatilho(_fato(coverage_state=estado)) is None


def test_tipos_invalidos_recusam() -> None:
    for vig in ("2026-03-01", datetime(2026, 3, 1)):
        with pytest.raises(BoasVindasRecusada) as e:
            avaliar_gatilho(_fato(vigencia_inicio=vig))
        assert e.value.code == "tipo_invalido"
    falso = SimpleNamespace(
        beneficiario_ref="b1",
        contrato_ref="c1",
        coverage_state="active",
        vigencia_inicio=VIG,
        origem="fonte_cobertura",
    )
    with pytest.raises(BoasVindasRecusada) as e:
        avaliar_gatilho(falso)  # type: ignore[arg-type]
    assert e.value.code == "tipo_invalido"


def test_chave_sem_colisao_de_separador() -> None:
    reg = RegistroDeIntencoes()
    reg.registrar(avaliar_gatilho(_fato(beneficiario_ref="a|b", contrato_ref="c")))  # type: ignore[arg-type]
    reg.registrar(avaliar_gatilho(_fato(beneficiario_ref="a", contrato_ref="b|c")))  # type: ignore[arg-type]
    assert len(reg) == 2


def test_tipo_de_plano_invalido() -> None:
    for t in ("Individual", "xyz"):
        with pytest.raises(BoasVindasRecusada) as e:
            documentos_exigidos(t)  # type: ignore[arg-type]
        assert e.value.code == "tipo_plano_invalido"


def test_intencao_forjada_recusada() -> None:
    forjada = IntencaoDeBoasVindas("b1", "c1", VIG, bv.chave_de("b1", "c1"), "selo-qualquer")
    with pytest.raises(BoasVindasRecusada) as e:
        convite_ao_programa(forjada)
    assert e.value.code == "sem_cobertura_ativa"
    with pytest.raises(BoasVindasRecusada) as e:
        RegistroDeIntencoes().registrar(forjada)
    assert e.value.code == "sem_cobertura_ativa"
    gate = OutboundGate(registro_de_producao(), supressao=SupressaoFixa(frozenset()))
    with pytest.raises(BoasVindasRecusada) as e:
        asyncio.run(enviar_boas_vindas(forjada, gate=gate, template_id=MODELO))
    assert e.value.code == "sem_cobertura_ativa"


def test_pendente_com_recibo_nao_vira_entregue() -> None:
    with pytest.raises(BoasVindasRecusada) as e:
        marcar_entregue("pendente", recibo_ref="rec-1")
    assert e.value.code == "documento_nao_disponivel"


# ------------------------------------------- CD-123: identificador do modelo injetado, zero literal na fonte
class _PortaoQueNaoPodeSerChamado:
    async def avaliar(self, pedido: object) -> object:
        raise AssertionError("o portao nao pode ser chamado sem modelo injetado")


@pytest.mark.parametrize(
    "modelo",
    [
        None,
        "",
        5,
        "ret boas_vindas v1",
        "RET.BOAS_VINDAS.V1",
        "ret.boas_vindas",
        " ret.boas_vindas.v1",
        "ret..v1",
    ],
)
def test_sem_modelo_injetado_recusa(modelo: object) -> None:
    intencao = avaliar_gatilho(_fato())
    assert intencao is not None
    with pytest.raises(BoasVindasRecusada) as e:
        asyncio.run(
            enviar_boas_vindas(
                intencao,
                gate=_PortaoQueNaoPodeSerChamado(),  # type: ignore[arg-type]
                template_id=modelo,  # type: ignore[arg-type]
            )
        )
    assert e.value.code == "modelo_nao_injetado"
    assert str(modelo) not in str(e.value) or str(modelo) == ""


def test_modelo_injetado_nao_publicado_nem_ratificado() -> None:
    registro = registro_de_producao()
    modelo = registro.templates[MODELO]
    assert modelo.aprovado is False and modelo.meta_status != "approved"
    assert modelo.purpose_ref == bv.FINALIDADE_BOAS_VINDAS
    assert registro.finalidade_ratificada(modelo.purpose_ref) is False


def test_fonte_sem_nome_de_sinal_e_sem_emissor() -> None:
    fonte = Path(inspect.getfile(bv)).read_text(encoding="utf-8")
    # Mesmo padrao generico da cerca da E-SF-RT-01: nenhum nome `ret.*` na fonte do modulo.
    assert re.findall(r"(?<![\w.])ret\.[a-z0-9_]+\.[a-z0-9_]+", fonte) == []
    tree = ast.parse(fonte)
    modulos = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    modulos += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    for m in modulos:
        for vedado in ("telemetry", "medicao", "measurement", "retention_signals", "eventos", "outbox"):
            assert vedado not in m
    chamadas = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not {c for c in chamadas if c.startswith(("emit", "publicar", "registrar_evento"))}
