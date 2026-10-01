"""A bateria do Lucas (`maezo.agents.lucas.bateria`) rodada OFFLINE no grafo real.

O que e' real aqui: o grafo do Lucas e as DUAS tabelas de decisao (avaliadas a partir dos `.dmn`
vivos, nao de linhas fixas). O que e' falso: modelo, WhatsApp, motor e auditoria — so' o que o
ambiente de teste nao pode ter. O objetivo e' provar que as HIPOTESES dos casos batem com as
tabelas ANTES de gastar uma execucao no ambiente de desenvolvimento.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from maezo.agents.lucas.bateria import GravadorDeEnvios, executar_caso, veredito
from maezo.agents.lucas.bateria_casos import CASOS
from maezo.agents.lucas.graph import LucasGraph
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table


class _DmnVivo:
    """Avalia as tabelas `.dmn` reais (primeiro acerto), no formato que o grafo espera."""

    def __init__(self) -> None:
        self._tabelas = {
            chave: read_live_table(DMN_DIR / f"{chave}.dmn")
            for chave in ("lucas_billing_admissibility", "lucas_escalation_routing")
        }

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        key = decision_key
        tabela = self._tabelas.get(key)
        if tabela is None:
            raise DmnEvaluationError(f"tabela desconhecida: {key}")
        veredito_ = evaluate(tabela, {nome: variables[nome] for nome in tabela.input_names})
        return [veredito_.saidas], DmnVersion(key=key, id=f"{key}:1:offline", version=1, deployment_id="offline")


class _Modelo:
    async def generate(self, prompt: str, **_: Any) -> str:
        return "Recebemos sua mensagem e vamos te ajudar com as informacoes da sua cobranca."


class _Zap:
    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        return {"suppressed_synthetic": True}


def _grafo() -> tuple[Any, GravadorDeEnvios]:
    gravador = GravadorDeEnvios(_Zap())
    grafo = LucasGraph(
        inference=_Modelo(),  # type: ignore[arg-type]
        dmn=_DmnVivo(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=gravador,  # type: ignore[arg-type]
    )
    return grafo.compile_graph().compile(), gravador


def test_ids_dos_casos_sao_unicos_e_cada_um_tem_hipotese() -> None:
    ids = [c["id"] for c in CASOS]
    assert len(ids) == len(set(ids))
    assert all(c["esperado"] for c in CASOS)


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
async def test_cada_caso_roda_no_grafo_real_sem_excecao(caso: dict[str, Any]) -> None:
    grafo, gravador = _grafo()
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    assert "erro_da_execucao" not in resultado, resultado.get("erro_da_execucao")


#: ACHADO ABERTO (bateria do Lucas, 01/10/2026): quando a tabela `lucas_billing_admissibility` cai no
#: catch-all "ambiguidade de cobranca" (L07, L09, L18), o grafo mapeia QUALQUER `ESCALAR_HUMANO` para
#: `motivo_humano="inadimplencia_detectada"` — o humano recebe um caso rotulado como inadimplencia
#: sem haver indicio de atraso. A tabela nao expoe um token que distinga as duas regras, entao a
#: correcao e' uma mudanca de DMN (sign-off do financeiro/PO), nao deste PR. Este teste FIXA o achado:
#: quando for corrigido, ele cai e deve ser atualizado junto com os casos.
_ACHADOS_ABERTOS = {"L07", "L09", "L18"}


async def test_veredito_offline_contra_as_tabelas_vivas() -> None:
    """Todo caso fecha OK, exceto os achados abertos acima — que DEVEM divergir, e so' neles."""
    divergentes: set[str] = set()
    for indice, caso in enumerate(CASOS):
        grafo, gravador = _grafo()
        resultado = await executar_caso(grafo, gravador, caso, indice=indice, execucao="offline")
        status, div, obs = veredito(caso, resultado)
        if status != "OK":
            divergentes.add(caso["id"])
            assert status == "DIVERGE", (caso["id"], obs)
    assert divergentes == _ACHADOS_ABERTOS


async def test_ciclos_ilegiveis_escalam_para_humano_em_vez_de_derrubar_o_turno() -> None:
    caso = next(c for c in CASOS if c["id"] == "L24")
    grafo, gravador = _grafo()
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    assert resultado["final"]["route"] == "escalate_human"
    assert resultado["final"]["motivo_humano"] == "ambiguidade"
    assert resultado["final"]["process_started"] is True
