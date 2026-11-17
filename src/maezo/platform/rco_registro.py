"""Registro de decisoes das regras do pacote K1 do rco (ADR-0077 §4; unidade 5c-2).

Uma linha APPEND-ONLY por avaliacao que casou uma regra do K1 (`rco_decisao_regra`, migracao 0035), em
qualquer estado manual. Em 5c-2 toda regra esta em `sombra` e o indicador `efeito_k1_habilitado` e falso:
o registro e so observacao, nenhum caso muda de rumo por causa dele.

Tres pecas, da mais pura para a menos:

1. `avaliar_regra`: a parte da linha que sai SO da linha casada da DMN, do pacote pinado e do arquivo de
   estado (estado manual, ativacao efetiva, modo). Tambem alimenta as chaves de auditoria `estadoManual`,
   `ativacaoEfetiva` e `modo` (`rco_pin.rco_avaliacao`), de modo que a auditoria e o registro dizem a
   mesma coisa sobre o mesmo instante.
2. `montar_linha`: funcao pura que junta a avaliacao com o contexto da tarefa (chave de negocio, chave da
   amostragem) e o ordinal, e calcula amostragem (`rco_ativacao.amostra_inicial`/`amostrar`) e a acao
   (`rco_ativacao.filtrar_saida`). Sem banco.
3. `PostgresRegistroDecisoesRco`: grava a linha. O ordinal e calculado DENTRO da transacao que grava,
   sob `pg_advisory_xact_lock` por (tenant, idRegra, versaoRegra): duas avaliacoes concorrentes da mesma
   regra se serializam e nunca recebem o mesmo ordinal (um indice unico parcial e a segunda barreira).

Ordinal. Conta so as decisoes que a revisao inicial mira (ADR-0077 §3): as adversas COERENTES COM A
CLASSE (`glosar` com `contas`/`administrativo`; `negar` com `administrativo`;
`rco_ativacao.CLASSES_COM_EFEITO`). Contar tambem `liberar` consumiria a janela antes de qualquer glosa;
contar uma combinacao incoerente (ex.: `negar` clinico, que vai ao humano) consumiria a janela com caso
que nunca chega a efeito. Qualquer outra decisao: ordinal nulo, nunca amostrada. O ordinal so alimenta a
amostra inicial da `plena` (`amostra_inicial`); e o MAX+1 das linhas JA GRAVADAS, lido na mesma transacao
que grava (nunca reservado antes). Linha perdida (DL-0182): com o efeito possivel (`modo` diferente de
`sombra`) o harness nao completa a tarefa sem a linha gravada (fail-closed; a re-entrega grava com a
mesma `chave_idempotencia`), entao nenhuma decisao com efeito fica fora da contagem; em `sombra` a perda
continua fail-open, porque nada age.

Janela da `conferida` (DL-0182). NAO e o ordinal: conta so conferencias CONFIRMADAS por humano
(`rco_ativacao.conferencias_confirmadas`; aqui, `_CONFIRMADAS_SQL`), por (tenant, idRegra, versaoRegra):
desfecho `sim` de decisao com `acao = conferencia`, depois do `nao` mais recente. Sombra e decisao que nao
foi conferencia nunca consomem a janela; versao nova comeca do zero pela chave. Lida sob o MESMO lock da
regra que `registrar_desfecho` toma, entao um `nao` gravado antes ja fecha a decisao seguinte.

Desfecho (`registrar_desfecho`, DL-0182). O clique de uma conferencia (`confirmar` -> `sim`, `rejeitar`
-> `nao`; `sem_desfecho` para a conferencia que nao saiu) vira LINHA NOVA em
`rco_decisao_regra_desfecho` (append-only). So decisao com `acao = conferencia`; o mesmo desfecho
repetido (re-tentativa) nao grava de novo; `sem_desfecho` nao sobrescreve `sim`/`nao`. Um `nao` zera a
janela e alerta a coordenacao (log estruturado so com codigos e ids, e a metrica
`maezo_rco_conferencia_rejeitada_total`).

Idempotencia. `chave_idempotencia` = sha256 de `tenant:task_id:decision_key`: a re-entrega da mesma
tarefa externa nao grava de novo nem consome ordinal (o INSERT cai em `ON CONFLICT DO NOTHING`).

Zona Geral: nenhuma coluna de beneficiario. A chave de negocio NAO e gravada (minimizacao, LGPD):
so vive em memoria, para o HMAC da amostragem. O vinculo com o caso e indireto: `task_id`, que entra
so em hash na `chave_idempotencia`, aponta para a linha de auditoria da tarefa; eliminado o registro de
auditoria daquele `task_id`, esta linha nao tem mais vinculo.
As saidas da DMN (`decisao`, `classeAdverso`, `motivoTab38`, `fila`) so sao gravadas se
tiverem forma de token curto: uma saida FEEL pode ecoar variavel de caso, e o que nao tem a forma
vira nulo, nunca texto livre.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Protocol, runtime_checkable

import structlog

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform import rco_ativacao as ativ
from maezo.platform.observability import record_rco_conferencia_rejeitada
from maezo.platform.rco_manifesto import ParametrosAtivacao, RegraK1
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

#: Segredo da amostragem (§4) e o identificador (NAO secreto) da chave que decidiu a linha. Provisionados
#: por humano; nenhum arquivo de deploy os define em 5c-2. Sem os dois, toda decisao amostravel e
#: amostrada com motivo `chave_ausente` (falhar para mais revisao e o lado seguro).
VARIAVEL_CHAVE_AMOSTRA: Final[str] = "RCO_AMOSTRA_HMAC_KEY"
VARIAVEL_ID_CHAVE_AMOSTRA: Final[str] = "RCO_AMOSTRA_HMAC_KEY_ID"

_TOKEN_SAIDA: Final = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ID_CHAVE: Final = re.compile(r"[A-Za-z0-9._:-]{1,64}")
CONCORDOU: Final[frozenset[str]] = frozenset({"sim", "nao", "sem_desfecho"})
MOTIVOS_AMOSTRA: Final[frozenset[str]] = frozenset(
    {ativ.MOTIVO_INICIAL, ativ.MOTIVO_ALEATORIA, ativ.MOTIVO_CHAVE_AUSENTE}
)


class RegistroRcoIndisponivelError(RuntimeError):
    """O registro nao respondeu (conexao, timeout, SQL). Quem chama decide; a tarefa nunca para por isso."""


def _token(valor: object) -> str | None:
    """A saida da DMN so e gravada com forma de token curto; qualquer outra coisa vira nulo."""
    if isinstance(valor, str) and _TOKEN_SAIDA.fullmatch(valor):
        return valor
    return None


@dataclass(frozen=True, slots=True)
class AvaliacaoRegra:
    """O que se sabe da regra casada no instante da avaliacao, sem contexto de tarefa nem banco."""

    decision_key: str
    id_regra: str
    versao_regra: str
    sha_dmn: str
    decisao: str | None
    classe_adverso: str | None
    motivo_tab38: str | None
    fila: str | None
    estado_manual: str
    ativacao_pacote: str
    ativacao_efetiva: str
    modo: str
    exige_aditivo: bool
    revisao_inicial_decisoes: int
    amostra_posterior_pct: int | float
    conferencia_negativa_admin_decisoes: int
    alertas: tuple[str, ...] = ()
    #: MZO-3: avaliacao feita pela SOMBRA do montador (`rco_sombra`), fora do caminho que decide. Sempre
    #: `modo=sombra` (o indicador do K1 nem e lido) e NUNCA consome ordinal nem entra na amostragem: a
    #: janela de revisao inicial (5d/5e) conta so decisoes do caminho que decide. A linha fica com
    #: `ordinal` e `motivo_amostra` nulos e `amostrada=false`, sem coluna nova (migracao 0035 intacta).
    sombra: bool = False

    @property
    def tem_ordinal(self) -> bool:
        """So a adversa coerente com a classe que a revisao inicial mira (ADR-0077 §3); nunca a sombra."""
        return (
            not self.sombra
            and self.decisao in ativ.DECISOES_COM_AMOSTRAGEM
            and self.classe_adverso in ativ.CLASSES_COM_EFEITO.get(self.decisao or "", frozenset())
        )

    def campos_de_auditoria(self) -> dict[str, str]:
        """As chaves camelCase de `dmn_versions` (ADR-0077 §4), todas em texto."""
        return {
            "estadoManual": self.estado_manual,
            "ativacaoEfetiva": self.ativacao_efetiva,
            "modo": self.modo,
        }


def avaliar_regra(
    *,
    decision_key: str,
    sha_dmn: str,
    regra: RegraK1,
    parametros: ParametrosAtivacao,
    row: Mapping[str, Any] | None,
    estados: ativ.EstadosManuais,
    habilitado: bool,
    sombra: bool = False,
) -> AvaliacaoRegra | None:
    """`None` quando a regra nao tem `ativacao`/`exige_aditivo` (manifesto MAJOR 1): nada a registrar.

    `override` e sempre `None` aqui: so o monitor de reversao (5e) o grava, e nunca promove.
    `sombra=True` (MZO-3): o indicador e tratado como desligado, qualquer que seja `habilitado`, e a
    avaliacao sai marcada (`AvaliacaoRegra.sombra`): `modo=sombra`, sem ordinal.
    """
    if regra.ativacao is None or regra.exige_aditivo is None:
        return None
    estado_manual = estados.estado_de(regra.id_regra, regra.versao_regra)
    efetivo = ativ.ativacao_efetiva(
        estado_manual=estado_manual,
        ativacao_pacote=regra.ativacao,
        override=None,
        efeito_k1_habilitado=habilitado and not sombra,
        arquivo_ilegivel=estados.ilegivel,
    )
    linha = row if row is not None else {}
    return AvaliacaoRegra(
        decision_key=decision_key,
        id_regra=regra.id_regra,
        versao_regra=regra.versao_regra,
        sha_dmn=sha_dmn,
        decisao=_token(linha.get("decisao")),
        classe_adverso=_token(linha.get("classeAdverso")),
        motivo_tab38=_token(linha.get("motivoTab38")),
        fila=_token(linha.get("fila")),
        estado_manual="desligada" if estados.ilegivel else estado_manual,
        ativacao_pacote=regra.ativacao,
        ativacao_efetiva=efetivo.ativacao_efetiva,
        modo=efetivo.modo,
        exige_aditivo=regra.exige_aditivo,
        revisao_inicial_decisoes=parametros.revisao_inicial_decisoes,
        amostra_posterior_pct=parametros.amostra_posterior_pct,
        conferencia_negativa_admin_decisoes=parametros.conferencia_negativa_admin_decisoes,
        alertas=efetivo.alertas,
        sombra=sombra,
    )


# ---------------------------------------------------------------------------
# Contexto da tarefa e a linha
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextoDecisao:
    """O que vem da tarefa, nao da regra. `chave_negocio` (bruta) so serve a amostragem; nunca e gravada
    nem aparece no `repr`."""

    tenant: str
    chave_idempotencia: str
    chave_negocio: str | None = field(default=None, repr=False)
    chave_amostra: bytes | None = field(default=None, repr=False)
    id_chave_amostra: str | None = None


def chave_idempotencia(tenant: str, task_id: str, decision_key: str) -> str:
    """sha256 de `tenant:task_id:decision_key`: a re-entrega da tarefa cai na mesma chave."""
    return hashlib.sha256(f"{tenant}:{task_id}:{decision_key}".encode()).hexdigest()


def chave_amostra_do_ambiente(
    ambiente: Mapping[str, str] | None = None,
) -> tuple[bytes | None, str | None]:
    """(chave, id) da amostragem. Qualquer um ausente, vazio ou com id fora do formato: `(None, None)`."""
    env = os.environ if ambiente is None else ambiente
    bruto = (env.get(VARIAVEL_CHAVE_AMOSTRA) or "").strip()
    id_chave = (env.get(VARIAVEL_ID_CHAVE_AMOSTRA) or "").strip()
    if not bruto or not _ID_CHAVE.fullmatch(id_chave):
        return None, None
    return bruto.encode("utf-8"), id_chave


def contexto_da_tarefa(
    *,
    tenant: str,
    task_id: str,
    decision_key: str,
    business_key: str | None,
    ambiente: Mapping[str, str] | None = None,
) -> ContextoDecisao:
    """Monta o contexto de UMA decisao. A chave de negocio fica so em memoria (amostragem)."""
    chave, id_chave = chave_amostra_do_ambiente(ambiente)
    return ContextoDecisao(
        tenant=tenant,
        chave_idempotencia=chave_idempotencia(tenant, task_id, decision_key),
        chave_negocio=business_key or None,
        chave_amostra=chave,
        id_chave_amostra=id_chave,
    )


COLUNAS: Final[tuple[str, ...]] = (
    "tenant",
    "chave_idempotencia",
    "id_regra",
    "versao_regra",
    "sha_dmn",
    "decision_key",
    "decisao",
    "classe_adverso",
    "motivo_tab38",
    "fila",
    "estado_manual",
    "ativacao_pacote",
    "ativacao_efetiva",
    "modo",
    "acao",
    "amostrada",
    "motivo_amostra",
    "id_chave_amostra",
    "ordinal",
)


@dataclass(frozen=True, slots=True)
class LinhaDecisao:
    """A linha pronta para o INSERT, na ordem de `COLUNAS`."""

    tenant: str
    chave_idempotencia: str
    id_regra: str
    versao_regra: str
    sha_dmn: str
    decision_key: str
    decisao: str | None
    classe_adverso: str | None
    motivo_tab38: str | None
    fila: str | None
    estado_manual: str
    ativacao_pacote: str
    ativacao_efetiva: str
    modo: str
    acao: str
    amostrada: bool
    motivo_amostra: str | None
    id_chave_amostra: str | None
    ordinal: int | None

    def params(self) -> tuple[Any, ...]:
        return tuple(getattr(self, c) for c in COLUNAS)


def montar_linha(
    av: AvaliacaoRegra,
    ctx: ContextoDecisao,
    ordinal: int | None,
    *,
    conferencias_confirmadas: int | None = None,
) -> LinhaDecisao:
    """Funcao pura. `ordinal` e `None` para decisao sem amostragem; para `negar`/`glosar` e o 1-based
    por (idRegra, versaoRegra) dado por quem grava, sob lock.

    Amostragem (§4): so `negar`/`glosar`. As primeiras `revisao_inicial_decisoes` sao `inicial`; depois,
    HMAC com a chave do ambiente (`aleatoria`), ou `chave_ausente` sem chave (toda adversa amostrada).
    A acao e a de `filtrar_saida` (em sombra, `registrar_apenas`). `conferencias_confirmadas` (DL-0182) e
    a contagem da janela da `conferida`, lida por quem grava sob o lock da regra; `None` (desconhecida)
    mantem a janela aberta: a decisao vai a conferencia.
    """
    amostrada, motivo = False, ""
    por_chave = False  # a chave HMAC decidiu a linha (amostrada ou nao): so assim o id dela e gravado
    if av.tem_ordinal:
        if ordinal is None:
            raise ValueError("montar_linha: decisao amostravel exige ordinal")
        amostrada, motivo = ativ.amostra_inicial(ordinal, av.revisao_inicial_decisoes)
        if not amostrada:
            amostrada, motivo = ativ.amostrar(
                ctx.chave_amostra,
                ctx.id_chave_amostra,
                ctx.chave_negocio or "",
                av.id_regra,
                av.versao_regra,
                av.amostra_posterior_pct,
            )
            por_chave = motivo != ativ.MOTIVO_CHAVE_AUSENTE
    elif ordinal is not None:
        raise ValueError("montar_linha: ordinal so existe para decisao amostravel")
    estado = ativ.EstadoEfetivo(av.modo, av.ativacao_efetiva, av.alertas)
    acao = ativ.filtrar_saida(
        estado,
        decisao=av.decisao,
        classe_adverso=av.classe_adverso,
        fila=av.fila,
        exige_aditivo=av.exige_aditivo,
        aditivo_cobre=None,
        conferencia_inicial=ativ.janela_de_conferencia_aberta(
            conferencias_confirmadas, av.conferencia_negativa_admin_decisoes
        ),
        amostrada=amostrada,
    )
    return LinhaDecisao(
        tenant=ctx.tenant,
        chave_idempotencia=ctx.chave_idempotencia,
        id_regra=av.id_regra,
        versao_regra=av.versao_regra,
        sha_dmn=av.sha_dmn,
        decision_key=av.decision_key,
        decisao=av.decisao,
        classe_adverso=av.classe_adverso,
        motivo_tab38=av.motivo_tab38,
        fila=av.fila,
        estado_manual=av.estado_manual,
        ativacao_pacote=av.ativacao_pacote,
        ativacao_efetiva=av.ativacao_efetiva,
        modo=av.modo,
        acao=acao.acao,
        amostrada=amostrada,
        motivo_amostra=motivo or None,
        # A auditoria reproduz a amostra com a chave que decidiu; `inicial` e `chave_ausente` nao a usaram.
        id_chave_amostra=ctx.id_chave_amostra if por_chave else None,
        ordinal=ordinal,
    )


# ---------------------------------------------------------------------------
# Banco (migracao 0035)
# ---------------------------------------------------------------------------


@runtime_checkable
class RegistroDecisoesRco(Protocol):
    """A porta do registro: grava UMA linha por avaliacao. `True` se gravou, `False` se ja existia."""

    @property
    def tenant(self) -> str: ...

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool: ...


_LOCK_SQL: Final = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
_ORDINAL_SQL: Final = (
    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM rco_decisao_regra "
    "WHERE tenant = $1 AND id_regra = $2 AND versao_regra = $3"
)
#: Janela da `conferida` (DL-0182): espelho SQL de `rco_ativacao.conferencias_confirmadas`. So desfechos
#: de decisao da regra com `acao = conferencia` (logo `modo = ativa`: sombra nunca entra); conta as
#: decisoes distintas com `sim` de id maior que o do `nao` mais recente. Indices da 0035:
#: `rco_decisao_regra_regra_idx` e `rco_decisao_regra_desfecho_fk_idx`.
_CONFIRMADAS_SQL: Final = (
    "WITH d AS ("
    "SELECT f.id, f.rco_decisao_regra_id AS decisao, f.concordou "
    "FROM rco_decisao_regra_desfecho f JOIN rco_decisao_regra r ON r.id = f.rco_decisao_regra_id "
    "WHERE r.tenant = $1 AND r.id_regra = $2 AND r.versao_regra = $3 "
    "AND r.acao = 'conferencia' AND r.modo = 'ativa') "
    "SELECT count(DISTINCT decisao) FROM d WHERE concordou = 'sim' "
    "AND id > COALESCE((SELECT max(id) FROM d WHERE concordou = 'nao'), 0)"
)
_INSERT_SQL: Final = (
    f"INSERT INTO rco_decisao_regra ({', '.join(COLUNAS)}) "
    f"VALUES ({', '.join(f'${i}' for i in range(1, len(COLUNAS) + 1))}) "
    "ON CONFLICT (tenant, chave_idempotencia) DO NOTHING RETURNING id"
)

Conectar = Callable[[], Awaitable[Any]]


#: Limites da transacao do registro (SET LOCAL): espera de lock e duracao de cada comando.
LOCK_TIMEOUT: Final[str] = "2s"
STATEMENT_TIMEOUT: Final[str] = "3s"
_FECHAR_TIMEOUT_S: Final[float] = 1.0


async def _fechar(conn: Any) -> None:
    """Fecha a conexao com limite; se travar (ou o chamador cancelou), `terminate()` derruba o socket."""
    try:
        await asyncio.wait_for(conn.close(), _FECHAR_TIMEOUT_S)
    except asyncio.CancelledError:
        conn.terminate()
        raise
    except Exception:
        conn.terminate()  # fechar e' higiene: a falha de fechar nunca vira falha do registro


def _chave_lock(tenant: str, id_regra: str, versao_regra: str) -> str:
    """Lock da REGRA: serializa ordinal, janela da `conferida` e desfecho da mesma (idRegra, versaoRegra)."""
    return f"rco-ordinal|{tenant}|{id_regra}|{versao_regra}"


def _usa_janela_de_conferencia(av: AvaliacaoRegra) -> bool:
    """So a `conferida` com efeito possivel le a contagem; em sombra nada age e a consulta nao roda."""
    return av.tem_ordinal and av.modo == "ativa" and av.ativacao_efetiva == "conferida"


# ---------------------------------------------------------------------------
# Desfecho da conferencia (DL-0182)
# ---------------------------------------------------------------------------

#: Contrato da conferencia de um clique (a UI do 5d-2 envia o clique; o banco guarda `concordou`).
CLIQUES_DA_CONFERENCIA: Final[Mapping[str, str]] = MappingProxyType(
    {"confirmar": ativ.CONCORDOU_SIM, "rejeitar": ativ.CONCORDOU_NAO, "sem_desfecho": ativ.SEM_DESFECHO}
)
#: Codigos de recusa do escritor de desfecho (vocabulario fechado; vai em log e na excecao, nunca dado).
RECUSAS_DESFECHO: Final[frozenset[str]] = frozenset(
    {
        "clique_desconhecido",
        "id_decisao_invalido",
        "desfecho_em_ausente",
        "desfecho_em_sem_fuso",
        "desfecho_em_em_sem_desfecho",
        "decisao_inexistente",
        "acao_nao_e_conferencia",
        "conferencia_ja_decidida",
    }
)


class DesfechoRecusadoError(ValueError):
    """O desfecho nao pode ser gravado. `codigo` e um de `RECUSAS_DESFECHO`; nada foi escrito."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


@dataclass(frozen=True, slots=True)
class ResultadoDesfecho:
    """`gravado=False`: o ultimo desfecho da decisao ja era este (re-tentativa), nada foi escrito.
    `janela_reiniciada`: um `nao` novo foi gravado e zerou a janela da regra (e o alerta saiu)."""

    gravado: bool
    concordou: str
    id_desfecho: int | None = None
    janela_reiniciada: bool = False


def concordancia_do_clique(clique: str) -> str:
    """`confirmar` -> `sim`, `rejeitar` -> `nao`, `sem_desfecho` -> `sem_desfecho`; outro: recusa."""
    concordou = CLIQUES_DA_CONFERENCIA.get(clique) if isinstance(clique, str) else None
    if concordou is None:
        raise DesfechoRecusadoError("clique_desconhecido")
    return concordou


def validar_desfecho_em(concordou: str, desfecho_em: datetime | None) -> None:
    """`sim`/`nao` exigem o instante da decisao humana, com fuso; `sem_desfecho` nao tem instante."""
    if concordou == ativ.SEM_DESFECHO:
        if desfecho_em is not None:
            raise DesfechoRecusadoError("desfecho_em_em_sem_desfecho")
        return
    if not isinstance(desfecho_em, datetime):
        raise DesfechoRecusadoError("desfecho_em_ausente")
    if desfecho_em.tzinfo is None or desfecho_em.utcoffset() is None:
        raise DesfechoRecusadoError("desfecho_em_sem_fuso")


def decidir_desfecho(*, acao: str, concordou: str, ultimo: str | None) -> bool:
    """Funcao pura: grava (`True`), ja gravado (`False`, idempotente) ou recusa.

    - So decisao com `acao = conferencia`: o clique de um clique so existe para ela. Desfecho de sombra
      ou de revisao (K5, monitor do 5e) e outra porta, com contrato proprio; nao entra pela conferencia
      nem conta para a janela.
    - O mesmo desfecho que o ULTIMO da decisao e re-tentativa: nao grava de novo.
    - `sem_desfecho` depois de `sim`/`nao` e recusado: conferencia decidida nao volta a ser indecisa.
    - `sim` <-> `nao` e correcao humana: grava linha nova (vale o ultimo por `id`, migracao 0035).
    """
    if acao != "conferencia":
        raise DesfechoRecusadoError("acao_nao_e_conferencia")
    if ultimo == concordou:
        return False
    if concordou == ativ.SEM_DESFECHO and ultimo in (ativ.CONCORDOU_SIM, ativ.CONCORDOU_NAO):
        raise DesfechoRecusadoError("conferencia_ja_decidida")
    return True


#: Alerta a coordenacao quando uma conferencia e rejeitada (a janela da regra volta a zero).
EVENTO_CONFERENCIA_REJEITADA: Final[str] = "rco_conferencia_rejeitada_janela_reiniciada"


def _alertar_rejeicao(
    *, tenant: str, id_regra: str, versao_regra: str, decisao_id: int, ativacao: str
) -> None:
    """Log estruturado (so codigos e ids: nunca chave de negocio, guia ou beneficiario) e a metrica."""
    logger.warning(
        EVENTO_CONFERENCIA_REJEITADA,
        tenant=tenant,
        id_regra=id_regra,
        versao_regra=versao_regra,
        rco_decisao_regra_id=decisao_id,
        ativacao_efetiva=ativacao,
    )
    record_rco_conferencia_rejeitada(ativacao)


_DECISAO_SQL: Final = (
    "SELECT id_regra, versao_regra, acao, ativacao_efetiva FROM rco_decisao_regra "
    "WHERE id = $1 AND tenant = $2"
)
_ULTIMO_DESFECHO_SQL: Final = (
    "SELECT concordou FROM rco_decisao_regra_desfecho "
    "WHERE rco_decisao_regra_id = $1 ORDER BY id DESC LIMIT 1"
)
_INSERT_DESFECHO_SQL: Final = (
    "INSERT INTO rco_decisao_regra_desfecho (rco_decisao_regra_id, concordou, desfecho_em) "
    "VALUES ($1, $2, $3) RETURNING id"
)


class PostgresRegistroDecisoesRco:
    """`RegistroDecisoesRco` sobre a 0035 via asyncpg, no schema do tenant (DL-0017).

    Uma conexao por operacao (o worker sincrono roda cada tarefa com `asyncio.run` proprio; a conexao
    asyncpg fica presa ao loop que a criou — mesma razao de `PostgresRazaoObrigacoesPagamento`).
    """

    __slots__ = ("_conectar", "_schema", "_tenant")

    def __init__(self, *, dsn: str, tenant: str, conectar: Conectar | None = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        if conectar is None:
            import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

            plain = normalize_dsn(dsn)

            async def _padrao() -> Any:
                return await asyncpg.connect(plain, timeout=5.0)

            conectar = _padrao
        self._conectar = conectar

    @property
    def tenant(self) -> str:
        return self._tenant

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        if contexto.tenant != self._tenant:
            raise ValueError("PostgresRegistroDecisoesRco: tenant do contexto difere do store")
        try:
            conn = await self._conectar()
            try:
                await conn.execute(f'SET search_path TO "{self._schema}", public')
                async with conn.transaction(isolation="read_committed"):
                    # Teto duro: o registro e observacional; nunca espera lock nem consulta sem limite.
                    await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                    await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    ordinal: int | None = None
                    confirmadas: int | None = None
                    if avaliacao.tem_ordinal:
                        # O lock vale ate o commit: a segunda transacao da mesma regra espera aqui e,
                        # em READ COMMITTED, o SELECT seguinte ja ve a linha que a primeira gravou.
                        await conn.execute(
                            _LOCK_SQL,
                            _chave_lock(self._tenant, avaliacao.id_regra, avaliacao.versao_regra),
                        )
                        ordinal = int(
                            await conn.fetchval(
                                _ORDINAL_SQL, self._tenant, avaliacao.id_regra, avaliacao.versao_regra
                            )
                        )
                        if _usa_janela_de_conferencia(avaliacao):
                            # Sob o mesmo lock que `registrar_desfecho` toma: um `nao` ja gravado conta.
                            confirmadas = int(
                                await conn.fetchval(
                                    _CONFIRMADAS_SQL,
                                    self._tenant,
                                    avaliacao.id_regra,
                                    avaliacao.versao_regra,
                                )
                            )
                    linha = montar_linha(avaliacao, contexto, ordinal, conferencias_confirmadas=confirmadas)
                    gravado = await conn.fetchval(_INSERT_SQL, *linha.params())
            finally:
                await _fechar(conn)
        except PROGRAMMING_ERRORS:
            raise  # bug do proprio codigo: sobe com a pilha (o chamador registra e segue)
        except Exception as exc:
            # So o TIPO: a mensagem do asyncpg pode trazer "Failing row contains ..." com a linha.
            raise RegistroRcoIndisponivelError(
                f"rco_decisao_regra indisponivel: {type(exc).__name__}"
            ) from None
        return gravado is not None

    async def _em_transacao(self, corpo: Callable[[Any], Awaitable[Any]]) -> Any:
        """Uma conexao, o schema do tenant, uma transacao com os tetos do registro; erro vira tipo so."""
        try:
            conn = await self._conectar()
            try:
                await conn.execute(f'SET search_path TO "{self._schema}", public')
                async with conn.transaction(isolation="read_committed"):
                    await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                    await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    return await corpo(conn)
            finally:
                await _fechar(conn)
        except PROGRAMMING_ERRORS:
            raise  # bug do proprio codigo: sobe com a pilha
        except DesfechoRecusadoError:
            raise  # recusa de contrato: o codigo ja diz o motivo, nada foi escrito
        except Exception as exc:
            raise RegistroRcoIndisponivelError(
                f"rco_decisao_regra indisponivel: {type(exc).__name__}"
            ) from None

    async def contar_conferencias_confirmadas(self, id_regra: str, versao_regra: str) -> int:
        """A contagem da janela da `conferida` (DL-0182) de uma regra deste tenant, como `registrar` a le."""

        async def corpo(conn: Any) -> int:
            return int(await conn.fetchval(_CONFIRMADAS_SQL, self._tenant, id_regra, versao_regra))

        return int(await self._em_transacao(corpo))

    async def registrar_desfecho(
        self, rco_decisao_regra_id: int, clique: str, *, desfecho_em: datetime | None
    ) -> ResultadoDesfecho:
        """Grava o desfecho humano de UMA conferencia (DL-0182). APPEND-ONLY: so INSERT.

        Contrato: `clique` em `CLIQUES_DA_CONFERENCIA`; `desfecho_em` com fuso em `confirmar`/`rejeitar`
        e `None` em `sem_desfecho`. Recusa (`DesfechoRecusadoError`, nada escrito): clique ou instante
        invalido, decisao de outro tenant ou inexistente, decisao cuja `acao` nao e `conferencia`,
        `sem_desfecho` sobre conferencia ja decidida. Idempotente: se o ultimo desfecho da decisao ja e
        o pedido, devolve `gravado=False`. Le o ultimo desfecho e grava sob o lock da REGRA (o mesmo de
        `registrar`), entao duas re-tentativas simultaneas gravam uma vez e a decisao seguinte da regra ja
        ve o desfecho. Um `nao` novo zera a janela e alerta a coordenacao depois do commit.
        """
        concordou = concordancia_do_clique(clique)
        validar_desfecho_em(concordou, desfecho_em)
        if isinstance(rco_decisao_regra_id, bool) or not isinstance(rco_decisao_regra_id, int):
            raise DesfechoRecusadoError("id_decisao_invalido")
        if rco_decisao_regra_id <= 0:
            raise DesfechoRecusadoError("id_decisao_invalido")

        async def corpo(conn: Any) -> tuple[ResultadoDesfecho, Any]:
            decisao = await conn.fetchrow(_DECISAO_SQL, rco_decisao_regra_id, self._tenant)
            if decisao is None:
                raise DesfechoRecusadoError("decisao_inexistente")
            if decisao["acao"] != "conferencia":
                raise DesfechoRecusadoError("acao_nao_e_conferencia")
            await conn.execute(
                _LOCK_SQL, _chave_lock(self._tenant, decisao["id_regra"], decisao["versao_regra"])
            )
            ultimo = await conn.fetchval(_ULTIMO_DESFECHO_SQL, rco_decisao_regra_id)
            if not decidir_desfecho(acao=decisao["acao"], concordou=concordou, ultimo=ultimo):
                return ResultadoDesfecho(gravado=False, concordou=concordou), decisao
            id_desfecho = await conn.fetchval(
                _INSERT_DESFECHO_SQL, rco_decisao_regra_id, concordou, desfecho_em
            )
            reinicio = concordou == ativ.CONCORDOU_NAO
            return (
                ResultadoDesfecho(
                    gravado=True,
                    concordou=concordou,
                    id_desfecho=int(id_desfecho),
                    janela_reiniciada=reinicio,
                ),
                decisao,
            )

        resultado: ResultadoDesfecho
        resultado, decisao = await self._em_transacao(corpo)
        if resultado.janela_reiniciada:
            _alertar_rejeicao(
                tenant=self._tenant,
                id_regra=decisao["id_regra"],
                versao_regra=decisao["versao_regra"],
                decisao_id=rco_decisao_regra_id,
                ativacao=decisao["ativacao_efetiva"],
            )
        return resultado


__all__ = [
    "CLIQUES_DA_CONFERENCIA",
    "COLUNAS",
    "CONCORDOU",
    "EVENTO_CONFERENCIA_REJEITADA",
    "RECUSAS_DESFECHO",
    "MOTIVOS_AMOSTRA",
    "VARIAVEL_CHAVE_AMOSTRA",
    "VARIAVEL_ID_CHAVE_AMOSTRA",
    "AvaliacaoRegra",
    "ContextoDecisao",
    "DesfechoRecusadoError",
    "LinhaDecisao",
    "PostgresRegistroDecisoesRco",
    "RegistroDecisoesRco",
    "RegistroRcoIndisponivelError",
    "ResultadoDesfecho",
    "avaliar_regra",
    "chave_amostra_do_ambiente",
    "chave_idempotencia",
    "concordancia_do_clique",
    "contexto_da_tarefa",
    "decidir_desfecho",
    "montar_linha",
    "validar_desfecho_em",
]
