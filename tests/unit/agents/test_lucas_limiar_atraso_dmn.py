"""R-047/F-031 (decisao do dono de 29/10/2026, reconfirmada em 31/10 na #1084): o limiar do atraso critico do
Lucas (>= 2 meses OU > 30 dias, pagador PF) saiu do Python e mora na DMN `lucas_billing_admissibility`, com
os MESMOS valores da rodada 12 (decisao do dono de 14/10/2026).

O que este arquivo prova, contra o XML vivo (`DmnDraftLocal`, leitor fail-closed) e o grafo REAL:
  - EQUIVALENCIA: numa grade que cobre as fronteiras (meses 0..3; dias None/0/29/30/31/90), a tabela escala
    por `inadimplencia` exatamente quando a formula historica do Python escalava — a formula fica CONGELADA
    aqui como oraculo do comportamento anterior, nao como regra de runtime;
  - FRONTEIRAS nomeadas: 29/30 dias e 1 mes respondem; 31 dias e 2 meses escalam; dias nulo nunca presume;
  - titulo de terceiro (`pagador_empresa=true`) nunca e' critico, nem com 5 meses / 150 dias;
  - o grafo manda os FATOS (`meses_vencidos`, `dias_atraso_max`), nunca o booleano decidido;
  - a lapide `politica_atraso` nao decide (devolve `None`) e seus espelhos batem com os literais do XML;
  - nenhum modulo de `src/` importa mais `politica_atraso` (o limiar nao volta pela porta dos fundos).
A paridade leitor x motor destas mesmas fronteiras e' provada em
`tests/integration/agents/test_lucas_dmn_real.py::test_limiar_de_atraso_no_motor_real`.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.lucas.graph import LucasGraph, LucasState, meses_vencidos, new_lucas_state
from maezo.agents.lucas.politica_atraso import DIAS_MINIMO_PADRAO, MESES_MINIMO_PADRAO, LimiaresDeAtraso
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, read_live_table

_SRC = Path(__file__).resolve().parents[3] / "src" / "maezo"
_TABELA = DMN_DIR / "lucas_billing_admissibility.dmn"

_MESES = (0, 1, 2, 3)
_DIAS: tuple[int | None, ...] = (None, 0, 29, 30, 31, 90)


def _formula_historica(meses: int, dias: int | None) -> bool:
    """O `LimiaresDeAtraso().critico` de antes de R-047, congelado como ORACULO (2 meses / 30 dias)."""
    return meses >= 2 or (dias is not None and dias > 30)


async def _dmn(*, meses: int, dias: int | None, pagador_empresa: bool = False, tipo: str) -> dict[str, Any]:
    entrada = {
        "tipo_solicitacao": tipo,
        "status_conciliado": meses == 0,
        "ciclos_sem_conciliacao": meses,
        "valores_disponiveis": True,
        "valores_com_atraso": meses > 0 or bool(dias),
        "pagador_empresa": pagador_empresa,
        "meses_vencidos": meses,
        "dias_atraso_max": dias,
    }
    linhas, _ = await DmnDraftLocal().evaluate("lucas_billing_admissibility", entrada)
    return linhas[0]


# --- DMN (XML vivo): equivalencia e fronteiras ------------------------------------------------------


@pytest.mark.parametrize("tipo", ["consulta_valores", "vencimento", "status_pagamento", "boleto"])
@pytest.mark.parametrize("dias", _DIAS)
@pytest.mark.parametrize("meses", _MESES)
async def test_tabela_equivale_a_formula_historica_na_grade_das_fronteiras(
    meses: int, dias: int | None, tipo: str
) -> None:
    linha = await _dmn(meses=meses, dias=dias, tipo=tipo)
    escalou_por_atraso = (linha["roteamento"], linha["categoria"]) == ("ESCALAR_HUMANO", "inadimplencia")
    assert escalou_por_atraso is _formula_historica(meses, dias)


@pytest.mark.parametrize(
    ("meses", "dias", "regra_esperada"),
    [
        (1, 29, None),
        (1, 30, None),
        (1, 31, "dias"),
        (0, 31, "dias"),
        (1, 0, None),
        (2, 0, "meses"),
        (2, None, "meses"),
        (1, None, None),
        (0, None, None),
    ],
)
async def test_fronteiras_29_30_31_dias_e_1_2_meses(
    meses: int, dias: int | None, regra_esperada: str | None
) -> None:
    linha = await _dmn(meses=meses, dias=dias, tipo="consulta_valores")
    if regra_esperada is None:
        assert (linha["roteamento"], linha["categoria"]) == ("RESPONDER", "-")
    else:
        assert (linha["roteamento"], linha["categoria"]) == ("ESCALAR_HUMANO", "inadimplencia")


@pytest.mark.parametrize("tipo", ["consulta_valores", "vencimento", "status_pagamento", "boleto", "2a_via"])
async def test_titulo_de_terceiro_nunca_e_critico_pela_tabela(tipo: str) -> None:
    linha = await _dmn(meses=5, dias=150, pagador_empresa=True, tipo=tipo)
    assert (linha["roteamento"], linha["categoria"]) == ("RESPONDER", "-")


def test_os_literais_do_limiar_moram_no_xml_e_os_espelhos_batem() -> None:
    tabela = read_live_table(_TABELA)
    assert "atraso_critico" not in tabela.input_names
    regras = {r.rule_id: dict(zip(tabela.input_names, r.inputs, strict=True)) for r in tabela.rules}
    meses = regras["lba_r_atraso_critico"]
    dias = regras["lba_r_atraso_critico_dias"]
    assert (meses["meses_vencidos"], meses["dias_atraso_max"], meses["pagador_empresa"]) == (
        ">= 2",
        "-",
        "false",
    )
    assert (dias["meses_vencidos"], dias["dias_atraso_max"], dias["pagador_empresa"]) == (
        "-",
        "> 30",
        "false",
    )
    assert (meses["meses_vencidos"], dias["dias_atraso_max"]) == (
        f">= {MESES_MINIMO_PADRAO}",
        f"> {DIAS_MINIMO_PADRAO}",
    )
    # As duas regras de atraso vem ANTES de qualquer resposta (FIRST): o critico escala primeiro.
    ordem = list(regras)
    assert ordem.index("lba_r_atraso_critico_dias") == ordem.index("lba_r_atraso_critico") + 1
    assert ordem.index("lba_r_atraso_critico") == 1


# --- Python: so' fatos, nenhuma decisao --------------------------------------------------------------


@pytest.mark.parametrize(("meses", "dias"), [(m, d) for m in _MESES for d in _DIAS])
def test_a_lapide_nao_decide(meses: int, dias: int | None) -> None:
    assert LimiaresDeAtraso().critico(meses_vencidos=meses, dias_atraso_max=dias) is None


def test_nenhum_modulo_de_src_importa_a_politica_de_atraso() -> None:
    importadores: list[str] = []
    for caminho in _SRC.rglob("*.py"):
        for no in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"))):
            de_from = isinstance(no, ast.ImportFrom) and (no.module or "").endswith("politica_atraso")
            direto = isinstance(no, ast.Import) and any(a.name.endswith("politica_atraso") for a in no.names)
            if de_from or direto:
                importadores.append(str(caminho.relative_to(_SRC)))
    assert importadores == []


def _competencias(vencidas: int) -> list[dict[str, Any]]:
    lista: list[dict[str, Any]] = []
    for i, mes in enumerate(["2026-10", "2026-09", "2026-08"]):
        base = {
            "competencia": mes,
            "vencimento": f"{mes}-10",
            "valor_total": "320.25",
            "valor_coparticipacao": "0.00",
        }
        if i < vencidas:
            lista.append({**base, "situacao": "vencida", "valor_saldo": "320.25"})
        else:
            lista.append({**base, "situacao": "paga", "valor_saldo": "0.00", "liquidado_em": f"{mes}-08"})
    return lista


def _estado(*, ciclos: int, vencidas: int, dias: int | None) -> LucasState:
    fatos: dict[str, Any] = {
        "status_conciliado": ciclos == 0 and vencidas == 0,
        "ciclos_sem_conciliacao": ciclos,
        "numero_boleto": "",
        "cnab_ref": "amh-billing:2026-10-13",
        "valor_em_aberto": "320.25" if vencidas else "0.00",
        "vencimento_referencia": "2026-10-10",
        "competencias_cobranca": _competencias(vencidas),
        "pagador_tipo": "pessoa_fisica",
    }
    if dias is not None:
        fatos["dias_atraso_max"] = dias
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_r047",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-r047",
            "to_hash": "hash-r047",
            "intencao": "cobranca_info",
            "tipo_solicitacao": "consulta_valores",
            **fatos,
        }
    )


class _Inferencia:
    async def generate(self, prompt: str, **kw: Any) -> str:
        del prompt, kw
        return "Narrativa para o humano."


class _Envio:
    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, text, idempotency_key
        return {"ok": True}


class _DmnEspia(DmnDraftLocal):
    def __init__(self) -> None:
        super().__init__()
        self.entradas: list[dict[str, Any]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], Any]:
        if decision_key == "lucas_billing_admissibility":
            self.entradas.append(dict(variables))
        return await super().evaluate(decision_key, variables, tenant=tenant)


async def _turno(estado: LucasState) -> tuple[dict[str, Any], _DmnEspia]:
    dmn = _DmnEspia()
    grafo = LucasGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_Envio(),
        hoje=lambda: date(2026, 10, 13),
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, dmn


@pytest.mark.parametrize(
    ("ciclos", "vencidas", "dias", "meses_esperado", "escala"),
    [
        (1, 1, 29, 1, False),
        (1, 1, 30, 1, False),
        (1, 1, 31, 1, True),
        (1, 1, None, 1, False),
        (2, 1, 0, 2, True),
        (0, 2, 0, 2, True),
        (1, 0, 0, 1, False),
    ],
)
async def test_grafo_manda_os_fatos_e_a_tabela_decide_nas_fronteiras(
    ciclos: int, vencidas: int, dias: int | None, meses_esperado: int, escala: bool
) -> None:
    estado = _estado(ciclos=ciclos, vencidas=vencidas, dias=dias)
    assert meses_vencidos(estado) == meses_esperado
    final, dmn = await _turno(estado)
    (entrada,) = dmn.entradas
    assert "atraso_critico" not in entrada
    assert (entrada["meses_vencidos"], entrada["dias_atraso_max"]) == (meses_esperado, dias)
    assert final["route"] == ("escalate_human" if escala else "respond_member")
    if escala:
        assert final["motivo_humano"] == "inadimplencia_detectada"
    assert escala is _formula_historica(meses_esperado, dias)
