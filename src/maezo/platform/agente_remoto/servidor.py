"""O servico do agente de ENTRADA (ADR-0067 fase 4b): executa, em processo, as operacoes da porta que o
receptor pede por HTTP interno.

Na partida compoe as dependencias DO MESMO JEITO que o receptor (`webhooks/service.py::
build_agent_dependencies`, a mesma composicao, sem a ponte do acesso, o Lucas e o acesso) e carrega o agente
pelo framework (`maezo.agents.carregar_agente_de_entrada(AGENT_ID)`). O `HelenaDispatcher` que a
composicao devolve e' so' o PORTADOR das dependencias: daqui ele so' monta o `config` do grafo
(`_compile_turn_graph`) e o dos nos (`_dependencias_do_agente`), exatamente as funcoes que o receptor
usaria em processo — nenhuma lista de chaves duplicada. O remetente do turno e' um `RemetenteRemoto` que
volta a REPLICA do receptor que abriu o turno (o `retorno` do pedido) com o token. O checkpointer e' o
MESMO Postgres (mesmo
DSN), entao o thread da conversa e' o mesmo.

Toda rota de operacao exige o corpo assinado (`X-Maezo-Assinatura`); sem assinatura valida = 401 sem corpo. A
resposta tambem vai assinada. Uma excecao da operacao volta so' com o NOME do tipo (500), e o log leva so' o
tipo — nunca texto, hash, numero ou estado.

Defesa em profundidade: o thread do checkpoint tem de ser deste tenant (`wa:{tenant}:...`, e passa de novo
por `checkpoint_thread_config`, que recusa id sem pseudonimo keyed), e um `tenant_id` no estado tem de ser o
deste servico.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import FastAPI, Request, Response

from maezo.contratos import conversa_remota as fio
from maezo.contratos.conversa import AgenteDeEntrada
from maezo.platform.health import CheckResult, create_health_app
from maezo.runtime.checkpoint import checkpoint_thread_config

from .cliente import RemetenteRemoto, corpo_de_erro
from .rota_de_envio import resposta_assinada

if TYPE_CHECKING:
    from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher

logger = structlog.get_logger(__name__)

Operacao = Callable[[dict[str, Any]], Awaitable[Any]]


class _PrecisaIdentidade(Exception):  # noqa: N818 — sinal interno de controle de fluxo, nao um erro
    """O agente quis o perfil da AMH e o pedido ainda nao o trouxe: o cliente resolve e chama de novo."""


@dataclass
class ServicoDoAgente:
    """O agente carregado + o portador das dependencias + o caminho de volta ao receptor."""

    entrada: AgenteDeEntrada
    dependencias: HelenaDispatcher
    chave: bytes
    receptor: httpx.AsyncClient

    @property
    def tenant_id(self) -> str:
        return self.dependencias.tenant_id

    # --- montagem por pedido ------------------------------------------------------------------------------
    def _remetente(self, pedido: Mapping[str, Any]) -> RemetenteRemoto | None:
        token = pedido.get("token")
        if token is None:
            return None
        if not isinstance(token, str) or not token:
            raise ValueError("token de remetente invalido")
        retorno = pedido.get("retorno")
        if not fio.retorno_valido(retorno):
            raise ValueError("endereco de retorno fora do formato")
        # O envio volta a REPLICA que abriu o turno (o token so' existe nela), nunca ao nome do servico.
        return RemetenteRemoto(http=self.receptor, chave=self.chave, token=token, retorno=str(retorno))

    @staticmethod
    def _flag_do_lucas(pedido: Mapping[str, Any]) -> bool:
        return pedido.get("roteador_lucas_enabled") is True

    def _compilar(self, pedido: Mapping[str, Any]) -> Any:
        compiled, saver = self.dependencias._compile_turn_graph(
            self._remetente(pedido), roteador_lucas_enabled=self._flag_do_lucas(pedido)
        )
        # O receptor e o servico tem de concordar sobre a persistencia: um lado com checkpointer e o outro sem
        # e' erro de implantacao, e seguir assim perderia (ou inventaria) a memoria da conversa.
        if (pedido.get("persistente") is True) != (saver is not None):
            raise RuntimeError(
                "agente remoto: persistencia divergente entre o receptor e o servico do agente"
            )
        return compiled

    def _config_de_thread(self, config: Any) -> Any:
        if config is None:
            return None
        configuravel = config.get("configurable") if isinstance(config, dict) else None
        thread_id = configuravel.get("thread_id") if isinstance(configuravel, dict) else None
        if not isinstance(thread_id, str) or not thread_id.startswith(f"wa:{self.tenant_id}:"):
            raise PermissionError("agente remoto: thread de outro tenant (ou fora do formato)")
        ns = configuravel.get("checkpoint_ns", "") if isinstance(configuravel, dict) else ""
        return checkpoint_thread_config(thread_id, checkpoint_ns=ns if isinstance(ns, str) else "")

    def _conferir_tenant(self, estado: Mapping[str, Any]) -> None:
        """Todo estado (e todo estado adiado com campos) tem de DIZER o tenant, e ele tem de ser este."""
        if estado.get("tenant_id") != self.tenant_id:
            raise PermissionError("agente remoto: estado sem tenant_id ou de outro tenant")

    def _materializar(self, estado: Any) -> dict[str, Any]:
        """O estado do pedido; um marcador ADIADO e' montado aqui, pela logica do agente."""
        if not isinstance(estado, dict):
            raise ValueError("estado fora do protocolo")
        tipo = estado.get(fio.ESTADO_ADIADO)
        if tipo is None:
            self._conferir_tenant(estado)
            return estado
        campos = estado.get("campos")
        if not isinstance(campos, dict):
            raise ValueError("estado adiado fora do protocolo")
        if tipo == fio.ADIADO_INICIAL:
            self._conferir_tenant(campos)
            return self.entrada.estado_inicial(**campos)
        if tipo == fio.ADIADO_RETOMADA:
            self._conferir_tenant(campos)
            return self.entrada.estado_de_retomada(**campos)
        if tipo == fio.ADIADO_MEMORIA_NEUTRA:
            return self.entrada.memoria_neutra()
        raise ValueError("estado adiado desconhecido")

    # --- as operacoes da porta ----------------------------------------------------------------------------
    async def invocar(self, pedido: dict[str, Any]) -> Any:
        compiled = self._compilar(pedido)
        bruto = pedido.get("estado")
        estado = self._materializar(bruto)
        resultado = await compiled.ainvoke(estado, self._config_de_thread(pedido.get("config")))
        # O despachante pergunta, logo depois, se ESTE texto pede uma pessoa (porta sincrona): a resposta vai
        # junto, para o cliente nao precisar de uma chamada sincrona.
        pedido_de_pessoa: bool | None = None
        if isinstance(bruto, dict) and bruto.get(fio.ESTADO_ADIADO) == fio.ADIADO_INICIAL:
            texto = bruto["campos"].get("message_body")
            if isinstance(texto, str):
                pedido_de_pessoa = bool(self.entrada.eh_pedido_explicito_de_pessoa(texto))
        return {"resultado": dict(resultado), "pedido_de_pessoa": pedido_de_pessoa}

    async def ler_estado(self, pedido: dict[str, Any]) -> Any:
        compiled = self._compilar(pedido)
        retrato = await compiled.aget_state(self._config_de_thread(pedido.get("config")))
        valores = getattr(retrato, "values", None)
        return {
            "values": dict(valores) if isinstance(valores, Mapping) else {},
            "next": [str(n) for n in (getattr(retrato, "next", None) or ())],
        }

    async def atualizar_estado(self, pedido: dict[str, Any]) -> Any:
        compiled = self._compilar(pedido)
        as_node = pedido.get("as_node")
        resultado = await compiled.aupdate_state(
            self._config_de_thread(pedido.get("config")),
            self._materializar(pedido.get("values")),
            as_node=as_node if isinstance(as_node, str) else None,
        )
        return dict(resultado) if isinstance(resultado, Mapping) else None

    def _dependencias_dos_nos(self, pedido: Mapping[str, Any]) -> dict[str, Any]:
        return self.dependencias._dependencias_do_agente(
            self._remetente(pedido), roteador_lucas_enabled=self._flag_do_lucas(pedido)
        )

    async def escalar_identidade(self, pedido: dict[str, Any]) -> Any:
        motivo = pedido.get("motivo")
        if not isinstance(motivo, str):
            raise ValueError("motivo fora do protocolo")
        estado = self._materializar(pedido.get("estado"))
        return dict(
            await self.entrada.escalar_identidade(self._dependencias_dos_nos(pedido), estado, motivo=motivo)
        )

    async def escalar_e_responder(self, pedido: dict[str, Any]) -> Any:
        estado = self._materializar(pedido.get("estado"))
        return dict(await self.entrada.escalar_e_responder(self._dependencias_dos_nos(pedido), estado))

    async def pedido_de_pessoa(self, pedido: dict[str, Any]) -> Any:
        texto = pedido.get("texto")
        if not isinstance(texto, str):
            raise ValueError("texto fora do protocolo")
        return bool(self.entrada.eh_pedido_explicito_de_pessoa(texto))

    async def resposta_de_cadastro(self, pedido: dict[str, Any]) -> Any:
        texto = pedido.get("texto")
        if not isinstance(texto, str):
            raise ValueError("texto fora do protocolo")
        emergencia = pedido.get("emergencia") is True
        resolvida = pedido.get("identidade_resolvida") is True
        identidade = pedido.get("identidade")

        async def _resolver() -> dict[str, Any] | None:
            if not resolvida:
                raise _PrecisaIdentidade
            return identidade if isinstance(identidade, dict) else None

        try:
            resposta = await self.entrada.resposta_de_cadastro(
                texto,
                consultas_ligadas=pedido.get("consultas_ligadas") is True,
                fonte_ligada=pedido.get("fonte_ligada") is True,
                sinal_de_emergencia=lambda _texto: emergencia,
                resolver_identidade=_resolver if pedido.get("identidade_disponivel") is True else None,
            )
        except _PrecisaIdentidade:
            return {"precisa_identidade": True}
        if resposta is None:
            return {"resposta": None}
        return {"resposta": {"tipo": resposta.tipo, "texto": resposta.texto, "com_dados": resposta.com_dados}}

    def operacoes(self) -> dict[str, Operacao]:
        return {
            fio.ROTA_INVOCAR: self.invocar,
            fio.ROTA_LER_ESTADO: self.ler_estado,
            fio.ROTA_ATUALIZAR_ESTADO: self.atualizar_estado,
            fio.ROTA_ESCALAR_IDENTIDADE: self.escalar_identidade,
            fio.ROTA_ESCALAR_E_RESPONDER: self.escalar_e_responder,
            fio.ROTA_PEDIDO_DE_PESSOA: self.pedido_de_pessoa,
            fio.ROTA_RESPOSTA_DE_CADASTRO: self.resposta_de_cadastro,
        }


def _montar_rota(
    app: FastAPI,
    rota: str,
    chave: bytes,
    operacao: Operacao,
    agent_id: str,
    nonces: fio.GuardaDeNonces,
    relogio: Callable[[], float],
) -> None:
    async def _tratar(request: Request) -> Response:
        corpo = await request.body()
        # Versao, assinatura (rota/carimbo/nonce/corpo), desvio de relogio e nonce inedito; senao 401 seco.
        assinatura = fio.conferir_pedido(chave, nonces, rota, request.headers, corpo, relogio=relogio)
        if assinatura is None:
            return Response(status_code=401)
        try:
            pedido = fio.decodificar(corpo)
        except ValueError:
            return Response(status_code=400)
        if not isinstance(pedido, dict):
            return Response(status_code=400)
        try:
            resultado = await operacao(pedido)
            # Codificado DENTRO do try: um tipo sem representacao no fio vira erro do protocolo, nao 500 cru.
            return resposta_assinada(chave, assinatura, {"resultado": resultado})
        except Exception as exc:  # so' o tipo atravessa; o despachante do receptor decide (externa ou bug).
            logger.warning(
                "agente_remoto_operacao_falhou", agent_id=agent_id, rota=rota, error_type=type(exc).__name__
            )
            return resposta_assinada(chave, assinatura, corpo_de_erro(exc), status_code=500)

    app.add_api_route(rota, _tratar, methods=["POST"], include_in_schema=False)


def criar_app(
    servico: ServicoDoAgente | None,
    *,
    chave: bytes,
    is_live: Callable[[], bool] | None = None,
    motivo_indisponivel: str | None = None,
    relogio: Callable[[], float] = time.time,
) -> FastAPI:
    """O app interno: `/healthz`, `/readyz`, `/metrics` e as rotas assinadas da porta.

    `servico=None` (a composicao falhou): o processo continua vivo, `/readyz` diz por que, e toda operacao
    responde 503 — a mesma recusa honesta do receptor sem despachante (501), nunca um turno inventado.
    """

    async def dependencias_prontas() -> CheckResult:
        if servico is None:
            return CheckResult(
                name="agente_de_entrada", healthy=False, detail=motivo_indisponivel or "sem dependencias"
            )
        return CheckResult(
            name="agente_de_entrada", healthy=True, detail=f"agent_id={servico.entrada.agent_id}"
        )

    app = create_health_app(readiness_checks=[dependencias_prontas], is_live=is_live)
    if servico is None:

        async def _indisponivel(request: Request) -> Response:
            return Response(status_code=503)

        for rota in (
            fio.ROTA_INVOCAR,
            fio.ROTA_LER_ESTADO,
            fio.ROTA_ATUALIZAR_ESTADO,
            fio.ROTA_ESCALAR_IDENTIDADE,
            fio.ROTA_ESCALAR_E_RESPONDER,
            fio.ROTA_PEDIDO_DE_PESSOA,
            fio.ROTA_RESPOSTA_DE_CADASTRO,
        ):
            app.add_api_route(rota, _indisponivel, methods=["POST"], include_in_schema=False)
        return app
    # Anti-replay: um so' conjunto de nonces vistos (60 s) para todas as rotas deste servico.
    nonces = fio.GuardaDeNonces(relogio=relogio)
    for rota, operacao in servico.operacoes().items():
        _montar_rota(app, rota, chave, operacao, servico.entrada.agent_id, nonces, relogio)
    return app


__all__ = ["ServicoDoAgente", "criar_app"]
