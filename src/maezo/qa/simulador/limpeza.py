"""Limpeza do que as conversas simuladas deixam no dev, e a conferencia de que o motor ficou limpo.

ENTRE CONVERSAS no mesmo telefone sintetico (`reiniciar_conversa`): apaga o thread de checkpoint da
Helena (historico curto, memoria clinica, coleta), a linha do roteador (`conversa_agente_ativo`,
que guarda o assunto e o mes da ultima resposta do Lucas), o cache de identidade da Helena e os
casos simulados da conversa anterior. A linha do ACESSO fica: a pessoa ja' foi verificada nesta
execucao (a validade e' de horas), e verificar de novo a cada conversa so' gastaria chamadas da AMH.

NO FIM (`limpar_execucao`, no `finally` do `__main__` e no `--limpar <run_id>`): o mesmo, mais as
linhas do acesso (`conversa_acesso_beneficiario`) de todas as conversas da execucao. Depois
`conferir_motor` pergunta ao motor REAL se existe algum caso ESC dessas conversas (ativo ou no
historico): tem de ser zero, porque o interceptador nunca deixou um start passar.

So' `conversation_id` keyed (`wa:{tenant}:hk1_...`) entra aqui. Nenhum telefone, CPF ou texto.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.runtime.caso_clinico import chave_do_escalonamento

logger = structlog.get_logger(__name__)

TABELA_ROTEADOR = "conversa_agente_ativo"
TABELA_ACESSO = "conversa_acesso_beneficiario"


@dataclass
class ResultadoDaLimpeza:
    conversas: int = 0
    checkpoints_apagados: int = 0
    linhas_roteador: int = 0
    linhas_acesso: int = 0
    casos_no_motor: int = 0
    erros: list[str] = field(default_factory=list)

    @property
    def motor_limpo(self) -> bool:
        return self.casos_no_motor == 0 and not any(e.startswith("motor:") for e in self.erros)

    def como_dict(self) -> dict[str, Any]:
        return {
            "conversas": self.conversas,
            "checkpoints_apagados": self.checkpoints_apagados,
            "linhas_roteador": self.linhas_roteador,
            "linhas_acesso": self.linhas_acesso,
            "casos_no_motor": self.casos_no_motor,
            "motor_limpo": self.motor_limpo,
            "erros": list(self.erros),
        }


def _linhas_afetadas(status: Any) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


class Limpeza:
    def __init__(
        self,
        *,
        dsn: str,
        tenant_id: str,
        saver: Any,
        identidade: Any = None,
        escalonamentos: Any = None,
        motor_real: Any = None,
        pool: Any = None,
    ) -> None:
        self._dsn = normalize_dsn(dsn)
        self._tenant = tenant_id
        self._schema = schema_for_tenant(tenant_id)
        self._prefixo = f"wa:{tenant_id}:"
        self._saver = saver
        self._identidade = identidade
        self._escalonamentos = escalonamentos
        self._motor = motor_real
        self._pool = pool

    def _conferir(self, conversation_id: str) -> None:
        if not conversation_id.startswith(self._prefixo) or ":hk1_" not in conversation_id:
            raise ValueError("limpeza: conversation_id fora do formato keyed deste tenant")

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2, setup=self._search_path)
        return self._pool

    async def _search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def _apagar(self, tabela: str, conversation_ids: Sequence[str]) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                f"DELETE FROM {tabela} WHERE tenant = $1 AND conversation_id = ANY($2::text[])",
                self._tenant,
                list(conversation_ids),
            )
        return _linhas_afetadas(status)

    async def _apagar_checkpoint(self, conversation_id: str) -> bool:
        if self._saver is None:
            return False
        await self._saver.adelete_thread(conversation_id)
        return True

    async def reiniciar_conversa(self, conversation_id: str) -> None:
        """Entre duas conversas no mesmo telefone. Levanta se nao conseguir: conversa nova sobre
        memoria velha invalidaria a medicao."""
        self._conferir(conversation_id)
        await self._apagar_checkpoint(conversation_id)
        await self._apagar(TABELA_ROTEADOR, [conversation_id])
        if self._identidade is not None:
            self._identidade.invalidar(conversation_id)
        if self._escalonamentos is not None:
            self._escalonamentos.esquecer(self._tenant, conversation_id)

    async def apagar_acesso(self, conversation_id: str) -> int:
        """Antes de uma conversa do FLUXO DE ACESSO: a maquina recomeca do zero (sem linha)."""
        self._conferir(conversation_id)
        return await self._apagar(TABELA_ACESSO, [conversation_id])

    async def limpar_execucao(self, conversation_ids: Sequence[str]) -> ResultadoDaLimpeza:
        resultado = ResultadoDaLimpeza(conversas=len(conversation_ids))
        ids = sorted(set(conversation_ids))
        for cid in ids:
            self._conferir(cid)
        for cid in ids:
            try:
                if await self._apagar_checkpoint(cid):
                    resultado.checkpoints_apagados += 1
            except Exception as exc:
                resultado.erros.append(f"checkpoint:{type(exc).__name__}")
            if self._identidade is not None:
                self._identidade.invalidar(cid)
        for tabela, campo in ((TABELA_ROTEADOR, "linhas_roteador"), (TABELA_ACESSO, "linhas_acesso")):
            try:
                setattr(resultado, campo, await self._apagar(tabela, ids) if ids else 0)
            except Exception as exc:
                resultado.erros.append(f"{tabela}:{type(exc).__name__}")
        await self.conferir_motor(ids, resultado)
        logger.info("simulador_limpeza", **resultado.como_dict())
        return resultado

    async def conferir_motor(self, conversation_ids: Sequence[str], resultado: ResultadoDaLimpeza) -> None:
        """Zero caso ESC destas conversas no motor REAL (ativo ou no historico)."""
        if self._motor is None:
            resultado.erros.append("motor:sem_transporte")
            return
        procurar = getattr(self._motor, "find_any_instance", None) or self._motor.find_active_instance
        for cid in conversation_ids:
            for clinico in (False, True):
                chave = chave_do_escalonamento(self._tenant, cid, clinico=clinico)
                try:
                    if await procurar(chave) is not None:
                        resultado.casos_no_motor += 1
                except Exception as exc:
                    resultado.erros.append(f"motor:{type(exc).__name__}")

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


__all__ = ["TABELA_ACESSO", "TABELA_ROTEADOR", "Limpeza", "ResultadoDaLimpeza"]
