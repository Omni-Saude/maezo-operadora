"""A rota `/receptor/simular` do Canal de Teste: assinatura, cercas e formato do envelope.

O teste que importa mais e' o primeiro: a assinatura produzida pelo canal e' validada pela
FUNCAO REAL do receptor (`verify_hub_signature`), nao por uma reimplementacao do HMAC aqui.
E' o unico jeito de o teste pegar o defeito classico desta rota — serializar o envelope uma
vez para assinar e outra para enviar, o que produz 401 por uma virgula de diferenca.

O envelope tambem e' passado pelo parser REAL do receptor (`extract_inbound_messages`), porque
uma assinatura perfeita sobre um envelope que o receptor nao entende so' troca 401 por silencio.
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any

import pytest

from maezo.platform.webhooks.whatsapp.dispatch import extract_inbound_messages
from maezo.platform.webhooks.whatsapp.security import verify_hub_signature

SEGREDO = "segredo-de-teste-nao-e-o-da-meta"
TELEFONE_OK = "5511900000001"


class _FalsoRfile:
    def __init__(self, dados: bytes) -> None:
        self._dados = dados

    def read(self, n: int) -> bytes:
        return self._dados[:n]


class _CanalSimulado:
    """Exercita `_simular_whatsapp` sem subir socket: captura o que ela responde e envia."""

    def __init__(self, servidor: Any, pedido: dict[str, Any]) -> None:
        self._servidor = servidor
        corpo = json.dumps(pedido).encode()
        self.rfile = _FalsoRfile(corpo)
        self.headers = {"Content-Length": str(len(corpo))}
        self.path = "/receptor/simular"
        self.status: int | None = None
        self.corpo_resposta: dict[str, object] | None = None
        self.enviado: tuple[bytes, str] | None = None

    # --- superficie que `_simular_whatsapp` usa do BaseHTTPRequestHandler ---
    def _responder_json(self, status: int, corpo: dict[str, object]) -> None:
        self.status = status
        self.corpo_resposta = corpo

    def send_response(self, status: int) -> None:
        self.status = status

    def end_headers(self) -> None:
        return None

    def executar(self) -> None:
        self._servidor.Handler._simular_whatsapp(self)


@pytest.fixture(autouse=True)
def _devolver_modulo_ao_estado_limpo():
    """Cada teste aqui RECARREGA o modulo do canal, e as constantes ficam com o ambiente do
    ultimo teste. Sem isto, um teste futuro que importe o canal herdaria a rota LIGADA por
    efeito colateral de outro arquivo — o tipo de acoplamento que so' aparece meses depois.
    Limpa explicitamente em vez de confiar na ordem de teardown do monkeypatch.
    """
    yield
    for nome in ("RECEPTOR_URL", "WHATSAPP_APP_SECRET", "CANAL_SIMULAR_RECEPTOR"):
        os.environ.pop(nome, None)
    importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))


@pytest.fixture
def canal(monkeypatch: pytest.MonkeyPatch):
    """Modulo do canal recarregado com a rota LIGADA e o envio interceptado."""
    monkeypatch.setenv("RECEPTOR_URL", "http://receptor.interno:8080")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SEGREDO)
    monkeypatch.setenv("CANAL_SIMULAR_RECEPTOR", "1")
    servidor = importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))

    enviados: list[tuple[bytes, str, str]] = []

    class _RespostaFalsa:
        status = 200

        def read(self) -> bytes:
            return b'{"status":"ok","dispatched":1,"failed":0}'

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def _urlopen_falso(req: Any, timeout: int = 0) -> _RespostaFalsa:
        enviados.append((req.data, req.headers["X-hub-signature-256"], req.full_url))
        return _RespostaFalsa()

    monkeypatch.setattr(servidor.urllib.request, "urlopen", _urlopen_falso)
    servidor._enviados = enviados  # type: ignore[attr-defined]
    return servidor


def _disparar(servidor: Any, **pedido: Any) -> _CanalSimulado:
    chamada = _CanalSimulado(servidor, pedido)
    chamada.executar()
    return chamada


def test_assinatura_e_validada_pela_funcao_real_do_receptor(canal: Any) -> None:
    """Os bytes assinados sao EXATAMENTE os bytes enviados — o defeito classico da rota."""
    chamada = _disparar(canal, texto="dor no peito e falta de ar", telefone=TELEFONE_OK)

    assert chamada.status == 200
    corpo, assinatura, url = canal._enviados[0]
    assert url == "http://receptor.interno:8080/webhook"
    assert verify_hub_signature(corpo, assinatura, SEGREDO) is True
    # E com o segredo errado NAO valida — prova que a verificacao acima nao e' vacua.
    assert verify_hub_signature(corpo, assinatura, "outro-segredo") is False


def test_envelope_e_entendido_pelo_parser_real_do_receptor(canal: Any) -> None:
    chamada = _disparar(canal, texto="minha filha esta com febre", telefone=TELEFONE_OK)
    corpo, _, _ = canal._enviados[0]

    eventos = extract_inbound_messages(json.loads(corpo))

    assert len(eventos) == 1
    assert eventos[0].text == "minha filha esta com febre"
    assert eventos[0].from_number == TELEFONE_OK
    assert eventos[0].message_id.startswith("wamid.CANAL-TESTE-")
    assert chamada.status == 200


@pytest.mark.parametrize(
    "telefone",
    [
        "5511988887777",  # numero real fora da faixa
        "551190000000",  # curto demais
        "551190000000123",  # a faixa como PREFIXO de um numero mais longo
        "x5511900000001",  # faixa como sufixo
        "",
    ],
)
def test_telefone_fora_da_faixa_de_teste_e_recusado(canal: Any, telefone: str) -> None:
    """A cerca do telefone e' ancorada nas duas pontas — prefixo e sufixo nao passam."""
    chamada = _disparar(canal, texto="oi", telefone=telefone)

    assert chamada.status == 400
    assert "faixa de teste" in str(chamada.corpo_resposta)
    assert canal._enviados == []


def test_texto_vazio_e_recusado(canal: Any) -> None:
    chamada = _disparar(canal, texto="   ", telefone=TELEFONE_OK)
    assert chamada.status == 400
    assert canal._enviados == []


def test_texto_acima_do_teto_e_recusado(canal: Any) -> None:
    chamada = _disparar(canal, texto="a" * 2001, telefone=TELEFONE_OK)
    assert chamada.status == 400
    assert canal._enviados == []


def test_rota_nao_existe_sem_o_portao_explicito(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ter segredo e URL NAO basta: sem `CANAL_SIMULAR_RECEPTOR=1` a rota devolve 404."""
    monkeypatch.setenv("RECEPTOR_URL", "http://receptor.interno:8080")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SEGREDO)
    monkeypatch.delenv("CANAL_SIMULAR_RECEPTOR", raising=False)
    servidor = importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))

    chamada = _disparar(servidor, texto="oi", telefone=TELEFONE_OK)

    assert chamada.status == 404


def test_sem_segredo_a_rota_recusa_em_vez_de_assinar_com_string_vazia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECEPTOR_URL", "http://receptor.interno:8080")
    monkeypatch.setenv("CANAL_SIMULAR_RECEPTOR", "1")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    servidor = importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))

    chamada = _disparar(servidor, texto="oi", telefone=TELEFONE_OK)

    assert chamada.status == 503


def test_corpo_nao_json_nao_derruba_a_rota(canal: Any) -> None:
    chamada = _CanalSimulado(canal, {})
    chamada.rfile = _FalsoRfile(b"nao e json")
    chamada.headers = {"Content-Length": "10"}
    chamada.executar()

    assert chamada.status == 400
    assert canal._enviados == []
