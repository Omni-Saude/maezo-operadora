"""A rota de ingresso do agente: o caminho que executa um turno.

Estes testes existem porque o defeito que motivou o módulo era invisível em teste unitário:
`Harness.invoke` estava correto e testado, e ninguém o chamava. Então o que se prova aqui
não é o grafo — é a FIAÇÃO: que a rota existe, que ela recusa entrada malformada antes de
gastar um turno, e que o `thread_id` que ela deriva passa no portão de PHI.

O último é o que mais importa. `AUTH-{tenant}-{guia}` é RECUSADO por
`assert_phi_safe_thread_id`, e sem a derivação a primeira invocação real falharia em
produção com `ValueError` — parecendo "o agente não funciona".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maezo.runtime.agent_runtime.ingress import build_ingress_router
from maezo.runtime.checkpoint import assert_phi_safe_thread_id

CHAVE_HMAC = "a" * 64


@dataclass
class _SettingsFalsas:
    agent_id: str = "rafael"


@dataclass
class _HarnessFalso:
    """Registra o que recebeu. NÃO executa grafo — o objeto sob teste é a rota."""

    resultado: dict[str, Any] = field(default_factory=dict)
    chamadas: list[tuple[dict[str, Any], str | None]] = field(default_factory=list)

    async def invoke(self, state: dict[str, Any], *, thread_id: str | None = None) -> dict[str, Any]:
        self.chamadas.append((state, thread_id))
        return self.resultado


@dataclass
class _EstadoFalso:
    settings: _SettingsFalsas = field(default_factory=_SettingsFalsas)
    harness: _HarnessFalso | None = None


def _cliente(estado: _EstadoFalso) -> TestClient:
    app = FastAPI()
    app.include_router(build_ingress_router(estado))  # type: ignore[arg-type]  # duck-typed de propósito
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _chave_de_pseudonimo(monkeypatch: pytest.MonkeyPatch) -> None:
    """O pseudonimizador é fail-closed sem chave (ADR-0035) — e isso é correto."""
    monkeypatch.setenv("PHI_HMAC_KEY", CHAVE_HMAC)
    from maezo.platform.privacy.key_scrubber import reset_egress_pseudonymizer_cache

    reset_egress_pseudonymizer_cache()


_PEDIDO_MINIMO = {
    "tenant_id": "amh",
    "numero_guia_tiss": "12345678",
    "canal": "portal_tiss",
    "codigo_procedimento_tuss": "40901114",
}


def test_sem_harness_responde_503_e_nao_500() -> None:
    """Bring-up incompleto é indisponibilidade temporária, não erro do servidor.

    Um 500 aqui faria um balanceador tratar "ainda subindo" como "quebrado".
    """
    r = _cliente(_EstadoFalso(harness=None)).post("/v1/autorizacoes", json=_PEDIDO_MINIMO)
    assert r.status_code == 503
    assert "bring-up" in r.json()["detail"]


def test_campo_obrigatorio_ausente_nao_gasta_um_turno() -> None:
    harness = _HarnessFalso()
    r = _cliente(_EstadoFalso(harness=harness)).post(
        "/v1/autorizacoes", json={"tenant_id": "amh", "canal": "portal_tiss"}
    )
    assert r.status_code == 422
    assert "numero_guia_tiss" in r.json()["detail"]
    assert harness.chamadas == [], "recusou depois de invocar — o turno foi gasto em vão"


def test_campo_de_saida_plantado_e_recusado() -> None:
    """Uma chave OUTPUT-ONLY vinda do chamador é injeção, e o construtor estrito a barra.

    `route` é calculado pelo grafo. Aceitá-lo do chamador deixaria alguém decidir o desfecho
    da autorização por fora da avaliação.
    """
    harness = _HarnessFalso()
    r = _cliente(_EstadoFalso(harness=harness)).post(
        "/v1/autorizacoes", json={**_PEDIDO_MINIMO, "route": "auto_approve"}
    )
    assert r.status_code == 422
    assert "route" in r.json()["detail"]
    assert harness.chamadas == []


def test_thread_id_derivado_passa_no_portao_de_phi() -> None:
    """O ponto central: a business key crua é recusada; a derivada é aceita."""
    with pytest.raises(ValueError, match="KEYED pseudonym"):
        assert_phi_safe_thread_id("AUTH-amh-12345678")

    harness = _HarnessFalso(resultado={"business_key": "AUTH-amh-12345678", "route": "human_auditor"})
    r = _cliente(_EstadoFalso(harness=harness)).post("/v1/autorizacoes", json=_PEDIDO_MINIMO)

    assert r.status_code == 200
    (estado_recebido, thread_id) = harness.chamadas[0]
    assert thread_id is not None
    assert thread_id.startswith("AUTH-hk1_"), thread_id
    assert_phi_safe_thread_id(thread_id)  # não levanta

    # A guia NÃO aparece no thread_id — é o que torna a linha de checkpoint irreversível.
    assert "12345678" not in thread_id
    # …mas continua no estado que o grafo recebe, porque a business key do CIB Seven precisa dela.
    assert estado_recebido["numero_guia_tiss"] == "12345678"


def test_resposta_devolve_contrato_fechado_e_a_duracao() -> None:
    harness = _HarnessFalso(
        resultado={
            "business_key": "AUTH-amh-12345678",
            "route": "human_auditor",
            "desfecho": "ANALISE_HUMANA",
            "process_started": True,
            "campo_interno_do_grafo": "não deve sair",
        }
    )
    r = _cliente(_EstadoFalso(harness=harness)).post("/v1/autorizacoes", json=_PEDIDO_MINIMO)

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["agent_id"] == "rafael"
    assert isinstance(corpo["duracao_ms"], int)
    assert corpo["resultado"]["desfecho"] == "ANALISE_HUMANA"
    assert "campo_interno_do_grafo" not in corpo["resultado"], "contrato de saída vazou campo interno"


def test_falha_dentro_do_turno_vira_502_com_o_tipo_do_erro() -> None:
    """502 e não 500: o turno falhou no grafo/dependências, não no tratamento da requisição."""

    @dataclass
    class _HarnessQueFalha:
        async def invoke(self, state: dict[str, Any], *, thread_id: str | None = None) -> dict[str, Any]:
            raise RuntimeError("cibseven indisponivel")

    r = _cliente(_EstadoFalso(harness=_HarnessQueFalha())).post(  # type: ignore[arg-type]
        "/v1/autorizacoes", json=_PEDIDO_MINIMO
    )
    assert r.status_code == 502
    assert "RuntimeError" in r.json()["detail"]
    assert "cibseven indisponivel" in r.json()["detail"]
