"""R-092 (F-081/F-042) — aprovacao colegiada do comite em SP-OP-PAGTO-001 (worker side).

O BPMN modela `UT_AprovacaoComite` dentro de `SP_AprovacaoComite` (multi-instancia sequencial,
cardinalidade `quorum_comite` da DMN `pagto_alcada`); cada voto passa por
`operadora.pagto.register_committee_vote`, que so REGISTRA o voto no livro `votos_comite`. Quem
libera (`release_high_value_payment`, guard colegiado DARK — `MAEZO_PAGTO_COMITE_COLEGIADO`) confere
o livro: quorum da DMN, membros DISTINTOS, nenhum membro igual ao decisor do lastro ou ao analista de
admissibilidade (SoD -> `ERR_PAYMENT_SOD_NOT_HUMAN`), todo voto APROVAR com tier e valor coerentes.

Estilo guard-test: cada vetor de bypass e um caso negativo proprio; com a flag desligada o guard
historico fica byte-a-byte (provado).
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness, is_guard_refusal_code
from maezo.tools.workers.pagto import (
    COMITE_ULTIMO_VOTO_VAR,
    COMITE_VOTO_VARIABLES,
    ERR_PAYMENT_RELEASE_NOT_HUMAN,
    ERR_PAYMENT_SOD_NOT_HUMAN,
    PAGTO_COMITE_COLEGIADO_VAR,
    VOTOS_COMITE_VAR,
    PagtoError,
    register_committee_vote,
    register_pagto_workers,
    release_high_value_payment,
    route_aprovacao,
)

_ABSENT = object()
_VALOR = 5_000_000_000  # R$ 50MM — acima do maior tier (catch-all comite)
_MEMBRO_A = "comite-membro-sintetico-001"
_MEMBRO_B = "comite-membro-sintetico-002"


def _voto(membro: str, *, voto: str = "APROVAR", tier: Any = 4, valor: Any = _VALOR) -> dict[str, Any]:
    return {"membro": membro, "voto": voto, "tier": tier, "valor_aprovado_cents": valor}


def _liberacao(**overrides: Any) -> dict[str, Any]:
    """Escopo no `ST_ReleaseHighValue` apos um comite de 2 membros distintos que aprovaram."""
    base: dict[str, Any] = {
        "decisao_pagamento": "APROVAR",
        "aprovador_id": _MEMBRO_B,
        "aprovador_tier": 4,
        "justificativa_aprovacao": "Fundamentacao sintetica do comite",
        "valor_aprovado_cents": _VALOR,
        "valor_maximo_aprovavel_cents": _VALOR,
        "faixa_valor": "ANALISE_HUMANA",
        "tier_minimo": 4,
        "quorum_comite": 2,
        "lastro_decisor_id": "analista-contas-007",
        "analista_admissibilidade_id": "analista-cp-003",
        VOTOS_COMITE_VAR: [_voto(_MEMBRO_A), _voto(_MEMBRO_B)],
    }
    for key, value in overrides.items():
        if value is _ABSENT:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def _recusa(variables: dict[str, Any]) -> PagtoError:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(variables, comite_colegiado=True)
    return excinfo.value


# ---------------------------------------------------------------------------------------------
# 1. Livro de votos — FATO, nunca decide
# ---------------------------------------------------------------------------------------------


def test_primeiro_voto_da_rodada_zera_o_livro() -> None:
    """`loopCounter == 0`: o livro de uma rodada anterior nunca empresta votos a esta."""
    out = register_committee_vote(
        {
            "loopCounter": 0,
            "decisao_pagamento": "APROVAR",
            "aprovador_id": f"  {_MEMBRO_A} ",
            "aprovador_tier": 4,
            "valor_aprovado_cents": _VALOR,
            VOTOS_COMITE_VAR: [_voto("membro-rodada-anterior")],
        }
    )
    assert out == {VOTOS_COMITE_VAR: [_voto(_MEMBRO_A)], COMITE_ULTIMO_VOTO_VAR: "APROVAR"}


def test_voto_seguinte_acrescenta_ao_livro() -> None:
    out = register_committee_vote(
        {
            "loopCounter": 1,
            "decisao_pagamento": "CANCELAR",
            "aprovador_id": _MEMBRO_B,
            "aprovador_tier": 4,
            VOTOS_COMITE_VAR: [_voto(_MEMBRO_A)],
        }
    )
    assert out[VOTOS_COMITE_VAR] == [_voto(_MEMBRO_A), _voto(_MEMBRO_B, voto="CANCELAR", valor=None)]
    assert out[COMITE_ULTIMO_VOTO_VAR] == "CANCELAR"


@pytest.mark.parametrize("decisao", [None, "", "   ", 7, "aprovar"])
def test_voto_omitido_ou_invalido_nunca_vira_aprovar(decisao: Any) -> None:
    """O eco que a completionCondition le nunca e APROVAR sem o literal exato do humano."""
    out = register_committee_vote({"loopCounter": 0, "decisao_pagamento": decisao, "aprovador_id": _MEMBRO_A})
    assert out[COMITE_ULTIMO_VOTO_VAR] != "APROVAR"


@pytest.mark.parametrize("tier,valor", [(True, False), ("4", "5000000000"), (4.0, 5e9)])
def test_tier_e_valor_mal_tipados_ficam_none_no_livro(tier: Any, valor: Any) -> None:
    out = register_committee_vote(
        {
            "loopCounter": 0,
            "decisao_pagamento": "APROVAR",
            "aprovador_id": _MEMBRO_A,
            "aprovador_tier": tier,
            "valor_aprovado_cents": valor,
        }
    )
    assert out[VOTOS_COMITE_VAR] == [_voto(_MEMBRO_A, tier=None, valor=None)]


@pytest.mark.parametrize("rodada", [_ABSENT, None, -1, True, "0", 1.0])
def test_loop_counter_desconhecido_recusa(rodada: Any) -> None:
    variables: dict[str, Any] = {"decisao_pagamento": "APROVAR", "aprovador_id": _MEMBRO_A}
    if rodada is not _ABSENT:
        variables["loopCounter"] = rodada
    with pytest.raises(PagtoError) as excinfo:
        register_committee_vote(variables)
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN


def test_livro_corrompido_na_rodada_seguinte_descarta_entradas_nao_dict() -> None:
    out = register_committee_vote(
        {
            "loopCounter": 1,
            "decisao_pagamento": "APROVAR",
            "aprovador_id": _MEMBRO_B,
            "aprovador_tier": 4,
            "valor_aprovado_cents": _VALOR,
            VOTOS_COMITE_VAR: ["lixo", _voto(_MEMBRO_A)],
        }
    )
    assert out[VOTOS_COMITE_VAR] == [_voto(_MEMBRO_A), _voto(_MEMBRO_B)]


# ---------------------------------------------------------------------------------------------
# 2. Guard colegiado (flag ligada)
# ---------------------------------------------------------------------------------------------


def test_comite_de_membros_distintos_que_aprovaram_libera() -> None:
    out = release_high_value_payment(_liberacao(), comite_colegiado=True)
    assert out == {"pagamento_liberado": True, "tipo_liberacao": "humano_alcada"}


def test_flag_desligada_guard_historico_inalterado() -> None:
    """Sem a flag, o livro nao e lido: o mesmo escopo SEM livro libera como sempre liberou."""
    variables = _liberacao(**{VOTOS_COMITE_VAR: _ABSENT})
    assert release_high_value_payment(variables)["pagamento_liberado"] is True
    assert release_high_value_payment(variables, comite_colegiado=False)["pagamento_liberado"] is True


@pytest.mark.parametrize("flag", ["true", 1, "1"])
def test_flag_lixo_nao_liga_o_guard(flag: Any) -> None:
    variables = _liberacao(**{VOTOS_COMITE_VAR: _ABSENT})
    assert release_high_value_payment(variables, comite_colegiado=flag)["pagamento_liberado"] is True


def test_quorum_1_aprovador_singular_inalterado_com_a_flag() -> None:
    """Faixas L1..L3 (quorum 1 na DMN) nao exigem livro mesmo com a flag."""
    variables = _liberacao(quorum_comite=1, **{VOTOS_COMITE_VAR: _ABSENT})
    assert release_high_value_payment(variables, comite_colegiado=True)["pagamento_liberado"] is True


@pytest.mark.parametrize("quorum", [_ABSENT, None, 0, -2, True, "2", 2.0])
def test_quorum_ausente_ou_mal_formado_recusa(quorum: Any) -> None:
    erro = _recusa(_liberacao(quorum_comite=quorum))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "quorum_comite invalido" in erro.message


@pytest.mark.parametrize("livro", [_ABSENT, None, [], "[]", {"membro": _MEMBRO_A}])
def test_livro_ausente_ou_mal_formado_recusa(livro: Any) -> None:
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: livro}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "votos_comite" in erro.message


def test_um_so_membro_nao_atinge_o_quorum() -> None:
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_B)]}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "quorum_comite nao atingido" in erro.message


def test_quorum_maior_exige_mais_membros() -> None:
    erro = _recusa(_liberacao(quorum_comite=3))
    assert "quorum_comite nao atingido" in erro.message


@pytest.mark.parametrize("repetido", [_MEMBRO_B, f" {_MEMBRO_B.upper()} "])
def test_mesmo_membro_votando_duas_vezes_e_violacao_de_sod(repetido: str) -> None:
    """Controle dual com UMA pessoa nao e controle dual: SoD, mesmo com grafia diferente."""
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [_voto(repetido), _voto(_MEMBRO_B)]}))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN
    assert "votou mais de uma vez" in erro.message


def test_membro_igual_ao_decisor_do_lastro_e_violacao_de_sod() -> None:
    erro = _recusa(_liberacao(lastro_decisor_id=_MEMBRO_A))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN
    assert "membro do comite == lastro_decisor_id" in erro.message


def test_membro_igual_ao_analista_de_admissibilidade_e_violacao_de_sod() -> None:
    erro = _recusa(_liberacao(analista_admissibilidade_id=_MEMBRO_A))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN
    assert "membro do comite == analista_admissibilidade_id" in erro.message


@pytest.mark.parametrize("voto", ["RECUSAR", "CANCELAR", "SOLICITAR_INFO", "", "aprovar"])
def test_voto_que_nao_aprova_no_livro_recusa(voto: str) -> None:
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A, voto=voto), _voto(_MEMBRO_B)]}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "voto != APROVAR" in erro.message


@pytest.mark.parametrize("tier", [3, None, "4", True])
def test_membro_com_tier_abaixo_do_requerido_recusa(tier: Any) -> None:
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A, tier=tier), _voto(_MEMBRO_B)]}))
    assert "tier do membro abaixo" in erro.message


@pytest.mark.parametrize("valor", [_VALOR - 1, None, str(_VALOR)])
def test_membro_que_aprovou_outro_valor_recusa(valor: Any) -> None:
    """Ninguem do comite aprovou o valor que sairia: recusa (nunca o maior, nunca o ultimo)."""
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A, valor=valor), _voto(_MEMBRO_B)]}))
    assert "valor aprovado do membro difere" in erro.message


def test_coordenacao_que_assume_sozinha_um_caso_de_comite_nao_libera() -> None:
    """Estouro de SLA -> coordenacao assume com UM aprovador: fora do livro -> recusa."""
    erro = _recusa(_liberacao(aprovador_id="coord-fin-sintetico-009"))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "aprovador_id fora do livro" in erro.message


@pytest.mark.parametrize("entrada", ["lixo", {"voto": "APROVAR"}, {"membro": "   "}])
def test_entrada_de_livro_mal_formada_recusa(entrada: Any) -> None:
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [entrada, _voto(_MEMBRO_A), _voto(_MEMBRO_B)]}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN


def test_recusa_colegiada_nao_vaza_identidades_nem_valores() -> None:
    erro = _recusa(
        _liberacao(
            lastro_decisor_id=_MEMBRO_A,
            **{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A, valor=123), _voto(_MEMBRO_A)]},
        )
    )
    assert _MEMBRO_A not in erro.message
    assert "123" not in erro.message and str(_VALOR) not in erro.message


def test_codigo_sod_do_comite_entra_na_trilha_de_recusa_auditada() -> None:
    assert is_guard_refusal_code(ERR_PAYMENT_SOD_NOT_HUMAN)


def test_sod_do_comite_prevalece_sobre_o_codigo_generico() -> None:
    erro = _recusa(_liberacao(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_B, voto="RECUSAR"), _voto(_MEMBRO_B)]}))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN
    assert "voto != APROVAR" in erro.message  # o erro generico continua reportado junto


# ---------------------------------------------------------------------------------------------
# 3. Fato do gateway + fiacao de register_pagto_workers
# ---------------------------------------------------------------------------------------------


def _dmn_comite() -> FakeDmnTransport:
    fake = FakeDmnTransport()
    fake.register(
        "pagto_alcada",
        [{"faixa_valor": "ANALISE_HUMANA", "grupo_aprovador": "comite-financeiro", "tier_minimo": 4}],
    )
    return fake


@pytest.mark.parametrize(("flag", "esperado"), [(False, False), (True, True), ("true", False), (1, False)])
def test_calculate_facts_escreve_a_posicao_da_implantacao(flag: Any, esperado: bool) -> None:
    out = route_aprovacao(
        {"tenant_id": "amh", "valor_pagamento_cents": _VALOR, PAGTO_COMITE_COLEGIADO_VAR: True},
        dmn=_dmn_comite(),
        comite_colegiado=flag,
    )
    # Escrito em TODO caminho: a semente de start com o mesmo nome e sempre sobrescrita.
    assert out[PAGTO_COMITE_COLEGIADO_VAR] is esperado


def _registrado(**seams: Any) -> WorkerHarness:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="unit-test-r092")
    register_pagto_workers(harness, None, dmn=_dmn_comite(), **seams)
    return harness


@pytest.mark.parametrize(
    ("seams", "esperado"),
    [({}, False), ({"pagto_comite_colegiado": True}, True), ({"pagto_comite_colegiado": "true"}, False)],
)
def test_seam_chega_ao_calculate_facts(seams: dict[str, Any], esperado: bool) -> None:
    worker = _registrado(**seams).registry.get("operadora.pagto.calculate_facts")
    assert worker is not None
    out = worker.execute({"tenant_id": "amh", "valor_pagamento_cents": _VALOR})
    assert out[PAGTO_COMITE_COLEGIADO_VAR] is esperado


@pytest.mark.parametrize(("seams", "recusa"), [({}, False), ({"pagto_comite_colegiado": True}, True)])
def test_seam_chega_ao_guard_de_liberacao(seams: dict[str, Any], recusa: bool) -> None:
    worker = _registrado(**seams).registry.get("operadora.pagto.release_high_value_payment")
    assert worker is not None
    variables = _liberacao(**{VOTOS_COMITE_VAR: _ABSENT})
    if recusa:
        with pytest.raises(ValueError, match="votos_comite"):
            worker.execute(variables)
    else:
        assert worker.execute(variables)["pagamento_liberado"] is True


def test_livro_de_votos_registrado_com_escopo_de_leitura_explicito() -> None:
    """Menor privilegio: o topico novo nunca busca todas as variaveis da instancia."""
    harness = _registrado()
    assert "operadora.pagto.register_committee_vote" in harness.registered_topics
    escopo = harness._topic_variables["operadora.pagto.register_committee_vote"]
    assert escopo == COMITE_VOTO_VARIABLES
    assert {"loopCounter", "decisao_pagamento", "aprovador_id", VOTOS_COMITE_VAR} <= set(escopo)
