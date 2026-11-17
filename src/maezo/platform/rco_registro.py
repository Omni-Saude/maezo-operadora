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

Ordinal. Conta so as decisoes que a revisao inicial e a conferencia inicial miram (ADR-0077 §3): as
adversas COERENTES COM A CLASSE (`glosar` com `contas`/`administrativo`; `negar` com `administrativo`;
`rco_ativacao.CLASSES_COM_EFEITO`). Contar tambem `liberar` consumiria a janela de 30 antes de qualquer
glosa; contar uma combinacao incoerente (ex.: `negar` clinico, que vai ao humano) consumiria a janela
com caso que nunca chega a efeito. Qualquer outra decisao: ordinal nulo, nunca amostrada.
ATENCAO (5d/5e): a linha perdida no caminho FAIL-OPEN do harness desloca os ordinais seguintes (o MAX+1
nao ve a linha que nao entrou); antes de ligar qualquer efeito a janela inicial precisa de outra fonte.

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
from typing import Any, Final, Protocol, runtime_checkable

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform import rco_ativacao as ativ
from maezo.platform.rco_manifesto import ParametrosAtivacao, RegraK1
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

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


def montar_linha(av: AvaliacaoRegra, ctx: ContextoDecisao, ordinal: int | None) -> LinhaDecisao:
    """Funcao pura. `ordinal` e `None` para decisao sem amostragem; para `negar`/`glosar` e o 1-based
    por (idRegra, versaoRegra) dado por quem grava, sob lock.

    Amostragem (§4): so `negar`/`glosar`. As primeiras `revisao_inicial_decisoes` sao `inicial`; depois,
    HMAC com a chave do ambiente (`aleatoria`), ou `chave_ausente` sem chave (toda adversa amostrada).
    A acao e a de `filtrar_saida` (em sombra, `registrar_apenas`); `conferencia_inicial` e o caso entre as
    primeiras `conferencia_negativa_admin_decisoes`.
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
        conferencia_inicial=ordinal is not None and ordinal <= av.conferencia_negativa_admin_decisoes,
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
    return f"rco-ordinal|{tenant}|{id_regra}|{versao_regra}"


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
                    linha = montar_linha(avaliacao, contexto, ordinal)
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


__all__ = [
    "COLUNAS",
    "CONCORDOU",
    "MOTIVOS_AMOSTRA",
    "VARIAVEL_CHAVE_AMOSTRA",
    "VARIAVEL_ID_CHAVE_AMOSTRA",
    "AvaliacaoRegra",
    "ContextoDecisao",
    "LinhaDecisao",
    "PostgresRegistroDecisoesRco",
    "RegistroDecisoesRco",
    "RegistroRcoIndisponivelError",
    "avaliar_regra",
    "chave_amostra_do_ambiente",
    "chave_idempotencia",
    "contexto_da_tarefa",
    "montar_linha",
]
