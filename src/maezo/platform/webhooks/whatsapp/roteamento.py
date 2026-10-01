"""Roteamento por conversa do numero unico (ADR-0062; plano `docs/plans/lucas-numero-unico.md`).

QUEM ESTA COM A CONVERSA. Com o numero unico a Helena atende a entrada e o Lucas atende cobranca.
Entre uma mensagem e a seguinte alguem precisa lembrar quem esta ativo. Isso mora na tabela
`conversa_agente_ativo` (migration 0017), uma linha por `(tenant, conversation_id)`, fora do
estado dos dois grafos (§2.1: o `checkpoint_ns` no grafo raiz e' ignorado pelo LangGraph, e dois
grafos no mesmo thread se sobrescrevem).

TRES PECAS, nesta ordem de pureza:

1. `decidir_transicao` — PURA. Recebe a linha atual (ou nada) e o que aconteceu no turno, devolve
   para quem a conversa vai e por que. E' a maquina de §2.2, e a tabela de verdade dela esta
   fixada em `tests/unit/platform/webhooks/whatsapp/test_roteamento.py`.
2. `AgenteAtivoStore` — a porta de persistencia, com escrita por compare-and-set (`revisao`), e o
   adaptador `PostgresAgenteAtivoStore`.
3. `ConversaRouter` — junta as duas: le, decide, grava por CAS, rele' e recalcula se perdeu a
   corrida, e purga as linhas velhas no maximo uma vez a cada 10 minutos por processo.

A REGRA ESTRUTURAL (§2.3) que `decidir_transicao` implementa por construcao: so' um `handoff`
tipado da Helena NESTE turno leva a conversa para o Lucas. Todo outro caminho que nao casa uma
linha da tabela vai para a Helena — nunca se fica no Lucas por omissao.

ONDA (c): SOMBRA. O roteador e' construido so' com `MAEZO_ROTEADOR_LUCAS` ligado (`service.py`).
Sem o turno do Lucas no despachante ele nao muda resposta nenhuma: grava `helena` + motivo e loga
os sinais lexicos. ONDA (e): com o Lucas presente (`HelenaDispatcher.lucas_turno`), a Helena emite
o `handoff` tipado, o despachante executa o Lucas e grava `lucas` aqui (`lucas_disponivel=True`,
passado pela raiz de composicao). Desligado, o despachante recebe `roteador=None` e o caminho e' o
de hoje.

PHI: a tabela guarda o `conversation_id` keyed (ADR-0035), enums e `YYYY-MM`. Nenhum telefone,
nenhum texto; os logs deste modulo carregam so' tokens.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, Protocol, get_args

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

from .pre_roteamento import PreRoteamento, SinaisLexicos

logger = structlog.get_logger(__name__)

TABELA: Final[str] = "conversa_agente_ativo"

AgenteAtivo = Literal["helena", "lucas"]
#: Os tokens de `transicao_motivo` (§2.2). O CHECK da migration 0017 repete esta lista, e
#: `test_roteamento.py` prova que as duas nao divergem.
TransicaoMotivo = Literal[
    "inicio",
    "retorno_inatividade",
    "retorno_saude",
    "retorno_pedido_humano",
    "retorno_falha",
    "handoff_cobranca",
    "continua_lucas",
    "retorno_fora_do_canal",
    "lucas_encerrou",
]
#: Dominio fechado de `cobranca_subtipo` (§2.4), o mesmo do CHECK da coluna.
CobrancaSubtipo = Literal[
    "boleto_2via",
    "vencimento",
    "confirmacao_pagamento",
    "contestacao",
    "cobranca_recebida",
    "cancelamento",
    "outro",
]

AGENTES: Final[tuple[str, ...]] = get_args(AgenteAtivo)
TRANSICOES: Final[tuple[str, ...]] = get_args(TransicaoMotivo)
COBRANCA_SUBTIPOS: Final[tuple[str, ...]] = get_args(CobrancaSubtipo)

_COMPETENCIA = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: Motivos de escalonamento da Helena que sao SAUDE (§2.2, linha `retorno_saude`).
_MOTIVOS_DE_SAUDE: Final[frozenset[str]] = frozenset(
    {"red_flag_clinico", "risco_psicossocial", "intencao_clinica"}
)
#: Intencoes do classify que sao saude por si so'.
_INTENCOES_DE_SAUDE: Final[frozenset[str]] = frozenset({"symptom", "clinical_question"})
#: Intencoes que deixam o Lucas continuar (§2.2, `continua_lucas`): "ok", "e o de setembro?".
_INTENCOES_QUE_CONTINUAM: Final[frozenset[str]] = frozenset({"greeting", "information"})
#: Intencoes que tiram a conversa do Lucas com o texto fixo (§2.2, `retorno_fora_do_canal`).
_INTENCOES_FORA_DO_CANAL: Final[frozenset[str]] = frozenset({"outside_channel", "scheduling"})

#: Purga (§3): ate' 100 linhas com mais de 30 dias, no maximo 1 vez a cada 10 minutos por processo.
PURGA_IDADE: Final[timedelta] = timedelta(days=30)
PURGA_INTERVALO: Final[timedelta] = timedelta(minutes=10)
PURGA_LOTE: Final[int] = 100

#: Quantas vezes o roteador rele' e recalcula apos perder o CAS; esgotadas, levanta o conflito.
MAX_TENTATIVAS_CAS: Final[int] = 3


class RoteamentoError(RuntimeError):
    """Falha do roteador. A mensagem e' sempre um token de classe, nunca texto de beneficiario."""


class ConflitoDeRevisaoError(RoteamentoError):
    """O CAS foi perdido `MAX_TENTATIVAS_CAS` vezes seguidas."""


def _agora() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------
# O que aconteceu no turno.
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PedidoDeHandoff:
    """O `handoff` tipado da Helena para o Lucas (§2.4), visto pelo roteador.

    O despachante o deriva do `HandoffCobranca` que a Helena emitiu (onda e), so' depois de o
    turno do Lucas ter rodado. Nenhum texto: o subtipo e a competencia.
    """

    cobranca_subtipo: CobrancaSubtipo
    competencia: str | None = None

    def __post_init__(self) -> None:
        if self.cobranca_subtipo not in COBRANCA_SUBTIPOS:
            raise ValueError("roteamento: cobranca_subtipo fora do dominio")
        if self.competencia is not None and not _COMPETENCIA.match(self.competencia):
            raise ValueError("roteamento: competencia fora do formato YYYY-MM")


@dataclass(frozen=True, slots=True)
class EventoDoTurno:
    """Os fatos do turno que a maquina le'. Nenhum texto: so' tokens e booleanos."""

    intent: str | None = None
    escalation_motivo: str | None = None
    abriu_coleta: bool = False
    falha_tecnica: bool = False
    psychosocial_risk: bool = False
    sintoma_codigo: str | None = None
    pedido_humano_lexico: bool = False
    sinal_saude_lexico: bool = False
    handoff: PedidoDeHandoff | None = None
    lucas_escalou: bool = False

    @property
    def tem_sinal_de_saude(self) -> bool:
        return (
            self.escalation_motivo in _MOTIVOS_DE_SAUDE
            or self.abriu_coleta
            or self.psychosocial_risk
            or bool(self.sintoma_codigo)
            or self.intent in _INTENCOES_DE_SAUDE
            or self.sinal_saude_lexico
        )

    @property
    def tem_pedido_humano(self) -> bool:
        return (
            self.escalation_motivo == "solicitacao_humano"
            or self.intent == "human_request"
            or self.pedido_humano_lexico
        )


def evento_do_resultado_da_helena(
    resultado: Mapping[str, Any],
    sinais: SinaisLexicos | None,
    *,
    handoff: PedidoDeHandoff | None = None,
    lucas_escalou: bool = False,
) -> EventoDoTurno:
    """Traduz o estado final do turno da Helena nos fatos que a maquina le'.

    So' le' chaves de saida que JA existem no `HelenaState`; o texto da mensagem e o da resposta
    nunca sao lidos. `falha_tecnica` cobre os tres jeitos de o turno terminar em falha: o motivo
    `falha_tecnica` (classify falhou, handoff recusado), o start que falhou e a resposta honesta de
    falha de start.
    """
    motivo = resultado.get("escalation_motivo")
    falha = (
        motivo == "falha_tecnica"
        or bool(resultado.get("start_failed"))
        or resultado.get("response_kind") == "falha_tecnica_start"
    )
    intent = resultado.get("intent")
    sintoma = resultado.get("sintoma_codigo")
    return EventoDoTurno(
        intent=intent if isinstance(intent, str) else None,
        escalation_motivo=motivo if isinstance(motivo, str) else None,
        abriu_coleta=resultado.get("response_kind") == "collect",
        falha_tecnica=falha,
        psychosocial_risk=resultado.get("psychosocial_risk") is True,
        sintoma_codigo=sintoma if isinstance(sintoma, str) and sintoma else None,
        pedido_humano_lexico=bool(sinais and sinais.pedido_humano),
        sinal_saude_lexico=bool(sinais and sinais.sinal_saude),
        handoff=handoff,
        lucas_escalou=lucas_escalou,
    )


# ---------------------------------------------------------------------------------------
# A linha persistida e a decisao.
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LinhaAgenteAtivo:
    conversation_id: str
    agente_ativo: AgenteAtivo
    revisao: int
    transicao_motivo: TransicaoMotivo
    lucas_cobranca_subtipo: CobrancaSubtipo | None
    lucas_competencia: str | None
    ativo_desde: datetime
    ultimo_turno_em: datetime
    expira_em: datetime


@dataclass(frozen=True, slots=True)
class Decisao:
    agente_ativo: AgenteAtivo
    transicao_motivo: TransicaoMotivo
    #: `FRASE_PASSAGEM_COBRANCA` so' na primeira passagem (de `helena` para `lucas`).
    enviar_frase_passagem: bool = False
    lucas_cobranca_subtipo: CobrancaSubtipo | None = None
    lucas_competencia: str | None = None


def _para_helena(motivo: TransicaoMotivo) -> Decisao:
    return Decisao(agente_ativo="helena", transicao_motivo=motivo)


def decidir_transicao(atual: LinhaAgenteAtivo | None, evento: EventoDoTurno, *, agora: datetime) -> Decisao:
    """A maquina de estados de §2.2. PURA: sem I/O, sem relogio proprio, sem LLM.

    Duas fases. A primeira decide DE ONDE se parte: linha ausente parte de `helena` com motivo
    `inicio`; linha vencida (`expira_em < agora`) parte de `helena` com `retorno_inatividade`; linha
    viva parte do agente gravado. A segunda aplica os eventos do turno na ordem de §2.2, o primeiro
    que casar vence:

      1. sinal de saude (escalonamento clinico, coleta, risco, sintoma, ou lexico) -> helena
      2. pedido de pessoa (escalonamento por `solicitacao_humano`, intencao ou lexico) -> helena
      3. falha tecnica -> helena
      4. `handoff` tipado -> lucas (`handoff_cobranca`; frase so' vindo da Helena), ou
         `lucas_encerrou` se o Lucas terminou o turno escalando
      5. Lucas ativo + `greeting`/`information` -> lucas (`continua_lucas`), ou `lucas_encerrou`
      6. Lucas ativo + `outside_channel`/`scheduling` -> helena (`retorno_fora_do_canal`)
      7. Lucas ativo + qualquer outra coisa -> helena (`retorno_falha`): estado injustificado nao
         fica no Lucas
      8. nada casou com a Helena ativa -> helena, mantendo o motivo de partida

    Saude antes de pedido de pessoa e' o conflito P2 x P5 de §1.2: vale o escalonamento clinico,
    que tambem leva a uma pessoa, com prioridade maior.
    """
    if atual is None:
        de: AgenteAtivo = "helena"
        motivo_de_partida: TransicaoMotivo = "inicio"
        subtipo: CobrancaSubtipo | None = None
        competencia: str | None = None
    elif atual.expira_em < agora:
        de, motivo_de_partida, subtipo, competencia = "helena", "retorno_inatividade", None, None
    else:
        de = atual.agente_ativo
        motivo_de_partida = atual.transicao_motivo
        subtipo, competencia = atual.lucas_cobranca_subtipo, atual.lucas_competencia

    if evento.tem_sinal_de_saude:
        return _para_helena("retorno_saude")
    if evento.tem_pedido_humano:
        return _para_helena("retorno_pedido_humano")
    if evento.falha_tecnica:
        return _para_helena("retorno_falha")
    if evento.handoff is not None:
        if evento.lucas_escalou:
            return _para_helena("lucas_encerrou")
        return Decisao(
            agente_ativo="lucas",
            transicao_motivo="handoff_cobranca",
            enviar_frase_passagem=de == "helena",
            lucas_cobranca_subtipo=evento.handoff.cobranca_subtipo,
            lucas_competencia=evento.handoff.competencia,
        )
    if de == "lucas":
        if evento.intent in _INTENCOES_QUE_CONTINUAM:
            if evento.lucas_escalou:
                return _para_helena("lucas_encerrou")
            return Decisao(
                agente_ativo="lucas",
                transicao_motivo="continua_lucas",
                lucas_cobranca_subtipo=subtipo,
                lucas_competencia=competencia,
            )
        if evento.intent in _INTENCOES_FORA_DO_CANAL:
            return _para_helena("retorno_fora_do_canal")
        return _para_helena("retorno_falha")
    return _para_helena(motivo_de_partida)


# ---------------------------------------------------------------------------------------
# Persistencia.
# ---------------------------------------------------------------------------------------
class AgenteAtivoStore(Protocol):
    """A porta da tabela `conversa_agente_ativo`. Toda escrita e' compare-and-set."""

    async def ler(self, conversation_id: str) -> LinhaAgenteAtivo | None: ...

    async def gravar(self, linha: LinhaAgenteAtivo, *, revisao_esperada: int | None) -> bool:
        """`revisao_esperada=None` insere (perde se a linha ja' existe); senao atualiza so' se a
        revisao gravada for a esperada, e a revisao nova e' `esperada + 1`. `False` = CAS perdido."""
        ...

    async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int: ...


_COLUNAS: Final[str] = (
    "conversation_id, agente_ativo, revisao, transicao_motivo, lucas_cobranca_subtipo, "
    "lucas_competencia, ativo_desde, ultimo_turno_em, expira_em"
)
_SELECT_SQL: Final[str] = f"SELECT {_COLUNAS} FROM {TABELA} WHERE tenant = $1 AND conversation_id = $2"
_INSERT_SQL: Final[str] = (
    f"INSERT INTO {TABELA} (tenant, {_COLUNAS}) VALUES ($1, $2, $3, 0, $4, $5, $6, $7, $8, $9) "
    "ON CONFLICT (tenant, conversation_id) DO NOTHING"
)
_UPDATE_SQL: Final[str] = (
    f"UPDATE {TABELA} SET agente_ativo = $3, revisao = revisao + 1, transicao_motivo = $4, "
    "lucas_cobranca_subtipo = $5, lucas_competencia = $6, ativo_desde = $7, ultimo_turno_em = $8, "
    "expira_em = $9 WHERE tenant = $1 AND conversation_id = $2 AND revisao = $10"
)
_PURGE_SQL: Final[str] = (
    f"DELETE FROM {TABELA} WHERE tenant = $1 AND conversation_id IN ("
    f"SELECT conversation_id FROM {TABELA} WHERE tenant = $1 AND ultimo_turno_em < $2 "
    "ORDER BY ultimo_turno_em LIMIT $3)"
)


def _linhas_afetadas(status: Any) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


class PostgresAgenteAtivoStore:
    """`AgenteAtivoStore` sobre asyncpg, por tenant (`search_path` pinado em todo acquire).

    O tenant entra na PK, em todo `WHERE` e num CHECK que amarra o prefixo do `conversation_id`
    ao proprio tenant. Uma conversa de outro tenant e' recusada AQUI, antes de qualquer SQL.
    """

    def __init__(self, *, dsn: str, tenant: str, pool: Any = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._prefixo = f"wa:{tenant}:"
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    def _conferir(self, conversation_id: str) -> None:
        if not conversation_id.startswith(self._prefixo):
            raise RoteamentoError("roteamento: conversa de outro tenant")

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def ler(self, conversation_id: str) -> LinhaAgenteAtivo | None:
        self._conferir(conversation_id)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, self._tenant, conversation_id)
        if row is None:
            return None
        return LinhaAgenteAtivo(
            conversation_id=str(row["conversation_id"]),
            agente_ativo=row["agente_ativo"],
            revisao=int(row["revisao"]),
            transicao_motivo=row["transicao_motivo"],
            lucas_cobranca_subtipo=row["lucas_cobranca_subtipo"],
            lucas_competencia=row["lucas_competencia"],
            ativo_desde=row["ativo_desde"],
            ultimo_turno_em=row["ultimo_turno_em"],
            expira_em=row["expira_em"],
        )

    async def gravar(self, linha: LinhaAgenteAtivo, *, revisao_esperada: int | None) -> bool:
        self._conferir(linha.conversation_id)
        valores = (
            self._tenant,
            linha.conversation_id,
            linha.agente_ativo,
            linha.transicao_motivo,
            linha.lucas_cobranca_subtipo,
            linha.lucas_competencia,
            linha.ativo_desde,
            linha.ultimo_turno_em,
            linha.expira_em,
        )
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            if revisao_esperada is None:
                status = await conn.execute(_INSERT_SQL, *valores)
            else:
                status = await conn.execute(_UPDATE_SQL, *valores, revisao_esperada)
        return _linhas_afetadas(status) == 1

    async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(_PURGE_SQL, self._tenant, antes_de, limite)
        return _linhas_afetadas(status)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


# ---------------------------------------------------------------------------------------
# O roteador.
# ---------------------------------------------------------------------------------------
class ConversaRouter:
    """Le', decide e grava UMA vez por mensagem recebida, depois do turno (§2.2).

    Se o turno cai, nada e' gravado e a reentrega recalcula. Se outra replica gravou antes (CAS
    perdido), o roteador rele' a linha e recalcula ate' `max_tentativas` vezes.

    `lucas_disponivel=False` (onda c) e' a trava da sombra: uma decisao que levaria ao Lucas
    vira `helena`/`retorno_falha` e um ERRO no log, porque gravar `lucas` sem ninguem para atender
    seria um fato inventado na tabela.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        store: AgenteAtivoStore,
        lexicos: PreRoteamento,
        inatividade: timedelta,
        lucas_disponivel: bool = False,
        relogio: Callable[[], datetime] = _agora,
        relogio_monotonico: Callable[[], float] = time.monotonic,
        purga_idade: timedelta = PURGA_IDADE,
        purga_intervalo: timedelta = PURGA_INTERVALO,
        purga_lote: int = PURGA_LOTE,
        max_tentativas: int = MAX_TENTATIVAS_CAS,
    ) -> None:
        if inatividade <= timedelta(0):
            raise ValueError("roteamento: inatividade deve ser positiva")
        if max_tentativas < 1:
            raise ValueError("roteamento: max_tentativas deve ser >= 1")
        self.tenant_id = tenant_id
        self._store = store
        self._lexicos = lexicos
        self._inatividade = inatividade
        self._lucas_disponivel = lucas_disponivel
        self._relogio = relogio
        self._monotonico = relogio_monotonico
        self._purga_idade = purga_idade
        self._purga_intervalo_s = purga_intervalo.total_seconds()
        self._purga_lote = purga_lote
        self._max_tentativas = max_tentativas
        self._ultima_purga: float | None = None

    @property
    def versao_lexicos(self) -> str:
        return self._lexicos.versao

    def pre_rotear(self, texto: str) -> SinaisLexicos:
        """A camada deterministica de §2.3, antes de qualquer LLM."""
        return self._lexicos.avaliar(texto)

    async def agente_ativo(self, conversation_id: str) -> AgenteAtivo:
        """Quem esta' com a conversa AGORA, para a Helena saber se a frase de passagem sai (onda
        e). So' LEITURA: a escrita continua sendo uma por mensagem, em `registrar_turno`. Linha
        ausente ou vencida e' `helena` — a mesma partida de `decidir_transicao`."""
        atual = await self._store.ler(conversation_id)
        if atual is None or atual.expira_em < self._relogio():
            return "helena"
        return atual.agente_ativo

    def _linha(
        self, conversation_id: str, atual: LinhaAgenteAtivo | None, decisao: Decisao, agora: datetime
    ) -> LinhaAgenteAtivo:
        continua = (
            atual is not None and atual.expira_em >= agora and atual.agente_ativo == decisao.agente_ativo
        )
        return LinhaAgenteAtivo(
            conversation_id=conversation_id,
            agente_ativo=decisao.agente_ativo,
            revisao=0 if atual is None else atual.revisao + 1,
            transicao_motivo=decisao.transicao_motivo,
            lucas_cobranca_subtipo=decisao.lucas_cobranca_subtipo,
            lucas_competencia=decisao.lucas_competencia,
            ativo_desde=atual.ativo_desde if continua and atual is not None else agora,
            ultimo_turno_em=agora,
            expira_em=agora + self._inatividade,
        )

    async def registrar_turno(
        self,
        *,
        conversation_id: str,
        sinais: SinaisLexicos | None,
        resultado_helena: Mapping[str, Any],
        handoff: PedidoDeHandoff | None = None,
        lucas_escalou: bool = False,
    ) -> Decisao:
        evento = evento_do_resultado_da_helena(
            resultado_helena, sinais, handoff=handoff, lucas_escalou=lucas_escalou
        )
        for tentativa in range(1, self._max_tentativas + 1):
            atual = await self._store.ler(conversation_id)
            agora = self._relogio()
            decisao = decidir_transicao(atual, evento, agora=agora)
            if decisao.agente_ativo == "lucas" and not self._lucas_disponivel:
                logger.error(
                    "roteador_lucas_indisponivel",
                    tenant_id=self.tenant_id,
                    conversation_id=conversation_id,
                    transicao=decisao.transicao_motivo,
                    detail="decisao levaria ao Lucas, que nao tem entrada neste build; gravado helena",
                )
                decisao = _para_helena("retorno_falha")
            linha = self._linha(conversation_id, atual, decisao, agora)
            gravou = await self._store.gravar(
                linha, revisao_esperada=None if atual is None else atual.revisao
            )
            if gravou:
                await self._talvez_purgar(agora)
                return decisao
            logger.warning(
                "roteador_cas_perdido",
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                tentativa=tentativa,
            )
        raise ConflitoDeRevisaoError("roteamento: CAS perdido em todas as tentativas")

    async def _talvez_purgar(self, agora: datetime) -> None:
        marca = self._monotonico()
        if self._ultima_purga is not None and marca - self._ultima_purga < self._purga_intervalo_s:
            return
        self._ultima_purga = marca
        try:
            apagadas = await self._store.purgar_antigas(
                antes_de=agora - self._purga_idade, limite=self._purga_lote
            )
        except Exception as exc:  # a purga nunca derruba o turno; fica no log de erro
            logger.error("roteador_purga_falhou", tenant_id=self.tenant_id, error_type=type(exc).__name__)
            return
        if apagadas:
            logger.info("roteador_purga", tenant_id=self.tenant_id, apagadas=apagadas)
