"""CD-144: aceite unico do prospect cobre {1, 5, 7}; o portao de CAMPANHA para prospect admite so' 5 e 7.

Dados sinteticos. Nada liga envio: com o mapa `purpose_ref` -> numero vazio, toda campanha para prospect
recusa.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

import pytest

from maezo.gateway.communications import campanha as cp
from maezo.gateway.communications import finalidades_do_aceite as fa
from maezo.gateway.communications.campanha import (
    Campanha,
    CampanhaRecusadaError,
    MotivoCampanha,
    PortaoDeCampanha,
)
from maezo.gateway.communications.finalidades_do_aceite import (
    FINALIDADES_DE_CAMPANHA_PARA_PROSPECT,
    FINALIDADES_DO_ACEITE,
    NUMERO_DA_FINALIDADE,
    AceiteDeFinalidades,
    AceiteRecusado,
    aceite_autoriza,
    campanha_admitida_para_prospect,
    validar_aceite,
)
from maezo.gateway.communications.outbound_gate import PedidoDeEnvio

_RAIZ = Path(fa.__file__).resolve().parents[4]
FINALIDADE_SINTETICA = "finalidade_sintetica_cd144"


def _aceite(**kw: Any) -> AceiteDeFinalidades:
    base: dict[str, Any] = {"tipo_titular": "prospect", "finalidades": frozenset({1, 7})}
    base.update(kw)
    return AceiteDeFinalidades(**base)


def _recusa(aceite: object) -> str:
    with pytest.raises(AceiteRecusado) as e:
        validar_aceite(aceite)
    assert str(e.value) == e.value.code
    return e.value.code


# --- tabela do aceite --------------------------------------------------------------------------


def test_tabela_do_aceite_fechada() -> None:
    assert dict(FINALIDADES_DO_ACEITE) == {
        "lead": frozenset({1, 5, 7}),
        "prospect": frozenset({1, 5, 7}),
        "beneficiario": frozenset({1, 2, 3, 4, 5, 6}),
    }
    assert 7 not in FINALIDADES_DO_ACEITE["beneficiario"]
    assert isinstance(FINALIDADES_DO_ACEITE, MappingProxyType)


@pytest.mark.parametrize(
    "finalidades",
    [frozenset({1}), frozenset({7}), frozenset({1, 7}), frozenset({1, 5, 7}), frozenset({5})],
)
def test_subconjunto_nao_vazio_do_prospect_aceito(finalidades: frozenset[int]) -> None:
    marca = True if 5 in finalidades else None
    aceito = validar_aceite(_aceite(finalidades=finalidades, marca_finalidade_5=marca))
    assert aceito.finalidades == finalidades


@pytest.mark.parametrize("tipo", ["lead", "prospect"])
def test_lead_e_prospect_tem_a_mesma_tabela(tipo: str) -> None:
    assert validar_aceite(_aceite(tipo_titular=tipo)).finalidades == frozenset({1, 7})
    for fora in (2, 3, 4, 6):
        assert _recusa(_aceite(tipo_titular=tipo, finalidades=frozenset({fora}))) == (
            "aceite_finalidade_fora_da_tabela"
        )


def test_aceite_vazio_recusa() -> None:
    assert _recusa(_aceite(finalidades=frozenset())) == "aceite_vazio"


@pytest.mark.parametrize("fora", [0, 2, 3, 4, 6, 8])
def test_prospect_fora_de_1_5_7_recusa(fora: int) -> None:
    assert _recusa(_aceite(finalidades=frozenset({1, fora}))) == "aceite_finalidade_fora_da_tabela"


def test_finalidade_7_nunca_no_aceite_do_beneficiario() -> None:
    aceite = _aceite(tipo_titular="beneficiario", finalidades=frozenset({1, 7}))
    assert _recusa(aceite) == "aceite_finalidade_fora_da_tabela"


@pytest.mark.parametrize("marca", [None, False])
def test_finalidade_5_sem_marca_explicita_recusa(marca: bool | None) -> None:
    assert _recusa(_aceite(finalidades=frozenset({5, 7}), marca_finalidade_5=marca)) == (
        "aceite_finalidade_5_sem_marca"
    )


def test_marca_da_5_sem_a_finalidade_5_recusa() -> None:
    assert _recusa(_aceite(marca_finalidade_5=True)) == "aceite_marca_sem_finalidade_5"


def test_marca_da_5_sem_valor_padrao_verdadeiro() -> None:
    assert AceiteDeFinalidades("prospect", frozenset({5})).marca_finalidade_5 is None


@pytest.mark.parametrize(
    "aceite",
    [
        None,
        {"tipo_titular": "prospect", "finalidades": [1]},
        AceiteDeFinalidades("prospect", {1}),  # type: ignore[arg-type]
        AceiteDeFinalidades("prospect", frozenset({True})),
        AceiteDeFinalidades("prospect", frozenset({"1"})),  # type: ignore[arg-type]
        AceiteDeFinalidades("prospect", frozenset({5}), marca_finalidade_5=1),  # type: ignore[arg-type]
    ],
)
def test_aceite_malformado_recusa(aceite: object) -> None:
    assert _recusa(aceite) == "aceite_invalido"


@pytest.mark.parametrize("tipo", ["Prospect", "Lead", "outro", "", None])
def test_tipo_de_titular_fora_da_tabela_recusa(tipo: object) -> None:
    assert _recusa(_aceite(tipo_titular=tipo)) == "aceite_titular_desconhecido"


def test_recusa_nao_ecoa_valor() -> None:
    with pytest.raises(AceiteRecusado) as e:
        validar_aceite(_aceite(tipo_titular="maria-gestante-01"))
    assert "maria" not in str(e.value)


# --- autorizacao: ausencia = nao autorizada; nada herdado --------------------------------------------


def test_ausencia_nao_autoriza_e_nada_e_herdado() -> None:
    aceite = _aceite(finalidades=frozenset({1}))
    assert aceite_autoriza(aceite, 1) is True
    for outra in (5, 7, 2):
        assert aceite_autoriza(aceite, outra) is False
    assert aceite_autoriza(None, 1) is False
    assert aceite_autoriza(_aceite(finalidades=frozenset()), 1) is False
    assert aceite_autoriza(aceite, "1") is False
    assert aceite_autoriza(aceite, True) is False


def test_aceite_invalido_nunca_autoriza_nem_a_listada() -> None:
    sem_marca = _aceite(finalidades=frozenset({5, 7}))
    assert aceite_autoriza(sem_marca, 7) is False


# --- o portao de CAMPANHA: so' 5 e 7 para prospect, separado da tabela do aceite -----------------------


def test_tabela_do_aceite_nao_e_licenca_de_campanha() -> None:
    assert frozenset({5, 7}) == FINALIDADES_DE_CAMPANHA_PARA_PROSPECT
    assert 1 in FINALIDADES_DO_ACEITE["prospect"]
    assert 1 not in FINALIDADES_DE_CAMPANHA_PARA_PROSPECT
    assert FINALIDADES_DO_ACEITE["prospect"] > FINALIDADES_DE_CAMPANHA_PARA_PROSPECT


def test_mapa_de_numero_vazio_recusa_toda_campanha_para_prospect() -> None:
    assert dict(NUMERO_DA_FINALIDADE) == {}
    assert isinstance(NUMERO_DA_FINALIDADE, MappingProxyType)
    for ref in ("cobranca_lembrete_pre_vencimento", "retencao_onboarding", FINALIDADE_SINTETICA, "", None, 5):
        assert campanha_admitida_para_prospect(ref) is False


@pytest.mark.parametrize(("numero", "admitida"), [(1, False), (2, False), (5, True), (6, False), (7, True)])
def test_numero_publicado_decide_a_campanha(
    monkeypatch: pytest.MonkeyPatch, numero: int, admitida: bool
) -> None:
    monkeypatch.setattr(fa, "NUMERO_DA_FINALIDADE", MappingProxyType({FINALIDADE_SINTETICA: numero}))
    assert campanha_admitida_para_prospect(FINALIDADE_SINTETICA) is admitida


def _campanha() -> Campanha:
    return Campanha(
        campanha_ref="camp-cd144",
        finalidade=FINALIDADE_SINTETICA,
        segmento={"regiao": "SP", "etapa": "cotacao"},
        aprovacao_humana_ref="aprov-sintetica",
        parada=False,
    )


def _pedido(tipo_titular: str) -> PedidoDeEnvio:
    return PedidoDeEnvio(
        template_id="tpl-sintetico",
        finalidade=FINALIDADE_SINTETICA,
        tipo_titular=tipo_titular,
        titular_ref="titular-sintetico",
        canal_ref="canal-sintetico",
        variaveis=(),
        permissao=None,
        chave_idempotencia="k-cd144",
    )


@pytest.mark.parametrize("tipo", ["lead", "prospect"])
async def test_portao_recusa_campanha_para_prospect_com_mapa_vazio(
    monkeypatch: pytest.MonkeyPatch, tipo: str
) -> None:
    camp = _campanha()
    monkeypatch.setattr(cp, "REGISTRO_DE_CAMPANHAS", (camp,))
    gate = AsyncMock()
    with pytest.raises(CampanhaRecusadaError) as e:
        await PortaoDeCampanha().avaliar_envio(camp, _pedido(tipo), gate=gate)
    assert e.value.motivo is MotivoCampanha.CAMPAIGN_PURPOSE_NOT_ALLOWED_FOR_PROSPECT
    assert str(e.value) == "CAMPAIGN_PURPOSE_NOT_ALLOWED_FOR_PROSPECT"
    gate.avaliar.assert_not_called()


@pytest.mark.parametrize("tipo", ["lead", "prospect"])
@pytest.mark.parametrize(("numero", "chega_ao_gate"), [(1, False), (5, True), (7, True)])
async def test_portao_so_deixa_5_e_7_chegarem_ao_gate(
    monkeypatch: pytest.MonkeyPatch, numero: int, chega_ao_gate: bool, tipo: str
) -> None:
    camp = _campanha()
    monkeypatch.setattr(cp, "REGISTRO_DE_CAMPANHAS", (camp,))
    monkeypatch.setattr(fa, "NUMERO_DA_FINALIDADE", MappingProxyType({FINALIDADE_SINTETICA: numero}))
    gate = AsyncMock()
    if chega_ao_gate:
        # Chegar ao gate nao e' autorizar: o gate continua decidindo (e hoje recusa tudo por conta propria).
        await PortaoDeCampanha().avaliar_envio(camp, _pedido(tipo), gate=gate)
        gate.avaliar.assert_awaited_once()
    else:
        with pytest.raises(CampanhaRecusadaError) as e:
            await PortaoDeCampanha().avaliar_envio(camp, _pedido(tipo), gate=gate)
        assert e.value.motivo is MotivoCampanha.CAMPAIGN_PURPOSE_NOT_ALLOWED_FOR_PROSPECT
        gate.avaliar.assert_not_called()


async def test_recusas_anteriores_vem_antes(monkeypatch: pytest.MonkeyPatch) -> None:
    camp = _campanha()
    gate = AsyncMock()
    with pytest.raises(CampanhaRecusadaError) as e:
        await PortaoDeCampanha().avaliar_envio(camp, _pedido("prospect"), gate=gate)
    assert e.value.motivo is MotivoCampanha.CAMPAIGN_NOT_REGISTERED
    monkeypatch.setattr(cp, "REGISTRO_DE_CAMPANHAS", (camp,))
    divergente = dataclasses.replace(_pedido("prospect"), finalidade="outra")
    with pytest.raises(CampanhaRecusadaError) as e2:
        await PortaoDeCampanha().avaliar_envio(camp, divergente, gate=gate)
    assert e2.value.motivo is MotivoCampanha.CAMPAIGN_PURPOSE_MISMATCH
    gate.avaliar.assert_not_called()


class _StrQueCasa(str):
    """Subclasse comum: casa com "prospect" por igualdade; so' o tipo exato a recusa."""


class _StrQueNaoCasa(str):
    __hash__ = str.__hash__

    def __eq__(self, other: object) -> bool:
        return False

    def __ne__(self, other: object) -> bool:
        return True


@pytest.mark.parametrize(
    "tipo",
    [
        "Prospect",
        "prospect ",
        "LEAD",
        "",
        "outro",
        None,
        1,
        _StrQueNaoCasa("prospect"),
        _StrQueCasa("prospect"),
        _StrQueCasa("lead"),
    ],
)
async def test_tipo_de_titular_fora_dos_tres_recusa_no_portao(
    monkeypatch: pytest.MonkeyPatch, tipo: object
) -> None:
    camp = _campanha()
    monkeypatch.setattr(cp, "REGISTRO_DE_CAMPANHAS", (camp,))
    monkeypatch.setattr(fa, "NUMERO_DA_FINALIDADE", MappingProxyType({FINALIDADE_SINTETICA: 7}))
    gate = AsyncMock()
    pedido = dataclasses.replace(_pedido("prospect"), tipo_titular=tipo)  # type: ignore[arg-type]
    with pytest.raises(CampanhaRecusadaError) as e:
        await PortaoDeCampanha().avaliar_envio(camp, pedido, gate=gate)
    assert e.value.motivo is MotivoCampanha.CAMPAIGN_SUBJECT_KIND_NOT_ALLOWED
    assert str(e.value) == "CAMPAIGN_SUBJECT_KIND_NOT_ALLOWED"
    gate.avaliar.assert_not_called()


async def test_beneficiario_nao_e_afetado_pela_regra_do_prospect(monkeypatch: pytest.MonkeyPatch) -> None:
    # A regra so' acrescenta recusa para lead/prospect; o beneficiario segue para o gate como antes.
    camp = _campanha()
    monkeypatch.setattr(cp, "REGISTRO_DE_CAMPANHAS", (camp,))
    gate = AsyncMock()
    await PortaoDeCampanha().avaliar_envio(camp, _pedido("beneficiario"), gate=gate)
    gate.avaliar.assert_awaited_once()


# --- fronteira --------------------------------------------------------------------------------------


def test_sem_importador_em_src_fora_do_portao() -> None:
    usos = [
        str(p.relative_to(_RAIZ))
        for p in (_RAIZ / "src" / "maezo").rglob("*.py")
        if p.resolve() != Path(fa.__file__).resolve()
        and "finalidades_do_aceite" in p.read_text(encoding="utf-8")
    ]
    # Lista fechada e ordenada (CD-166): o portao de campanha e a guarda da recuperacao (E-SF-16).
    assert sorted(usos) == [
        "src/maezo/gateway/communications/campanha.py",
        "src/maezo/gateway/communications/recovery_dispatch.py",
    ]


def test_nenhum_arquivo_de_src_preenche_o_mapa_de_numero() -> None:
    # O mapa so' e' preenchido por monkeypatch em teste; em src, nenhuma escrita nem setattr dele.
    padrao = re.compile(r"NUMERO_DA_FINALIDADE\s*(:|\[|=|\.update|\|=)|setattr\([^)]*NUMERO_DA_FINALIDADE")
    achados = []
    for p in (_RAIZ / "src" / "maezo").rglob("*.py"):
        texto = p.read_text(encoding="utf-8")
        for linha in texto.splitlines():
            if padrao.search(linha) and not linha.lstrip().startswith("#"):
                achados.append(f"{p.relative_to(_RAIZ)}: {linha.strip()}")
    # A unica definicao e' a do proprio modulo, vazia.
    assert achados == [
        "src/maezo/gateway/communications/finalidades_do_aceite.py: "
        "NUMERO_DA_FINALIDADE: Final[Mapping[str, int]] = MappingProxyType({})"
    ], achados
    assert dict(fa.NUMERO_DA_FINALIDADE) == {}


def test_modulo_sem_assert_sem_norma_e_sem_prazo() -> None:
    fonte = inspect.getsource(fa)
    assert not [n for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Assert)]
    baixo = fonte.lower()
    normativos = (r"\blei\b", r"\bart\.", r"\brn ?\d", r"\bresolu", r"\blgpd\b", r"\bdias?\b", r"\bprazo\b")
    for proibido in normativos:
        assert not re.search(proibido, baixo), proibido
