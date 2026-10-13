"""O despachante leva o PEDIDO DE LISTA ao Lucas e o roteador lembra o mes RESPONDIDO (teste real do dono,
12/10/2026: "as ultimas tres pagas" virou um mes so'; "de qual ano mesmo??" reaproveitou `None`)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from maezo.platform.webhooks.whatsapp import lucas_turno as lt
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
from maezo.platform.webhooks.whatsapp.roteamento import ContextoDoTurno, MemoriaDoLucas


class _Roteador:
    def __init__(self, contexto: Any = None, falha: bool = False) -> None:
        self._contexto = contexto
        self._falha = falha
        self.lidas = 0

    async def contexto_do_lucas(self, conversation_id: str) -> Any:
        self.lidas += 1
        if self._falha:
            raise RuntimeError("banco fora")
        return self._contexto


def _handoff(subtipo: str = "consulta_valores", competencia: str | None = None) -> dict[str, Any]:
    return {
        "para": "lucas",
        "cobranca_subtipo": subtipo,
        "competencia": competencia,
        "cobranca_foco": "geral",
        "message_ref": "hk1_ref",
    }


def _eu(roteador: _Roteador | None) -> Any:
    return SimpleNamespace(roteador=roteador, tenant_id="amh")


async def _com_lista(roteador: _Roteador | None, texto: str, handoff: dict[str, Any]) -> Any:
    return await HelenaDispatcher._handoff_com_lista(_eu(roteador), texto, "wa:amh:hk1_ab", handoff)


async def test_pergunta_do_dono_leva_o_pedido_de_lista() -> None:
    ajustado = await _com_lista(
        None, "Quais foram as últimas três mensalidades pagas por mim?", _handoff(competencia="2026-10")
    )
    assert ajustado["cobranca_lista"] == {"quantidade": 3, "de": None, "ate": None, "situacao": "paga"}
    assert ajustado["cobranca_subtipo"] == "consulta_valores"
    # O handoff ajustado continua valido para o `LucasTurno` (chave opcional, dominio fechado).
    assert lt.validar_handoff(ajustado).cobranca_lista is not None


async def test_pergunta_de_um_mes_nao_mexe_no_handoff() -> None:
    roteador = _Roteador(("consulta_valores", "2026-07"))
    original = _handoff()
    assert await _com_lista(roteador, "Qual o valor da minha última mensalidade?", original) is original
    assert roteador.lidas == 0


async def test_confirmacao_de_pagamento_em_lista_vira_consulta_de_valores() -> None:
    ajustado = await _com_lista(None, "já paguei as últimas três?", _handoff("confirmacao_pagamento"))
    assert ajustado["cobranca_subtipo"] == "consulta_valores"


async def test_insistencia_lida_como_outro_reaproveita_o_assunto_anterior() -> None:
    ajustado = await _com_lista(
        _Roteador(("consulta_valores", None)), "Mas eu te perguntei das últimas três", _handoff("outro")
    )
    assert ajustado["cobranca_subtipo"] == "consulta_valores"
    assert ajustado["cobranca_lista"]["quantidade"] == 3


@pytest.mark.parametrize("contexto", [None, ("contestacao", None)])
async def test_outro_sem_assunto_informativo_anterior_continua_outro(contexto: Any) -> None:
    ajustado = await _com_lista(_Roteador(contexto), "as últimas três", _handoff("outro"))
    assert ajustado["cobranca_subtipo"] == "outro"


async def test_falha_de_leitura_nao_derruba_a_lista() -> None:
    ajustado = await _com_lista(_Roteador(falha=True), "as últimas três", _handoff("outro"))
    assert ajustado["cobranca_subtipo"] == "outro" and "cobranca_lista" in ajustado


@pytest.mark.parametrize("subtipo", ["cancelamento", "contestacao", "cobranca_recebida", "boleto_2via"])
async def test_outros_assuntos_levam_a_lista_mas_nao_mudam(subtipo: str) -> None:
    ajustado = await _com_lista(None, "as últimas três", _handoff(subtipo))
    assert ajustado["cobranca_subtipo"] == subtipo


# --- O mes que o roteador lembra -----------------------------------------------------------------


def _final(*competencias: str, route: str = "respond_member", **extra_fatos: Any) -> dict[str, Any]:
    fatos = {"competencias": [{"competencia": c, "situacao": "paga"} for c in competencias], **extra_fatos}
    return {"route": route, "mensagem": {"recusa_de_saida": False, "fatos": fatos}}


def test_lembra_o_mes_respondido_quando_nenhum_foi_pedido() -> None:
    assert lt.competencia_a_lembrar(_final("2026-10"), None) == "2026-10"


def test_lista_respondida_nao_lembra_mes() -> None:
    assert lt.competencia_a_lembrar(_final("2026-10", "2026-09"), "2026-10") is None


def test_sem_resposta_informativa_lembra_o_pedido() -> None:
    assert lt.competencia_a_lembrar(_final("2026-10", route="escalate_human"), "2026-07") == "2026-07"
    assert lt.competencia_a_lembrar({"route": "respond_member"}, None) is None
    recusada = _final("2026-10")
    recusada["mensagem"]["recusa_de_saida"] = True
    assert lt.competencia_a_lembrar(recusada, "2026-07") == "2026-07"


async def test_de_qual_ano_depois_de_resposta_sem_mes_pedido_repete_o_mes_respondido() -> None:
    """A sequencia do dono: pergunta sem mes -> Lucas responde outubro -> "de qual ano mesmo??"."""
    lembrado = lt.competencia_a_lembrar(_final("2026-10"), None)
    ajustado = await HelenaDispatcher._handoff_com_esclarecimento(
        _eu(_Roteador(("consulta_valores", lembrado))),
        "de qual ano mesmo??",
        "wa:amh:hk1_ab",
        _handoff(),
    )
    assert ajustado["competencia"] == "2026-10"
    assert ajustado["cobranca_subtipo"] == "consulta_valores"


# --- Contexto do turno do Lucas (simulador, 13/10/2026) -------------------------------------------


class _RoteadorComContexto:
    def __init__(self, contexto: Any = None, falha: bool = False) -> None:
        self._contexto = contexto
        self._falha = falha

    async def contexto_do_turno(self, conversation_id: str) -> Any:
        if self._falha:
            raise RuntimeError("banco fora")
        return self._contexto


async def test_contexto_do_turno_chega_inteiro_ao_turno() -> None:
    contexto = ContextoDoTurno(
        revisao=7, assunto_anterior="boleto_2via", memoria=MemoriaDoLucas("0123456789abcdef", 1)
    )
    eu = SimpleNamespace(roteador=_RoteadorComContexto(contexto), tenant_id="amh")
    assert await HelenaDispatcher._contexto_do_turno_do_lucas(eu, "wa:amh:hk1_ab") == contexto


@pytest.mark.parametrize("roteador", [None, _RoteadorComContexto(falha=True)])
async def test_sem_roteador_ou_com_falha_o_contexto_e_o_neutro(roteador: Any) -> None:
    eu = SimpleNamespace(roteador=roteador, tenant_id="amh")
    assert await HelenaDispatcher._contexto_do_turno_do_lucas(eu, "wa:amh:hk1_ab") == ContextoDoTurno()
