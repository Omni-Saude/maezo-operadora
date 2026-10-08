"""Acesso do beneficiario no despachante (DL-0083): gate antes dos agentes, escalonamento, ponte, PII.

Sem rede: o LLM EXPLODE se for chamado (prova que nenhum agente roda fora de `verificado`), a AMH e' dublê.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import structlog

from maezo.gateway.amh_interop import AmhSubjectVerifyHasher
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.acesso_ponte import (
    ConsentimentoDoAcesso,
    RefsVerificadas,
    ResolvedorVerificado,
)
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.platform.webhooks.whatsapp.test_acesso import (
    CPF,
    NASC,
    REF,
    TENANT_AMH,
    FakeConsentimentos,
    FakeResolvedor,
    FakeVerificacao,
    Relogio,
)

TENANT = "amh"
FONE = "5511977776666"
CHAVE = "k" * 24


class _LlmQueExplode:
    async def generate(self, prompt: str, **_kw: Any) -> str:
        raise AssertionError("nenhum agente pode rodar fora de `verificado`")


class _Cliente:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.r"}]}


class _Identidade:
    """Dublê de `IdentidadeHelena`: devolve a identidade de vocabulario fechado e conta as chamadas."""

    def __init__(self, identidade: dict[str, Any] | None) -> None:
        self.identidade = identidade
        self.chamadas = 0
        self.invalidacoes: list[str] = []

    async def resolver_com_desfecho(self, numero: str, *, conversation_id: str, pseudo_id: str) -> Any:
        self.chamadas += 1

        class R:
            pass

        r = R()
        r.identidade = self.identidade  # type: ignore[attr-defined]
        r.desfecho = "reconhecido" if self.identidade else "indeterminado"  # type: ignore[attr-defined]
        return r

    def invalidar(self, conversation_id: str) -> None:
        self.invalidacoes.append(conversation_id)


class _GrafoMarcador:
    """Substitui o grafo da Helena: registra que um turno de agente rodou (e o texto que recebeu)."""

    rodou: list[str] = []

    def compile(self, *, checkpointer: Any = None) -> _GrafoMarcador:
        return self

    async def ainvoke(self, estado: Any, _config: Any) -> dict[str, Any]:
        _GrafoMarcador.rodou.append(estado["message_body"])
        return {"response_text": "resposta do agente", "conversation_id": estado["conversation_id"]}


def _montar(identidade: dict[str, Any] | None = None) -> tuple[HelenaDispatcher, _Cliente, dict[str, Any]]:
    relogio = Relogio()
    hasher = AmhSubjectVerifyHasher(key=CHAVE, amh_tenant=TENANT_AMH)
    verif = FakeVerificacao(hasher)
    verif.cadastrar_cpf(CPF, REF)
    consent = FakeConsentimentos()
    servico = ac.AcessoBeneficiario(
        store=ac.AcessoStoreEmMemoria(),
        verification=verif,
        consents=consent,
        resolvedor=FakeResolvedor(),
        hash_telefone=lambda n: "a" * 64,
        hasher=hasher,
        lexicos=pre_roteamento.carregar(),
        amh_tenant=TENANT_AMH,
        hash_scheme="amh-subject-verify-v1",
        purpose_of_use="sharing_amh_internal",
        relogio=relogio,
    )
    cliente = _Cliente()
    cib = FakeCibSevenTransport()
    audit = FakeStartAuditSink()
    d = HelenaDispatcher(
        tenant_id=TENANT,
        inference=_LlmQueExplode(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cib,
        whatsapp_client=cliente,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=audit,
        acesso=servico,
        refs_verificadas=RefsVerificadas(),
    )
    d.identidade = _Identidade(identidade)  # type: ignore[assignment]
    return d, cliente, {"verif": verif, "consent": consent, "audit": audit, "servico": servico, "cib": cib}


def _m(texto: str, n: int = 0) -> InboundMessage:
    return InboundMessage(from_number=FONE, text=texto, message_id=f"wamid.{n}.{texto[:3]}")


async def _verificar(d: HelenaDispatcher) -> None:
    await d.dispatch(_m("oi", 1))
    await d.dispatch(_m("ACEITO", 2))
    r = await d.dispatch(_m(CPF, 3))
    assert r["acesso_estado"] == "verificado" and r["response_text"] == ac.RESPOSTA_VERIFICADO


async def test_agentes_nao_rodam_antes_da_verificacao(monkeypatch: pytest.MonkeyPatch) -> None:
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, cliente, _ = _montar()
    r1 = await d.dispatch(_m("quero saber da minha mensalidade", 1))
    r2 = await d.dispatch(_m("nao quero", 2))
    assert r1["response_text"] == r2["response_text"] == ac.TEXTO_CONSENTIMENTO
    assert [t for _, t in cliente.sent] == [ac.TEXTO_CONSENTIMENTO] * 2
    assert _GrafoMarcador.rodou == []


async def test_depois_de_verificado_o_fluxo_de_sempre_roda_e_a_cpf_nunca_chega_ao_grafo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, cliente, _ = _montar()
    with structlog.testing.capture_logs() as logs:
        await _verificar(d)
        r = await d.dispatch(_m("estou com dor de cabeca", 4))
    assert r["response_text"] == "resposta do agente"
    assert _GrafoMarcador.rodou == ["estou com dor de cabeca"]  # so' a mensagem pos-verificacao
    assert CPF not in repr(logs) and FONE not in repr(logs)
    assert all(CPF not in t for _, t in cliente.sent)


async def test_ponte_entrega_a_referencia_verificada_so_durante_o_turno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vistos: list[tuple[str | None, str | None]] = []

    class Grafo(_GrafoMarcador):
        async def ainvoke(self, estado: Any, _config: Any) -> dict[str, Any]:
            refs = d.refs_verificadas
            assert refs is not None
            ref = refs.ref_de(estado["beneficiario_pseudo_id"])
            vistos.append((ref, refs.consent_de(ref) if ref else None))
            return {"response_text": "ok", "conversation_id": estado["conversation_id"]}

    monkeypatch.setattr(dispatch_module, "build", lambda _c: Grafo())
    d, _, _ = _montar()
    await _verificar(d)
    await d.dispatch(_m("bom dia", 5))
    assert vistos == [(REF, "consent-1")]
    assert d.refs_verificadas is not None and d.refs_verificadas._por_pseudo_id == {}  # removida no fim


async def test_resolvedor_verificado_e_consentimento_do_acesso() -> None:
    refs = RefsVerificadas()
    resolvedor = ResolvedorVerificado(refs)

    class Base:
        async def decisao(self, ref: str, purpose: str) -> str | None:
            return "base-legal:execucao-de-contrato"

    consent = ConsentimentoDoAcesso(refs, Base())
    assert await resolvedor.portable_ref_com_desfecho("p", phone_hash="x") == (None, "indisponivel")
    assert await consent.decisao(REF, "x") == "base-legal:execucao-de-contrato"
    with refs.do_turno("p", REF, "consent-9"):
        assert await resolvedor.portable_ref("p", phone_hash=None) == REF
        assert (await resolvedor.portable_ref_com_desfecho("outro", phone_hash=None))[0] is None
        assert await consent.decisao(REF, "x") == "consent-9"
    assert await resolvedor.portable_ref("p", phone_hash=None) is None


async def test_tres_falhas_abrem_o_escalonamento_solicitacao_humano_sem_modelo() -> None:
    d, cliente, ctx = _montar()
    await d.dispatch(_m("oi", 1))
    await d.dispatch(_m("ACEITO", 2))
    await d.dispatch(_m("11144477735", 3))
    await d.dispatch(_m("123", 4))
    antes = len(ctx["audit"].calls)
    r = await d.dispatch(_m("52998224724", 5))
    assert r["response_text"] == ac.BLOQUEADO and r["escalation_started"] is True
    assert len(ctx["audit"].calls) > antes  # auditoria antes do efeito, pelo chokepoint de sempre
    chamadas = [c for c, _ in ctx["audit"].calls]
    assert any("solicitacao_humano" in repr(c) for c in chamadas)
    r = await d.dispatch(_m("oi", 6))
    assert r["response_text"] == ac.BLOQUEADO_CURTO and r["escalation_started"] is False
    assert "52998224724" not in repr(ctx["audit"].calls) and "11144477735" not in repr(ctx["audit"].calls)


async def test_aviso_de_identidade_fica_desligado_com_o_acesso(monkeypatch: pytest.MonkeyPatch) -> None:
    capturado: list[Any] = []

    class Grafo(_GrafoMarcador):
        async def ainvoke(self, estado: Any, _config: Any) -> dict[str, Any]:
            capturado.append(estado.get("identidade_desfecho"))
            return {"response_text": "ok", "conversation_id": estado["conversation_id"]}

    monkeypatch.setattr(dispatch_module, "build", lambda _c: Grafo())
    d, _, _ = _montar(identidade=_IDENT)
    await _verificar(d)
    await d.dispatch(_m("bom dia", 5))
    assert capturado == [None]


_IDENT = {
    "portable_subject_ref": REF,
    "faixa_etaria": "adulto",
    "plano_ativo": True,
    "vigencia_inicio": "2025-01-01",
    "vigencia_fim": "2026-12-31",
    "carencia_vigente": False,
    "titular_ref": None,
}


@pytest.mark.parametrize(
    ("pergunta", "esperado"),
    [
        ("meu plano está ativo?", "Seu plano está ativo, com vigência de 01/01/2025 até 31/12/2026."),
        ("qual a vigência do meu plano", "O seu plano consta com vigência de 01/01/2025 até 31/12/2026."),
        ("tenho carência?", "Consta no seu cadastro que não há carência vigente."),
        ("qual minha faixa etária", "A sua faixa etária no cadastro é: adulto (18 a 59 anos)."),
        ("sabe quem sou eu?", "Sim: você foi identificado como beneficiário da Austa Clínicas."),
    ],
)
async def test_helena_responde_dados_do_plano_por_modelo_fixo(
    monkeypatch: pytest.MonkeyPatch, pergunta: str, esperado: str
) -> None:
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, cliente, _ = _montar(identidade=_IDENT)
    await _verificar(d)
    r = await d.dispatch(_m(pergunta, 7))
    assert r["response_text"] == esperado
    assert cliente.sent[-1][1] == esperado and _GrafoMarcador.rodou == []
    for proibido in ("Maria", FONE, CPF, "1985"):
        assert proibido not in esperado


async def test_pergunta_de_plano_com_sinal_de_saude_vai_para_o_grafo(monkeypatch: pytest.MonkeyPatch) -> None:
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, _, _ = _montar(identidade=_IDENT)
    await _verificar(d)
    await d.dispatch(_m("meu plano esta ativo? estou com dor no peito", 8))
    assert len(_GrafoMarcador.rodou) == 1


async def test_cadastro_indisponivel_responde_frase_fixa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, _, _ = _montar(identidade=None)
    await _verificar(d)
    r = await d.dispatch(_m("meu plano esta ativo?", 9))
    assert "não consegui consultar o seu cadastro" in r["response_text"]


async def test_nova_verificacao_invalida_o_cache_da_identidade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, _, _ = _montar()
    await _verificar(d)
    ident = d.identidade
    assert isinstance(ident, _Identidade) and len(ident.invalidacoes) == 1
    await d.dispatch(_m("oi tudo bem", 5))
    assert len(ident.invalidacoes) == 1  # so' a verificacao invalida; turnos comuns reusam o cache


async def test_com_a_flag_desligada_o_despachante_e_o_de_sempre(monkeypatch: pytest.MonkeyPatch) -> None:
    _GrafoMarcador.rodou.clear()
    monkeypatch.setattr(dispatch_module, "build", lambda _c: _GrafoMarcador())
    d, cliente, _ = _montar()
    d.acesso = None
    d.refs_verificadas = None
    await d.dispatch(_m("qualquer coisa", 1))
    assert _GrafoMarcador.rodou == ["qualquer coisa"] and cliente.sent == []


def test_repr_da_mensagem_nao_mostra_texto_nem_telefone() -> None:
    m = InboundMessage(from_number=FONE, text=CPF, message_id="wamid.x")
    assert CPF not in repr(m) and FONE not in repr(m)


def test_data_do_teste_e_o_nascimento_do_dublê() -> None:
    assert date(1985, 3, 7) == NASC
