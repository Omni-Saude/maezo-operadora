"""Dubles em memoria do job do lembrete (so' testes: a cerca §8.4 proibe `Fake*` em raiz de producao)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maezo.agents.lucas.fonte_cobranca import CompetenciaCobranca, FatosCobranca, Indisponivel
from maezo.gateway.recipient_custody import RecipientRecord
from maezo.platform.lembretes.config import Allowlist, ConfigJob
from maezo.platform.lembretes.envio import Enviador
from maezo.platform.lembretes.job import Beneficiario, Dependencias

TENANT = "amh"
CONV_A = "wa:amh:hk1_aaaa0001"
CONV_B = "wa:amh:hk1_bbbb0002"
CONV_C = "wa:amh:hk1_cccc0003"
TELEFONE_A = "5511977770001"
TELEFONE_B = "5511977770002"
TELEFONE_C = "5511977770003"

#: 13/10/2026 10:00 em Sao Paulo (UTC-3): dentro do horario, hoje = 2026-10-13.
AGORA = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)


def competencia(
    mes: str = "2026-10",
    *,
    situacao: str = "em_aberto",
    vencimento: str | None = "2026-10-16",
    valor_total: str | None = "320.25",
) -> CompetenciaCobranca:
    return CompetenciaCobranca(
        competencia=mes, situacao=situacao, vencimento=vencimento, valor_total=valor_total
    )


def fatos(
    *competencias: CompetenciaCobranca,
    pagador: str | None = "pessoa_fisica",
    administradora: bool = False,
) -> FatosCobranca:
    return FatosCobranca(
        status_conciliado=False,
        ciclos_sem_conciliacao=0,
        numero_boleto="",
        cnab_ref="amh-billing:2026-10-13",
        competencias=competencias or (competencia(),),
        pagador_tipo=pagador,
        cobranca_administradora=administradora,
    )


def benef(conversation_id: str, ref: str | None = None) -> Beneficiario:
    return Beneficiario(
        conversation_id=conversation_id,
        portable_ref=ref or f"ref-{conversation_id[-4:]}",
        consent_ref=f"consent-{conversation_id[-4:]}",
    )


class FakeAcesso:
    def __init__(self, beneficiarios: Sequence[Beneficiario]) -> None:
        self.beneficiarios = list(beneficiarios)
        self.chamadas = 0

    async def verificados(self) -> Sequence[Beneficiario]:
        self.chamadas += 1
        return self.beneficiarios


class FakeFatos:
    def __init__(self, por_conversa: dict[str, FatosCobranca | Indisponivel]) -> None:
        self.por_conversa = por_conversa
        self.consultadas: list[str] = []

    async def de(self, beneficiario: Beneficiario) -> FatosCobranca | Indisponivel:
        self.consultadas.append(beneficiario.conversation_id)
        return self.por_conversa.get(beneficiario.conversation_id, Indisponivel("sujeito_nao_resolvido"))


class FakeStore:
    def __init__(self, optouts: Sequence[str] = ()) -> None:
        self._optouts = set(optouts)
        self.reservas: dict[tuple[str, str], str] = {}
        self.chamadas = 0

    async def reservar(
        self, conversation_id: str, competencia: str, agora: datetime, canal_envio: str
    ) -> bool:
        self.chamadas += 1
        chave = (conversation_id, competencia)
        if chave in self.reservas:
            return False
        self.reservas[chave] = canal_envio
        return True

    async def liberar(self, conversation_id: str, competencia: str) -> None:
        self.reservas.pop((conversation_id, competencia), None)

    async def ja_lembrado(self, conversation_id: str, competencia: str) -> bool:
        return (conversation_id, competencia) in self.reservas

    async def registrar_optout(self, conversation_id: str, agora: datetime) -> None:
        self._optouts.add(conversation_id)

    async def remover_optout(self, conversation_id: str) -> None:
        self._optouts.discard(conversation_id)

    async def optouts(self) -> frozenset[str]:
        self.chamadas += 1
        return frozenset(self._optouts)


class FakeSupressao:
    def __init__(self, vigentes: Sequence[tuple[str, str]] = (), *, falha: bool = False) -> None:
        self._vigentes = frozenset(vigentes)
        self._falha = falha

    async def vigentes(self) -> frozenset[tuple[str, str]]:
        if self._falha:
            raise RuntimeError("fonte de supressao fora")
        return self._vigentes


class FakeContatos:
    def __init__(self, contatos: dict[str, RecipientRecord]) -> None:
        self.contatos = contatos

    async def lookup(self, *, tenant_id: str, conversation_id: str) -> RecipientRecord | None:
        return self.contatos.get(conversation_id)


class FakeCliente:
    """O cliente do WhatsApp: registra TUDO que seria enviado."""

    def __init__(self, *, falhar: int = 0, ack: dict[str, Any] | None = None) -> None:
        self.texto: list[tuple[str, str]] = []
        self.template: list[tuple[str, str, list[str], str]] = []
        self.chaves: list[str | None] = []
        self._falhar = falhar
        self._ack = ack or {"messages": [{"id": "wamid.x"}]}

    @property
    def total(self) -> int:
        return len(self.texto) + len(self.template)

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if self._falhar > 0:
            self._falhar -= 1
            raise RuntimeError("meta fora")
        self.texto.append((to, text))
        self.chaves.append(idempotency_key)
        return self._ack

    async def send_template(
        self,
        to: str,
        template_name: str,
        parameters: list[str],
        *,
        language: str = "pt_BR",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if self._falhar > 0:
            self._falhar -= 1
            raise RuntimeError("meta fora")
        self.template.append((to, template_name, parameters, language))
        self.chaves.append(idempotency_key)
        return self._ack


class GateGravador:
    """O gate do principal `lucas`, como contador: cada envio passa por aqui."""

    def __init__(self) -> None:
        self.embrulhados = 0

    def __call__(self, inner: Any) -> Any:
        self.embrulhados += 1
        return inner


def contato(telefone: str, horas: float = 1.0) -> RecipientRecord:
    return RecipientRecord(phone=telefone, last_inbound_at=AGORA - timedelta(hours=horas))


class Cenario:
    """Monta as `Dependencias` com os dubles e expoe-os."""

    def __init__(
        self,
        *,
        beneficiarios: Sequence[Beneficiario],
        fatos_por_conversa: dict[str, FatosCobranca | Indisponivel],
        contatos: dict[str, RecipientRecord],
        cfg: ConfigJob,
        optouts: Sequence[str] = (),
        suprimidos: Sequence[tuple[str, str]] = (),
        supressao_falha: bool = False,
        cliente: FakeCliente | None = None,
    ) -> None:
        self.cfg = cfg
        self.acesso = FakeAcesso(beneficiarios)
        self.fatos = FakeFatos(fatos_por_conversa)
        self.store = FakeStore(optouts)
        self.cliente = cliente or FakeCliente()
        self.gate = GateGravador()
        self.pausas: list[float] = []

        async def dormir(s: float) -> None:
            self.pausas.append(s)

        self.deps = Dependencias(
            tenant_id=TENANT,
            acesso=self.acesso,
            fatos=self.fatos,
            store=self.store,
            supressao=FakeSupressao(suprimidos, falha=supressao_falha),
            contatos=FakeContatos(contatos),
            enviador=Enviador(
                tenant_id=TENANT,
                client=self.cliente,
                gate=self.gate,
                allowlist=cfg.allowlist,
                template_nome=cfg.template_nome,
                template_idioma=cfg.template_idioma,
            ),
            chave_de_saida=lambda identificador: f"k:{len(identificador)}",
            dormir=dormir,
        )


def allowlist(*conversas: str) -> Allowlist:
    return Allowlist(frozenset(conversas))
