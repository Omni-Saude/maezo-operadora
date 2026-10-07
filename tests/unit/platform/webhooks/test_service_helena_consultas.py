"""Fatos do plano na Helena (DL de 07/10/2026): flag, composicao fail-closed e a fonte `ConsultasPlanoAmh`.

1. `MAEZO_HELENA_CONSULTAS_AMH` nasce desligada e EXIGE a identidade ligada (o settings recusa o boot).
2. Ligada sem configuracao, sem o OpenAPI TINA, sem o escopo `interop/tina.read` ou sem o pin v1.2 (o
   estado real: manifest DRAFT), o receptor RECUSA servir — nunca sobe "sem consultas" em silencio.
3. Com os contratos pinados (sinteticos), a mesma composicao da identidade traz o adaptador TINA sob o
   principal `helena`, e o despachante passa a fonte ao grafo (no' `consultar_plano`).
4. A fonte: base legal fixa, uma leitura por subtipo, recusa/prazo/outro sujeito -> `None`, nunca levanta.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import structlog
from pydantic import ValidationError

import maezo.platform.webhooks.service as svc
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import CarenciasView, ElegibilidadeView, RequisicoesView
from tests.unit.platform.webhooks.test_service_roteador import _amh, _checkpointer_ok, _settings

__all__ = ["_checkpointer_ok"]  # fixture reexportada

_ESCOPOS_COM_TINA = "interop/billing.read interop/subject.resolve interop/profile.read interop/tina.read"


def _com_consultas(tmp_path: object, **sobre: object) -> dict[str, object]:
    base = Path(str(tmp_path))
    (base / "tina.yaml").write_bytes(b"z")
    valores: dict[str, object] = {
        **_amh(tmp_path),
        "roteador_lucas_enabled": False,
        "lucas_fonte_cobranca": "simulada",
        "helena_identidade_amh": True,
        "helena_consultas_amh": True,
        "amh_interop_scopes": _ESCOPOS_COM_TINA,
        "amh_tina_openapi_path": str(base / "tina.yaml"),
    }
    valores.update(sobre)
    return valores


# --- 1. a flag -----------------------------------------------------------------------------------


def test_consultas_nascem_desligadas() -> None:
    assert _settings().helena_consultas_amh is False


def test_consultas_le_o_nome_canonico_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_HELENA_CONSULTAS_AMH", "true")
    monkeypatch.setenv("MAEZO_HELENA_IDENTIDADE_AMH", "true")
    assert _settings().helena_consultas_amh is True


def test_consultas_sem_identidade_recusa_o_boot() -> None:
    with pytest.raises(ValidationError, match="MAEZO_HELENA_IDENTIDADE_AMH"):
        _settings(helena_consultas_amh=True)


def test_desligada_o_despachante_nao_tem_fonte_e_o_grafo_nao_tem_o_no() -> None:
    dispatcher, _ = svc._build_dispatcher(_settings())
    assert dispatcher.consultas_plano is None
    compilado, _ = dispatcher._compile_turn_graph(object())  # type: ignore[arg-type]
    assert "consultar_plano" not in compilado.nodes


# --- 2. composicao fail-closed --------------------------------------------------------------------


async def test_ligada_sem_pin_v1_2_recusa_servir(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
    _checkpointer_ok: None,  # noqa: F811
) -> None:
    """Estado real de hoje: o lock NAO tem `manifest_v1_2` (DRAFT). Com billing/subject resolvidos, e'
    o adaptador TINA que recusa — e com ele o boot inteiro."""
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr
    from tests.unit.adapters.amh.test_tina import _openapi as contrato_tina

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    (Path(str(tmp_path)) / "tina.yaml").write_bytes(contrato_tina())
    valores = _com_consultas(tmp_path)
    valores["amh_tina_openapi_path"] = str(Path(str(tmp_path)) / "tina.yaml")
    state = svc.WebhookState(settings=_settings(runtime_mode="kubernetes", **valores))
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None
    assert state.helena_consultas is None
    erro = state.dispatcher_error or ""
    assert "helena identity build failed" in erro and "tina_contract_invalid" in erro
    assert "segredo-SINTETICO" not in erro


@pytest.mark.parametrize(
    ("sobre", "esperado"),
    [
        ({"amh_tina_openapi_path": None}, "amh_tina_openapi_path"),
        (
            {"amh_interop_scopes": "interop/billing.read interop/subject.resolve interop/profile.read"},
            "escopos",
        ),
    ],
)
async def test_ligada_sem_openapi_ou_escopo_tina_recusa_servir(
    tmp_path: object,
    _checkpointer_ok: None,  # noqa: F811
    sobre: dict[str, object],
    esperado: str,
) -> None:
    state = svc.WebhookState(
        settings=_settings(runtime_mode="kubernetes", **_com_consultas(tmp_path, **sobre))
    )
    await svc._bring_up_dependencies(state)
    assert state.dispatcher is None
    assert esperado in (state.dispatcher_error or "")


def test_build_com_consultas_e_identidade_desligada_recusa() -> None:
    settings = _settings()
    object.__setattr__(settings, "helena_consultas_amh", True)  # contorna o validador: defesa em profundidade
    dispatcher, _ = svc._build_dispatcher(settings)
    with pytest.raises(ValueError, match="exige MAEZO_HELENA_IDENTIDADE_AMH"):
        svc._build_helena_amh(settings, dispatcher)


# --- 3. com contratos pinados (sinteticos) ---------------------------------------------------------


def _pinar_tudo(monkeypatch: pytest.MonkeyPatch, base: Path) -> None:
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr
    from maezo.adapters.amh import tina
    from maezo.adapters.amh.contract import load_contract_pin
    from tests.unit.adapters.amh.test_subject_resolution import _contrato as contrato_sr
    from tests.unit.adapters.amh.test_tina import _openapi as contrato_tina

    class _Billing:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Billing)
    sr_raw = contrato_sr()
    tina_raw = contrato_tina().replace(b"purpose1", b"sharing_amh_internal")
    (base / "s.yaml").write_bytes(sr_raw)
    (base / "t.yaml").write_bytes(tina_raw)
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        sr,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={sr.ARTIFACT: hashlib.sha256(sr_raw).hexdigest()}
        ),
    )
    monkeypatch.setattr(
        tina,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={tina.ARTIFACT: hashlib.sha256(tina_raw).hexdigest()}
        ),
    )


async def test_com_contratos_pinados_a_fonte_sai_sob_o_principal_helena_e_chega_ao_grafo(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato
    from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh

    base = Path(str(tmp_path))
    _pinar_tudo(monkeypatch, base)
    settings = _settings(
        **_com_consultas(
            tmp_path,
            amh_subject_resolution_openapi_path=str(base / "s.yaml"),
            amh_tina_openapi_path=str(base / "t.yaml"),
        )
    )
    dispatcher, _ = svc._build_dispatcher(settings)
    identidade, consultas = svc._build_helena_amh(settings, dispatcher)
    assert identidade is not None
    assert isinstance(consultas, ConsultasPlanoAmh)
    assert isinstance(consultas.consentimento, BaseLegalExecucaoDeContrato)
    executor = consultas.port._executor  # type: ignore[attr-defined]
    assert executor._seam.principal == "helena" and executor._version == "helena@v0"
    assert "interop/tina.read" in executor._tokens.scopes
    dispatcher.identidade = identidade
    dispatcher.consultas_plano = consultas
    compilado, _ = dispatcher._compile_turn_graph(object())  # type: ignore[arg-type]
    assert "consultar_plano" in compilado.nodes
    await identidade.aclose()


# --- 4. a fonte ------------------------------------------------------------------------------------

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_ELEG = ElegibilidadeView(REF, True, (), "2026-10-06T10:00:00Z", frozenset())
_CAR = CarenciasView(REF, 0, (), "2026-10-06T10:00:00Z")
_REQ = RequisicoesView(REF, (), "2026-10-06T10:00:00Z")


class _Port:
    def __init__(self, resultado: Any = None, atraso: float = 0.0) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []
        self._resultado = resultado
        self._atraso = atraso

    async def _r(self, nome: str, ref: str, padrao: Any, **kw: Any) -> Any:
        self.chamadas.append((nome, kw))
        await asyncio.sleep(self._atraso)
        return self._resultado if self._resultado is not None else PortResult.ok(padrao)

    async def get_elegibilidade(self, ref: str, **kw: Any) -> Any:
        return await self._r("elegibilidade", ref, _ELEG, **kw)

    async def get_carencias(self, ref: str, **kw: Any) -> Any:
        return await self._r("carencias", ref, _CAR, **kw)

    async def get_requisicoes(self, ref: str, **kw: Any) -> Any:
        return await self._r("requisicoes", ref, _REQ, **kw)


class _BaseLegal:
    def __init__(self, valor: str | None = "base-legal:execucao-de-contrato") -> None:
        self.valor = valor

    async def decisao(self, ref: str, purpose: str) -> str | None:
        return self.valor


def _fonte(port: _Port, base_legal: _BaseLegal | None = None) -> Any:
    from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh

    return ConsultasPlanoAmh(
        port=port, consentimento=base_legal or _BaseLegal(), purpose_of_use="sharing_amh_internal"
    )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("subtipo", "leitura", "view"),
    [
        ("elegibilidade", "elegibilidade", _ELEG),
        ("carteirinha", "elegibilidade", _ELEG),
        ("carencia", "carencias", _CAR),
        ("autorizacao", "requisicoes", _REQ),
    ],
)
async def test_fonte_le_uma_vez_pelo_subtipo_com_a_base_legal(subtipo: str, leitura: str, view: Any) -> None:
    port = _Port()
    with structlog.testing.capture_logs() as logs:
        assert await _fonte(port).consultar(REF, subtipo) == view
    [(nome, kw)] = port.chamadas
    assert nome == leitura
    assert kw["consent_decision_ref"] == "base-legal:execucao-de-contrato"
    assert kw["purpose_of_use"] == "sharing_amh_internal"
    assert REF not in repr(logs)


async def test_fonte_limita_requisicoes_e_carencias() -> None:
    port = _Port()
    await _fonte(port).consultar(REF, "autorizacao")
    await _fonte(port).consultar(REF, "carencia")
    assert port.chamadas[0][1]["limite"] == 5 and port.chamadas[1][1]["limite"] == 20


@pytest.mark.parametrize(
    "port",
    [
        _Port(PortResult.refused(Reason.NOT_FOUND)),
        _Port(PortResult.refused(Reason.TIMEOUT)),
        _Port(
            PortResult.ok(
                ElegibilidadeView("amh:psr:v1:outro", True, (), "2026-10-06T10:00:00Z", frozenset())
            )
        ),
    ],
)
async def test_fonte_recusa_ou_outro_sujeito_vira_none(port: _Port) -> None:
    assert await _fonte(port).consultar(REF, "elegibilidade") is None


async def test_fonte_sem_base_legal_nao_le_nada() -> None:
    port = _Port()
    assert await _fonte(port, _BaseLegal(None)).consultar(REF, "elegibilidade") is None
    assert port.chamadas == []


async def test_fonte_que_estoura_o_prazo_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.platform.webhooks.whatsapp import helena_consultas

    monkeypatch.setattr(helena_consultas, "PRAZO_TOTAL_S", 0.01)
    assert await _fonte(_Port(atraso=0.2)).consultar(REF, "elegibilidade") is None


async def test_fonte_nunca_levanta() -> None:
    class _Quebrado(_Port):
        async def get_elegibilidade(self, ref: str, **kw: Any) -> Any:
            raise RuntimeError("detalhe")

    assert await _fonte(_Quebrado()).consultar(REF, "elegibilidade") is None
    assert await _fonte(_Port()).consultar(REF, "boleto") is None
