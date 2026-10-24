"""PLANO e ATENDIMENTO HUMANO com os ajustes da etapa 7 (dono, 23/10/2026, DL-0154; Plano de ajustes v2,
etapa 8).

- orientacao do plano ("como peco reembolso?") com o roteador em `plano` e o modelo lendo orientacao: fica
  orientacao, sem o cadastro inteiro no lugar da resposta; carencia e carteirinha ditas continuam consulta;
- o "sim" a uma oferta e o pedido de pessoa lidos pelo modelo abrem o caso ("pode passar pra equipe sim");
  sem eles, nenhum caso;
- o "sim"/"nao" a "Quer que eu encerre o seu atendimento?" lido no contexto.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import roteador_v7 as rv7
from maezo.agents.helena.habilidades.atendimento_humano import escalar
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _starts
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _grafo, _Modelo


def _eventos(logs: list[dict[str, Any]]) -> set[str]:
    return {str(e.get("event")) for e in logs}


@pytest.mark.parametrize(
    "fala",
    [
        "como faço pra pedir reembolso de consulta?",
        "qual o telefone da central do plano?",
        "onde eu vejo a rede credenciada?",
    ],
)
async def test_orientacao_do_plano_nao_vira_o_cadastro(fala: str) -> None:
    modelo = _Modelo([_decisao("plano")], [_classify(intent="information"), "", ""])
    grafo, _whatsapp, cib, fonte = _grafo(modelo)
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(fala))
    assert resultado.get("intent") == "information"
    assert "helena_habilidade_fecha_intencao" not in _eventos(logs)
    assert fonte.pedidos == [] and _starts(cib) == 0


@pytest.mark.parametrize(
    "fala", ["ainda estou em carência de alguma coisa?", "qual o número da minha carteirinha?"]
)
async def test_carencia_e_carteirinha_ditas_continuam_consulta(fala: str) -> None:
    modelo = _Modelo([_decisao("plano")], [_classify(intent="information")])
    grafo, _whatsapp, _cib, _fonte = _grafo(modelo)
    resultado = await grafo.ainvoke(_entrada(fala))
    assert resultado.get("intent") == "consulta_plano"


def _leitura(**campos: Any) -> rv7.DecisaoV7:
    v7, ruins = rv7.ler_decisao(decisao_v7(**campos))
    assert v7 is not None, ruins
    return v7


@pytest.mark.parametrize(
    ("fala", "leitura", "esperado"),
    [
        ("pode encerrar, já resolvi", {"habilidade": "equipe", "responde": True, "sim_nao": "sim"}, "sim"),
        ("isso, pode fechar", {"habilidade": "equipe", "responde": True, "sim_nao": "sim"}, "sim"),
        ("deixa aberto por enquanto", {"habilidade": "equipe", "responde": True, "sim_nao": "nao"}, "nao"),
        ("hmm, não sei", {"habilidade": "equipe", "responde": True, "sim_nao": "sem_clareza"}, None),
    ],
)
def test_sim_ou_nao_ao_encerramento_no_contexto(
    fala: str, leitura: dict[str, Any], esperado: str | None
) -> None:
    marca = rv7.LEITURA_DO_TURNO.set(_leitura(**leitura))
    try:
        assert escalar._sim_ou_nao_ao_encerramento(fala) == esperado
    finally:
        rv7.LEITURA_DO_TURNO.reset(marca)


def test_sem_o_roteador_o_encerramento_le_as_respostas_curtas() -> None:
    assert escalar._sim_ou_nao_ao_encerramento("sim") == "sim"
    assert escalar._sim_ou_nao_ao_encerramento("nao") == "nao"
    assert escalar._sim_ou_nao_ao_encerramento("pode encerrar, já resolvi") is None


@pytest.mark.parametrize(
    "fala", ["pode passar pra equipe sim, mas rápido", "manda pra eles então", "pode ser, chama alguém"]
)
async def test_sim_lido_pelo_modelo_abre_o_caso_da_equipe(fala: str) -> None:
    modelo = _Modelo(
        [_decisao("equipe", responde=True, sim_nao="sim")], [_classify(intent="human_request"), ""]
    )
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada(fala))
    assert "helena_trava_caso_sem_pedido" not in _eventos(logs)
    assert _starts(cib) == 1


@pytest.mark.parametrize("fala", ["beleza, valeu", "ok, entendi", "tá bom então"])
async def test_cortesia_com_o_modelo_lendo_pedido_de_pessoa_nao_abre_caso_se_recusa(fala: str) -> None:
    modelo = _Modelo(
        [_decisao("equipe", recusa=True, sim_nao="nao")], [_classify(intent="human_request"), ""]
    )
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala))
    assert _starts(cib) == 0
