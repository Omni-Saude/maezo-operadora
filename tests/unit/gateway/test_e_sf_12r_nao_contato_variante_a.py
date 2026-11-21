"""E-SF-12r — não-contato, variante (a): estado fechado afirmado, nada promovido.

O que esta linha NÃO faz (CD-228): não promove `SP-OP-SUPP-001` às duas listas de chaves de
processo (ADR-0016), não muda a contagem de 15 chaves, não toca `src/`, nem o BPMN, o DMN ou o
arquivo de aprovações. O registro de admissão A6 (dono e encarregado) não existe; enquanto não
existir, o estado abaixo é o que vale, e qualquer desvio quebra um teste daqui.

Cada detector estático deste arquivo tem autoteste: a mesma função que varre o estado real é
exercitada contra uma entrada sintética violadora e tem de acusá-la (um detector que nunca acusa
não prova nada). Guardas que já pinam a mesma coisa noutros arquivos são referenciadas, não
duplicadas — `test_e_sf_12r_reused_guards_are_still_present` falha se alguma sumir.
"""

from __future__ import annotations

import ast
import typing
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import BaseModel

from maezo.gateway import effect_pep
from maezo.gateway.communications.whatsapp_templates import RegistroDeTemplates, registro_de_producao
from maezo.gateway.vendor_suppression import (
    SLA_BUSINESS_DAYS,
    SLA_ESCALATION_FRACTION,
    SLA_REMINDER_FRACTION,
    SuppressionCommand,
    SuppressionRecord,
)
from maezo.tools import process_allowlist
from maezo.tools.process_allowlist import ProcessAllowlist, ProcessKeyNotAllowedError, ensure_allowed

_REPO = Path(__file__).resolve().parents[3]
_CHAVE = "SP-OP-SUPP-001"
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn"
_NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
_BENEFICIARIO = "beneficiario"

#: Os três temporizadores do relógio do SUPP: id -> (variável do timeDate, interrompe a tarefa?).
_TIMERS_ESPERADOS: Mapping[str, tuple[str, bool]] = {
    "BT_Lembrete50": ("${t_lembrete_iso}", False),
    "BT_Escala80": ("${t_escalonado_iso}", False),
    "BT_Timeout100": ("${t_deadline_iso}", True),
}


# --------------------------------------------------------------------------- detectores


def _violacoes_das_listas(listas: Mapping[str, Collection[str]]) -> list[str]:
    """Acusa: chave SUPP presente, contagem diferente de 15 ou espelhos divergentes."""
    problemas: list[str] = []
    for nome, chaves in listas.items():
        if _CHAVE in chaves:
            problemas.append(f"{nome}: contém {_CHAVE}")
        if len(chaves) != 15:
            problemas.append(f"{nome}: {len(chaves)} chaves, esperado 15")
    conjuntos = {frozenset(c) for c in listas.values()}
    if len(conjuntos) > 1:
        problemas.append("espelhos divergem")
    return problemas


def _timers_do_bpmn(xml: str) -> dict[str, tuple[str, bool]]:
    """id -> (expressão timeDate, cancelActivity) de todo boundaryEvent com timer do processo."""
    raiz = ET.fromstring(xml)
    achados: dict[str, tuple[str, bool]] = {}
    for evento in raiz.iter(f"{{{_NS['bpmn']}}}boundaryEvent"):
        definicao = evento.find("bpmn:timerEventDefinition", _NS)
        if definicao is None:
            continue
        data = definicao.find("bpmn:timeDate", _NS)
        expressao = (data.text or "").strip() if data is not None else ""
        interrompe = evento.get("cancelActivity", "true") != "false"
        achados[evento.get("id", "")] = (expressao, interrompe)
    return achados


def _categorias_de(modelo: type[BaseModel]) -> frozenset[str]:
    """Valores aceitos pelo campo `categoria_sujeito` (Literal) de um modelo pydantic."""
    anotacao = modelo.model_fields["categoria_sujeito"].annotation
    return frozenset(str(v) for v in typing.get_args(anotacao))


def _beneficiario_alcancavel(registro: RegistroDeTemplates) -> bool:
    """Verdadeiro se o registro deixaria um envio proativo chegar a beneficiário."""
    return _BENEFICIARIO in registro.oposicao_executavel_para


# --------------------------------------------------------------------------- autotestes


def test_detector_das_listas_acusa_entrada_violadora() -> None:
    quinze = frozenset(f"SP-OP-X-{i:03d}" for i in range(15))
    assert _violacoes_das_listas({"a": quinze, "b": quinze}) == []
    com_supp = (quinze - {"SP-OP-X-000"}) | {_CHAVE}
    assert any(_CHAVE in p for p in _violacoes_das_listas({"a": com_supp, "b": com_supp}))
    assert any("16 chaves" in p for p in _violacoes_das_listas({"a": quinze | {_CHAVE}}))
    outra = (quinze - {"SP-OP-X-000"}) | {"SP-OP-Y-001"}
    assert "espelhos divergem" in _violacoes_das_listas({"a": quinze, "b": outra})


def test_detector_de_timers_acusa_entrada_violadora() -> None:
    xml = f"""<bpmn:definitions xmlns:bpmn="{_NS["bpmn"]}"><bpmn:process id="p">
      <bpmn:boundaryEvent id="BT_Lembrete50" cancelActivity="false">
        <bpmn:timerEventDefinition><bpmn:timeDate>${{t_lembrete_iso}}</bpmn:timeDate></bpmn:timerEventDefinition>
      </bpmn:boundaryEvent>
      <bpmn:boundaryEvent id="BT_Timeout100">
        <bpmn:timerEventDefinition><bpmn:timeDuration>P10D</bpmn:timeDuration></bpmn:timerEventDefinition>
      </bpmn:boundaryEvent>
    </bpmn:process></bpmn:definitions>"""
    achados = _timers_do_bpmn(xml)
    assert achados["BT_Lembrete50"] == ("${t_lembrete_iso}", False)
    assert achados["BT_Timeout100"] == ("", True)
    assert achados != dict(_TIMERS_ESPERADOS)


def test_detector_de_categoria_acusa_modelo_com_beneficiario() -> None:
    from typing import Literal

    class _ComBeneficiario(BaseModel):
        categoria_sujeito: Literal["vendedor", "lead", "beneficiario"]

    assert _BENEFICIARIO in _categorias_de(_ComBeneficiario)


def test_detector_de_alcance_acusa_registro_aberto_a_beneficiario() -> None:
    assert _beneficiario_alcancavel(RegistroDeTemplates(oposicao_executavel_para=frozenset({_BENEFICIARIO})))
    assert not _beneficiario_alcancavel(RegistroDeTemplates())


# --------------------------------------------------------------------------- estado fechado


def test_chave_supp_fora_das_duas_listas_e_espelhos_em_paridade() -> None:
    listas = {
        "process_allowlist.KNOWN_PROCESS_KEYS": process_allowlist.KNOWN_PROCESS_KEYS,
        "process_allowlist.DEFAULT_ALLOWED_PROCESS_KEYS": process_allowlist.DEFAULT_ALLOWED_PROCESS_KEYS,
        "effect_pep.KNOWN_PROCESS_KEYS": effect_pep.KNOWN_PROCESS_KEYS,
    }
    assert _violacoes_das_listas(listas) == []


def test_ensure_allowed_recusa_a_chave_supp() -> None:
    with pytest.raises(ProcessKeyNotAllowedError):
        ensure_allowed(_CHAVE)
    with pytest.raises(ProcessKeyNotAllowedError):
        ProcessAllowlist().ensure_allowed(_CHAVE)


def test_ensure_allowed_recusa_por_pertinencia_e_nao_por_formato() -> None:
    """A recusa vem da lista, não do formato: a chave tem a forma válida SP-OP-<DOMINIO>-<NNN>."""
    assert process_allowlist._PROCESS_KEY_PATTERN.match(_CHAVE)
    ensure_allowed("SP-OP-ESCALATION-001")  # controle: uma chave da lista passa


def test_temporizadores_50_80_100_inalterados() -> None:
    assert _timers_do_bpmn(_BPMN.read_text(encoding="utf-8")) == dict(_TIMERS_ESPERADOS)


def test_fracoes_do_relogio_inalteradas() -> None:
    assert Fraction(1, 2) == SLA_REMINDER_FRACTION
    assert Fraction(4, 5) == SLA_ESCALATION_FRACTION
    assert SLA_BUSINESS_DAYS == 15


def test_supressao_nao_tem_categoria_beneficiario() -> None:
    for modelo in (SuppressionCommand, SuppressionRecord):
        categorias = _categorias_de(modelo)
        assert _BENEFICIARIO not in categorias, modelo.__name__
        assert categorias == {"vendedor", "lead"}, modelo.__name__


def test_registro_publicado_nao_alcanca_beneficiario() -> None:
    publicado = registro_de_producao()
    assert not _beneficiario_alcancavel(publicado)
    assert publicado.oposicao_executavel_para == frozenset()


def test_registro_vazio_nao_alcanca_beneficiario() -> None:
    assert RegistroDeTemplates().oposicao_executavel_para == frozenset()


def test_nenhum_registro_de_admissao_a6_versionado() -> None:
    achados = [
        p.relative_to(_REPO).as_posix()
        for p in _REPO.glob("**/A6-ADMISSION-RECORD*.json")
        if ".git" not in p.parts and ".venv" not in p.parts
    ]
    assert achados == []


# --------------------------------------------------------------------------- reuso

#: Guardas que já pinam partes deste estado (arquivo -> nomes). Referenciadas, não duplicadas.
_REUSED: Mapping[str, frozenset[str]] = {
    "tests/unit/test_commercial_admission_a6.py": frozenset(
        {
            "test_both_process_allowlists_stay_fifteen_without_the_key",
            "test_the_pep_universe_drops_the_suppression_key_even_when_declared",
            "test_default_composition_never_threads_the_store",
        }
    ),
    "tests/unit/gateway/test_effect_pep.py": frozenset(
        {"test_the_mirrored_process_key_constants_match_the_adr0016_source"}
    ),
    "tests/unit/gateway/test_e_sf_12_nao_contato_fence.py": frozenset(
        {
            "test_supp_key_not_agent_startable",
            "test_gt4_registered_unhonored_opposition_is_suppressed_in_list_build",
            "test_gt4_beneficiario_has_no_suppression_support",
        }
    ),
    "tests/unit/gateway/test_esf13_outbound_gate.py": frozenset(
        {
            "test_beneficiario_sem_oposicao_executavel_e_recusado",
            "test_wa_consent_v2_nunca_vale_para_contato_proativo_nem_com_registro_aberto",
            "test_resposta_1a1_inbound_nao_passa_pelo_gate",
        }
    ),
    "tests/unit/spec/test_suppression_routing_wiring.py": frozenset(
        {
            "test_os_tres_timers_do_relogio_leem_as_variaveis_do_registro",
            "test_so_o_timeout_100_e_interrupting",
        }
    ),
}


def _funcoes_definidas(caminho: Path) -> frozenset[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return frozenset(
        n.name for n in ast.walk(arvore) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def test_detector_de_reuso_acusa_guarda_ausente(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.py"
    arquivo.write_text("def test_a() -> None:\n    pass\n", encoding="utf-8")
    assert {"test_a", "test_b"} - _funcoes_definidas(arquivo) == {"test_b"}


def test_e_sf_12r_reused_guards_are_still_present() -> None:
    for relativo, nomes in _REUSED.items():
        faltando = nomes - _funcoes_definidas(_REPO / relativo)
        assert not faltando, f"{relativo}: guardas reusadas sumiram: {sorted(faltando)}"
