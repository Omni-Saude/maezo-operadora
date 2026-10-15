"""DL-0111 (`MAEZO_CANCEL_PF_V1`): desistencia do cancelamento com a pergunta de confirmacao pendente.

O defeito (roteiro `cancelamento_v1`, 15/10/2026): "Nao, deixa pra la" depois de "Voce quer pedir para
cancelar o seu plano?" era lido pela Helena como encerramento e ela agradecia, sem confirmar nada. Com a chave
ligada e a pergunta pendente, a desistencia vai ao Lucas ANTES da Helena (atalho deterministico). Sem pendencia,
ou com a chave desligada, o caminho de sempre.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import TEXTO_CONFIRMA_CANCELAMENTO, ref_de_resposta
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
from maezo.platform.webhooks.whatsapp.pre_roteamento import SinaisLexicos
from maezo.platform.webhooks.whatsapp.roteamento import ContextoDoTurno, MemoriaDoLucas
from maezo.runtime.cobranca_intencao import e_desistencia_do_cancelamento

_CONVERSA = "wa:amh:hk1_desistencia"


class _Roteador:
    def __init__(self, contexto: ContextoDoTurno) -> None:
        self._contexto = contexto
        self.leituras = 0

    async def contexto_do_turno(self, conversation_id: str) -> ContextoDoTurno:
        del conversation_id
        self.leituras += 1
        return self._contexto


class _Lucas:
    def __init__(self, *, v1: bool) -> None:
        self.cancelamento_pf_v1 = v1


def _pendente() -> ContextoDoTurno:
    return ContextoDoTurno(
        assunto_anterior="cancelamento",
        memoria=MemoriaDoLucas(ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO), 0),
    )


def _sinais(*, pessoa: bool = False, saude: bool = False) -> SinaisLexicos:
    return SinaisLexicos(
        pedido_humano=pessoa, sinal_saude=saude, termos_pedido_humano=0, termos_sinal_saude=0, versao="t"
    )


def _dispatcher(*, v1: bool, contexto: ContextoDoTurno | None = None) -> tuple[HelenaDispatcher, _Roteador]:
    roteador = _Roteador(contexto if contexto is not None else _pendente())
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=None,  # type: ignore[arg-type]
        dmn=None,  # type: ignore[arg-type]
        cibseven=None,  # type: ignore[arg-type]
        whatsapp_client=None,  # type: ignore[arg-type]
        pseudonymizer=None,  # type: ignore[arg-type]
        audit_sink=None,  # type: ignore[arg-type]
        roteador=roteador,  # type: ignore[arg-type]
        lucas_turno=_Lucas(v1=v1),  # type: ignore[arg-type]
    )
    return dispatcher, roteador


@pytest.mark.parametrize(
    "texto",
    ["Não", "não", "Não, deixa pra lá", "esquece", "melhor não", "mudei de ideia", "não quero mais"],
)
async def test_desistencia_com_confirmacao_pendente_vai_direto_ao_lucas(texto: str) -> None:
    dispatcher, _ = _dispatcher(v1=True)
    atalho: Any = await dispatcher._atalho_de_desistencia(texto, _CONVERSA, _sinais())
    assert atalho is not None
    handoff = atalho["handoff"]
    assert handoff["para"] == "lucas" and handoff["cobranca_subtipo"] == "cancelamento"
    assert handoff["cobranca_confirmacao"] == "nega"
    assert atalho["response_text"] == ""  # a Helena nao diz nada: quem responde e' o Lucas


async def test_chave_desligada_nao_tem_atalho() -> None:
    dispatcher, roteador = _dispatcher(v1=False)
    assert await dispatcher._atalho_de_desistencia("Não, deixa pra lá", _CONVERSA, _sinais()) is None
    assert roteador.leituras == 0


@pytest.mark.parametrize("contexto", [ContextoDoTurno(), ContextoDoTurno(assunto_anterior="cancelamento")])
async def test_nao_sem_confirmacao_pendente_segue_o_fluxo_normal(contexto: ContextoDoTurno) -> None:
    dispatcher, _ = _dispatcher(v1=True, contexto=contexto)
    assert await dispatcher._atalho_de_desistencia("Não", _CONVERSA, _sinais()) is None


async def test_assunto_anterior_de_outro_tema_nao_ativa_o_atalho() -> None:
    contexto = ContextoDoTurno(
        assunto_anterior="consulta_valores",
        memoria=MemoriaDoLucas(ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO), 0),
    )
    dispatcher, _ = _dispatcher(v1=True, contexto=contexto)
    assert await dispatcher._atalho_de_desistencia("Não", _CONVERSA, _sinais()) is None


@pytest.mark.parametrize("texto", ["SIM, QUERO CANCELAR", "sim", "quero cancelar", "obrigado"])
async def test_resposta_afirmativa_ou_outra_mensagem_nao_e_desistencia(texto: str) -> None:
    dispatcher, roteador = _dispatcher(v1=True)
    assert await dispatcher._atalho_de_desistencia(texto, _CONVERSA, _sinais()) is None
    assert roteador.leituras == 0


@pytest.mark.parametrize("sinais", [None, _sinais(pessoa=True), _sinais(saude=True)])
async def test_sinal_de_saude_ou_pedido_de_pessoa_ou_lexico_falho_seguem_a_helena(
    sinais: SinaisLexicos | None,
) -> None:
    dispatcher, _ = _dispatcher(v1=True)
    assert await dispatcher._atalho_de_desistencia("Não", _CONVERSA, sinais) is None


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Não", True),
        ("Não, deixa pra lá", True),
        ("esquece", True),
        ("melhor não", True),
        ("mudei de ideia", True),
        ("não quero mais", True),
        ("SIM, QUERO CANCELAR", False),
        ("sim, quero cancelar", False),
        ("não, sim quero cancelar", False),
        ("qual o valor da mensalidade?", False),
        ("obrigado", False),
    ],
)
def test_lexico_da_desistencia(texto: str, esperado: bool) -> None:
    assert e_desistencia_do_cancelamento(texto) is esperado
