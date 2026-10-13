"""Teste real do dono (12/10/2026): o Lucas LISTA varios meses quando a pergunta pede e o dado existe.

Transcricao: "Quais foram as ultimas tres mensalidades pagas por mim?" -> so' "outubro de 2026"; "Mas eu te
perguntei das ultimas tres" -> outubro de novo; "de qual ano mesmo??" -> o mes lembrado era `None`.

Decisoes do dono: havendo o dado, lista (mes com ano, valor, data de pagamento); com menos meses do que o
pedido, "Encontrei so' N ... nos dados que tenho:"; num assunto sem lista, "Consigo te mostrar um mes por
vez." e o mes de sempre. Tudo em TEXTO FIXO dos fatos, contra o grafo REAL e a DMN DRAFT (`DmnDraftLocal`).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    SEGUNDA_VIA_CANAIS,
    UM_MES_POR_VEZ,
    LucasGraph,
    LucasState,
    competencias_da_resposta,
    new_lucas_state,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.runtime.cobranca_lista import PedidoDeLista
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 12)
_FECHO = "(dados de 08/10)"


def _mes(competencia: str, situacao: str = "paga", **extra: str) -> dict[str, str]:
    fato = {
        "competencia": competencia,
        "situacao": situacao,
        "vencimento": f"{competencia}-25",
        "valor_total": "8389.53",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "0.00" if situacao == "paga" else "8389.53",
        "boleto": "****0037",
    }
    if situacao == "paga":
        fato["liquidado_em"] = f"{competencia}-06"
    return {**fato, **extra}


_NOVEMBRO = _mes("2026-11", "em_aberto")
_OUTUBRO = _mes("2026-10")
_SETEMBRO = _mes("2026-09", valor_coparticipacao="35.10")
_AGOSTO = _mes("2026-08", valor_total="8269.53")
_JULHO = _mes("2026-07")


class _Inferencia:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return "Narrativa para o humano."


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


def _estado(
    tipo: str, lista: PedidoDeLista | None, *competencias: dict[str, str], foco: str = "geral", **extra: Any
) -> LucasState:
    entrada: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_1210",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1210",
        "to_hash": "hash-1210",
        "intencao": "cobranca_info",
        "tipo_solicitacao": tipo,
        "cobranca_foco": foco,
        "status_conciliado": True,
        "ciclos_sem_conciliacao": 0,
        "numero_boleto": "****0037",
        "cnab_ref": "amh-billing:2026-10-08",
        "valor_em_aberto": "0.00",
        "dias_atraso_max": 0,
        "competencias_cobranca": list(competencias),
        **extra,
    }
    if lista is not None:
        entrada["cobranca_lista"] = lista.como_handoff()
    return new_lucas_state(entrada)


async def _turno(estado: LucasState) -> tuple[dict[str, Any], list[str], _Inferencia]:
    envio = _Envio()
    inferencia = _Inferencia()
    grafo = LucasGraph(
        inference=inferencia,  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: _HOJE,
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio.textos, inferencia


def _sem_recusa(final: dict[str, Any], texto: str) -> None:
    assert (
        motivo_de_recusa(texto, "mensagem", final["mensagem"]["fatos"] | {"dados_de": "2026-10-08"}) is None
    )


_TODOS = (_NOVEMBRO, _OUTUBRO, _SETEMBRO, _AGOSTO, _JULHO)


async def test_ultimas_tres_pagas_com_dado_suficiente_lista_as_tres() -> None:
    final, textos, inferencia = await _turno(
        _estado("consulta_valores", PedidoDeLista(3, situacao="paga"), *_TODOS)
    )
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [
        "Suas últimas 3 mensalidades pagas:\n"
        "• outubro de 2026: R$ 8.389,53, paga em 06/10.\n"
        "• setembro de 2026: R$ 8.389,53, paga em 06/09.\n"
        "• agosto de 2026: R$ 8.269,53, paga em 06/08.\n"
        f"{_FECHO}"
    ]
    assert inferencia.prompts == [], "nenhum modelo redige: texto fixo dos fatos"
    _sem_recusa(final, textos[0])
    assert competencias_da_resposta(final) == ("2026-10", "2026-09", "2026-08")


async def test_com_menos_meses_do_que_o_pedido_diz_o_que_tem() -> None:
    final, textos, _ = await _turno(
        _estado("consulta_valores", PedidoDeLista(3, situacao="paga"), _NOVEMBRO, _OUTUBRO, _SETEMBRO)
    )
    assert textos == [
        "Encontrei só 2 mensalidades pagas nos dados que tenho:\n"
        "• outubro de 2026: R$ 8.389,53, paga em 06/10.\n"
        "• setembro de 2026: R$ 8.389,53, paga em 06/09.\n"
        f"{_FECHO}"
    ]
    _sem_recusa(final, textos[0])


async def test_coparticipacao_dos_ultimos_meses() -> None:
    final, textos, _ = await _turno(
        _estado("consulta_valores", PedidoDeLista(3), _OUTUBRO, _SETEMBRO, _AGOSTO, foco="coparticipacao")
    )
    assert textos == [
        "Suas últimas 3 coparticipações:\n"
        "• outubro de 2026: sem coparticipação.\n"
        "• setembro de 2026: R$ 35,10.\n"
        "• agosto de 2026: sem coparticipação.\n"
        f"{_FECHO}"
    ]
    _sem_recusa(final, textos[0])


async def test_intervalo_de_meses() -> None:
    final, textos, _ = await _turno(
        _estado("consulta_valores", PedidoDeLista(3, de="2026-07", ate="2026-09"), *_TODOS)
    )
    assert textos == [
        "Suas mensalidades de julho de 2026 a setembro de 2026:\n"
        "• setembro de 2026: R$ 8.389,53, paga em 06/09.\n"
        "• agosto de 2026: R$ 8.269,53, paga em 06/08.\n"
        "• julho de 2026: R$ 8.389,53, paga em 06/07.\n"
        f"{_FECHO}"
    ]
    _sem_recusa(final, textos[0])


async def test_ano_inteiro_so_conta_os_meses_que_ja_existem() -> None:
    """ "as de 2026" em outubro: novembro ja' gerado conta, dezembro nao; vieram 5 dos 11 meses."""
    final, textos, _ = await _turno(
        _estado("consulta_valores", PedidoDeLista(12, de="2026-01", ate="2026-12"), *_TODOS)
    )
    [texto] = textos
    assert texto.startswith("Encontrei só 5 mensalidades de 2026 nos dados que tenho:\n")
    assert "• novembro de 2026: R$ 8.389,53, em aberto, vence em 25/11." in texto
    assert texto.count("•") == 5
    _sem_recusa(final, texto)


async def test_todas_em_aberto() -> None:
    final, textos, _ = await _turno(
        _estado("consulta_valores", PedidoDeLista(12, situacao="em_aberto"), *_TODOS, foco="saldo")
    )
    assert textos == [f"Sua mensalidade em aberto:\n• novembro de 2026: R$ 8.389,53 em aberto.\n{_FECHO}"]
    _sem_recusa(final, textos[0])


async def test_sem_nenhum_mes_do_pedido_diz_e_responde_o_mais_recente() -> None:
    final, textos, _ = await _turno(
        _estado("consulta_valores", PedidoDeLista(12, de="2024-01", ate="2024-12"), _OUTUBRO, _SETEMBRO)
    )
    assert textos == [
        "Não encontrei mensalidades de 2024 nos dados que tenho. Sua mensalidade de outubro de 2026 foi de "
        f"R$ 8.389,53 e já está paga (você pagou em 06/10). {_FECHO}"
    ]
    _sem_recusa(final, textos[0])
    assert competencias_da_resposta(final) == (), "o mes oferecido nao e' o respondido"


async def test_assunto_sem_lista_avisa_um_mes_por_vez() -> None:
    final, textos, _ = await _turno(_estado("2a_via", PedidoDeLista(3), _OUTUBRO, _SETEMBRO))
    assert final["route"] == "respond_member"
    assert textos == [
        f"{UM_MES_POR_VEZ} {SEGUNDA_VIA_CANAIS} A mensalidade de outubro de 2026 já está paga. {_FECHO}"
    ]


async def test_sem_pedido_de_lista_nada_muda() -> None:
    final, textos, _ = await _turno(_estado("consulta_valores", None, *_TODOS, foco="valor"))
    assert textos == [
        "Sua mensalidade de novembro de 2026 é de R$ 8.389,53, com vencimento em 25/11. " + _FECHO
    ]
    assert competencias_da_resposta(final) == ("2026-11",)


async def test_lista_com_atraso_continua_escalando() -> None:
    final, textos, _ = await _turno(
        _estado(
            "consulta_valores", PedidoDeLista(3), _mes("2026-09", "vencida"), _OUTUBRO, dias_atraso_max=17
        )
    )
    assert final["route"] == "escalate_human"
    assert all("•" not in texto for texto in textos)
    assert competencias_da_resposta(final) == ()


@pytest.mark.parametrize(
    "lista",
    [
        {"quantidade": 3},
        {"quantidade": 99, "de": None, "ate": None, "situacao": None},
        "ultimas tres",
    ],
)
async def test_pedido_de_lista_fora_da_forma_e_ignorado(lista: object) -> None:
    estado = _estado("consulta_valores", None, *_TODOS, foco="valor")
    estado["cobranca_lista"] = lista  # type: ignore[typeddict-item]
    _, textos, _ = await _turno(estado)
    assert "•" not in textos[0]
