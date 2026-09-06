"""Gap `WEBHOOK-LOG-RAW-WAMID` — nenhum `wamid` BRUTO pode sair num campo de log.

Por que isto e um vazamento de PHI e nao ruido de telemetria: um `wamid` NAO e um token opaco.
Ele embute o telefone da contraparte em base64 dentro do proprio payload — o mesmo fato que fez a
chave DURAVEL de dedup ser pseudonimizada (`security.py::hash_message_id`,
`test_dedup_keys.py::test_a_real_shaped_wamid_leaks_the_phone_number_in_base64`). Enquanto o banco
guardava `hk1_...`, `app.py::_run_one_message` e tres linhas de `dispatch.py` mandavam o wamid
bruto para o stdout do receptor — ou seja, para o coletor de logs e para a retencao dele, fora de
qualquer inventario LGPD.

O que este arquivo prende, em duas camadas:

* COMPORTAMENTO — captura o structlog dos tres caminhos vivos (falha em `app.py`, ack de nao-texto,
  inicio de turno em `dispatch`) e afirma que o wamid bruto, o telefone que ele embute e ate o
  prefixo do payload base64 NAO aparecem, e que o que aparece e o pseudonimo keyed `hk1_`;
* ESTRUTURA — uma cerca AST sobre TODOS os modulos do pacote (derivada da arvore, nao de uma lista
  escrita a mao) que reprova qualquer chamada de logger que receba a expressao crua
  `<algo>.message_id` / `message_id`, ou um kwarg chamado `message_id`, mesmo sob outro nome de
  campo. A cerca tem auto-teste: ela e provada capaz de reprovar um trecho sintetico violador.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer
from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module
from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.dispatch import (
    HelenaDispatcher,
    InboundMessage,
    InboundNonTextMessage,
)
from maezo.platform.webhooks.whatsapp.security import (
    MESSAGE_ID_LOG_OMITTED,
    hash_message_id,
    log_safe_message_id,
)
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dedup_fakes import FakeDedupRegistry

_TENANT = "amh"
_PHONE = "5511999999999"
#: Um id de mensagem com a FORMA real da Cloud API: `wamid.` + base64 de um envelope cujo primeiro
#: campo e o telefone da contraparte em ASCII. Nao e um valor de producao capturado — e montado a
#: partir de `_PHONE` (identico ao de `test_dedup_keys.py`), entao prova a FORMA sem enviar o wamid
#: de ninguem para o repositorio.
_WAMID = "wamid.HBgNNTUxMTk5OTk5OTk5ORUCABIYFDNBMEE1NkY3RUUxQjA5RkQyNkE5AA=="
_WAMID_B64 = _WAMID.split(".", 1)[1]

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"


def _pseudonymizer() -> Pseudonymizer:
    return Pseudonymizer()


def _expected_pseudonym(message_id: str = _WAMID) -> str:
    return hash_message_id(message_id, _TENANT, _pseudonymizer())


def _assert_no_raw_wamid(logs: list[dict[str, Any]]) -> None:
    """Nenhuma renderizacao do wamid bruto — nem ele, nem o telefone que ele embute, nem um
    prefixo do payload base64 (um casamento parcial ainda seria vazamento)."""
    rendered = repr(logs)
    assert _WAMID not in rendered, "o wamid bruto chegou a uma linha de log"
    assert _PHONE not in rendered, "o telefone que o wamid embute chegou a uma linha de log"
    assert _WAMID_B64[:16] not in rendered, "um prefixo do payload base64 do wamid chegou ao log"
    for entry in logs:
        assert "message_id" not in entry, f"campo `message_id` cru no evento {entry.get('event')!r}"


# ---------------------------------------------------------------------------
# A premissa: um wamid NAO e opaco (re-afirmada aqui para o arquivo se sustentar sozinho)
# ---------------------------------------------------------------------------


def test_the_wamid_used_here_really_does_carry_the_phone_number() -> None:
    decoded = base64.b64decode(_WAMID_B64 + "=" * (-len(_WAMID_B64) % 4))
    assert _PHONE.encode() in decoded


# ---------------------------------------------------------------------------
# `log_safe_message_id` — o unico renderizador autorizado
# ---------------------------------------------------------------------------


def test_log_safe_message_id_is_the_same_keyed_pseudonym_the_dedup_key_uses() -> None:
    rendered = log_safe_message_id(_WAMID, _TENANT, _pseudonymizer())
    assert rendered == _expected_pseudonym()
    assert rendered.startswith(KEYED_PSEUDONYM_PREFIX)
    assert _WAMID not in rendered
    assert _PHONE not in rendered
    assert _WAMID_B64[:16] not in rendered


def test_log_safe_message_id_without_a_pseudonymizer_omits_instead_of_hashing_unkeyed() -> None:
    """Sem pseudonimizador o campo vira um marcador constante — NUNCA um sha256 sem chave do
    wamid, que seria reversivel pela mesma tabela precomputada que `hash_message_id` combate."""
    rendered = log_safe_message_id(_WAMID, _TENANT, None)
    assert rendered == MESSAGE_ID_LOG_OMITTED
    assert _WAMID not in rendered
    assert _PHONE not in rendered
    assert hashlib.sha256(_WAMID.encode()).hexdigest() not in rendered


def test_log_safe_message_id_without_a_wamid_omits_too() -> None:
    assert log_safe_message_id("", _TENANT, _pseudonymizer()) == MESSAGE_ID_LOG_OMITTED


# ---------------------------------------------------------------------------
# dispatch.py — ack de nao-texto e inicio de turno
# ---------------------------------------------------------------------------


class _ExplodingInference:
    async def generate(self, prompt: str, **_kwargs: Any) -> str:
        raise AssertionError("nenhum destes caminhos pode chamar o LLM")


def _dispatcher(client: Any | None = None) -> HelenaDispatcher:
    return HelenaDispatcher(
        tenant_id=_TENANT,
        inference=_ExplodingInference(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=client if client is not None else _FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=_pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
    )


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.reply.1"}]}


async def test_non_text_ack_telemetry_carries_the_pseudonym_never_the_raw_wamid() -> None:
    dispatcher = _dispatcher()

    with structlog.testing.capture_logs() as logs:
        await dispatcher.acknowledge_non_text(
            InboundNonTextMessage(from_number=_PHONE, message_type="document", message_id=_WAMID)
        )

    _assert_no_raw_wamid(logs)
    started = [entry for entry in logs if entry["event"] == "whatsapp_non_text_ack_started"]
    sent = [entry for entry in logs if entry["event"] == "whatsapp_non_text_ack_sent"]
    assert len(started) == 1 and len(sent) == 1
    assert started[0]["message_pseudonym"] == _expected_pseudonym()
    assert sent[0]["message_pseudonym"] == _expected_pseudonym()


async def test_dispatch_turn_started_telemetry_carries_the_pseudonym_never_the_raw_wamid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`helena_dispatch_turn_started` e emitido ANTES do `ainvoke`, entao um grafo que explode
    ainda exercita a linha de log — sem LLM, sem DMN e sem engine."""

    class _ExplodingGraph:
        def compile(self, *, checkpointer: Any = None) -> Any:
            return self

        async def ainvoke(self, _state: Any, _config: Any) -> Any:
            raise RuntimeError("turno explodiu depois do log de inicio")

    monkeypatch.setattr(dispatch_module, "build", lambda _config: _ExplodingGraph())
    dispatcher = _dispatcher()

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(RuntimeError, match="turno explodiu"),
    ):
        await dispatcher.dispatch(InboundMessage(from_number=_PHONE, text="oi", message_id=_WAMID))

    _assert_no_raw_wamid(logs)
    started = [entry for entry in logs if entry["event"] == "helena_dispatch_turn_started"]
    assert len(started) == 1
    assert started[0]["message_pseudonym"] == _expected_pseudonym()


# ---------------------------------------------------------------------------
# app.py — o caminho entrada -> dispatch -> falha
# ---------------------------------------------------------------------------


class _FailingDispatcher:
    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        raise RuntimeError("dispatch boom")

    async def acknowledge_non_text(self, message: InboundNonTextMessage) -> dict[str, Any]:
        raise RuntimeError("ack boom")


def _settings() -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(app_secret=APP_SECRET, verify_token=VERIFY_TOKEN, tenant_id=_TENANT)


def _payload() -> bytes:
    envelope = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": _PHONE, "id": _WAMID, "type": "text", "text": {"body": "oi"}}
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(envelope).encode()


async def _post(app: FastAPI, payload: bytes) -> httpx.Response:
    signature = f"sha256={hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()}"
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/webhook", content=payload, headers={"X-Hub-Signature-256": signature})


def _guard() -> WhatsAppDedupGuard:
    return WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=_pseudonymizer(), tenant=_TENANT)


async def test_webhook_dispatch_failure_logs_the_pseudonym_never_the_raw_wamid() -> None:
    """O caminho vivo do defeito: `whatsapp_dispatch_failed` carregava `message_id=<wamid bruto>`."""
    app = create_app(_settings(), dispatcher=_FailingDispatcher(), dedup=_guard())  # type: ignore[arg-type]

    with structlog.testing.capture_logs() as logs:
        resp = await _post(app, _payload())

    assert resp.status_code == 500
    _assert_no_raw_wamid(logs)
    failed = [entry for entry in logs if entry["event"] == "whatsapp_dispatch_failed"]
    assert len(failed) == 1
    assert failed[0]["message_pseudonym"] == _expected_pseudonym()
    assert failed[0]["tenant"] == _TENANT


async def test_webhook_dispatch_failure_without_a_dedup_guard_omits_instead_of_leaking() -> None:
    """Sem guard nao ha pseudonimizador alcancavel em `app.py`: o campo vira o marcador — e o
    wamid continua fora do log. A ausencia do guard ja e anunciada em nivel error na mesma
    requisicao (`whatsapp_webhook_dedup_guard_absent`), entao nao e um caminho silencioso."""
    app = create_app(_settings(), dispatcher=_FailingDispatcher(), dedup=None)  # type: ignore[arg-type]

    with structlog.testing.capture_logs() as logs:
        resp = await _post(app, _payload())

    assert resp.status_code == 500
    _assert_no_raw_wamid(logs)
    failed = [entry for entry in logs if entry["event"] == "whatsapp_dispatch_failed"]
    assert len(failed) == 1
    assert failed[0]["message_pseudonym"] == MESSAGE_ID_LOG_OMITTED
    assert any(entry["event"] == "whatsapp_webhook_dedup_guard_absent" for entry in logs)


# ---------------------------------------------------------------------------
# Cerca estatica (AST) — nenhuma chamada de logger recebe a expressao crua do wamid
# ---------------------------------------------------------------------------

#: Nomes que ligam um logger neste pacote. Derivado por leitura dos modulos, nao adivinhado: a
#: cerca falha alto (abaixo) se algum modulo ligar um logger com um nome fora desta lista.
_LOGGER_BINDINGS = frozenset({"logger", "_stdlib_logger", "log", "_logger"})
#: A expressao que carrega o wamid bruto — como atributo (`message.message_id`) ou como nome solto.
_RAW_WAMID_NAME = "message_id"
#: FRONTEIRA DE REDACAO: funcoes cuja SAIDA comprovadamente nao contem o wamid bruto, entao passar
#: o wamid a elas dentro de uma chamada de log e o comportamento CORRETO, nao a violacao. A lista e
#: curta e cada entrada tem a sua propriedade afirmada em teste de comportamento neste mesmo
#: arquivo (`test_every_sanitizer_on_the_fence_allowlist_really_sanitizes`) — nenhuma entra por
#: confianca no nome.
_SANITIZERS = frozenset(
    {"log_safe_message_id", "hash_message_id", "pseudonym", "inbound_key", "outbound_key"}
)


def _package_modules() -> list[Path]:
    """Todo modulo do pacote do webhook WhatsApp, derivado da ARVORE (o diretorio do pacote
    importado), nunca de uma lista escrita a mao que envelheceria em silencio."""
    package_dir = Path(dispatch_module.__file__).parent
    return sorted(package_dir.glob("*.py"))


def _logger_calls(tree: ast.AST) -> list[ast.Call]:
    """Toda chamada `<binding>.<nivel>(...)` onde `<binding>` liga um logger."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _LOGGER_BINDINGS
    ]


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _walk_until_sanitized(node: ast.AST) -> list[ast.AST]:
    """Todo no do subtree, PODANDO em cada chamada a um sanitizador de `_SANITIZERS`.

    A poda e o que distingue `dedup_key=dedup.inbound_key(message.message_id)` (correto: o wamid
    entra, o pseudonimo keyed sai) de `message_pseudonym=message.message_id` (o defeito). Ela vale
    exatamente para a chamada podada — qualquer outro uso cru no MESMO log continua visivel."""
    if isinstance(node, ast.Call) and _callee_name(node) in _SANITIZERS:
        return []
    collected: list[ast.AST] = [node]
    for child in ast.iter_child_nodes(node):
        collected.extend(_walk_until_sanitized(child))
    return collected


def _raw_wamid_violations(call: ast.Call) -> list[str]:
    """Motivos pelos quais ESTA chamada de log carregaria o wamid bruto.

    Duas familias, porque renomear o campo nao redige nada:
      * um kwarg chamado `message_id` (o campo bruto original), qualquer que seja o valor;
      * a expressao crua `<algo>.message_id` / `message_id` em QUALQUER argumento (inclusive
        dentro de f-strings e de chamadas aninhadas) que NAO esteja dentro de um sanitizador —
        inclusive sob um nome de campo inocente como `message_pseudonym`.
    """
    reasons: list[str] = []
    for keyword in call.keywords:
        if keyword.arg == _RAW_WAMID_NAME:
            reasons.append(f"kwarg `{_RAW_WAMID_NAME}=`")
    subtrees: list[ast.AST] = list(call.args) + [kw.value for kw in call.keywords]
    for subtree in subtrees:
        for node in _walk_until_sanitized(subtree):
            if isinstance(node, ast.Attribute) and node.attr == _RAW_WAMID_NAME:
                reasons.append(f"expressao crua `.{_RAW_WAMID_NAME}` fora de um sanitizador")
            elif isinstance(node, ast.Name) and node.id == _RAW_WAMID_NAME:
                reasons.append(f"nome cru `{_RAW_WAMID_NAME}` fora de um sanitizador")
    return reasons


def _scan(source: str, label: str) -> tuple[list[str], int]:
    tree = ast.parse(source)
    calls = _logger_calls(tree)
    findings = [
        f"{label}:{call.lineno} — {reason}" for call in calls for reason in _raw_wamid_violations(call)
    ]
    return findings, len(calls)


def test_every_sanitizer_on_the_fence_allowlist_really_sanitizes() -> None:
    """A poda da cerca so e legitima se cada nome da allowlist de fato apagar o wamid. Afirmado
    sobre as funcoes REAIS, nao sobre o nome delas."""
    guard = _guard()
    outputs = {
        "log_safe_message_id": log_safe_message_id(_WAMID, _TENANT, _pseudonymizer()),
        "hash_message_id": hash_message_id(_WAMID, _TENANT, _pseudonymizer()),
        "pseudonym": guard.pseudonym(_WAMID),
        "inbound_key": guard.inbound_key(_WAMID),
        "outbound_key": guard.outbound_key(_WAMID),
    }
    assert set(outputs) == set(_SANITIZERS), "a allowlist da cerca e esta lista divergiram"
    for name, rendered in outputs.items():
        assert _WAMID not in rendered, f"{name} devolveu o wamid bruto"
        assert _PHONE not in rendered, f"{name} devolveu o telefone embutido no wamid"
        assert _WAMID_B64[:16] not in rendered, f"{name} devolveu um prefixo do payload base64"
        assert KEYED_PSEUDONYM_PREFIX in rendered, f"{name} nao usou o esquema keyed `hk1_`"


def test_no_logger_call_in_the_whatsapp_webhook_package_carries_a_raw_wamid() -> None:
    modules = _package_modules()
    assert [path.name for path in modules] == [
        "__init__.py",
        "app.py",
        "dedup.py",
        "dispatch.py",
        "security.py",
        "settings.py",
    ], "o pacote mudou de forma — reveja a cerca antes de ajustar esta lista"

    findings: list[str] = []
    calls_per_module: dict[str, int] = {}
    for path in modules:
        module_findings, call_count = _scan(path.read_text(encoding="utf-8"), path.name)
        findings.extend(module_findings)
        calls_per_module[path.name] = call_count

    # A cerca nao pode ser verde por nao ter olhado nada: os dois modulos que o gap nomeia tem
    # chamadas de logger de verdade.
    assert calls_per_module["app.py"] > 0
    assert calls_per_module["dispatch.py"] > 0
    assert findings == [], "wamid bruto numa chamada de logger:\n" + "\n".join(findings)


def test_the_wamid_fence_actually_rejects_a_violating_snippet() -> None:
    """Auto-teste: a cerca acima seria verde tambem se o detector nao detectasse nada. Os quatro
    primeiros trechos sao as formas que o defeito teve ou teria (campo cru, campo renomeado sobre o
    valor cru, valor cru dentro de f-string, valor cru embrulhado numa funcao que NAO redige); os
    dois ultimos sao as formas corretas, que a cerca precisa deixar passar."""
    campo_cru, _ = _scan('logger.error("e", message_id=message.message_id)', "sintetico")
    renomeado, _ = _scan('logger.info("e", message_pseudonym=message.message_id)', "sintetico")
    fstring, _ = _scan('logger.info("e", detail=f"id={message.message_id}")', "sintetico")
    falso_wrap, _ = _scan('logger.info("e", message_pseudonym=str(message.message_id))', "sintetico")
    posicional, _ = _scan('_stdlib_logger.warning("id=%s", message.message_id)', "sintetico")
    keyed, _ = _scan(
        'logger.info("e", message_pseudonym=log_safe_message_id(m.message_id, t, p))', "sintetico"
    )
    dedup_key, _ = _scan('logger.info("e", dedup_key=dedup.inbound_key(m.message_id))', "sintetico")

    assert campo_cru, "a cerca nao pegaria o campo cru original"
    assert renomeado, "a cerca nao pegaria o valor cru sob um nome de campo inocente"
    assert fstring, "a cerca nao pegaria o valor cru interpolado numa f-string"
    assert falso_wrap, "a poda estaria larga demais: `str(...)` nao e um sanitizador"
    assert posicional, "a cerca nao pegaria o valor cru como argumento posicional"
    assert keyed == [], "a cerca reprovaria a forma correta (pseudonimo keyed)"
    assert dedup_key == [], "a cerca reprovaria a derivacao de chave de dedup, que ja e keyed"
