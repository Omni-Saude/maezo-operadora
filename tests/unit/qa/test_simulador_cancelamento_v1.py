"""Roteiro de aceite `cancelamento_v1` (DL-0111) e a garantia de que NENHUM cancelamento real nasce dele.

O roteiro usa beneficiarios REAIS do roster. Nao ha' escrita no Tasy, entao o risco seria uma tarefa falsa na
fila do cadastro: o interceptador finge SP-OP-CANCEL-001 (marcando a instancia fingida como teste) e a
limpeza pergunta ao motor REAL, no fim, se alguma chave CANCEL vista no roteiro tem instancia viva.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas import cancelamento_pf as cpf
from maezo.agents.lucas.graph import TEXTO_CONFIRMA_CANCELAMENTO
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.qa.simulador import aceite
from maezo.qa.simulador import composicao as comp
from maezo.qa.simulador.limpeza import Limpeza
from maezo.qa.simulador.registro import Sujeito
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport

_P = cpf.protocolo_do_cancelamento("simulador-1")


def _seam() -> SeamContext:
    return build_agent_seam_context(tenant="amh", agent_id="lucas")


# --- o roteiro --------------------------------------------------------------------------------------------


def test_o_roteiro_existe_compila_e_conversa_com_um_titular_pf() -> None:
    roteiro = aceite.ROTEIROS["cancelamento_v1"]
    aceite.validar(roteiro)
    assert aceite.SUJEITO_DO_ROTEIRO["cancelamento_v1"] == aceite.SUJEITO_PF_TITULAR
    assert "dono_v1" not in aceite.SUJEITO_DO_ROTEIRO  # os outros roteiros seguem com o dono
    assert all(s.resolveu for s in roteiro), "todo segmento declara o criterio RESOLVEU?"
    assert all(p.agente == "lucas" for s in roteiro for p in s.passos)


#: As respostas que o Lucas da' em cada passo, com a chave ligada (os textos REAIS do codigo).
_RESPOSTAS = {
    ("1-titular-pf-pede-cancelamento", 1): TEXTO_CONFIRMA_CANCELAMENTO,
    ("1-titular-pf-pede-cancelamento", 2): cpf.TEXTO_PEDIDO_RECEBIDO.format(protocolo=_P),
    ("1-titular-pf-pede-cancelamento", 3): TEXTO_CONFIRMA_CANCELAMENTO,
    ("1-titular-pf-pede-cancelamento", 4): cpf.TEXTO_PEDIDO_JA_REGISTRADO.format(protocolo=_P),
    ("2-titular-pf-desiste", 1): TEXTO_CONFIRMA_CANCELAMENTO,
    ("2-titular-pf-desiste", 2): cpf.TEXTO_DESISTENCIA,
}
_ESCALACOES = {("1-titular-pf-pede-cancelamento", 2): [{"motivo": "cancelamento"}]}


def test_os_textos_reais_do_lucas_passam_no_roteiro() -> None:
    """O roteiro e o codigo concordam: cada passo aprova com o texto que o Lucas de fato envia."""
    for segmento in aceite.CANCELAMENTO_V1:
        anterior = None
        for i, passo in enumerate(segmento.passos, start=1):
            resposta = _RESPOSTAS[(segmento.nome, i)]
            falhas = aceite.avaliar_passo(
                passo, [("lucas", resposta)], _ESCALACOES.get((segmento.nome, i), []), anterior
            )
            assert falhas == [], (segmento.nome, i, falhas)
            anterior = resposta


@pytest.mark.parametrize(
    "ruim",
    [
        "Pronto, o seu plano foi cancelado. Protocolo: " + _P + ".",
        "Recebemos o seu pedido de cancelamento.",  # sem protocolo
        "Recebemos o seu pedido de cancelamento. Protocolo: " + _P + ". Antes, temos um desconto.",
    ],
)
def test_o_que_nao_pode_reprova_o_passo_do_recebimento(ruim: str) -> None:
    passo = aceite.CANCELAMENTO_V1[0].passos[1]
    assert aceite.avaliar_passo(passo, [("lucas", ruim)], [{"motivo": "cancelamento"}], None)


def test_o_relatorio_traz_o_resolveu() -> None:
    resultado = aceite.ResultadoDoSegmento(nome="1-titular-pf-pede-cancelamento", telefone_rotulo="r1")
    sujeito = Sujeito(rotulo="s1", cpf="00000000000", nascimento=date(2000, 1, 1), nome="Pessoa Sintetica")
    criterios = {s.nome: s.resolveu for s in aceite.CANCELAMENTO_V1}
    dados = aceite.como_json([resultado], sujeito, criterios=criterios)
    assert dados["segmentos"][0]["resolveu"] == criterios["1-titular-pf-pede-cancelamento"]
    assert "RESOLVEU? SIM" in aceite.secao_do_relatorio(dados, "cancelamento_v1")
    # Sem criterio (os roteiros de antes): a chave nem aparece.
    assert "resolveu" not in aceite.como_json([resultado], sujeito)["segmentos"][0]


# --- nenhum cancelamento real -------------------------------------------------------------------------------


async def test_o_interceptador_marca_o_pedido_fingido_como_teste_e_lembra_a_chave() -> None:
    registro = comp.RegistroDeEscalonamentos()
    real = FakeCibSevenTransport()
    motor = gate_cibseven(comp.InterceptorDeEscalonamento(real, registro), _seam())
    chave = "CANCEL-amh-pseudo-roteiro"
    await motor.start_process_instance(comp.PROCESSO_CANCELAMENTO, chave, {"source_agent_id": "lucas"})
    status = await motor.inner.get_process_status(chave)
    assert status.variables["instancia_de_teste"] is True
    assert status.variables["marcador_teste"] == comp.MARCADOR_DE_TESTE
    assert registro.cancelamentos_registrados == {chave}
    registro.esquecer("amh", "wa:amh:hk1_x")  # conversa nova: o caso fingido some...
    assert registro.ativa(chave) is None
    assert registro.cancelamentos_registrados == {chave}  # ...mas a limpeza ainda confere a chave
    assert await real.find_active_instance(chave) is None  # o motor real nunca viu nada


async def test_reivindicacao_de_execucao_anterior_nao_trava_o_portao_no_simulador() -> None:
    """SP-OP-CANCEL-001 e' EXCLUSIVE: para uma chave CANCEL sem caso fingido, a geracao anterior ENCERROU."""
    interceptador = comp.InterceptorDeEscalonamento(FakeCibSevenTransport(), comp.RegistroDeEscalonamentos())
    achada = await interceptador.find_any_instance("CANCEL-amh-pseudo-antigo")
    assert achada is not None and achada.state == "COMPLETED"
    assert await interceptador.find_active_instance("CANCEL-amh-pseudo-antigo") is None


class _Motor:
    def __init__(self, vivas: set[str]) -> None:
        self.vivas = vivas
        self.perguntas: list[str] = []

    async def find_active_instance(self, business_key: str) -> object | None:
        self.perguntas.append(business_key)
        return object() if business_key in self.vivas else None


class _Pool:
    def acquire(self) -> Any:
        class _Ctx:
            async def __aenter__(self) -> Any:
                class _C:
                    async def execute(self, sql: str, *args: Any) -> str:
                        return "DELETE 0"

                return _C()

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()

    async def close(self) -> None:
        return None


def _limpeza(registro: comp.RegistroDeEscalonamentos, motor: _Motor) -> Limpeza:
    return Limpeza(
        dsn="postgresql+asyncpg://u:p@h/db",
        tenant_id="amh",
        saver=None,
        escalonamentos=registro,
        motor_real=motor,
        pool=_Pool(),
    )


async def test_a_limpeza_confere_no_motor_real_os_pedidos_de_cancelamento_do_roteiro() -> None:
    registro = comp.RegistroDeEscalonamentos()
    registro.registrar("CANCEL-amh-pseudo-roteiro", {}, comp.PROCESSO_CANCELAMENTO)
    motor = _Motor(set())
    resultado = await _limpeza(registro, motor).limpar_execucao(["wa:amh:hk1_abc"])
    assert "CANCEL-amh-pseudo-roteiro" in motor.perguntas
    assert resultado.cancelamentos_no_motor == 0 and resultado.motor_limpo
    assert resultado.como_dict()["cancelamentos_no_motor"] == 0


async def test_um_pedido_real_vivo_deixa_o_motor_sujo_e_reprova() -> None:
    registro = comp.RegistroDeEscalonamentos()
    registro.registrar("CANCEL-amh-pseudo-roteiro", {}, comp.PROCESSO_CANCELAMENTO)
    resultado = await _limpeza(registro, _Motor({"CANCEL-amh-pseudo-roteiro"})).limpar_execucao(
        ["wa:amh:hk1_abc"]
    )
    assert resultado.cancelamentos_no_motor == 1 and not resultado.motor_limpo
