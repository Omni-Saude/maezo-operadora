"""F2 FECHADA (avaliacao r4 de 22/10/2026): com o roteador ativo, a habilidade escolhida limita as intencoes
do turno e as regras de palavra que levariam o turno para OUTRA habilidade nao competem mais (ex.: a pergunta
de saude que cita "plano" nao vira o despejo dos dados do cadastro).

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _starts
from tests.unit.agents.test_helena_roteador_ativo import _decisao, _grafo, _Modelo


def _eventos(logs: list[dict[str, object]]) -> set[object]:
    return {e.get("event") for e in logs}


@pytest.mark.parametrize(
    "fala",
    [
        "quais exames de prevenção são indicados na minha idade, pelo plano?",
        "que vacinas um adulto deve tomar? meu plano tem isso?",
        "pra que serve o exame de ferritina que meu plano pediu?",
    ],
)
async def test_saude_que_cita_o_plano_nao_vira_consulta_do_cadastro(fala: str) -> None:
    modelo = _Modelo([_decisao("saude")], [_classify(intent="information"), "", ""])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada(fala))
    assert "helena_pergunta_do_plano_vai_a_consulta" not in _eventos(logs)
    assert not _eventos(logs) & {"helena_consulta_plano", "helena_consulta_recusada"}
    assert _starts(cib) == 0


@pytest.mark.parametrize(
    "fala",
    [
        "ainda estou em carência de alguma coisa?",
        "minha carência de parto já acabou?",
        "falta carência pra cirurgia?",
    ],
)
async def test_plano_com_intencao_de_fora_vai_a_consulta_do_plano(fala: str) -> None:
    modelo = _Modelo([_decisao("plano")], [_classify(intent="information")])
    grafo, _whatsapp, _cib, _fonte = _grafo(modelo)
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(fala))
    assert "helena_habilidade_fecha_intencao" in _eventos(logs)
    assert resultado.get("intent") == "consulta_plano"
    # O subtipo fechado chega a consulta (nunca "fora do dominio"); a fonte de teste nao tem fatos.
    assert not any(e.get("motivo") == "subtipo_fora_do_dominio" for e in logs)
    assert _eventos(logs) & {"helena_consulta_plano", "helena_consulta_recusada"}


@pytest.mark.parametrize("fala", ["tudo certo, obrigada", "bom dia!", "ok, entendi"])
async def test_conversa_nao_vira_pedido_de_pessoa_nem_consulta(fala: str) -> None:
    modelo = _Modelo([_decisao("conversa")], [_classify(intent="human_request"), ""])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(fala))
    assert _starts(cib) == 0
    assert resultado.get("intent") in ("information", "greeting")
    assert "helena_consulta_plano" not in _eventos(logs)


@pytest.mark.parametrize(
    "fala", ["não quero falar com atendente agora", "ninguém precisa me ligar", "só queria entender melhor"]
)
async def test_equipe_nunca_forca_o_caso_sem_pedido_explicito(fala: str) -> None:
    modelo = _Modelo([_decisao("equipe")], [_classify(intent="greeting"), ""])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala))
    assert _starts(cib) == 0


async def test_sem_roteador_decidindo_as_regras_de_sempre_valem() -> None:
    from maezo.agents.helena.graph import HelenaGraph

    assert HelenaGraph._regra_vale("plano") is True


@pytest.mark.parametrize(
    "fala",
    [
        "Quando vence o boleto? E meu plano cobre dermatologista?",
        "qual o valor da minha mensalidade deste mes?",
        "me manda a segunda via, por favor",
    ],
)
async def test_cobranca_com_intencao_de_fora_vai_ao_lucas_com_subtipo(fala: str) -> None:
    modelo = _Modelo([_decisao("cobranca")], [_classify(intent="information")])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(fala))
    assert resultado.get("intent") == "cobranca" and resultado.get("cobranca_subtipo")
    assert not any(e.get("event") == "helena_handoff_recusado" for e in logs)
    assert _starts(cib) == 0


@pytest.mark.parametrize(
    "fala",
    [
        "Quero tirar uma dúvida sobre minha saúde",
        "tenho uma pergunta de saúde",
        "posso perguntar sobre saúde?",
    ],
)
async def test_saude_sem_sintoma_nunca_abre_caso_de_enfermagem(fala: str) -> None:
    modelo = _Modelo([_decisao("saude")], [_classify(intent="clinical_question"), "", ""])
    grafo, _whatsapp, cib, _fonte = _grafo(modelo)
    await grafo.ainvoke(_entrada(fala))
    assert _starts(cib) == 0
