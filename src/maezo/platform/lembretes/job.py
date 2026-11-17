"""O job do lembrete proativo de cobranca: selecao, trava e envio. Sem I/O proprio (tudo por portas).

ORDEM, e o que cada passo garante:
  0. Desligado (`MAEZO_LEMBRETE_COBRANCA=false`, o padrao): devolve na primeira linha. Nenhuma porta e'
     tocada: nao le' banco, nao le' a fonte, nao envia.
  1. Envio REAL exige (a) allowlist quando `exigir_allowlist` (padrao) e (b) 08:00-20:00 America/Sao_Paulo.
  2. Allowlist: com a lista definida, a conversa fora dela e' descartada ANTES de qualquer leitura da fonte
     de cobranca (nem a cobranca dela e' consultada). O `Enviador` repete a trava no envio.
  3. Por candidato: opt-out, supressao (SP-OP-SUPP-001), aceite do termo com a finalidade 2 (E-SF-21r;
     sem ele a cobranca nem e' lida), fatos de cobranca (fonte do Lucas), regra de selecao (PF, em aberto,
     N dias), ja' lembrado.
  4. DRY-RUN (o padrao): para aqui e so' CONTA (`enviaria`). Nao grava, nao envia.
  5. Real: contato (custodia), janela de 24 h -> texto livre ou template, reserva idempotente ANTES do
     envio (liberada se o envio falhar), pausa entre envios, teto por execucao.

TRAVA DE OPOSICAO DO BENEFICIARIO (CD-25, GT-4 lado de saida). Logo depois do interruptor, ANTES de
qualquer outra coisa (allowlist, horario, gate, leitura de acesso/cobranca/supressao/opt-out), a rodada
inteira e o simulador sao recusados com `BENEFICIARY_OPPOSITION_NOT_EXECUTABLE` enquanto nao houver uma
PORTA DE OPOSICAO EXECUTAVEL para `beneficiario` (`Dependencias.oposicao_beneficiario`). Hoje nenhuma existe:
SP-OP-SUPP-001 nao registra oposicao de `beneficiario` (GTM-04/D4 aberto). A trava e' incondicional e
independe do gate e do registro: mesmo com tudo ratificado, sem a porta nada sai. QUAL e' o canal/fonte de
oposicao do beneficiario e' ponto do DPO (GAP) — este modulo so' declara o ponto de extensao
(`PortaDeOposicaoBeneficiario`) e nenhuma raiz de producao o preenche.

OUTBOUNDGATE (E-SF-13 / GTM-E-41; ADDENDUM-01 §7 (q), RT-D4). O lembrete e' mensagem PROATIVA a
beneficiario, entao passa pelo `OutboundGate` (`gateway/communications/outbound_gate.py`), a fonte unica
das verificacoes de envio proativo:
  * logo depois do interruptor (e do dry-run/allowlist/horario), UMA pre-verificacao sem dado
    (`finalidade_aberta`): finalidade `cobranca_lembrete_pre_vencimento` nao ratificada ou titular
    `beneficiario` sem oposicao executavel => conta `recusado_outbound_<MOTIVO>` e PARA, antes de ler o
    acesso, a fonte de cobranca ou a supressao;
  * por candidato, a verificacao COMPLETA (`avaliar`) antes de contar `enviaria` ou enviar; o `Enviador`
    so' envia com a `AutorizacaoDeEnvio` que o gate emitiu.
CRITERIO DE SELECAO (E-SF-21r, E37): a permissao do candidato e' o ACEITE do termo de contato, finalidade 2
(cobranca), lido pela porta `Dependencias.aceites` (`aceite.py`) ANTES de qualquer fato de cobranca. O
consentimento de ATENDIMENTO (`atendimento_whatsapp` / `wa-consent-v2`) so' identifica a conversa
verificada; nunca e' apresentado como permissao. Nenhuma fonte de aceite existe em producao (termo em
minuta, registro de aceites nao publicado), entao ninguem e' selecionado; e mesmo com aceite, o gate
recusa enquanto a finalidade nao for ratificada. Envio real fechado.

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
from maezo.gateway.communications.outbound_gate import (
    AutorizacaoDeEnvio,
    Decisao,
    Motivo,
    OutboundGate,
    PedidoDeEnvio,
    PermissaoDeContato,
)
from maezo.gateway.recipient_custody import RecipientRecord, dentro_da_janela
from maezo.platform.webhooks.whatsapp.acesso import SHA256_TEXTO_CONSENTIMENTO, VERSAO_TEXTO_CONSENTIMENTO

from .aceite import LeitorDeAceiteDeContato, permissao_do_aceite
from .armazenamento import LembreteStore
from .config import ConfigJob
from .envio import DestinoForaDaAllowlistError, Enviador
from .regras import dentro_do_horario, escolher_competencia, hoje_no_brasil
from .texto import MensagemLembrete, compor_lembrete, compor_teste

logger = structlog.get_logger(__name__)

#: A finalidade e o template do lembrete no registro `spec/communications/whatsapp-templates.yaml`.
FINALIDADE_LEMBRETE = "cobranca_lembrete_pre_vencimento"
TEMPLATE_LEMBRETE = "lembrete_mensalidade_v1"
TIPO_TITULAR = "beneficiario"
#: O escopo do consentimento de atendimento (`gateway/amh_interop.py::CONSENT_SCOPE`; teste de paridade).
ESCOPO_CONSENTIMENTO_ATENDIMENTO = "atendimento_whatsapp"


@dataclass(frozen=True, slots=True)
class Beneficiario:
    """Uma conversa com acesso VERIFICADO e consentimento vigente. So' referencias pseudonimas."""

    conversation_id: str
    portable_ref: str = field(repr=False)
    consent_ref: str = field(repr=False)


def permissao_do_acesso(b: Beneficiario) -> PermissaoDeContato:
    """O consentimento de ATENDIMENTO do acesso verificado, com o nome certo. NAO e' o criterio de selecao.

    Desde E-SF-21r (E37) o job seleciona pelo aceite do termo de contato (`aceite.py`). Esta funcao fica
    para provar que, se alguem apresentar o consentimento de atendimento ao gate, ele e' recusado como
    permissao de contato proativo (A9 GT-3; RT-D4).
    """
    return PermissaoDeContato(
        origem="consentimento_atendimento",
        escopo=ESCOPO_CONSENTIMENTO_ATENDIMENTO,
        texto_versao=VERSAO_TEXTO_CONSENTIMENTO,
        texto_sha256=SHA256_TEXTO_CONSENTIMENTO,
        prova_ref=b.consent_ref,
    )


class LeitorDeAcesso(Protocol):
    async def verificados(self) -> Sequence[Beneficiario]: ...


class LeitorDeFatos(Protocol):
    """Os fatos de cobranca do beneficiario, pela fonte do Lucas (`FonteCobranca`)."""

    async def de(self, beneficiario: Beneficiario) -> FatosCobranca | Indisponivel: ...


#: Motivo da trava de oposicao do beneficiario (CD-25). Contado como `recusado_<MOTIVO>`.
BENEFICIARY_OPPOSITION_NOT_EXECUTABLE = "BENEFICIARY_OPPOSITION_NOT_EXECUTABLE"


class PortaDeOposicaoBeneficiario(Protocol):
    """PONTO DE EXTENSAO (GAP / DPO): a oposicao EXECUTAVEL do beneficiario antes de qualquer envio.

    Nenhuma implementacao existe em `src/`: o canal e a fonte de oposicao do beneficiario sao decisao do DPO
    (GTM-04/D4), nao deste job. `executavel()` diz se a oposicao pode ser honrada AGORA; `False` ou excecao
    recusam a rodada (desconhecido nunca vira "sem oposicao")."""

    async def executavel(self) -> bool: ...


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
    #: `None` = o gate de PRODUCAO (registro publicado; fechado). Testes injetam um registro sintetico.
    outbound: OutboundGate | None = None
    #: E-SF-21r (E37): a fonte do aceite do termo de contato (finalidade 2). `None` (o unico valor em
    #: producao hoje) = ninguem tem aceite => ninguem e' selecionado.
    aceites: LeitorDeAceiteDeContato | None = None
    #: So' testes: permissao ja' resolvida, no lugar da fonte de aceite.
    permissao_de: Callable[[Beneficiario], PermissaoDeContato | None] | None = None
    #: `None` (o unico valor em producao hoje) = nao ha' oposicao executavel => rodada recusada (CD-25).
    oposicao_beneficiario: PortaDeOposicaoBeneficiario | None = None

    def __post_init__(self) -> None:
        if self.outbound is None:
            self.outbound = OutboundGate.de_producao(supressao=self.supressao)

    @property
    def gate(self) -> OutboundGate:
        if self.outbound is None:  # pragma: no cover - __post_init__ sempre preenche
            raise RuntimeError("lembrete: OutboundGate ausente")
        return self.outbound


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


async def _permissao(deps: Dependencias, b: Beneficiario) -> PermissaoDeContato | None:
    """O criterio de selecao (E37): o aceite do termo, finalidade 2. Fonte que falha = nenhuma."""
    if deps.permissao_de is None and deps.aceites is None:
        return None
    try:
        if deps.permissao_de is not None:
            return deps.permissao_de(b)
        assert deps.aceites is not None
        return permissao_do_aceite(await deps.aceites.de(b))
    except Exception:  # fonte de aceite fora do ar: desconhecido nunca vira aceite
        return None


def _pedido(
    b: Beneficiario, parametros: tuple[str, ...], chave: str, permissao: PermissaoDeContato | None
) -> PedidoDeEnvio:
    return PedidoDeEnvio(
        template_id=TEMPLATE_LEMBRETE,
        finalidade=FINALIDADE_LEMBRETE,
        tipo_titular=TIPO_TITULAR,
        titular_ref=b.portable_ref,
        canal_ref=b.conversation_id,
        variaveis=parametros,
        permissao=permissao,
        chave_idempotencia=chave,
    )


def _contar_recusa(resumo: Resumo, decisao: Decisao) -> None:
    motivo = decisao.motivo.value if decisao.motivo is not None else "sem_autorizacao"
    resumo.contar(f"recusado_outbound_{motivo}")


async def _oposicao_executavel(deps: Dependencias) -> bool:
    porta = deps.oposicao_beneficiario
    if porta is None:
        return False
    try:
        return await porta.executavel() is True
    except Exception:  # porta fora do ar: desconhecido nunca libera envio
        return False


def _fechado(deps: Dependencias) -> Motivo | None:
    return deps.gate.finalidade_aberta(FINALIDADE_LEMBRETE, tipo_titular=TIPO_TITULAR)


async def executar(deps: Dependencias, cfg: ConfigJob, *, agora: datetime) -> Resumo:
    """UMA rodada do job. `agora` e' injetado (testes e relogio unico da rodada)."""
    resumo = Resumo(modo=_modo(cfg), dentro_do_horario=dentro_do_horario(agora))
    if not cfg.habilitado:
        resumo.contar("desligado")
        return resumo
    if not await _oposicao_executavel(deps):
        resumo.contar(f"recusado_{BENEFICIARY_OPPOSITION_NOT_EXECUTABLE}")
        resumo.registrar()
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
    fechado = _fechado(deps)
    if fechado is not None:
        resumo.contar(f"recusado_outbound_{fechado.value}")
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
        # Criterio de selecao (E37) ANTES de ler qualquer fato de cobranca: sem aceite da finalidade 2,
        # a cobranca da pessoa nem e' lida.
        permissao = await _permissao(deps, b)
        if permissao is None:
            resumo.contar("sem_aceite_finalidade_cobranca")
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
        mensagem = compor_lembrete(
            competencia=escolhida.competencia,
            valor_total=escolhida.valor_total,
            vencimento=escolhida.vencimento,
        )
        chave = deps.chave_de_saida(f"lembrete:{b.conversation_id}:{escolhida.competencia}")
        decisao = await deps.gate.avaliar(_pedido(b, mensagem.parametros_template, chave, permissao))
        if not decisao.permitido or decisao.autorizacao is None:
            _contar_recusa(resumo, decisao)
            continue
        if not real:
            resumo.contar("enviaria")
            continue
        entregue = await _enviar_um(
            deps,
            resumo,
            agora=agora,
            conversation_id=b.conversation_id,
            competencia=escolhida.competencia,
            mensagem=mensagem,
            reservar=True,
            chave=chave,
            autorizacao=decisao.autorizacao,
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
    autorizacao: AutorizacaoDeEnvio,
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
            autorizacao=autorizacao,
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
    if not await _oposicao_executavel(deps):
        resumo.contar(f"recusado_{BENEFICIARY_OPPOSITION_NOT_EXECUTABLE}")
        resumo.registrar()
        return resumo
    if cfg.allowlist is None or not cfg.allowlist.permite(conversation_id):
        resumo.contar("recusado_fora_da_allowlist")
        return resumo
    if not cfg.dry_run and not ignorar_horario and not resumo.dentro_do_horario:
        resumo.contar("fora_do_horario")
        return resumo
    fechado = _fechado(deps)
    if fechado is not None:
        resumo.contar(f"recusado_outbound_{fechado.value}")
        resumo.registrar()
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
    beneficiario = next(
        (b for b in await deps.acesso.verificados() if b.conversation_id == conversation_id), None
    )
    if beneficiario is None:
        # Sem acesso verificado nao ha' titular nem permissao a apresentar ao gate: nada sai.
        resumo.contar("recusado_sem_acesso_verificado")
        resumo.registrar()
        return resumo
    chave = deps.chave_de_saida(f"lembrete-teste:{conversation_id}:{int(agora.timestamp())}")
    permissao = await _permissao(deps, beneficiario)
    if permissao is None:
        resumo.contar("sem_aceite_finalidade_cobranca")
        resumo.registrar()
        return resumo
    decisao = await deps.gate.avaliar(_pedido(beneficiario, mensagem.parametros_template, chave, permissao))
    if not decisao.permitido or decisao.autorizacao is None:
        _contar_recusa(resumo, decisao)
        resumo.registrar()
        return resumo
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
        chave=chave,
        autorizacao=decisao.autorizacao,
    )
    resumo.registrar()
    return resumo
