"""E-SF-16 (TB-E-18-a, TB-E-09-d): guarda de despacho da recuperacao, variante fechada. Dados sinteticos."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maezo.domain.rn413_contracting_clock import EstadoDoRelogio, EventoCandidato, estado_do_relogio
from maezo.gateway.communications.finalidades_do_aceite import AceiteDeFinalidades
from maezo.gateway.communications.recovery_eligibility import MotivoRecuperacao, RecuperacaoRecusada
from maezo.platform.webhooks.whatsapp import recuperacao as rc
from maezo.platform.webhooks.whatsapp.recuperacao import (
    MotivoDespachoRecuperacao,
    PedidoDeRecuperacao,
    RecuperacaoDespachoRecusado,
    despachar_recuperacao,
    guardar_envio_de_recuperacao,
    ler_chave_da_recuperacao,
)

D = "6" * 64
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_RAIZ = Path(rc.__file__).resolve().parents[5]


class _Supressao:
    def __init__(self, vigentes: frozenset[tuple[str, str]] = frozenset(), *, falha: bool = False) -> None:
        self._vigentes = vigentes
        self._falha = falha
        self.chamadas = 0

    async def vigentes(self) -> frozenset[tuple[str, str]]:
        self.chamadas += 1
        if self._falha:
            raise ConnectionError("fonte fora")
        return self._vigentes


def _relogio(dia: int = 3) -> EstadoDoRelogio:
    return estado_do_relogio(
        [EventoCandidato(D, "prospect_save_com_valores", INICIO)],
        proponente_digest=D,
        agora=INICIO + timedelta(days=dia),
    )


def _pedido(**kw: object) -> PedidoDeRecuperacao:
    base: dict[str, object] = {
        "tipo_titular": "prospect",
        "titular_ref": "titular-sintetico-1",
        "etapa": "proposta_incompleta",
        "tipo_mensagem": "lembrete",
        "aceite": AceiteDeFinalidades("prospect", frozenset({7})),
        "aceite_registrado": False,
        "toques_ja_enviados": 0,
    }
    base.update(kw)
    return PedidoDeRecuperacao(**base)  # type: ignore[arg-type]


async def _motivo(
    pedido: PedidoDeRecuperacao, *, supressao: _Supressao | None = None, dia: int = 3
) -> object:
    try:
        await guardar_envio_de_recuperacao(pedido, relogio=_relogio(dia), supressao=supressao or _Supressao())
    except (RecuperacaoDespachoRecusado, RecuperacaoRecusada) as e:
        assert str(e) == e.motivo.value
        return e.motivo
    pytest.fail("deveria recusar")


# --- estado fechado ------------------------------------------------------------------------------------


async def test_chave_desligada_recusa_antes_de_qualquer_consulta() -> None:
    assert rc.RECUPERACAO_LIGADA is False
    sup = _Supressao()
    with pytest.raises(RecuperacaoDespachoRecusado) as e:
        await despachar_recuperacao(_pedido(), relogio=_relogio(), supressao=sup)
    assert e.value.motivo is MotivoDespachoRecuperacao.RECOVERY_SWITCH_OFF
    assert sup.chamadas == 0


@pytest.mark.parametrize("valor", [True, "ligado", "1", 1, "on"])
def test_leitor_recusa_ligar(valor: object) -> None:
    with pytest.raises(RecuperacaoDespachoRecusado) as e:
        ler_chave_da_recuperacao(valor)
    assert e.value.motivo is MotivoDespachoRecuperacao.RECOVERY_SWITCH_OFF


@pytest.mark.parametrize("valor", [None, False, "desligado"])
def test_leitor_aceita_so_desligado(valor: object) -> None:
    assert ler_chave_da_recuperacao(valor) is False


async def test_cadencia_proposta_sem_base_e_zero_nada_sai() -> None:
    assert rc.TOQUES_PERMITIDOS_PROPOSTO_SEM_BASE == 0
    assert await _motivo(_pedido()) is MotivoDespachoRecuperacao.RECOVERY_CADENCE_EXHAUSTED


# --- ordem: oposicao antes de tudo ---------------------------------------------------------------------


async def test_oposicao_registrada_recusa_antes_de_tudo() -> None:
    sup = _Supressao(frozenset({("titular-sintetico-1", "whatsapp")}))
    # Mesmo com tudo o mais errado, a oposicao vem primeiro.
    ruim = _pedido(tipo_mensagem="pedido_de_documentacao", tipo_titular="beneficiario", aceite=None)
    assert await _motivo(ruim, supressao=sup) is MotivoDespachoRecuperacao.RECOVERY_OPPOSITION_REGISTERED
    assert sup.chamadas == 1


async def test_fonte_de_supressao_fora_recusa() -> None:
    assert await _motivo(_pedido(), supressao=_Supressao(falha=True)) is (
        MotivoDespachoRecuperacao.RECOVERY_SUPPRESSION_UNAVAILABLE
    )


async def test_oposicao_em_outro_canal_nao_bloqueia_este_mas_segue_recusando_pela_cadencia() -> None:
    sup = _Supressao(frozenset({("titular-sintetico-1", "email")}))
    assert await _motivo(_pedido(), supressao=sup) is MotivoDespachoRecuperacao.RECOVERY_CADENCE_EXHAUSTED


# --- tipo de mensagem -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tipo", ["pedido_de_documentacao", "documentacao", "Documento_pendente", "pericia_ou_entrevista"]
)
async def test_recovery_message_cannot_be_documentation_request(tipo: str) -> None:
    assert await _motivo(_pedido(tipo_mensagem=tipo)) is MotivoDespachoRecuperacao.DOCUMENT_REQUEST_FORBIDDEN


@pytest.mark.parametrize("tipo", ["oferta", "Lembrete", "", None, 1])
async def test_tipo_de_mensagem_fora_da_classe_recusa(tipo: object) -> None:
    assert await _motivo(_pedido(tipo_mensagem=tipo)) is (
        MotivoDespachoRecuperacao.RECOVERY_MESSAGE_KIND_NOT_ALLOWED
    )


# --- elegibilidade re-checada no envio ------------------------------------------------------------------


async def test_elegibilidade_rechecada_no_envio() -> None:
    assert await _motivo(_pedido(), dia=25) is MotivoRecuperacao.RECOVERY_CLOCK_BLOCKED
    assert await _motivo(_pedido(aceite_registrado=True)) is MotivoRecuperacao.RECOVERY_AFTER_ACCEPTANCE
    assert await _motivo(_pedido(tipo_titular="beneficiario")) is (
        MotivoRecuperacao.RECOVERY_SUBJECT_KIND_NOT_ALLOWED
    )
    assert await _motivo(_pedido(etapa="contrato_pendente_declaracao_de_saude")) is (
        MotivoRecuperacao.RECOVERY_AFTER_ACCEPTANCE
    )


# --- permissao: finalidade 7 do proprio titular --------------------------------------------------------


@pytest.mark.parametrize(
    "aceite",
    [
        None,
        "comercial",
        AceiteDeFinalidades("prospect", frozenset({1})),
        AceiteDeFinalidades("prospect", frozenset({5}), marca_finalidade_5=True),
        AceiteDeFinalidades("prospect", frozenset({5, 7})),  # 5 sem marca: aceite invalido
        AceiteDeFinalidades("lead", frozenset({7})),  # aceite de outro tipo de titular
        AceiteDeFinalidades("beneficiario", frozenset({1, 2})),
    ],
)
async def test_recovery_refuses_generic_or_foreign_purpose_permission(aceite: object) -> None:
    assert await _motivo(_pedido(aceite=aceite)) is MotivoDespachoRecuperacao.RECONTACT_PERMISSION_UNQUALIFIED


async def test_com_cadencia_maior_que_zero_so_o_caso_valido_passa(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prova de que a guarda nao e' vazia: com um toque permitido (so' no teste), o pedido valido e' elegivel
    # e o segundo toque recusa. Elegivel nao e' autorizacao: o OutboundGate continua decidindo depois.
    monkeypatch.setattr(rc, "TOQUES_PERMITIDOS_PROPOSTO_SEM_BASE", 1)
    elegivel = await guardar_envio_de_recuperacao(_pedido(), relogio=_relogio(), supressao=_Supressao())
    assert elegivel.etapa == "proposta_incompleta"
    lead = _pedido(tipo_titular="lead", aceite=AceiteDeFinalidades("lead", frozenset({7})))
    assert (
        await guardar_envio_de_recuperacao(lead, relogio=_relogio(), supressao=_Supressao())
    ).proponente_digest
    for toques in (1, 2, -1, True, "0"):
        assert await _motivo(_pedido(toques_ja_enviados=toques)) is (
            MotivoDespachoRecuperacao.RECOVERY_CADENCE_EXHAUSTED
        )


# --- fronteira -----------------------------------------------------------------------------------------


def test_pedido_sem_dado_pessoal_nem_de_saude() -> None:
    campos = {f.name for f in dataclasses.fields(PedidoDeRecuperacao)}
    assert campos == {
        "tipo_titular",
        "titular_ref",
        "etapa",
        "tipo_mensagem",
        "aceite",
        "aceite_registrado",
        "toques_ja_enviados",
    }


def test_nao_importado_por_dispatch_nem_por_raiz_de_composicao() -> None:
    usos = [
        str(p.relative_to(_RAIZ))
        for p in (_RAIZ / "src" / "maezo").rglob("*.py")
        if p.resolve() != Path(rc.__file__).resolve()
        and ("whatsapp.recuperacao" in (t := p.read_text(encoding="utf-8")) or "from .recuperacao" in t)
    ]
    assert usos == []


def test_sem_assert_e_sem_nome_de_sinal_na_fonte() -> None:
    fonte = inspect.getsource(rc)
    assert not [n for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Assert)]
    assert "ret." not in fonte
