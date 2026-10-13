"""Contrato de EMPRESA no Lucas (decisao do dono, 13/10/2026).

A bateria de beneficiario simulado ouviu do Lucas "Sua mensalidade de outubro de 2026 e' de R$ 107.655,44":
a fatura da EMPRESA (todas as vidas do contrato) dita a um funcionario. A AMH passou a mandar o valor da
PESSOA e o `pagador_tipo`; aqui o Lucas o honra:

  - DMN: `pagador_empresa=true` + pergunta de cobranca -> RESPONDER (primeira linha), com qualquer
    `status_conciliado`/ciclos/atraso — que sao do titulo da empresa. Pergunta fora do vocabulario com
    ciclos >= 1 cai no catch-all (`ambiguidade`), NUNCA na regra de atraso (`inadimplencia`). PF continua
    como antes;
  - resposta: TEXTO FIXO "Sua mensalidade e' paga pela empresa contratante." — sem total, boleto,
    vencimento ou situacao da fatura da empresa; com a coparticipacao da pessoa SO' quando perguntada;
  - fonte AMH: `pagador_tipo` chega ao Lucas (inclusive sem os fatos de valor) e o boleto da empresa nao.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import FatosCobranca
from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
from maezo.agents.lucas.graph import (
    PROMPT_VERSIONS,
    TEXTO_EMPRESA_SEGUNDA_VIA,
    TEXTO_EMPRESA_VENCIMENTO,
    TEXTO_PAGADOR_EMPRESA,
    LucasGraph,
    LucasState,
    new_lucas_state,
    pagador_empresa,
)
from maezo.agents.lucas.prompts import PAGADOR_EMPRESA_TEXTO_VERSION, motivo_de_recusa
from maezo.ports.billing_status import BillingStatusView, BillingSummary, CompetenciaBilling
from maezo.ports.errors import PortResult
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_EXPLICACAO_EMPRESA = (
    f"{TEXTO_PAGADOR_EMPRESA} Por isso, a situação do pagamento fica com a empresa (normalmente o RH). Se "
    "quiser, posso te passar para alguém da nossa equipe."
)

#: A fatura da EMPRESA vencida (o caso da bateria): total, saldo e boleto sao do titulo da empresa.
_COMPETENCIAS_EMPRESA: list[dict[str, Any]] = [
    {
        "competencia": "2026-10",
        "situacao": "vencida",
        "vencimento": "2026-10-10",
        "valor_total": "107655.44",
        "valor_coparticipacao": "35.20",
        "valor_saldo": "107655.44",
    },
    {
        "competencia": "2026-09",
        "situacao": "paga",
        "vencimento": "2026-09-10",
        "valor_total": "320.25",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "0.00",
        "liquidado_em": "2026-09-08",
    },
]

_FATOS_EMPRESA: dict[str, Any] = {
    "status_conciliado": False,
    "ciclos_sem_conciliacao": 2,
    "numero_boleto": "",
    "cnab_ref": "amh-billing:2026-10-12",
    "valor_em_aberto": "107655.44",
    "dias_atraso_max": 3,
    "vencimento_referencia": "2026-10-10",
    "competencias_cobranca": _COMPETENCIAS_EMPRESA,
    "pagador_tipo": "pessoa_juridica",
}

_TIPOS_DE_COBRANCA = ("consulta_valores", "boleto", "2a_via", "vencimento", "status_pagamento")


class _Inferencia:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kw: Any) -> str:
        del kw
        self.prompts.append(prompt)
        return "Narrativa para o humano."


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


def _estado(tipo: str, *, intencao: str = "cobranca_info", **extra: Any) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_pj",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-pj",
            "to_hash": "hash-pj",
            "intencao": intencao,
            "tipo_solicitacao": tipo,
            **_FATOS_EMPRESA,
            **extra,
        }
    )


async def _turno(estado: LucasState) -> tuple[dict[str, Any], _Envio, _Inferencia]:
    envio = _Envio()
    inferencia = _Inferencia()
    grafo = LucasGraph(
        inference=inferencia,  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: date(2026, 10, 13),
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio, inferencia


async def _dmn(**entrada: Any) -> dict[str, Any]:
    base = {
        "status_conciliado": False,
        "ciclos_sem_conciliacao": 0,
        "valores_disponiveis": True,
        "valores_com_atraso": False,
        "pagador_empresa": False,
    }
    linhas, _ = await DmnDraftLocal().evaluate("lucas_billing_admissibility", {**base, **entrada})
    return linhas[0]


# --- DMN (XML vivo) ------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", _TIPOS_DE_COBRANCA)
@pytest.mark.parametrize(("ciclos", "atraso"), [(0, False), (0, True), (2, True)])
async def test_dmn_empresa_responde_sem_escalar_pelo_atraso_da_empresa(
    tipo: str, ciclos: int, atraso: bool
) -> None:
    linha = await _dmn(
        tipo_solicitacao=tipo, ciclos_sem_conciliacao=ciclos, valores_com_atraso=atraso, pagador_empresa=True
    )
    assert (linha["roteamento"], linha["categoria"]) == ("RESPONDER", "-")


async def test_dmn_empresa_fora_do_vocabulario_nunca_vira_inadimplencia() -> None:
    linha = await _dmn(tipo_solicitacao="", ciclos_sem_conciliacao=2, pagador_empresa=True)
    assert (linha["roteamento"], linha["categoria"]) == ("ESCALAR_HUMANO", "ambiguidade")


@pytest.mark.parametrize("tipo", ["consulta_valores", "status_pagamento", ""])
async def test_dmn_pessoa_fisica_com_atraso_continua_escalando(tipo: str) -> None:
    linha = await _dmn(tipo_solicitacao=tipo, ciclos_sem_conciliacao=1, pagador_empresa=False)
    assert (linha["roteamento"], linha["categoria"]) == ("ESCALAR_HUMANO", "inadimplencia")


# --- Grafo real ----------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", _TIPOS_DE_COBRANCA)
async def test_empresa_ouve_o_texto_fixo_e_nunca_a_fatura_da_empresa(tipo: str) -> None:
    final, envio, inferencia = await _turno(_estado(tipo))

    assert final["route"] == "respond_member"
    assert final["process_started"] is False
    # Rodada 4 (13/10/2026): a frase e a consequencia, uma vez — quem confirma o pagamento e' a empresa.
    # Rodada 8: por assunto — 2a via e vencimento/boleto tem o texto proprio (a empresa e' quem os tem).
    esperado = {
        "boleto": TEXTO_EMPRESA_SEGUNDA_VIA,
        "2a_via": TEXTO_EMPRESA_SEGUNDA_VIA,
        "vencimento": TEXTO_EMPRESA_VENCIMENTO,
    }.get(tipo, _EXPLICACAO_EMPRESA)
    assert envio.textos == [esperado]
    assert final["mensagem"]["prompt_version"] == PAGADOR_EMPRESA_TEXTO_VERSION
    assert inferencia.prompts == []
    texto = envio.textos[0]
    for proibido in ("107", "R$", "vencid", "atraso", "10/10"):
        assert proibido not in texto


async def test_empresa_inadimplencia_da_empresa_nao_escala_o_beneficiario() -> None:
    final, envio, _ = await _turno(_estado("status_pagamento", intencao="inadimplencia"))
    assert final["route"] == "respond_member"
    assert final.get("motivo_humano") != "inadimplencia_detectada"
    assert envio.textos == [_EXPLICACAO_EMPRESA]


async def test_empresa_coparticipacao_e_da_pessoa_e_aparece_quando_perguntada() -> None:
    final, envio, _ = await _turno(_estado("consulta_valores", cobranca_foco="coparticipacao"))
    [texto] = envio.textos
    assert texto == (
        # Rodada 5 (13/10/2026): so' o fato da coparticipacao, sem a frase da empresa.
        "Em outubro de 2026 sua coparticipação foi de R$ 35,20. (dados de 12/10)"
    )
    assert final["mensagem"]["recusa_de_saida"] is False
    assert "107" not in texto
    # Os fatos da cerca levam SO' a coparticipacao: o total da empresa nunca entra.
    assert final["mensagem"]["fatos"]["competencias"] == [
        {"competencia": "2026-10", "valor_coparticipacao": "35.20"}
    ]


async def test_empresa_coparticipacao_do_mes_pedido() -> None:
    _, envio, _ = await _turno(
        _estado("consulta_valores", cobranca_foco="coparticipacao", competencia="2026-09")
    )
    assert envio.textos == ["Em setembro de 2026 você não teve coparticipação: R$ 0,00. (dados de 12/10)"]


def test_a_cerca_recusa_o_total_da_empresa_nos_fatos_do_texto_fixo() -> None:
    fatos: dict[str, object] = {"pagador_tipo": "pessoa_juridica", "foco": None, "competencias": []}
    assert motivo_de_recusa(TEXTO_PAGADOR_EMPRESA, "mensagem", fatos) is None
    assert (
        motivo_de_recusa(f"{TEXTO_PAGADOR_EMPRESA} O total é R$ 107.655,44.", "mensagem", fatos) is not None
    )


@pytest.mark.parametrize(
    ("tipo", "esperado"), [("pessoa_juridica", True), ("pessoa_fisica", False), (None, False)]
)
def test_so_pessoa_juridica_e_empresa(tipo: str | None, esperado: bool) -> None:
    estado: dict[str, Any] = {} if tipo is None else {"pagador_tipo": tipo}
    assert pagador_empresa(estado) is esperado  # type: ignore[arg-type]


def test_a_versao_do_texto_esta_exportada() -> None:
    assert PROMPT_VERSIONS["pagador_empresa"] == PAGADOR_EMPRESA_TEXTO_VERSION == "pagador-empresa-v1"


# --- Fonte AMH -----------------------------------------------------------------------------------

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _view(pagador: str) -> BillingStatusView:
    return BillingStatusView(
        portable_subject_ref=REF,
        as_of="2026-10-13T12:00:00Z",
        fonte_atualizada_em="2026-10-12T09:00:00Z",
        resumo=BillingSummary(
            status_conciliado=False,
            ciclos_sem_conciliacao=1,
            valor_em_aberto="320.25",
            dias_atraso_max=3,
            pagador_tipo=pagador,
            criterio_conciliacao="situacao_paga_ou_liquidada",
        ),
        competencias=(
            CompetenciaBilling(
                competencia="2026-10",
                parcela=10,
                vencimento="2026-10-10",
                situacao="vencida",
                valor_total="320.25",
                valor_coparticipacao="35.20",
                valor_saldo="320.25",
                liquidado_em=None,
                boleto_numero_mascarado="****4821",
                boleto_disponivel_online=None,
            ),
        ),
        campos_ausentes=frozenset(),
    )


class _Billing:
    def __init__(self, view: BillingStatusView) -> None:
        self.view = view

    async def get_billing_status(self, ref: str, **kw: Any) -> PortResult[BillingStatusView]:
        del ref, kw
        return PortResult.ok(self.view)


class _Resolvedor:
    identidade_verificada = True

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        del pseudo_id, phone_hash
        return REF


class _Consentimento:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        del portable_ref, purpose_of_use
        return "consent-1"


def _fonte(pagador: str, *, valores: bool) -> FonteCobrancaAmh:
    billing: Any = _Billing(_view(pagador))
    return FonteCobrancaAmh(
        billing=billing,
        resolvedor=_Resolvedor(),
        consentimento=_Consentimento(),
        purpose_of_use="purpose1",
        fatos_de_valor=valores,
    )


@pytest.mark.parametrize("valores", [True, False])
async def test_fonte_empresa_leva_o_pagador_e_nao_o_boleto_da_empresa(valores: bool) -> None:
    fatos = await _fonte("pessoa_juridica", valores=valores).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    entrada = fatos.como_entrada_lucas()
    assert entrada["pagador_tipo"] == "pessoa_juridica"
    assert entrada["numero_boleto"] == ""
    assert all("boleto" not in c for c in entrada.get("competencias_cobranca", []))


async def test_fonte_pessoa_fisica_mantem_o_boleto() -> None:
    fatos = await _fonte("pessoa_fisica", valores=True).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    entrada = fatos.como_entrada_lucas()
    assert entrada["pagador_tipo"] == "pessoa_fisica"
    assert entrada["numero_boleto"] == "****4821"
    assert entrada["competencias_cobranca"][0]["boleto"] == "****4821"


@pytest.mark.parametrize("pagador", ["desconhecido", "empresa"])
async def test_fonte_pagador_desconhecido_ou_fora_do_enum_nao_cria_a_chave(pagador: str) -> None:
    fatos = await _fonte(pagador, valores=True).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert fatos.pagador_tipo == "desconhecido"
    assert "pagador_tipo" not in fatos.como_entrada_lucas()
