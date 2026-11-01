"""Reconciliador deterministico: terminais de SP-OP-CRED-001/SP-OP-FRAUDE-001 -> ledger (MPTT P3).

**Fonte unica do historico.** SO' a costura `resolve_historic_instance` do PR #1167
(`HistoricInstanceResolvingTransport`, `tools/mcp_cibseven/transport.py`), recebida PRONTA e
gated (`gate_cibseven` via `tool_registry.build_cibseven_seam`). Este modulo nao constroi
transporte, nao abre cliente HTTP e importa da costura so' Protocol/valor/erros — a cerca
`check_effect_chokepoint_fence.py` §8.1-§8.3 continua valendo.

**Dois modos, mesma projecao.**
* Por EVENTO: o chamador conhece a business key (`CRED-{tenant}-{prestador}[-{protocolo}]`,
  `FRAUDE-{tenant}-{numero_caso}`) — `reconciliar_cred_por_chave` / `reconciliar_fraude_por_chave`.
* NOTURNO: `varrer` percorre os prestadores que o ledger ja conhece e resolve, por filtro de
  variavel `prestador_id`, a instancia CRED e a FRAUDE de cada um. A costura do #1167 prova
  UMA instancia (pede duas linhas): prestador com mais de um ciclo CRED ou mais de um caso FRAUDE
  da' `HistoricInstanceAmbiguousError` -> `INDISPONIVEL` (limite declarado: a listagem entre
  instancias e' a lacuna "Cross-instance query" da analise §1, fora deste pacote).

**Falha fechada.** Qualquer leitura que nao prove UM terminal coerente — motor fora, timeout,
resposta contraditoria, ambiguidade, terminal fora do contrato v1, data malformada, chave de outro
tenant, ledger fora do ar — devolve `INDISPONIVEL` e NAO grava nada. So'
`HistoricInstanceNotFoundError` (capturado pelo nome) significa "nao ha instancia". Nunca um
status adivinhado.

**O que nao faz.** Nao decide credenciamento, descredenciamento nem fraude (L0/L1 — a decisao ja
foi de um humano numa User Task). Nao grava "em curso"/"aberta" (leitura viva do P6). Nao le
variavel PHI (a costura recusa antes da rede; os nomes pedidos aqui sao cadastrais).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

import structlog

from maezo.platform.prestador_status.ledger import (
    PRESTADOR_ID_RE,
    EntradaLedger,
    LedgerConflitoError,
    LedgerIndisponivelError,
    LedgerStatusPrestador,
)
from maezo.platform.prestador_status.projecao import (
    ESTADO_CONCLUIDO,
    ESTADOS_EM_CURSO,
    ESTADOS_ENCERRADOS_SEM_TERMINAL,
    PROCESSO_CRED,
    PROCESSO_FRAUDE,
    VERSAO_PROJECAO,
    ClasseTerminal,
    Dimensao,
    Origem,
    projetar,
)
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    HistoricInstanceNotFoundError,
    HistoricInstanceRecord,
    HistoricInstanceResolvingTransport,
)

logger = structlog.get_logger(__name__)

#: Variaveis lidas do historico — so' nomes CADASTRAIS (nenhum em PHI_PROCESS_VARS; a costura
#: recusaria antes da rede). `data_efeito_iso`: SP-OP-CRED-001 §"Variaveis de saida".
NOMES_CRED: Final = ("prestador_id", "data_efeito_iso", "tipo_prestador", "regiao_saude", "especialidade")
#: SP-OP-FRAUDE-001 §"Variaveis de entrada": o caso so' fala do prestador quando
#: `entidade_tipo == 'prestador'` e `prestador_id` esta presente.
NOMES_FRAUDE: Final = ("prestador_id", "entidade_tipo")
ENTIDADE_PRESTADOR: Final = "prestador"


class Resultado(StrEnum):
    GRAVADO = "GRAVADO"
    JA_REGISTRADO = "JA_REGISTRADO"
    SEM_DELTA = "SEM_DELTA"  # terminou num terminal que nao muda status, ou cancelada
    EM_CURSO = "EM_CURSO"  # instancia viva: nada a projetar ainda
    NAO_ENCONTRADO = "NAO_ENCONTRADO"  # o motor respondeu, bem-formado, que nao ha instancia
    FORA_DE_ESCOPO = "FORA_DE_ESCOPO"  # caso FRAUDE que nao referencia prestador
    INDISPONIVEL = "INDISPONIVEL"  # falha fechada: nada gravado, nada concluido


@dataclass(frozen=True, slots=True)
class ResultadoReconciliacao:
    origem: Origem
    seletor: str
    resultado: Resultado
    motivo: str | None = None
    entrada: EntradaLedger | None = None


def _texto(valor: Any) -> str | None:
    return valor if isinstance(valor, str) and valor.strip() else None


class _LeituraRecusadaError(Exception):
    """Interno: leitura recusada; vira `Resultado.INDISPONIVEL` com o motivo."""


class ReconciliadorStatusPrestador:
    """Projeta terminais do motor no ledger. Uma instancia por tenant (o do transporte)."""

    __slots__ = ("_ledger", "_processo", "_tenant", "_transport")

    def __init__(
        self,
        *,
        transport: HistoricInstanceResolvingTransport,
        ledger: LedgerStatusPrestador,
        tenant: str,
        processos: Mapping[Origem, str] | None = None,
    ) -> None:
        if not isinstance(transport, HistoricInstanceResolvingTransport):
            # Sem a costura do #1167 nao ha leitura honesta: recusa na construcao.
            raise TypeError("reconciliador exige um HistoricInstanceResolvingTransport (PR #1167)")
        self._transport = transport
        self._ledger = ledger
        self._tenant = tenant
        # `processos` so' existe para o teste de integracao apontar uma definicao ad hoc do motor;
        # em producao sao as chaves de SP-OP-CRED-001/SP-OP-FRAUDE-001.
        self._processo: dict[Origem, str] = {Origem.CRED: PROCESSO_CRED, Origem.FRAUDE: PROCESSO_FRAUDE}
        if processos is not None:
            self._processo.update(processos)

    # -- modo EVENTO -------------------------------------------------------------------------

    async def reconciliar_cred_por_chave(self, business_key: str) -> ResultadoReconciliacao:
        self._exige_chave(Origem.CRED, business_key)
        return await self._reconciliar(Origem.CRED, business_key, business_key=business_key, filtro=None)

    async def reconciliar_fraude_por_chave(self, business_key: str) -> ResultadoReconciliacao:
        self._exige_chave(Origem.FRAUDE, business_key)
        return await self._reconciliar(Origem.FRAUDE, business_key, business_key=business_key, filtro=None)

    # -- modo NOTURNO ------------------------------------------------------------------------

    async def reconciliar_prestador(self, prestador_id: str) -> tuple[ResultadoReconciliacao, ...]:
        if not PRESTADOR_ID_RE.fullmatch(prestador_id):
            raise ValueError("reconciliar_prestador: prestador_id fora do formato")
        filtro = {"prestador_id": prestador_id}
        return (
            await self._reconciliar(Origem.CRED, prestador_id, business_key="", filtro=filtro),
            await self._reconciliar(Origem.FRAUDE, prestador_id, business_key="", filtro=filtro),
        )

    async def varrer(self) -> tuple[ResultadoReconciliacao, ...]:
        """Varredura noturna sobre o universo que o ledger conhece. Ledger fora do ar = um unico
        `INDISPONIVEL` (sem universo nao ha varredura honesta)."""
        try:
            universo = await self._ledger.prestadores(self._tenant)
        except LedgerIndisponivelError as exc:
            logger.error("prestador_status_varredura_indisponivel", tenant=self._tenant, motivo=str(exc))
            return (ResultadoReconciliacao(Origem.CRED, "*", Resultado.INDISPONIVEL, "ledger_indisponivel"),)
        resultados: list[ResultadoReconciliacao] = []
        for prestador_id in universo:
            resultados.extend(await self.reconciliar_prestador(prestador_id))
        return tuple(resultados)

    # -- nucleo ------------------------------------------------------------------------------

    def _exige_chave(self, origem: Origem, business_key: str) -> None:
        prefixo = f"{origem.value}-{self._tenant}-"
        if not isinstance(business_key, str) or not business_key.startswith(prefixo):
            raise ValueError(f"business key de {origem.value} tem de comecar com {prefixo!r}")

    async def _reconciliar(
        self,
        origem: Origem,
        seletor: str,
        *,
        business_key: str,
        filtro: Mapping[str, str] | None,
    ) -> ResultadoReconciliacao:
        nomes = NOMES_CRED if origem is Origem.CRED else NOMES_FRAUDE
        try:
            registro = await self._transport.resolve_historic_instance(
                self._processo[origem], business_key=business_key, variable_equals=filtro, names=nomes
            )
        except HistoricInstanceNotFoundError:
            return ResultadoReconciliacao(origem, seletor, Resultado.NAO_ENCONTRADO)
        except CibSevenError as exc:
            return self._indisponivel(origem, seletor, f"historico:{type(exc).__name__}")
        try:
            return await self._projetar(origem, seletor, registro)
        except _LeituraRecusadaError as exc:
            return self._indisponivel(origem, seletor, str(exc))

    def _indisponivel(self, origem: Origem, seletor: str, motivo: str) -> ResultadoReconciliacao:
        logger.warning(
            "prestador_status_ledger_indisponivel",
            tenant=self._tenant,
            origem=origem.value,
            seletor=seletor,
            motivo=motivo,
        )
        return ResultadoReconciliacao(origem, seletor, Resultado.INDISPONIVEL, motivo)

    async def _projetar(
        self, origem: Origem, seletor: str, registro: HistoricInstanceRecord
    ) -> ResultadoReconciliacao:
        prefixo = f"{origem.value}-{self._tenant}-"
        if not registro.business_key.startswith(prefixo):
            raise _LeituraRecusadaError("chave_de_outro_tenant_ou_processo")
        estado = registro.state.strip().upper()
        if estado in ESTADOS_EM_CURSO:
            return ResultadoReconciliacao(origem, seletor, Resultado.EM_CURSO)
        if estado in ESTADOS_ENCERRADOS_SEM_TERMINAL:
            return ResultadoReconciliacao(origem, seletor, Resultado.SEM_DELTA, "encerrada_sem_terminal")
        if estado != ESTADO_CONCLUIDO:
            raise _LeituraRecusadaError(f"estado_desconhecido:{estado[:32]}")
        if registro.end_activity_id is None:
            raise _LeituraRecusadaError("concluida_sem_evento_final")
        projecao = projetar(origem, registro.end_activity_id)
        if projecao is None:
            raise _LeituraRecusadaError("terminal_fora_do_contrato_v1")
        if projecao.classe is ClasseTerminal.NAO_TERMINAL:
            raise _LeituraRecusadaError("evento_final_nao_terminal")

        variaveis = registro.variables
        if origem is Origem.FRAUDE and variaveis.get("entidade_tipo") != ENTIDADE_PRESTADOR:
            return ResultadoReconciliacao(origem, seletor, Resultado.FORA_DE_ESCOPO)
        prestador_id = variaveis.get("prestador_id")
        if not isinstance(prestador_id, str) or not PRESTADOR_ID_RE.fullmatch(prestador_id):
            raise _LeituraRecusadaError("prestador_id_ausente_ou_malformado")
        if projecao.classe is ClasseTerminal.SEM_DELTA:
            return ResultadoReconciliacao(origem, seletor, Resultado.SEM_DELTA, registro.end_activity_id)
        if projecao.dimensao is None or projecao.valor is None:  # pragma: no cover - Projecao garante
            raise _LeituraRecusadaError("projecao_incompleta")

        entrada = self._entrada(origem, registro, prestador_id, projecao.dimensao, projecao.valor)
        try:
            gravou = await self._ledger.registrar(entrada)
        except (LedgerIndisponivelError, LedgerConflitoError) as exc:
            raise _LeituraRecusadaError(f"ledger:{type(exc).__name__}") from exc
        resultado = Resultado.GRAVADO if gravou else Resultado.JA_REGISTRADO
        logger.info(
            "prestador_status_ledger_delta",
            tenant=self._tenant,
            origem=origem.value,
            prestador_id=prestador_id,
            valor=entrada.valor,
            resultado=resultado.value,
        )
        return ResultadoReconciliacao(origem, seletor, resultado, entrada=entrada)

    def _entrada(
        self,
        origem: Origem,
        registro: HistoricInstanceRecord,
        prestador_id: str,
        dimensao: Dimensao,
        valor: str,
    ) -> EntradaLedger:
        variaveis = registro.variables
        data_efeito: date | None = None
        if origem is Origem.CRED and variaveis.get("data_efeito_iso") is not None:
            bruta = variaveis["data_efeito_iso"]
            if not isinstance(bruta, str) or len(bruta) != 10:
                raise _LeituraRecusadaError("data_efeito_iso_malformada")
            try:
                data_efeito = date.fromisoformat(bruta)
            except ValueError:
                raise _LeituraRecusadaError("data_efeito_iso_malformada") from None
        cadastrais: dict[str, str | None] = {
            "tipo_prestador": None,
            "especialidade": None,
            "regiao_saude": None,
        }
        if origem is Origem.CRED:
            for nome in cadastrais:
                cadastrais[nome] = _texto(variaveis.get(nome))
        try:
            return EntradaLedger(
                tenant=self._tenant,
                prestador_id=prestador_id,
                origem=origem,
                dimensao=dimensao,
                valor=valor,
                data_efeito=data_efeito,
                chave_evento=f"{origem.value}:{registro.instance_id}:{registro.end_activity_id}",
                fonte_ref=registro.instance_id,
                evidencia_ref=registro.business_key,
                versao_projecao=VERSAO_PROJECAO,
                **cadastrais,
            )
        except ValueError as exc:
            # Um atributo do historico fora do formato do ledger: nada e' gravado pela metade.
            raise _LeituraRecusadaError(f"entrada_invalida:{exc}") from None


__all__ = [
    "NOMES_CRED",
    "NOMES_FRAUDE",
    "ReconciliadorStatusPrestador",
    "Resultado",
    "ResultadoReconciliacao",
]
