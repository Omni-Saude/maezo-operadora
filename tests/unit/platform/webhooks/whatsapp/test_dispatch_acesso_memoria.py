"""Revisao do PR #700 (P2): o checkpoint da Helena e' indexado pelo TELEFONE. Quando a pessoa verificada muda
(telefone compartilhado) ou o consentimento e' revogado, o despachante APAGA a memoria da conversa (historico
curto DL-0080, memoria clinica, coleta) antes de gravar o estado do acesso — a pessoa nova nunca herda a da
anterior. Grafo REAL da Helena sobre um `InMemorySaver` (o LLM explode se for chamado: nenhum agente roda).
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena.graph import memoria_de_conversa_neutra
from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from tests.unit.platform.webhooks.whatsapp.test_acesso import CPF
from tests.unit.platform.webhooks.whatsapp.test_dispatch_acesso import _m, _montar, _verificar

_MEMORIA_DO_TITULAR: dict[str, Any] = {
    "historico_conversa": [{"papel": "beneficiario", "texto": "segredo clinico do titular"}],
    "memoria_clinica": {"population": "adulto", "idade_anos": 41},
    "coleta_contexto": "dor no peito ha 2 dias",
    "coleta_rodadas": 2,
}


async def _semear(d: Any, conversation_id: str) -> Any:
    compiled, _ = d._compile_turn_graph(object())
    config = checkpoint_thread_config(conversation_id)
    await compiled.aupdate_state(config, _MEMORIA_DO_TITULAR, as_node="respond")
    return compiled, config


async def test_revogar_apaga_historico_e_memoria_clinica_do_checkpoint() -> None:
    d, _, _ = _montar()
    d.checkpointer = Checkpointer(saver=InMemorySaver())
    await _verificar(d)
    conversation_id = (await d.dispatch(_m("REVOGAR", 9)))["conversation_id"]
    # Reproduz o estado ANTES da correcao: a memoria do titular no checkpoint da conversa.
    compiled, config = await _semear(d, conversation_id)
    assert (await compiled.aget_state(config)).values["historico_conversa"]
    await d.dispatch(_m("ACEITO", 10))
    await d.dispatch(_m(CPF, 11))
    r = await d.dispatch(_m("REVOGAR", 12))
    assert r["acesso_estado"] == ac.REVOGADO
    valores = (await compiled.aget_state(config)).values
    neutra = memoria_de_conversa_neutra()
    for campo in ("historico_conversa", "memoria_clinica", "coleta_contexto", "coleta_rodadas"):
        assert valores[campo] == neutra[campo], campo
    assert "segredo clinico do titular" not in repr(valores)
    assert not (await compiled.aget_state(config)).next  # nenhum no' pendente: o proximo turno recomeca


async def test_falha_ao_apagar_a_memoria_nao_grava_o_estado_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    d, _, extras = _montar()
    d.checkpointer = Checkpointer(saver=InMemorySaver())
    await _verificar(d)

    async def _explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("checkpoint fora")

    monkeypatch.setattr(d, "_esquecer_memoria_da_conversa", _explode)
    with pytest.raises(RuntimeError):
        await d.dispatch(_m("REVOGAR", 9))
    [linha] = list(extras["servico"]._store._linhas.values())
    assert linha.estado == ac.VERIFICADO  # a reentrega refaz o REVOGAR inteiro


async def test_sem_checkpointer_nada_a_apagar_e_o_turno_segue() -> None:
    d, cliente, _ = _montar()
    await _verificar(d)
    r = await d.dispatch(_m("REVOGAR", 9))
    assert r["acesso_estado"] == ac.REVOGADO and cliente.sent[-1][1] == ac.RESPOSTA_REVOGADO


def test_memoria_neutra_cobre_toda_a_memoria_de_conversa() -> None:
    from maezo.agents.helena import graph

    assert set(memoria_de_conversa_neutra()) == set(graph._HELENA_MEMORIA_DE_CONVERSA)
    assert {"historico_conversa", "memoria_clinica"} <= set(memoria_de_conversa_neutra())


async def test_revogacao_pendente_no_teto_abre_escalonamento_com_o_resumo_da_revogacao() -> None:
    """Re-revisao do #700 (1): no teto de regravacoes da revogacao o despachante abre SP-OP-ESCALATION-001
    pelo caminho de atendente do acesso, com o resumo FIXO do motivo `revogacao_consentimento_pendente`."""
    from maezo.agents.helena.graph import RESUMO_REVOGACAO_PENDENTE

    d, cliente, ctx = _montar()
    await _verificar(d)
    ctx["consent"].falhar = True
    await d.dispatch(_m("REVOGAR", 9))
    abertos = []
    for n in range(10, 10 + 6):
        r = await d.dispatch(_m("ACEITO", n))
        assert r["response_text"] == ac.REVOGACAO_EM_PROCESSAMENTO and r["acesso_estado"] == ac.REVOGADO
        abertos.append(r["escalation_started"])
    assert abertos.count(True) == 1
    variaveis = repr(ctx["cib"]._variables)
    assert RESUMO_REVOGACAO_PENDENTE in variaveis and "solicitacao_humano" in variaveis
