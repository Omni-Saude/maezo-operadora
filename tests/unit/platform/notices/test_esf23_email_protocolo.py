"""E-SF-23 EM-01, variante FECHADA: canal escuro, sem rede, sem caixa corporativa, nada montado."""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import re
from pathlib import Path

import pytest

from maezo.platform.notices.email import admissao, caixa_de_saida, composicao
from maezo.platform.notices.email.admissao import QUALIFICACAO_DO_REPOSITORIO, admitir_canal
from maezo.platform.notices.email.caixa_de_saida import (
    PRAZO_DE_GUARDA_DA_PROVA,
    RegistroDeSaida,
    enviar_protocolo_por_email,
)
from maezo.platform.notices.email.composicao import compor_email_de_protocolo
from maezo.platform.notices.email.erros import CanalDeEmailRecusado, ComposicaoRecusada, EventoRecusado
from maezo.platform.notices.email.eventos import aplicar_evento_do_provedor
from maezo.platform.notices.email.porta import AceiteDoProvedor, MensagemDeEmail, TransporteEscuro

PKG = Path(__file__).resolve().parents[4] / "src" / "maezo" / "platform" / "notices"
SRC = PKG.parents[1]
ENDERECO = "pessoa@exemplo.invalid"


class Espiao:
    def __init__(self) -> None:
        self.chamadas: list[MensagemDeEmail] = []

    async def enviar(self, mensagem: MensagemDeEmail) -> AceiteDoProvedor:
        self.chamadas.append(mensagem)
        return AceiteDoProvedor(message_id=mensagem.message_id, provedor_ref="esp-falso")


def _enviar(transporte: Espiao | TransporteEscuro, **kw: object) -> RegistroDeSaida:
    args: dict[str, object] = {
        "qualificacao": QUALIFICACAO_DO_REPOSITORIO,
        "transporte": transporte,
        "destinatario_ref": "custodia:abc123",
        "protocolo_ref": "2026-10-0001",
        "etapas": ["proposta", "assinatura"],
        "message_id": "m-1",
    }
    args.update(kw)
    return asyncio.run(enviar_protocolo_por_email(**args))  # type: ignore[arg-type]


def _registro() -> RegistroDeSaida:
    return RegistroDeSaida(
        message_id="m-1", destinatario_ref="c:1", digest_do_corpo="0" * 64, estado="aceito_pelo_provedor"
    )


def test_transport_dark_by_default() -> None:
    with pytest.raises(CanalDeEmailRecusado) as e:
        asyncio.run(TransporteEscuro().enviar(MensagemDeEmail("m", "r", "a", "c")))
    assert e.value.code == "channel_disabled"
    with pytest.raises(CanalDeEmailRecusado) as e2:
        admitir_canal(QUALIFICACAO_DO_REPOSITORIO)
    assert e2.value.code == "protocol_email_channel_unqualified"
    espiao = Espiao()
    with pytest.raises(CanalDeEmailRecusado) as e3:
        _enviar(espiao, protocolo_ref="invalido com letras")  # recusa antes de compor
    assert e3.value.code == "protocol_email_channel_unqualified"
    assert espiao.chamadas == []


def test_recipient_address_never_in_outbox_or_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(caixa_de_saida, "admitir_canal", lambda q: None)
    espiao = Espiao()
    with caplog.at_level(logging.DEBUG):
        reg = _enviar(espiao, destinatario_ref="custodia:opaca-1")
    assert len(espiao.chamadas) == 1
    assert ENDERECO not in repr(reg) and ENDERECO not in caplog.text
    assert {f for f in RegistroDeSaida.__slots__} == {
        "message_id",
        "destinatario_ref",
        "digest_do_corpo",
        "estado",
    }
    assert "corpo" not in RegistroDeSaida.__slots__ and "endereco" not in RegistroDeSaida.__slots__
    assert reg.destinatario_ref == "custodia:opaca-1"


@pytest.mark.parametrize(
    ("protocolo", "etapas"),
    [
        ("2026-10-0001", ["declaracao de saude"]),
        ("2026-10-0001", ["diagnostico"]),
        ("ABC 123", ["proposta"]),
        ("2026 10", ["proposta"]),
        ("2026-10-0001", "proposta"),
    ],
)
def test_email_body_rejects_sensitive_field_classes(protocolo: str, etapas: object) -> None:
    with pytest.raises(ComposicaoRecusada) as e:
        compor_email_de_protocolo(protocolo_ref=protocolo, etapas=etapas)  # type: ignore[arg-type]
    assert e.value.code == "email_body_field_class_refused"
    assert "saude" not in str(e.value) and "ABC" not in str(e.value)


def test_email_body_has_no_hosted_or_bearer_url() -> None:
    _, corpo = compor_email_de_protocolo(
        protocolo_ref="2026-10-0001", etapas=list(composicao.ETAPAS_EXIBIVEIS)
    )
    assert not re.search(r"https?://|www\.", corpo, re.IGNORECASE)
    assert "ativação" in corpo
    assert not composicao._URL.search("texto limpo") and composicao._URL.search("veja www.x.com")


def test_mandatory_notice_cannot_carry_facultative_content() -> None:
    assert list(inspect.signature(compor_email_de_protocolo).parameters) == ["protocolo_ref", "etapas"]
    assunto, corpo = compor_email_de_protocolo(protocolo_ref="2026-10-0001", etapas=["pagamento"])
    assert assunto == "Protocolo da sua contratação"
    assert re.sub(r"\d", "", corpo.replace("2026-10-0001", "")) == corpo.replace("2026-10-0001", "")
    baixo = corpo.lower()
    for proibido in ("prazo", "dias", "art"):
        assert proibido not in baixo
    assert not re.search(r"\bRN\b", corpo)


def test_provider_accept_is_not_delivery() -> None:
    reg = _enviar_forcado()
    assert reg.estado == "aceito_pelo_provedor"
    assert len(reg.digest_do_corpo) == 64


def _enviar_forcado() -> RegistroDeSaida:
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(caixa_de_saida, "admitir_canal", lambda q: None)
        return _enviar(Espiao())
    finally:
        mp.undo()


def test_unauthenticated_esp_event_is_hint_only() -> None:
    reg = _registro()
    evento = {"message_id": "m-1", "type": "delivered"}
    assert aplicar_evento_do_provedor(reg, evento, verificador=lambda e: False) == reg

    def explode(e: object) -> bool:
        raise RuntimeError("x")

    assert aplicar_evento_do_provedor(reg, evento, verificador=explode) == reg
    assert aplicar_evento_do_provedor(reg, evento, verificador=lambda e: True).estado == "entregue"
    falho = aplicar_evento_do_provedor(
        reg, {"message_id": "m-1", "type": "bounced"}, verificador=lambda e: True
    )
    assert falho.estado == "falhou"
    assert (
        aplicar_evento_do_provedor(reg, {"message_id": "m-1", "type": "x"}, verificador=lambda e: True) == reg
    )


def test_delivery_event_for_foreign_message_refused() -> None:
    with pytest.raises(EventoRecusado) as e:
        aplicar_evento_do_provedor(
            _registro(), {"message_id": "outro", "type": "delivered"}, verificador=lambda e: True
        )
    assert e.value.code == "delivery_event_for_foreign_message"


def _arquivos() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def test_sem_ponte_de_caixa_corporativa_e_sem_rede() -> None:
    proibidos = {"httpx", "smtplib", "socket", "requests", "aiohttp", "imaplib", "poplib"}
    for arq in _arquivos():
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            nomes: list[str] = []
            if isinstance(no, ast.Import):
                nomes = [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom) and no.module:
                nomes = [no.module]
            assert not {n.split(".")[0] for n in nomes} & proibidos, arq
        texto = arq.read_text(encoding="utf-8").lower()
        for termo in ("imap", "pop3", "mailbox", "caixa corporativa"):
            assert termo not in texto, (arq, termo)


def test_nada_montado() -> None:
    for arq in SRC.rglob("*.py"):
        if PKG in arq.parents:
            continue
        assert "maezo.platform.notices" not in arq.read_text(encoding="utf-8"), arq


def test_prazo_de_guarda_sem_base() -> None:
    assert PRAZO_DE_GUARDA_DA_PROVA.valor is None
    assert PRAZO_DE_GUARDA_DA_PROVA.marca == "proposto, sem base"
    assert admissao.QUALIFICACAO_DO_REPOSITORIO.estado == "NAO_QUALIFICADO"
