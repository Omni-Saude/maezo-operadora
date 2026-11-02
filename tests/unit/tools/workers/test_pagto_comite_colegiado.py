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

import hashlib
from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import (
    FakeWorkerTransport,
    WorkerHarness,
    build_decision_basis,
    is_guard_refusal_code,
)
from maezo.tools.workers.pagto import (
    ASSUNCAO_COMITE_PERMITIDA,
    ASSUNCAO_COMITE_VAR,
    ASSUNCAO_COORDENACAO_REGISTRO_VAR,
    COMITE_SLA_ESTOURADO_EM_VAR,
    COMITE_ULTIMO_VOTO_VAR,
    COMITE_VOTO_VARIABLES,
    ERR_PAYMENT_RELEASE_NOT_HUMAN,
    ERR_PAYMENT_SOD_NOT_HUMAN,
    MOTIVO_ASSUNCAO_COMITE_VAR,
    PAGTO_COMITE_COLEGIADO_VAR,
    PAGTO_FATOS_PROIBIDOS_NA_SEMENTE,
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


# ---------------------------------------------------------------------------------------------
# 4. B1 do gate cego (#1172 @dd60b712): livro semeado no start nunca conta votos
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [True, False])
def test_calculate_facts_zera_o_livro_semeado(flag: bool) -> None:
    """O livro nasce vazio a cada calculo de fatos — uma semente de start com dois APROVAR nunca
    chega ao guard (com ou sem a flag)."""
    out = route_aprovacao(
        {
            "tenant_id": "amh",
            "valor_pagamento_cents": _VALOR,
            VOTOS_COMITE_VAR: [_voto("x-fantasma"), _voto("coord-c")],
        },
        dmn=_dmn_comite(),
        comite_colegiado=flag,
    )
    assert out[VOTOS_COMITE_VAR] == []


def test_coordenacao_assumindo_comite_interrompido_antes_do_1o_voto_nao_libera() -> None:
    """Estado apos calculate_facts + BRT_AlcadaRouting (livro vazio), SLA estourado antes de
    qualquer voto, coordenacao assume com APROVAR: o guard colegiado recusa."""
    variables = {
        "quorum_comite": 2,
        "tier_minimo": 4,
        "faixa_valor": "ANALISE_HUMANA",
        "valor_maximo_aprovavel_cents": _VALOR,
        VOTOS_COMITE_VAR: [],
        "decisao_coordenacao": "assumir_aprovacao",
        "decisao_pagamento": "APROVAR",
        "aprovador_id": "coord-fin-sintetico-009",
        "aprovador_tier": 4,
        "justificativa_aprovacao": "Fundamentacao sintetica da coordenacao",
        "valor_aprovado_cents": _VALOR,
    }
    erro = _recusa(variables)
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "votos_comite ausente ou vazio" in erro.message


def test_livro_esta_no_conjunto_proibido_na_semente() -> None:
    from maezo.tools.workers.pagto import PAGTO_FATOS_PROIBIDOS_NA_SEMENTE

    assert VOTOS_COMITE_VAR in PAGTO_FATOS_PROIBIDOS_NA_SEMENTE


# ---------------------------------------------------------------------------------------------
# 5. DEC-30 D3 (dono, #1084, 2026-11-02): a coordenacao completa o quorum apos estouro de SLA
#    SO se ja houver o minimo de votos APROVAR que a DMN `pagto_alcada` emite
#    (`votos_minimos_coordenacao`, 1 no catch-all), de membro(s) DISTINTO(S) da coordenacao.
# ---------------------------------------------------------------------------------------------

_COORD = "coord-fin-sintetico-009"
_SLA_EM = "2026-11-02T12:00:00.000+0000"
_MOTIVO_PERMITIDA = "SLA_ESTOURADO_UM_APROVAR_SEM_RECUSA"


def _coordenacao_assume(**overrides: Any) -> dict[str, Any]:
    """Escopo no `ST_ReleaseHighValue` quando a coordenacao assume (`assumir_aprovacao` + APROVAR)
    um comite interrompido pelo SLA depois de UM voto APROVAR do membro A. `assuncao_comite` e a
    saida que a DMN `pagto_comite_coordenacao` emite no motor para esse caso (DEC-31 D31-2)."""
    base = _liberacao(
        aprovador_id=_COORD,
        decisao_coordenacao="assumir_aprovacao",
        votos_minimos_coordenacao=1,
        **{
            VOTOS_COMITE_VAR: [_voto(_MEMBRO_A)],
            ASSUNCAO_COMITE_VAR: ASSUNCAO_COMITE_PERMITIDA,
            MOTIVO_ASSUNCAO_COMITE_VAR: _MOTIVO_PERMITIDA,
            COMITE_SLA_ESTOURADO_EM_VAR: _SLA_EM,
        },
    )
    for key, value in overrides.items():
        if value is _ABSENT:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def test_d3_um_aprovar_de_membro_distinto_coordenacao_completa_e_libera() -> None:
    out = release_high_value_payment(_coordenacao_assume(), comite_colegiado=True)
    assert out["pagamento_liberado"] is True
    assert out["tipo_liberacao"] == "humano_alcada"
    assert out["coordenacao_assumiu_comite"] is True


def test_d3_zero_votos_coordenacao_nunca_libera() -> None:
    erro = _recusa(_coordenacao_assume(**{VOTOS_COMITE_VAR: []}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "votos_comite ausente ou vazio" in erro.message


def test_d3_coordenacao_igual_ao_membro_que_votou_e_violacao_de_sod() -> None:
    """A mesma pessoa vota no comite e depois assume como coordenacao: controle dual com UMA pessoa."""
    for coord in (_MEMBRO_A, f"  {_MEMBRO_A.upper()} "):
        erro = _recusa(_coordenacao_assume(aprovador_id=coord))
        assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN
        assert "segregacao: coordenacao == membro do comite que votou" in erro.message
        # A mesma pessoa nunca soma: livro + coordenacao continua com 1 membro distinto.
        assert "quorum_comite nao atingido" in erro.message
        assert _MEMBRO_A not in erro.message


@pytest.mark.parametrize("voto", ["RECUSAR", "SOLICITAR_INFO", "CANCELAR", "", "aprovar"])
def test_d3_um_voto_que_nao_aprova_coordenacao_nao_libera(voto: str) -> None:
    erro = _recusa(_coordenacao_assume(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A, voto=voto)]}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "voto != APROVAR" in erro.message


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("lastro_decisor_id", _COORD), ("analista_admissibilidade_id", _COORD)],
)
def test_d3_coordenacao_igual_ao_decisor_ou_ao_analista_e_violacao_de_sod(campo: str, valor: str) -> None:
    erro = _recusa(_coordenacao_assume(**{campo: valor}))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN


def test_d3_membro_que_votou_igual_ao_decisor_do_lastro_continua_sod() -> None:
    erro = _recusa(_coordenacao_assume(lastro_decisor_id=_MEMBRO_A))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN


@pytest.mark.parametrize("minimo", [_ABSENT, None, 0, -1, True, "1", 1.0])
def test_d3_minimo_da_dmn_ausente_ou_mal_formado_recusa(minimo: Any) -> None:
    """Fail-closed: sem o fato da DMN (ou 0 — a opcao B rejeitada pelo dono) a coordenacao nao completa."""
    erro = _recusa(_coordenacao_assume(votos_minimos_coordenacao=minimo))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "votos_minimos_coordenacao invalido" in erro.message


def test_d3_minimo_vem_da_dmn_nao_do_python() -> None:
    """Com a DMN pedindo 2 votos previos, 1 voto nao basta (o numero nao mora no worker)."""
    erro = _recusa(_coordenacao_assume(votos_minimos_coordenacao=2, quorum_comite=3))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "abaixo do minimo da DMN" in erro.message
    # Com a DMN de assuncao permitindo (o worker nao re-decide a contagem de votos dela).
    out = release_high_value_payment(
        _coordenacao_assume(
            votos_minimos_coordenacao=2,
            quorum_comite=3,
            **{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A), _voto(_MEMBRO_B)]},
        ),
        comite_colegiado=True,
    )
    assert out["pagamento_liberado"] is True


def test_d3_coordenacao_so_acrescenta_um_membro_ao_quorum() -> None:
    """Quorum 3 com 1 voto previo: 1 + a coordenacao = 2 < 3 -> recusa."""
    erro = _recusa(_coordenacao_assume(quorum_comite=3))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "quorum_comite nao atingido" in erro.message


@pytest.mark.parametrize(
    "decisao_coordenacao",
    [_ABSENT, "", "prorrogar_prazo", "seguir_analise", "ASSUMIR_APROVACAO", " assumir_aprovacao ", 1],
)
def test_d3_sem_assumir_aprovacao_o_aprovador_fora_do_livro_continua_recusado(
    decisao_coordenacao: Any,
) -> None:
    erro = _recusa(_coordenacao_assume(decisao_coordenacao=decisao_coordenacao))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "aprovador_id fora do livro" in erro.message


def test_d3_coordenacao_com_tier_ou_valor_divergente_recusa() -> None:
    assert _recusa(_coordenacao_assume(aprovador_tier=3)).code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    erro = _recusa(_coordenacao_assume(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A, valor=_VALOR - 1)]}))
    assert "valor aprovado do membro difere" in erro.message


def test_d3_flag_desligada_inalterada() -> None:
    """Sem a flag o guard colegiado nao roda (comportamento historico, com ou sem livro)."""
    for variables in (_coordenacao_assume(), _coordenacao_assume(**{VOTOS_COMITE_VAR: []})):
        assert release_high_value_payment(variables)["pagamento_liberado"] is True


def test_d3_votos_previos_completos_pelo_comite_continuam_liberando_sem_a_coordenacao() -> None:
    """O caminho normal do comite (2 membros, o ultimo e o aprovador final) nao muda."""
    out = release_high_value_payment(_liberacao(decisao_coordenacao=""), comite_colegiado=True)
    assert out["pagamento_liberado"] is True


def test_d3_coordenacao_sem_identidade_nunca_conta_como_membro() -> None:
    erro = _recusa(_coordenacao_assume(aprovador_id="   "))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "aprovador_id ausente" in erro.message
    assert "quorum_comite nao atingido" in erro.message


# ---------------------------------------------------------------------------------------------
# 6. DEC-31 D31-2 (dono, #1084, 2026-11-02; refina a DEC-30 D3): "a coordinator can take over if
#    one member voted yes and the other did not answer before deadline". A regra (SLA do comite
#    estourado + EXATAMENTE 1 APROVAR + nenhum voto que nao aprove) mora na DMN
#    `pagto_comite_coordenacao` (motor, BRT_AssuncaoComite); o worker so exige a saida PERMITIDA
#    (igualdade exata), mantem SoD/tier/livro e AUDITA a assuncao com o motivo.
# ---------------------------------------------------------------------------------------------


def test_d31_2_um_aprovar_sla_estourado_coordenacao_distinta_com_tier_libera() -> None:
    out = release_high_value_payment(_coordenacao_assume(), comite_colegiado=True)
    assert out["pagamento_liberado"] is True


@pytest.mark.parametrize(
    "motivo",
    [
        "NENHUM_VOTO_APROVAR",  # 0 votos
        "VOTO_NAO_APROVAR_NA_RODADA",  # 1 APROVAR + 1 recusa
        "SLA_COMITE_NAO_ESTOURADO",  # SLA nao estourou
        "MAIS_DE_UM_VOTO_APROVAR",  # 2 APROVAR: caminho do comite, nao a assuncao
        "FATOS_DA_RODADA_AUSENTES",
    ],
)
def test_d31_2_dmn_recusou_a_assuncao_coordenacao_nao_libera(motivo: str) -> None:
    """Toda recusa da DMN mantem a recusa fail-closed de hoje, mesmo com livro, SoD e tier OK."""
    erro = _recusa(
        _coordenacao_assume(**{ASSUNCAO_COMITE_VAR: "RECUSADA", MOTIVO_ASSUNCAO_COMITE_VAR: motivo})
    )
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "assuncao do comite nao permitida pela DMN pagto_comite_coordenacao" in erro.message


@pytest.mark.parametrize("assuncao", [_ABSENT, None, "", "permitida", " PERMITIDA", "PERMITIDA ", True, 1])
def test_d31_2_saida_da_dmn_ausente_ou_fora_do_vocabulario_recusa(assuncao: Any) -> None:
    """Igualdade EXATA com o vocabulario fechado: sem a saida da DMN a coordenacao nunca libera."""
    erro = _recusa(_coordenacao_assume(**{ASSUNCAO_COMITE_VAR: assuncao}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "assuncao do comite nao permitida" in erro.message


def test_d31_2_zero_votos_continua_recusando_mesmo_com_dmn_permitindo() -> None:
    """Defesa em profundidade: o livro vazio recusa independentemente da saida da DMN."""
    erro = _recusa(_coordenacao_assume(**{VOTOS_COMITE_VAR: []}))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN


def test_d31_2_recusa_no_livro_continua_recusando_mesmo_com_dmn_permitindo() -> None:
    erro = _recusa(
        _coordenacao_assume(**{VOTOS_COMITE_VAR: [_voto(_MEMBRO_A), _voto(_MEMBRO_B, voto="RECUSAR")]})
    )
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "voto != APROVAR" in erro.message


def test_d31_2_coordenacao_igual_ao_membro_votante_e_sod() -> None:
    erro = _recusa(_coordenacao_assume(aprovador_id=_MEMBRO_A))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN


@pytest.mark.parametrize("campo", ["lastro_decisor_id", "analista_admissibilidade_id"])
def test_d31_2_coordenacao_igual_ao_solicitante_ou_analista_e_sod(campo: str) -> None:
    erro = _recusa(_coordenacao_assume(**{campo: f" {_COORD.upper()} "}))
    assert erro.code == ERR_PAYMENT_SOD_NOT_HUMAN


@pytest.mark.parametrize("tier", [3, 0, "abc", None])
def test_d31_2_coordenacao_com_tier_abaixo_do_minimo_recusa(tier: Any) -> None:
    erro = _recusa(_coordenacao_assume(aprovador_tier=tier))
    assert erro.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "tier-match falhou" in erro.message


def test_d31_2_assuncao_auditada_com_motivo_quem_quando_qual_voto() -> None:
    variaveis = _coordenacao_assume()
    out = release_high_value_payment(variaveis, comite_colegiado=True)
    assert out["coordenacao_assumiu_comite"] is True
    assert out[MOTIVO_ASSUNCAO_COMITE_VAR] == _MOTIVO_PERMITIDA
    registro = out[ASSUNCAO_COORDENACAO_REGISTRO_VAR]
    assert registro == {
        "coordenador_id": _COORD,
        "membro_votante_id": _MEMBRO_A,
        "voto_membro": "APROVAR",
        "comite_sla_estourado_em": _SLA_EM,
        "motivo_assuncao_comite": _MOTIVO_PERMITIDA,
        "justificativa_sha256": hashlib.sha256(b"Fundamentacao sintetica do comite").hexdigest(),
    }
    # O texto livre da justificativa nunca e copiado para o registro.
    assert "Fundamentacao" not in str(registro)
    # Trilha ADR-0007: o motivo e a marca de assuncao entram em claro (tokens limitados); as
    # identidades e a justificativa so pelo hash da entrada.
    basis = build_decision_basis(variaveis, out)
    assert basis["coordenacao_assumiu_comite"] is True
    assert basis[MOTIVO_ASSUNCAO_COMITE_VAR] == _MOTIVO_PERMITIDA
    assert basis["pagamento_liberado"] is True
    assert ASSUNCAO_COORDENACAO_REGISTRO_VAR not in basis
    assert _COORD not in str(basis) and _MEMBRO_A not in str(basis)


def test_d31_2_comite_completo_nao_registra_assuncao() -> None:
    """O caminho normal do comite (2 APROVAR, sem coordenacao) nao fabrica registro de assuncao."""
    out = release_high_value_payment(_liberacao(decisao_coordenacao=""), comite_colegiado=True)
    assert out == {"pagamento_liberado": True, "tipo_liberacao": "humano_alcada"}


def test_d31_2_aprovacao_singular_nao_registra_assuncao_nem_le_a_dmn() -> None:
    """Quorum 1 (aprovacao singular, inclusive assumida pela coordenacao): sem DMN de assuncao."""
    variaveis = _coordenacao_assume(quorum_comite=1, **{ASSUNCAO_COMITE_VAR: _ABSENT})
    out = release_high_value_payment(variaveis, comite_colegiado=True)
    assert out == {"pagamento_liberado": True, "tipo_liberacao": "humano_alcada"}


def test_d31_2_flag_desligada_inalterada() -> None:
    out = release_high_value_payment(_coordenacao_assume(**{ASSUNCAO_COMITE_VAR: "RECUSADA"}))
    assert out == {"pagamento_liberado": True, "tipo_liberacao": "humano_alcada"}


def test_d31_2_fatos_da_rodada_proibidos_na_semente() -> None:
    assert {
        "comite_votos_aprovar",
        "comite_votos_nao_aprovar",
        "comite_sla_estourado",
        COMITE_SLA_ESTOURADO_EM_VAR,
        ASSUNCAO_COMITE_VAR,
        MOTIVO_ASSUNCAO_COMITE_VAR,
    } <= PAGTO_FATOS_PROIBIDOS_NA_SEMENTE


def test_d31_2_motivo_mal_tipado_nunca_vira_token_de_auditoria() -> None:
    out = release_high_value_payment(
        _coordenacao_assume(**{MOTIVO_ASSUNCAO_COMITE_VAR: {"x": 1}, COMITE_SLA_ESTOURADO_EM_VAR: 7}),
        comite_colegiado=True,
    )
    assert out[MOTIVO_ASSUNCAO_COMITE_VAR] == ""
    assert out[ASSUNCAO_COORDENACAO_REGISTRO_VAR]["comite_sla_estourado_em"] is None
