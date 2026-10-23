"""A rota INTERNA do receptor por onde o envio de um turno remoto volta: `POST /interno/agentes/envio`.

ADR-0067 fase 4b. O turno da Helena roda no servico dela, mas o numero cru do beneficiario so' existe no
receptor, dentro do remetente cercado daquele turno (`_ScopedWhatsAppSender` + `gate_whatsapp`). O servico do
agente manda so' o TOKEN do turno, o hash do destino e o texto; aqui o token e' resolvido no registro do
`AgenteDeEntradaRemoto` e o envio sai pelo MESMO remetente cercado — mesma cerca de hash, mesma chave de
idempotencia de saida, mesmo gate.

Recusa sem detalhe: assinatura ausente/invalida = 401, corpo fora do protocolo = 400, token desconhecido ou
vencido = 404 (corpo vazio). Nunca loga texto, hash nem numero — so' o tipo de uma falha.
Montada pelo app do receptor SO' com o agente de entrada remoto (`whatsapp/app.py::create_app`); sem
`MAEZO_AGENTE_DE_ENTRADA_URL` a rota nem existe.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, Response

from maezo.contratos import conversa_remota as fio

from .cliente import AgenteDeEntradaRemoto, corpo_de_erro

logger = structlog.get_logger(__name__)


def resposta_assinada(assinar: Any, conteudo: Any, *, status_code: int = 200) -> Response:
    """Resposta do protocolo: corpo codificado + assinatura no cabecalho."""
    corpo = fio.codificar(conteudo)
    return Response(
        content=corpo,
        status_code=status_code,
        media_type=fio.TIPO_DO_CORPO,
        headers={fio.CABECALHO_ASSINATURA: assinar(corpo)},
    )


def registrar_rota_de_envio(app: FastAPI, agente: AgenteDeEntradaRemoto) -> None:
    """Monta `POST /interno/agentes/envio` em `app`, resolvendo tokens no registro de `agente`."""

    @app.post(fio.ROTA_ENVIO, include_in_schema=False)
    async def envio_do_agente_remoto(request: Request) -> Response:
        corpo = await request.body()
        if not agente.assinatura_valida(corpo, request.headers.get(fio.CABECALHO_ASSINATURA)):
            return Response(status_code=401)
        try:
            pedido = fio.decodificar(corpo)
        except ValueError:
            return Response(status_code=400)
        if not isinstance(pedido, dict):
            return Response(status_code=400)
        token, to_hash, text = pedido.get("token"), pedido.get("to_hash"), pedido.get("text")
        if not (isinstance(token, str) and isinstance(to_hash, str) and isinstance(text, str)):
            return Response(status_code=400)
        remetente = agente.registro.resolver(token)
        if remetente is None:
            return Response(status_code=404)
        try:
            resultado = await remetente.send(to_hash, text)
        except Exception as exc:  # o tipo volta ao agente (o no' `respond` dele decide), nunca a mensagem.
            logger.warning(
                "agente_remoto_envio_falhou", agent_id=agente.agent_id, error_type=type(exc).__name__
            )
            return resposta_assinada(agente.assinar, corpo_de_erro(exc), status_code=502)
        return resposta_assinada(agente.assinar, {"resultado": dict(resultado or {})})


__all__ = ["registrar_rota_de_envio", "resposta_assinada"]
