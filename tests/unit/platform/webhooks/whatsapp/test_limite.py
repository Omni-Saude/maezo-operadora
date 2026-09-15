"""O teto de volume do canal: janela deslizante por conversa e por tenant."""

from __future__ import annotations

import pytest

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.dispatch import (
    LIMITE_EXCEDIDO_TEXT,
    HelenaDispatcher,
    InboundMessage,
)
from maezo.platform.webhooks.whatsapp.limite import (
    ESCOPO_CONVERSA,
    ESCOPO_TENANT,
    JANELA_SEGUNDOS,
    LimitadorDeVolume,
)
from maezo.platform.webhooks.whatsapp.security import hash_phone


class _Relogio:
    """Relogio injetado: o teste nao dorme, e a janela e' exercitada no segundo exato."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def avancar(self, segundos: float) -> None:
        self.t += segundos


def _limitador(*, conversa: int = 3, tenant: int = 10) -> tuple[LimitadorDeVolume, _Relogio]:
    relogio = _Relogio()
    return LimitadorDeVolume(por_conversa=conversa, por_tenant=tenant, agora=relogio), relogio


def test_abaixo_do_teto_tudo_passa() -> None:
    lim, _ = _limitador(conversa=3)

    for _ in range(3):
        assert lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido


def test_a_mensagem_acima_do_teto_da_conversa_e_recusada() -> None:
    lim, _ = _limitador(conversa=3)
    for _ in range(3):
        lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")

    v = lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")

    assert not v.permitido
    assert v.escopo == ESCOPO_CONVERSA
    assert v.observadas == 3


def test_outra_conversa_do_mesmo_tenant_nao_e_afetada() -> None:
    """O laco de UM numero nao pode calar os outros beneficiarios."""
    lim, _ = _limitador(conversa=2, tenant=100)
    for _ in range(3):
        lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")

    assert lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_b").permitido


def test_a_janela_desliza_e_libera_no_segundo_certo() -> None:
    lim, relogio = _limitador(conversa=2)
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
    assert not lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido

    # Um pouco antes do fim da janela: ainda recusa.
    relogio.avancar(JANELA_SEGUNDOS - 0.1)
    assert not lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido

    # Passada a janela dos dois primeiros: libera de novo.
    relogio.avancar(0.2)
    assert lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido


def test_a_recusada_nao_entra_na_janela() -> None:
    """Contar o que foi barrado faria uma rajada prolongar o proprio bloqueio.

    Com teto 2: duas passam em t=0, uma e' recusada em t=0. Em t=61 as duas primeiras sairam da
    janela; se a RECUSADA tivesse entrado, ela ainda estaria la' e a proxima seria barrada.
    """
    lim, relogio = _limitador(conversa=2)
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
    relogio.avancar(0.5)
    assert not lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido

    relogio.avancar(JANELA_SEGUNDOS)

    assert lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido
    assert lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").permitido


def test_o_teto_do_tenant_barra_mesmo_com_conversas_diferentes() -> None:
    """O caso do incidente: mil pessoas escrevendo ao mesmo tempo, cada uma uma vez."""
    lim, _ = _limitador(conversa=100, tenant=3)
    for i in range(3):
        assert lim.registrar(tenant_id="amh", conversation_id=f"wa:amh:hk1_{i}").permitido

    v = lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_novo")

    assert not v.permitido
    assert v.escopo == ESCOPO_TENANT


def test_quando_os_dois_estouram_o_motivo_reportado_e_o_da_conversa() -> None:
    """O da conversa e' o ACIONAVEL (um numero em laco); o do tenant e' consequencia."""
    lim, _ = _limitador(conversa=2, tenant=2)
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")

    assert lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a").escopo == ESCOPO_CONVERSA


def test_tenants_diferentes_nao_se_contaminam() -> None:
    lim, _ = _limitador(conversa=100, tenant=2)
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_b")

    assert lim.registrar(tenant_id="outro", conversation_id="wa:outro:hk1_a").permitido


def test_teto_zero_desliga_aquele_escopo() -> None:
    lim, _ = _limitador(conversa=0, tenant=2)
    for _ in range(50):
        v = lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_a")
        if not v.permitido:
            assert v.escopo == ESCOPO_TENANT, "o teto da conversa devia estar desligado"
            break
    else:
        raise AssertionError("o teto do tenant devia ter barrado em algum momento")


def test_janelas_ociosas_sao_esquecidas() -> None:
    """Sem isto o dicionario cresce uma entrada por conversa VISTA, para sempre."""
    lim, relogio = _limitador(conversa=5)
    for i in range(10):
        lim.registrar(tenant_id="amh", conversation_id=f"wa:amh:hk1_{i}")

    relogio.avancar(JANELA_SEGUNDOS + 1)
    esquecidas = lim.esquecer_conversas_ociosas()

    assert esquecidas == 10
    # E uma conversa ainda ativa NAO e' esquecida.
    lim.registrar(tenant_id="amh", conversation_id="wa:amh:hk1_viva")
    assert lim.esquecer_conversas_ociosas() == 0


# ---------------------------------------------------------------------------------------------
# O teto dentro do despachante: nao roda o turno, responde honesto, conta.
# ---------------------------------------------------------------------------------------------

class _InferenciaProibida:
    """Se o teto funcionar, o modelo NUNCA e' chamado — que e' o ponto do teto."""

    async def generate(self, *a: object, **k: object) -> str:
        raise AssertionError("o turno rodou apesar do teto: o modelo foi chamado")


class _ClienteQueRegistra:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, object]:
        self.enviados.append((to, text))
        return {"ok": True}


def _despachante(cliente: _ClienteQueRegistra, lim: LimitadorDeVolume) -> HelenaDispatcher:
    return HelenaDispatcher(
        tenant_id="amh",
        inference=_InferenciaProibida(),
        dmn=None,
        cibseven=None,
        whatsapp_client=cliente,
        pseudonymizer=Pseudonymizer(),  # chave DEV deterministica (nao secreta)
        audit_sink=None,
        limitador=lim,
    )


def _mensagem(i: int) -> InboundMessage:
    return InboundMessage(from_number="5511900000001", message_id=f"wamid.{i}", text="oi")


@pytest.mark.asyncio
async def test_acima_do_teto_o_turno_nao_roda_e_a_pessoa_recebe_resposta() -> None:
    cliente = _ClienteQueRegistra()
    lim, _ = _limitador(conversa=1, tenant=100)
    # A janela ja' esta' cheia ANTES do despacho: o teste exercita a recusa, nao o turno feliz.
    # Preencher pelo proprio limitador (e nao por um `dispatch` anterior) e' o que permite a
    # inferencia PROIBIDA acima ser a prova de que o turno nao rodou.
    # A MESMA derivacao do despachante (`hash_phone`), nao uma parecida: o limitador e' indexado
    # pelo `conversation_id`, e uma chave montada de outro jeito simplesmente nao casaria — o teste
    # passaria a exercitar o turno feliz sem ninguem notar.
    conv = f"wa:amh:{hash_phone('5511900000001', 'amh', Pseudonymizer())}"
    lim.registrar(tenant_id="amh", conversation_id=conv)
    d = _despachante(cliente, lim)

    saida = await d.dispatch(_mensagem(2))

    assert saida["limite_excedido"] is True
    assert saida["escopo_do_limite"] == ESCOPO_CONVERSA
    # A recusa NAO e' silencio: o texto constante foi enviado.
    assert any(texto == LIMITE_EXCEDIDO_TEXT for _, texto in cliente.enviados)
