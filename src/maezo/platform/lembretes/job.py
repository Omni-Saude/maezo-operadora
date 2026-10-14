"""O job do lembrete proativo de cobranca: selecao, trava e envio. Sem I/O proprio (tudo por portas).

ORDEM, e o que cada passo garante:
  0. Desligado (`MAEZO_LEMBRETE_COBRANCA=false`, o padrao): devolve na primeira linha. Nenhuma porta e'
     tocada: nao le' banco, nao le' a fonte, nao envia.
  1. Envio REAL exige (a) allowlist quando `exigir_allowlist` (padrao) e (b) 08:00-20:00 America/Sao_Paulo.
  2. Allowlist: com a lista definida, a conversa fora dela e' descartada ANTES de qualquer leitura da fonte
     de cobranca (nem a cobranca dela e' consultada). O `Enviador` repete a trava no envio.
  3. Por candidato: opt-out, supressao (SP-OP-SUPP-001), fatos de cobranca (fonte do Lucas), regra de
     selecao (PF, em aberto, N dias), ja' lembrado.
  4. DRY-RUN (o padrao): para aqui e so' CONTA (`enviaria`). Nao grava, nao envia.
  5. Real: contato (custodia), janela de 24 h -> texto livre ou template, reserva idempotente ANTES do
     envio (liberada se o envio falhar), pausa entre envios, teto por execucao.

O que SAI do job para log: so' contagens por motivo. Nunca telefone, CPF, valor, nome ou conversation_id.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import structlog

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, Indisponivel
from maezo.gateway.recipient_custody import RecipientRecord, dentro_da_janela

from .armazenamento import LembreteStore
from .config import ConfigJob
from .envio import DestinoForaDaAllowlistError, Enviador
from .regras import dentro_do_horario, escolher_competencia, hoje_no_brasil
from .texto import MensagemLembrete, compor_lembrete, compor_teste

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Beneficiario:
    """Uma conversa com acesso VERIFICADO e consentimento vigente. So' referencias pseudonimas."""

    conversation_id: str
    portable_ref: str = field(repr=False)
    consent_ref: str = field(repr=False)


class LeitorDeAcesso(Protocol):
    async def verificados(self) -> Sequence[Beneficiario]: ...


class LeitorDeFatos(Protocol):
    """Os fatos de cobranca do beneficiario, pela fonte do Lucas (`FonteCobranca`)."""

    async def de(self, beneficiario: Beneficiario) -> FatosCobranca | Indisponivel: ...


class LeitorDeSupressao(Protocol):
    """As chaves de supressao vigentes (SP-OP-SUPP-001) como pares `(subject_ref, contact_channel)`. Levanta
    se a fonte nao responde: o job NAO envia sem saber (fail-closed)."""

    async def vigentes(self) -> frozenset[tuple[str, str]]: ...


class CustodiaDeContato(Protocol):
    async def lookup(self, *, tenant_id: str, conversation_id: str) -> RecipientRecord | None: ...


@dataclass(slots=True)
class Dependencias:
    tenant_id: str
    acesso: LeitorDeAcesso
    fatos: LeitorDeFatos
    store: LembreteStore
    supressao: LeitorDeSupressao
    contatos: CustodiaDeContato
    enviador: Enviador
    chave_de_saida: Callable[[str], str]
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass(slots=True)
class Resumo:
    modo: str
    dentro_do_horario: bool
    contadores: Counter[str] = field(default_factory=Counter)

    def contar(self, chave: str, n: int = 1) -> None:
        self.contadores[chave] += n

    def registrar(self) -> None:
        """Uma linha de log: modo, horario e CONTAGENS por motivo. Nada que identifique alguem."""
        logger.info(
            "lembrete_cobranca_resumo",
            modo=self.modo,
            dentro_do_horario=self.dentro_do_horario,
            contagens=dict(self.contadores),
        )


MODO_DESLIGADO = "desligado"
MODO_DRY_RUN = "dry_run"
MODO_REAL = "real"
MODO_SIMULACAO = "simulacao"
MODO_SIMULACAO_DRY_RUN = "simulacao_dry_run"


def _modo(cfg: ConfigJob) -> str:
    if not cfg.habilitado:
        return MODO_DESLIGADO
    return MODO_DRY_RUN if cfg.dry_run else MODO_REAL


def _suprimido(vigentes: frozenset[tuple[str, str]], b: Beneficiario) -> bool:
    return any(ref == b.portable_ref or canal == b.conversation_id for ref, canal in vigentes)


async def executar(deps: Dependencias, cfg: ConfigJob, *, agora: datetime) -> Resumo:
    """UMA rodada do job. `agora` e' injetado (testes e relogio unico da rodada)."""
    resumo = Resumo(modo=_modo(cfg), dentro_do_horario=dentro_do_horario(agora))
    if not cfg.habilitado:
        resumo.contar("desligado")
        return resumo
    real = not cfg.dry_run
    if real and cfg.exigir_allowlist and cfg.allowlist is None:
        resumo.contar("recusado_sem_allowlist")
        resumo.registrar()
        return resumo
    if real and not resumo.dentro_do_horario:
        resumo.contar("fora_do_horario")
        resumo.registrar()
        return resumo

    hoje = hoje_no_brasil(agora)
    optouts = await deps.store.optouts()
    vigentes = await deps.supressao.vigentes()
    candidatos = await deps.acesso.verificados()
    enviados = 0
    for b in candidatos:
        resumo.contar("candidatos")
        if cfg.allowlist is not None and not cfg.allowlist.permite(b.conversation_id):
            resumo.contar("fora_da_allowlist")
            continue
        if real and enviados >= cfg.limite_por_execucao:
            resumo.contar("limite_por_execucao")
            break
        if b.conversation_id in optouts:
            resumo.contar("optout")
            continue
        if _suprimido(vigentes, b):
            resumo.contar("suprimido")
            continue
        fatos = await deps.fatos.de(b)
        if isinstance(fatos, Indisponivel):
            resumo.contar(f"fonte_indisponivel_{fatos.motivo}")
            continue
        escolhida, motivo = escolher_competencia(fatos, hoje=hoje, dias_antecedencia=cfg.dias_antecedencia)
        if escolhida is None:
            resumo.contar(motivo or "sem_competencia_na_janela")
            continue
        if await deps.store.ja_lembrado(b.conversation_id, escolhida.competencia):
            resumo.contar("ja_lembrado")
            continue
        if not real:
            resumo.contar("enviaria")
            continue
        mensagem = compor_lembrete(
            competencia=escolhida.competencia,
            valor_total=escolhida.valor_total,
            vencimento=escolhida.vencimento,
        )
        entregue = await _enviar_um(
            deps,
            resumo,
            agora=agora,
            conversation_id=b.conversation_id,
            competencia=escolhida.competencia,
            mensagem=mensagem,
            reservar=True,
            chave=deps.chave_de_saida(f"lembrete:{b.conversation_id}:{escolhida.competencia}"),
        )
        if entregue:
            enviados += 1
            if cfg.pausa_entre_envios_s > 0:
                await deps.dormir(cfg.pausa_entre_envios_s)
    resumo.registrar()
    return resumo


async def _enviar_um(
    deps: Dependencias,
    resumo: Resumo,
    *,
    agora: datetime,
    conversation_id: str,
    competencia: str,
    mensagem: MensagemLembrete,
    reservar: bool,
    chave: str,
) -> bool:
    contato = await deps.contatos.lookup(tenant_id=deps.tenant_id, conversation_id=conversation_id)
    if contato is None:
        resumo.contar("sem_contato")
        return False
    via_template = not dentro_da_janela(contato.last_inbound_at, agora=agora)
    if via_template and not deps.enviador.tem_template:
        resumo.contar("fora_da_janela_sem_template")
        return False
    canal = "template" if via_template else "texto"
    if reservar and not await deps.store.reservar(conversation_id, competencia, agora, canal):
        resumo.contar("ja_lembrado")
        return False
    try:
        resultado = await deps.enviador.enviar(
            conversation_id=conversation_id,
            raw_to=contato.phone,
            mensagem=mensagem,
            via_template=via_template,
            chave=chave,
        )
    except DestinoForaDaAllowlistError:
        if reservar:
            await deps.store.liberar(conversation_id, competencia)
        resumo.contar("recusado_pela_allowlist")
        return False
    except Exception as exc:
        if reservar:
            await deps.store.liberar(conversation_id, competencia)
        resumo.contar("falha_envio")
        logger.error("lembrete_cobranca_falha_envio", error_type=type(exc).__name__)
        return False
    if not resultado.entregue:
        resumo.contar("suprimido_pelo_cliente")
        return False
    resumo.contar(f"enviado_{resultado.canal}")
    return True


async def simular(
    deps: Dependencias,
    cfg: ConfigJob,
    *,
    conversation_id: str,
    agora: datetime,
    ignorar_horario: bool = False,
) -> Resumo:
    """MODO DE TESTE do dono: UM lembrete `[TESTE]`, com fatos SINTETICOS, a UMA conversa da allowlist.

    Nao le' a fonte de cobranca, nao grava reserva (pode repetir), respeita opt-out, interruptor, dry-run
    e janela de 24 h/template como o real. Sem allowlist, ou conversa fora dela, nada sai.
    """
    resumo = Resumo(
        modo=MODO_SIMULACAO if cfg.habilitado and not cfg.dry_run else MODO_SIMULACAO_DRY_RUN,
        dentro_do_horario=dentro_do_horario(agora),
    )
    if not cfg.habilitado:
        resumo.modo = MODO_DESLIGADO
        resumo.contar("desligado")
        return resumo
    if cfg.allowlist is None or not cfg.allowlist.permite(conversation_id):
        resumo.contar("recusado_fora_da_allowlist")
        return resumo
    if not cfg.dry_run and not ignorar_horario and not resumo.dentro_do_horario:
        resumo.contar("fora_do_horario")
        return resumo
    if conversation_id in await deps.store.optouts():
        resumo.contar("optout")
        return resumo
    hoje = hoje_no_brasil(agora)
    vencimento = hoje + timedelta(days=cfg.dias_antecedencia)
    mensagem = compor_teste(
        competencia=f"{vencimento.year:04d}-{vencimento.month:02d}",
        valor_total="123.45",
        vencimento=vencimento,
    )
    if cfg.dry_run:
        resumo.contar("simularia")
        resumo.registrar()
        return resumo
    await _enviar_um(
        deps,
        resumo,
        agora=agora,
        conversation_id=conversation_id,
        competencia="teste",
        mensagem=mensagem,
        reservar=False,
        chave=deps.chave_de_saida(f"lembrete-teste:{conversation_id}:{int(agora.timestamp())}"),
    )
    resumo.registrar()
    return resumo
