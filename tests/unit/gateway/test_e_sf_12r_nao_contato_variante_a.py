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
import json
import subprocess
import typing
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import BaseModel

from maezo.gateway import effect_pep
from maezo.gateway.commercial_suppression_admission import A6_ADMISSION_ID, ADMISSION_RECORD_SCHEMA
from maezo.gateway.communications.outbound_gate import OutboundGate
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


def _e_registro_a6(objeto: object) -> bool:
    """Objeto JSON com `admission_id == "A6"` (com ou sem o schema da família de admissões)."""
    return isinstance(objeto, dict) and objeto.get("admission_id") == A6_ADMISSION_ID


def _contem_registro_a6(objeto: object) -> bool:
    """Percorre dicts/listas em qualquer profundidade procurando um registro de admissão A6."""
    pendentes: list[object] = [objeto]
    while pendentes:
        atual = pendentes.pop()
        if _e_registro_a6(atual):
            return True
        if isinstance(atual, dict):
            pendentes.extend(atual.values())
        elif isinstance(atual, list):
            pendentes.extend(atual)
    return False


def _registros_a6_em(caminhos: Collection[Path]) -> list[Path]:
    """Arquivos JSON que carregam um registro de admissão A6 — pelo CONTEÚDO, não pelo nome.

    O registro é identificado por `schema == ADMISSION_RECORD_SCHEMA` e `admission_id == "A6"`
    (`commercial_suppression_admission.py`); basta o `admission_id` para acusar. JSON ilegível é ignorado.
    """
    achados: list[Path] = []
    for caminho in caminhos:
        try:
            conteudo = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if _contem_registro_a6(conteudo):
            achados.append(caminho)
    return achados


def _json_versionados() -> list[Path]:
    """Só os `.json` rastreados pelo git (rápido; ignora .venv, caches e não-versionados)."""
    saida = subprocess.run(
        ["git", "ls-files", "-z", "*.json"], cwd=_REPO, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [_REPO / nome for nome in saida.split("\0") if nome]


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
    # timeDuration no lugar de timeDate: a expressão sai vazia e diverge da entrada esperada para o id.
    assert achados["BT_Timeout100"] != _TIMERS_ESPERADOS["BT_Timeout100"]
    # o timer de 80% ausente é acusado.
    assert set(_TIMERS_ESPERADOS) - set(achados) == {"BT_Escala80"}


def test_detector_de_categoria_acusa_modelo_com_beneficiario() -> None:
    from typing import Literal

    class _ComBeneficiario(BaseModel):
        categoria_sujeito: Literal["vendedor", "lead", "beneficiario"]

    assert _BENEFICIARIO in _categorias_de(_ComBeneficiario)


def test_detector_de_alcance_acusa_registro_aberto_a_beneficiario() -> None:
    assert _beneficiario_alcancavel(RegistroDeTemplates(oposicao_executavel_para=frozenset({_BENEFICIARIO})))
    assert not _beneficiario_alcancavel(RegistroDeTemplates())


def test_detector_de_registro_a6_acusa_arquivo_violador(tmp_path: Path) -> None:
    violador = tmp_path / "violador.json"
    violador.write_text(
        json.dumps({"itens": [{"schema": ADMISSION_RECORD_SCHEMA, "admission_id": A6_ADMISSION_ID}]}),
        encoding="utf-8",
    )
    so_o_id = tmp_path / "so_o_id.json"
    so_o_id.write_text(json.dumps({"admission_id": A6_ADMISSION_ID}), encoding="utf-8")
    limpo = tmp_path / "limpo.json"
    limpo.write_text(
        json.dumps({"schema": ADMISSION_RECORD_SCHEMA, "admission_id": "A5", "a_id": "A6"}), encoding="utf-8"
    )
    ilegivel = tmp_path / "ilegivel.json"
    ilegivel.write_text("{nao é json", encoding="utf-8")
    assert _registros_a6_em([violador, so_o_id, limpo, ilegivel]) == [violador, so_o_id]


# --------------------------------------------------------------------------- estado fechado


def test_chave_supp_fora_das_duas_listas_e_espelhos_em_paridade() -> None:
    listas = {
        "process_allowlist.KNOWN_PROCESS_KEYS": process_allowlist.KNOWN_PROCESS_KEYS,
        "process_allowlist.DEFAULT_ALLOWED_PROCESS_KEYS": process_allowlist.DEFAULT_ALLOWED_PROCESS_KEYS,
        "effect_pep.KNOWN_PROCESS_KEYS": effect_pep.KNOWN_PROCESS_KEYS,
    }
    assert _violacoes_das_listas(listas) == []


_MOTIVO_PERTINENCIA = "not in allowed set"
_MOTIVO_FORMATO = "invalid format; must match SP-OP-<DOMAIN>-<NNN>"


def test_ensure_allowed_recusa_a_chave_supp() -> None:
    with pytest.raises(ProcessKeyNotAllowedError):
        ensure_allowed(_CHAVE)
    with pytest.raises(ProcessKeyNotAllowedError):
        ProcessAllowlist().ensure_allowed(_CHAVE)


@pytest.mark.parametrize(
    "validar", [ensure_allowed, ProcessAllowlist().ensure_allowed], ids=["modulo", "instancia"]
)
def test_ensure_allowed_recusa_por_pertinencia_e_nao_por_formato(
    validar: typing.Callable[[str], None],
) -> None:
    """A recusa vem da lista, não do formato: a chave tem a forma válida SP-OP-<DOMINIO>-<NNN>."""
    assert process_allowlist._PROCESS_KEY_PATTERN.match(_CHAVE)
    with pytest.raises(ProcessKeyNotAllowedError) as recusa:
        validar(_CHAVE)
    assert recusa.value.process_key == _CHAVE
    assert recusa.value.reason == _MOTIVO_PERTINENCIA
    # controle: chave malformada cai no ramo de formato, com o outro motivo.
    with pytest.raises(ProcessKeyNotAllowedError) as malformada:
        validar("sp-op-supp-001")
    assert malformada.value.reason == _MOTIVO_FORMATO
    validar("SP-OP-ESCALATION-001")  # controle: uma chave da lista passa


def test_tenant_nao_consegue_acrescentar_a_chave_supp() -> None:
    """Chaves extras do tenant têm de estar no universo conhecido; SUPP não está, e o construtor recusa."""
    with pytest.raises(ProcessKeyNotAllowedError) as recusa:
        ProcessAllowlist(tenant_allowed_keys=frozenset({_CHAVE}))
    assert recusa.value.process_key == _CHAVE
    assert recusa.value.reason == "keys not in KNOWN_PROCESS_KEYS universe"
    with pytest.raises(ProcessKeyNotAllowedError):
        ProcessAllowlist(tenant_allowed_keys={_CHAVE, "SP-OP-ESCALATION-001"})


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


def test_composicao_de_producao_do_gate_fechada_para_beneficiario() -> None:
    """A composição de PRODUÇÃO (`OutboundGate.de_producao`), não só o registro solto, fica fechada."""
    gate = OutboundGate.de_producao(supressao=None)
    registro = gate._registro
    assert registro.oposicao_executavel_para == frozenset()
    assert not _beneficiario_alcancavel(registro)


def test_nenhum_registro_de_admissao_a6_versionado() -> None:
    versionados = _json_versionados()
    assert versionados, "git ls-files não listou nenhum .json — varredura não provaria nada"
    achados = [p.relative_to(_REPO).as_posix() for p in _registros_a6_em(versionados)]
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


_MARCAS_QUE_DESLIGAM = frozenset({"skip", "skipif", "xfail"})


def _desligada(funcao: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Verdadeiro se a função leva `pytest.mark.skip` / `skipif` / `xfail` (com ou sem chamada)."""
    for decorador in funcao.decorator_list:
        alvo = decorador.func if isinstance(decorador, ast.Call) else decorador
        partes = ast.unparse(alvo).split(".")
        if len(partes) >= 2 and partes[-2] == "mark" and partes[-1] in _MARCAS_QUE_DESLIGAM:
            return True
    return False


def _funcoes_definidas(caminho: Path) -> frozenset[str]:
    """Testes de módulo (corpo do módulo, nome `test_*`) que rodam: aninhados e desligados não contam."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return frozenset(
        n.name
        for n in arvore.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and n.name.startswith("test_")
        and not _desligada(n)
    )


def test_detector_de_reuso_acusa_guarda_ausente(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.py"
    arquivo.write_text(
        "import pytest\n"
        "def test_a() -> None:\n    pass\n"
        "def _apoio() -> None:\n    def test_aninhado() -> None:\n        pass\n"
        "@pytest.mark.skip(reason='x')\ndef test_pulado() -> None:\n    pass\n"
        "@pytest.mark.skipif(True, reason='x')\ndef test_condicional() -> None:\n    pass\n"
        "@pytest.mark.xfail\nasync def test_falha_esperada() -> None:\n    pass\n"
        "@pytest.mark.parametrize('x', [1])\ndef test_parametrizado(x: int) -> None:\n    pass\n",
        encoding="utf-8",
    )
    referenciados = {
        "test_a",
        "test_b",
        "test_aninhado",
        "test_pulado",
        "test_condicional",
        "test_falha_esperada",
        "test_parametrizado",
    }
    assert referenciados - _funcoes_definidas(arquivo) == {
        "test_b",
        "test_aninhado",
        "test_pulado",
        "test_condicional",
        "test_falha_esperada",
    }


def test_e_sf_12r_reused_guards_are_still_present() -> None:
    for relativo, nomes in _REUSED.items():
        faltando = nomes - _funcoes_definidas(_REPO / relativo)
        assert not faltando, f"{relativo}: guardas reusadas sumiram: {sorted(faltando)}"
