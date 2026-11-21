"""DL-0190 (ADR-0079, unidade U1): o prompt da narrativa do dossie leva so' a lista fechada.

O que estes testes travam:

1. O prompt enviado ao LLM nao contem o numero da guia nem o CID-10 (nem o valor sintetico,
   nem o nome da chave).
2. As chaves de fatos que chegam ao prompt sao EXATAMENTE a lista fechada
   `_CHAVES_FATOS_PROMPT`; uma chave nova so' entra se alguem atualizar a lista E este teste.
3. Nenhum campo da lista "nunca entra" do ADR-0079 (secao 2) volta ao prompt — por nome de
   chave e por valor plantado no estado.
4. O dossie devolvido ao humano e as variaveis do processo continuam levando guia e CID: a
   minimizacao e' do prompt, nao do caso.

Todos os valores sao SINTETICOS.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.rafael import graph as rafael_graph
from maezo.agents.rafael.graph import _CHAVES_FATOS_PROMPT, RafaelGraph, RafaelState, fatos_para_prompt
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

GUIA_SINTETICA = "FICT-GUIA-0000-SINTETICA"
CID_SINTETICO = "Q99.9-SINTETICO"

#: A lista fechada, repetida aqui DE PROPOSITO: mudar `_CHAVES_FATOS_PROMPT` sem mudar este
#: teste falha. Acrescentar chave ao prompt e' decisao (ADR-0079 §2 exige emenda), nao acidente.
CHAVES_ESPERADAS: tuple[str, ...] = (
    "codigo_procedimento_tuss",
    "categoria_procedimento",
    "carater_atendimento",
    "valor_estimado_brl",
    "beneficiario_ativo",
    "carencia_cumprida",
    "dut_atendida",
    "dentro_teto_l2",
    "rede_credenciada",
    "documentacao_completa",
    "admissibilidade",
    "recomendacao_auto",
    "dmn_refs",
    "sla_analise",
    "lacunas_enriquecimento",
)

#: Chaves de `fatos` que ficam no dossie do humano e FORA do prompt.
SO_NO_DOSSIE: frozenset[str] = frozenset({"numero_guia_tiss", "cid10"})

#: Lista "nunca entra" do ADR-0079 §2, em nomes de chave — fechada. Inclui os nomes que
#: existem hoje no estado do Rafael (`RafaelState`) e os nomes canonicos dos demais itens
#: (identificacao direta, quase-identificadores, pseudonimos, referencias FHIR, anexos,
#: CID/diagnostico, texto livre, profissional/estabelecimento, CPT/DLP).
NUNCA_ENTRA: frozenset[str] = frozenset(
    {
        # identificacao direta
        "nome",
        "nome_beneficiario",
        "cpf",
        "telefone",
        "email",
        "cns",
        "numero_carteirinha",
        "matricula_beneficiario",
        # identificacao indireta / quase-identificadores
        "endereco",
        "cep",
        "bairro",
        "data_nascimento",
        "data_atendimento",
        "data_internacao",
        "idade",
        "sexo",
        # vinculo do caso
        "numero_guia_tiss",
        "numero_guia_prestador",
        # pseudonimos
        "beneficiario_pseudo_id",
        "portable_subject_ref",
        "prestador_id",
        "business_key",
        # referencias FHIR e anexos
        "payload_ref",
        "coverage_ref",
        "patient_ref",
        "documentos_refs",
        "coverage_facts",
        "patient_facts",
        # CID / diagnostico
        "cid10",
        "cid10_referencia",
        "diagnostico",
        # texto livre
        "justificativa_clinica",
        "indicacao_clinica",
        "laudo",
        "notas",
        "fundamentacao",
        "mensagem_beneficiario",
        "resumo_contexto",
        # profissional / estabelecimento
        "crm",
        "cnpj",
        # CPT / DLP / declaracao de saude
        "cpt_vigente",
        "doenca_preexistente",
        "declaracao_saude",
    }
)


class _CapturaLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return "narrativa sintetica"


def _graph(llm: Any) -> RafaelGraph:
    return RafaelGraph(
        inference=llm,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


def _estado(**extra: Any) -> RafaelState:
    state: RafaelState = {
        "tenant_id": "tenant-sintetico",
        "numero_guia_tiss": GUIA_SINTETICA,
        "beneficiario_pseudo_id": "pseudo-sintetico-0001",
        "prestador_id": "PREST-SINTETICO-0001",
        "canal": "portal_tiss",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "carater_atendimento": "eletivo",
        "valor_estimado_brl": 250.0,
        "cid10": CID_SINTETICO,
        "documentos_refs": [{"ref": "doc-sintetico-0001"}],
        "requer_autorizacao": True,
        "documentacao_completa": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": False,
        "dut_atendida": None,  # type: ignore[typeddict-item]
        "dentro_teto_l2": True,
        "rede_credenciada": True,
        "route": "human_auditor",
        "motivo_auditor": "dmn_analise_humana",
    }
    state.update(extra)  # type: ignore[typeddict-item]
    return state


async def _dossie_e_prompt(
    state: RafaelState, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    capturados: list[dict[str, Any]] = []
    original = rafael_graph.render_fatos_para_prompt

    def _espia(facts: dict[str, Any]) -> str:
        capturados.append(dict(facts))
        return original(facts)

    monkeypatch.setattr(rafael_graph, "render_fatos_para_prompt", _espia)
    llm = _CapturaLLM()
    dossie = await _graph(llm)._build_dossier(state)
    assert len(llm.prompts) == 1 and len(capturados) == 1
    return dossie, llm.prompts[0], capturados[0]


def test_a_lista_fechada_e_a_esperada() -> None:
    """Muda a lista sem mudar este teste e ele falha — de proposito."""
    assert _CHAVES_FATOS_PROMPT == CHAVES_ESPERADAS


def test_a_lista_fechada_nao_tem_campo_que_nunca_entra() -> None:
    assert not set(_CHAVES_FATOS_PROMPT) & NUNCA_ENTRA


async def test_o_prompt_nao_leva_guia_nem_cid(monkeypatch: pytest.MonkeyPatch) -> None:
    _, prompt, _ = await _dossie_e_prompt(_estado(), monkeypatch)

    assert GUIA_SINTETICA not in prompt
    assert CID_SINTETICO not in prompt
    assert "numero_guia_tiss" not in prompt
    assert "cid10" not in prompt


async def test_as_chaves_do_prompt_sao_exatamente_a_lista(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, fatos_prompt = await _dossie_e_prompt(_estado(), monkeypatch)

    assert tuple(fatos_prompt) == CHAVES_ESPERADAS


async def test_todo_fato_do_dossie_esta_classificado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chave nova em `fatos` tem de ser classificada: vai ao prompt (lista) ou fica so' no dossie.

    Sem este teste, a lista fechada a protegeria do vazamento, mas a chave nova passaria sem
    ninguem decidir se ela deveria ir.
    """
    dossie, _, _ = await _dossie_e_prompt(_estado(), monkeypatch)

    assert set(dossie["fatos"]) == set(CHAVES_ESPERADAS) | SO_NO_DOSSIE


async def test_nada_da_lista_nunca_entra_chega_ao_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valor plantado em cada campo "nunca entra" (ADR-0079 §2) nao aparece no prompt.

    O estado recebe TODOS os nomes da lista, mesmo os que `RafaelState` nao declara hoje: se um
    dia `_build_dossier` passar a ler um deles, o valor sentinela denuncia.
    """
    plantados: dict[str, Any] = {chave: f"SENTINELA-{chave.upper()}-0001" for chave in NUNCA_ENTRA}
    plantados["documentos_refs"] = [{"ref": "SENTINELA-DOCUMENTOS_REFS-0001"}]
    plantados["coverage_facts"] = {"x": "SENTINELA-COVERAGE_FACTS-0001"}
    plantados["patient_facts"] = {"x": "SENTINELA-PATIENT_FACTS-0001"}

    _, prompt, fatos_prompt = await _dossie_e_prompt(_estado(**plantados), monkeypatch)

    assert not set(fatos_prompt) & NUNCA_ENTRA
    vazados = sorted(c for c in NUNCA_ENTRA if f"SENTINELA-{c.upper()}-0001" in prompt)
    assert not vazados, f"campos 'nunca entra' no prompt: {vazados}"
    assert "SENTINELA-" not in prompt


async def test_o_dossie_do_humano_e_as_variaveis_seguem_com_guia_e_cid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A minimizacao e' do PROMPT. O medico auditor e o processo continuam vendo o caso."""
    state = _estado()
    dossie, _, _ = await _dossie_e_prompt(state, monkeypatch)

    assert dossie["fatos"]["numero_guia_tiss"] == GUIA_SINTETICA
    assert dossie["fatos"]["cid10"] == CID_SINTETICO

    state["dossier"] = dossie
    variaveis = _graph(_CapturaLLM())._contract_variables(state)
    assert variaveis["numero_guia_tiss"] == GUIA_SINTETICA
    assert variaveis["cid10"] == CID_SINTETICO
    assert variaveis["dossie_rafael"]["fatos"]["cid10"] == CID_SINTETICO


def test_o_recorte_nao_muta_o_dicionario_do_dossie() -> None:
    fatos = {"numero_guia_tiss": GUIA_SINTETICA, "cid10": CID_SINTETICO, "sla_analise": "P5D"}
    recorte = fatos_para_prompt(fatos)

    assert recorte == {"sla_analise": "P5D"}
    assert fatos["cid10"] == CID_SINTETICO and fatos["numero_guia_tiss"] == GUIA_SINTETICA


async def test_os_bytes_do_prompt_sao_os_do_recorte_pela_lista(monkeypatch: pytest.MonkeyPatch) -> None:
    """U1c trocou o recorte pela lista (`_CHAVES_FATOS_PROMPT`, que fixava a ordem) pelo recorte so'
    das retiradas (ordem do montador) SEM mudar a versao do prompt. Isso so' vale enquanto os bytes
    forem os mesmos: o prompt de hoje tem de ser o que o recorte de antes da U1c produziria. Se a
    ordem do literal de `fatos` em `_build_dossier` mudar, este teste falha e a versao do prompt do
    Rafael tem de subir (bytes de prompt entram na proveniencia de auditoria)."""
    dossie, prompt, _ = await _dossie_e_prompt(_estado(), monkeypatch)
    fatos = dossie["fatos"]
    recorte_pela_lista = {chave: fatos[chave] for chave in CHAVES_ESPERADAS if chave in fatos}

    bloco = rafael_graph.render_fatos_para_prompt(recorte_pela_lista)
    assert rafael_graph.render_fatos_para_prompt(fatos_para_prompt(fatos)) == bloco
    assert prompt.endswith(f"\n{bloco}")
