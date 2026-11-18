"""DL-0191 (ADR-0079 §2, unidade U1b): o prompt da narrativa da Marina leva so' a lista fechada.

Mesmo desenho de `test_rafael_prompt_minimizacao.py` (DL-0190), por fluxo (contas, recurso,
reembolso):

1. O prompt enviado ao LLM nao contem o numero da guia nem o CID-10 (nem o valor sintetico,
   nem o nome da chave).
2. As chaves que chegam ao renderizador sao EXATAMENTE a lista fechada do fluxo, e o
   renderizador recebe essa mesma lista em `permitidos`.
3. Nenhum campo da lista "nunca entra" do ADR-0079 volta ao prompt — por nome de chave e por
   valor sentinela plantado no estado.
4. O dossie devolvido ao humano e as variaveis do processo continuam iguais: guia e CID seguem
   neles. A minimizacao e' do prompt, nao do caso.

Todos os valores sao SINTETICOS.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.marina import graph as marina_graph
from maezo.agents.marina.graph import (
    _PERMITIDOS_PROMPT_CONTAS,
    _PERMITIDOS_PROMPT_RECURSO,
    _PERMITIDOS_PROMPT_REEMBOLSO,
    MarinaGraph,
    MarinaState,
    fatos_para_prompt,
)
from maezo.runtime.prompt_format import render_fatos_para_prompt as render_fatos_original
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

GUIA_SINTETICA = "FICT-GUIA-0000-SINTETICA"
CID_SINTETICO = "Q99.9-SINTETICO"

#: As listas fechadas, repetidas aqui DE PROPOSITO: mudar a lista no grafo sem mudar este teste
#: falha. Acrescentar chave ao prompt e' decisao (ADR-0079 §2 exige emenda), nao acidente.
ESPERADO: dict[str, frozenset[str]] = {
    "contas": frozenset(
        {
            "numero_lote_tiss",
            "numero_conta",
            "competencia",
            "tipo_lote",
            "valor_apresentado_brl",
            "reason_codes_tiss",
            "categoria_normalizada",
            "glosa_classificada",
            "denial_ratio",
            "item_conforme_tabela",
            "divergencia_valor",
            "documentacao_anexa",
            "triagem",
            "indicio_fraude_sinalizado",
            "dmn_refs",
            "sla_analise",
            "lacunas_enriquecimento",
        }
    ),
    "recurso": frozenset(
        {
            "glosa_id",
            "glosa_type",
            "glosa_reason_code",
            "valor_glosado_brl",
            "codigo_procedimento_tuss",
            "glosa_existe",
            "dentro_prazo_recurso",
            "documentacao_recurso_completa",
            "admissibilidade_recurso",
            "elegibilidade_recurso",
            "grupo_revisor",
            "dmn_refs",
            "sla_analise",
            "lacunas_enriquecimento",
        }
    ),
    "reembolso": frozenset(
        {
            "protocolo_reembolso",
            "tipo_reembolso",
            "categoria_procedimento",
            "codigo_procedimento_tuss",
            "valor_solicitado_cents",
            "situacao_prazo",
            "relacao_com_titular",
            "motivo_nao_rede",
            "lacunas_enriquecimento",
        }
    ),
}

#: Chaves de `fatos` que ficam no dossie do humano e FORA do prompt, por fluxo.
SO_NO_DOSSIE: dict[str, frozenset[str]] = {
    "contas": frozenset({"numero_guia_tiss"}),
    "recurso": frozenset({"numero_guia_tiss", "cid10"}),
    "reembolso": frozenset({"numero_guia_tiss"}),
}

FLUXOS: tuple[str, ...] = ("contas", "recurso", "reembolso")

#: Lista "nunca entra" do ADR-0079 §2, em nomes de chave — a mesma de
#: `test_rafael_prompt_minimizacao.py`, repetida para que cada agente tenha a sua rede.
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
        "documentos_recurso_refs",
        "linhas_conta_refs",
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


def _graph(llm: Any) -> MarinaGraph:
    return MarinaGraph(
        inference=llm,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


def _estado(flow: str, **extra: Any) -> MarinaState:
    state: dict[str, Any] = {
        "tenant_id": "tenant-sintetico",
        "flow": flow,
        "route": "human_review",
        "motivo_humano": "dmn_analise_humana",
        "beneficiario_pseudo_id": "pseudo-sintetico-0001",
        "prestador_id": "PREST-SINTETICO-0001",
        "numero_guia_tiss": GUIA_SINTETICA,
        "cid10": CID_SINTETICO,
        "codigo_procedimento_tuss": "10101012",
    }
    if flow == "contas":
        state.update(
            {
                "numero_lote_tiss": "FICT-LOTE-0001",
                "numero_conta": "FICT-CONTA-0001",
                "competencia": "2026-10",
                "tipo_lote": "honorarios",
                "valor_apresentado_brl": 321.0,
                "item_conforme_tabela": True,
                "divergencia_valor": False,
            }
        )
    elif flow == "recurso":
        state.update(
            {
                "glosa_id": "FICT-GLOSA-0001",
                "glosa_type": "administrativa",
                "glosa_reason_code": "1801",
                "valor_glosado_brl": 99.0,
                "glosa_existe": True,
                "dentro_prazo_recurso": True,
                "documentacao_recurso_completa": False,
            }
        )
    else:
        state.update(
            {
                "protocolo_reembolso": "FICT-REEMB-0001",
                "tipo_reembolso": "consulta",
                "categoria_procedimento": "consulta",
                "valor_solicitado_cents": 25000,
                "situacao_prazo": "DENTRO",
                "relacao_com_titular": "titular",
                "motivo_nao_rede": "indisponibilidade",
            }
        )
    state.update(extra)
    return state  # type: ignore[return-value]


async def _dossie_e_prompt(
    state: MarinaState, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], str, dict[str, Any], frozenset[str]]:
    capturados: list[tuple[dict[str, Any], frozenset[str]]] = []
    original = render_fatos_original

    def _espia(facts: Any, *, booleanos: Any, permitidos: frozenset[str], **kw: Any) -> str:
        capturados.append((dict(facts), permitidos))
        return original(facts, booleanos=booleanos, permitidos=permitidos, **kw)

    monkeypatch.setattr(marina_graph, "render_fatos_para_prompt", _espia)
    llm = _CapturaLLM()
    dossie = await _graph(llm)._build_dossier(state, route="human_review")
    assert len(llm.prompts) == 1 and len(capturados) == 1
    fatos_prompt, permitidos = capturados[0]
    return dossie, llm.prompts[0], fatos_prompt, permitidos


def test_as_listas_fechadas_sao_as_esperadas() -> None:
    """Muda uma lista sem mudar este teste e ele falha — de proposito."""
    assert ESPERADO["contas"] == _PERMITIDOS_PROMPT_CONTAS
    assert ESPERADO["recurso"] == _PERMITIDOS_PROMPT_RECURSO
    assert ESPERADO["reembolso"] == _PERMITIDOS_PROMPT_REEMBOLSO


@pytest.mark.parametrize("flow", FLUXOS)
def test_a_lista_fechada_nao_tem_campo_que_nunca_entra(flow: str) -> None:
    assert not ESPERADO[flow] & NUNCA_ENTRA


@pytest.mark.parametrize("flow", FLUXOS)
async def test_o_prompt_nao_leva_guia_nem_cid(flow: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _, prompt, _, _ = await _dossie_e_prompt(_estado(flow), monkeypatch)

    assert GUIA_SINTETICA not in prompt
    assert CID_SINTETICO not in prompt
    assert "numero_guia_tiss" not in prompt
    assert "cid10" not in prompt


@pytest.mark.parametrize("flow", FLUXOS)
async def test_as_chaves_do_prompt_sao_exatamente_a_lista(flow: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, fatos_prompt, permitidos = await _dossie_e_prompt(_estado(flow), monkeypatch)

    assert set(fatos_prompt) == ESPERADO[flow]
    assert permitidos == ESPERADO[flow]


@pytest.mark.parametrize("flow", FLUXOS)
async def test_todo_fato_do_dossie_esta_classificado(flow: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chave nova em `fatos` tem de ser classificada: vai ao prompt (lista) ou fica so' no dossie."""
    dossie, _, _, _ = await _dossie_e_prompt(_estado(flow), monkeypatch)

    assert set(dossie["fatos"]) == ESPERADO[flow] | SO_NO_DOSSIE[flow]


@pytest.mark.parametrize("flow", FLUXOS)
@pytest.mark.parametrize("chave", sorted(NUNCA_ENTRA))
async def test_sentinela_de_cada_campo_que_nunca_entra(
    flow: str, chave: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valor sentinela plantado em UM campo "nunca entra" de cada vez nao aparece no prompt.

    O estado recebe o nome mesmo que `MarinaState` nao o declare: se um dia um montador de fatos
    passar a le-lo, a sentinela denuncia.
    """
    sentinela = f"SENTINELA-{chave.upper()}-0001"
    valor: Any = sentinela
    if chave.endswith("_refs"):
        valor = [{"ref": sentinela}]
    elif chave.endswith("_facts"):
        valor = {"x": sentinela}

    _, prompt, fatos_prompt, _ = await _dossie_e_prompt(_estado(flow, **{chave: valor}), monkeypatch)

    assert chave not in fatos_prompt
    assert sentinela not in prompt


@pytest.mark.parametrize("flow", FLUXOS)
async def test_o_dossie_do_humano_segue_com_guia_e_cid(flow: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A minimizacao e' do PROMPT: o dossie e' o dicionario completo do montador do fluxo."""
    state = _estado(flow)
    dossie, _, _, _ = await _dossie_e_prompt(state, monkeypatch)

    montador = {
        "contas": MarinaGraph._contas_facts,
        "recurso": MarinaGraph._recurso_facts,
        "reembolso": MarinaGraph._reembolso_facts,
    }[flow]
    assert dossie["fatos"] == montador(state)
    assert dossie["fatos"]["numero_guia_tiss"] == GUIA_SINTETICA
    if flow == "recurso":
        assert dossie["fatos"]["cid10"] == CID_SINTETICO


async def test_as_variaveis_do_processo_seguem_com_guia_e_cid_no_recurso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _estado("recurso")
    dossie, _, _, _ = await _dossie_e_prompt(state, monkeypatch)
    state["dossier"] = dossie

    variaveis = _graph(_CapturaLLM())._contract_variables(state)

    assert variaveis["numero_guia_tiss"] == GUIA_SINTETICA
    assert variaveis["cid10"] == CID_SINTETICO
    assert variaveis["dossie_marina"]["fatos"]["cid10"] == CID_SINTETICO
    assert variaveis["dossie_marina"]["fatos"]["numero_guia_tiss"] == GUIA_SINTETICA


async def test_as_variaveis_do_processo_seguem_com_guia_nas_contas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _estado("contas")
    dossie, _, _, _ = await _dossie_e_prompt(state, monkeypatch)
    state["dossier"] = dossie

    variaveis = _graph(_CapturaLLM())._contract_variables(state)

    assert variaveis["numero_guia_tiss"] == GUIA_SINTETICA
    assert variaveis["dossie_marina"]["fatos"]["numero_guia_tiss"] == GUIA_SINTETICA


def test_o_recorte_nao_muta_o_dicionario_do_dossie() -> None:
    fatos = {"numero_guia_tiss": GUIA_SINTETICA, "cid10": CID_SINTETICO, "sla_analise": "P5D"}
    recorte = fatos_para_prompt(fatos, _PERMITIDOS_PROMPT_RECURSO)

    assert recorte == {"sla_analise": "P5D"}
    assert fatos["cid10"] == CID_SINTETICO and fatos["numero_guia_tiss"] == GUIA_SINTETICA
