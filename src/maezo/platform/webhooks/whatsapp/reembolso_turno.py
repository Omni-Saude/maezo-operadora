"""Turno do pedido de reembolso no WhatsApp (DL-0159/DL-0160), atras da chave `MAEZO_REEMBOLSO_V1`.

Deterministico, SEM modelo: a maquina de estados (`domain/reembolso/pedido.py`) decide o texto; este
modulo so' LE e GRAVA a coleta (CAS), RECEBE o arquivo (baixa da Meta e entrega a custodia) e ABRE o
processo. O Lucas nunca diz se a pessoa tem direito, nunca calcula e nunca promete prazo ou pagamento.

QUEM CHAMA. `HelenaDispatcher._responder_reembolso`, DEPOIS do acesso do beneficiario (a conversa e'
`verificada`) e ANTES do agente. Devolve `None` quando a mensagem nao e' do reembolso — o fluxo de sempre
segue intacto. Emergencia/saude na mensagem vence: a coleta fica guardada e a mensagem vai ao grafo.

CUSTODIA DO ARQUIVO. `CustodiaDeArquivo` e' uma PORTA. O provedor da Zona PHI de hoje
(`gateway/documents/postgres.py`) so' aceita upload de pessoa da equipe sobre um caso JA' existente; um
arquivo que chega do beneficiario ANTES de existir processo precisa de uma entrada propria (principal de
sistema, triagem do arquivo, politica de retencao), que e' decisao de arquitetura/DPO. Sem a porta o turno
NAO e' construido (`service.py` registra e segue sem reembolso): nunca se aceita um arquivo que nao se sabe
onde guardar.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Final, Protocol

import structlog

from maezo.domain.reembolso import pedido as rp
from maezo.gateway.reembolso_anexos import AnexoGuardado, AnexoIndisponivelError
from maezo.platform.webhooks.whatsapp.reembolso_store import (
    ColetaGravada,
    ColetaReembolsoStore,
)
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    StartOutcome,
    correlate_message_audited,
    start_process_idempotent,
)
from maezo.tools.mcp_whatsapp.media import BaixadorDeMidia, MidiaRecusada
from maezo.tools.workers.phi_vars import redact_free_text

logger = structlog.get_logger(__name__)

#: A mensagem que devolve o pedido a analise com a resposta da beneficiaria (BPMN
#: `Msg_ReembolsoDocsRecebidos`).
MENSAGEM_RESPOSTA: Final[str] = "msg.reembolso.docs_received"

#: A coleta nao concluida vence em 24h (a janela de atendimento da Meta); depois a pessoa recomeca.
VALIDADE_DA_COLETA: Final[timedelta] = timedelta(hours=24)
#: Depois de aberto o protocolo continua consultavel por mais tempo.
VALIDADE_DEPOIS_DE_ABERTO: Final[timedelta] = timedelta(days=7)
_TENTATIVAS_DE_CAS: Final[int] = 3

SYSTEM_PROMPT_VERSION_REEMBOLSO: Final[str] = "reembolso-v1-deterministico"


class CustodiaDeArquivo(Protocol):
    """Entrega os bytes a Zona PHI e devolve a referencia OPACA (`document_ref`) e o sha256 do conteudo
    (`gateway/reembolso_anexos.py::CustodiaDeAnexosS3`, DL-0161)."""

    async def guardar_anexo(self, *, conversation_id: str, mime: str, conteudo: bytes) -> AnexoGuardado: ...


@dataclass(frozen=True, slots=True)
class RespostaReembolso:
    """O que o despachante envia. `evento` e' um token fechado para log/metrica (nunca texto)."""

    texto: str
    evento: str
    protocolo: str | None = None


_NOVO_PEDIDO: Final[re.Pattern[str]] = re.compile(r"\b(novo|outro|nova|outra)\b")
_PROTOCOLO_PERGUNTA: Final[re.Pattern[str]] = re.compile(r"\bprotocolo\b")


def _quer_novo_pedido(texto: str) -> bool:
    return _NOVO_PEDIDO.search(rp.normalizar(texto)) is not None


def _pergunta_protocolo(texto: str) -> bool:
    return _PROTOCOLO_PERGUNTA.search(rp.normalizar(texto)) is not None


class ReembolsoTurno:
    def __init__(
        self,
        *,
        tenant_id: str,
        store: ColetaReembolsoStore,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        custodia: CustodiaDeArquivo,
        baixador: BaixadorDeMidia,
        agent_version: str = "lucas-reembolso-1",
        agora: Callable[[], datetime] | None = None,
    ) -> None:
        self._tenant = tenant_id
        self._store = store
        self._cibseven = cibseven
        self._audit_sink = audit_sink
        self._custodia = custodia
        self._baixador = baixador
        self._agent_version = agent_version
        self._agora = agora or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------------------------------
    def _hoje(self) -> date:
        return self._agora().date()

    async def _ler_vivo(self, conversation_id: str) -> ColetaGravada | None:
        linha = await self._store.ler(conversation_id)
        if linha is not None and linha.vencida(self._agora()):
            return _vencida(linha)
        return linha

    async def _gravar(
        self,
        conversation_id: str,
        pedido: rp.PedidoReembolso,
        *,
        atual: ColetaGravada | None,
        protocolo: str | None = None,
    ) -> bool:
        agora = self._agora()
        validade = VALIDADE_DEPOIS_DE_ABERTO if pedido.etapa in ("aberto", "resposta") else VALIDADE_DA_COLETA
        return await self._store.gravar(
            conversation_id,
            pedido,
            protocolo=protocolo,
            agora=agora,
            expira_em=agora + validade,
            revisao_esperada=None if atual is None else atual.revisao,
        )

    # ------------------------------------------------------------------------------------------
    async def tratar_texto(
        self,
        *,
        conversation_id: str,
        texto: str,
        beneficiario_pseudo_id: str,
        sinal_de_emergencia: bool,
    ) -> RespostaReembolso | None:
        """Um turno de TEXTO. `None` = nao e' do reembolso (ou a emergencia vence): o fluxo de sempre."""
        if sinal_de_emergencia:
            return None
        for _ in range(_TENTATIVAS_DE_CAS):
            atual = await self._ler_vivo(conversation_id)
            resposta = await self._turno_de_texto(conversation_id, texto, beneficiario_pseudo_id, atual)
            if not isinstance(resposta, _CasPerdido):
                return resposta
        logger.warning("reembolso_cas_perdido", tenant_id=self._tenant, conversation_id=conversation_id)
        return RespostaReembolso(rp.TEXTO_FALHA_AO_REGISTRAR, "cas_perdido")

    async def _turno_de_texto(
        self,
        conversation_id: str,
        texto: str,
        beneficiario_pseudo_id: str,
        atual: ColetaGravada | None,
    ) -> RespostaReembolso | None | _CasPerdido:
        pedido = atual.pedido if atual is not None else None
        if atual is not None and pedido is not None and pedido.etapa == "resposta":
            return await self._texto_na_resposta(conversation_id, texto, atual)
        if pedido is not None and pedido.etapa == "aberto":
            if atual is not None and (rp.pede_reembolso(texto) or _pergunta_protocolo(texto)):
                if _quer_novo_pedido(texto):
                    return await self._comecar(conversation_id, atual)
                return RespostaReembolso(
                    rp.TEXTO_PEDIDO_JA_REGISTRADO.format(protocolo=atual.protocolo or ""),
                    "ja_registrado",
                    atual.protocolo,
                )
            # DL-0161: a equipe pediu informacao? Entao esta mensagem e' a resposta (senao, o fluxo de
            # sempre).
            if atual is None or await self._pendente(atual.protocolo) is None:
                return None
            if rp.e_terminei(texto):
                return RespostaReembolso(rp.TEXTO_RESPOSTA_VAZIA, "resposta_vazia")
            limpo = _limpar(texto)
            if len(limpo) > rp.MAX_RESPOSTA_TEXTO:
                return RespostaReembolso(rp.TEXTO_RESPOSTA_LONGA, "resposta_longa")
            novo = replace(pedido, etapa="resposta", resposta_texto=limpo)
            if not await self._gravar(conversation_id, novo, atual=atual, protocolo=atual.protocolo):
                return _CAS_PERDIDO
            return RespostaReembolso(rp.TEXTO_RESPOSTA_INICIADA, "resposta_iniciada", atual.protocolo)
        if pedido is None or not pedido.ativo:
            if not rp.pede_reembolso(texto):
                return None
            return await self._comecar(conversation_id, atual)
        saida = rp.avancar(pedido, texto, hoje=self._hoje())
        if saida.acao == "abrir_processo":
            return await self._abrir(conversation_id, saida.pedido, beneficiario_pseudo_id, atual)
        if not await self._gravar(conversation_id, saida.pedido, atual=atual):
            return _CAS_PERDIDO
        return RespostaReembolso(saida.texto, f"etapa_{saida.pedido.etapa}")

    async def _comecar(
        self, conversation_id: str, atual: ColetaGravada | None
    ) -> RespostaReembolso | _CasPerdido:
        saida = rp.iniciar()
        if not await self._gravar(conversation_id, saida.pedido, atual=atual):
            return _CAS_PERDIDO
        return RespostaReembolso(saida.texto, "iniciado")

    # ------------------------------------------------------------------------------------------
    async def _abrir(
        self,
        conversation_id: str,
        pedido: rp.PedidoReembolso,
        beneficiario_pseudo_id: str,
        atual: ColetaGravada | None,
    ) -> RespostaReembolso | _CasPerdido:
        """Confirmacao: abre SP-OP-REEMBOLSO-001 pelo start idempotente e auditado.

        A chave de negocio e' `REEMB-{tenant}-{protocolo}` (a do contrato). O mesmo pedido confirmado duas
        vezes tem o mesmo protocolo e reencontra a instancia viva. Falha do motor: a coleta NAO avanca (a
        pessoa pode confirmar de novo) e o texto diz a verdade — nada foi enviado para analise."""
        variaveis = rp.variaveis_do_processo(
            pedido,
            tenant_id=self._tenant,
            conversation_id=conversation_id,
            beneficiario_pseudo_id=beneficiario_pseudo_id,
            hoje=self._hoje(),
        )
        protocolo = str(variaveis["protocolo_reembolso"])
        provenance = AgentDecisionProvenance(
            agent_id=rp.AGENT_ID_REEMBOLSO,
            agent_version=self._agent_version,
            tenant_id=self._tenant,
            model_id="deterministico",
            prompt_version=SYSTEM_PROMPT_VERSION_REEMBOLSO,
            decision_basis={"route": "reembolso_confirmado", "origem_solicitacao": "agente_lucas"},
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=rp.PROCESS_KEY_REEMBOLSO,
                business_key=f"REEMB-{self._tenant}-{protocolo}",
                variables=variaveis,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except CibSevenError:
            logger.error("reembolso_start_falhou", tenant_id=self._tenant, conversation_id=conversation_id)
            return RespostaReembolso(rp.TEXTO_FALHA_AO_REGISTRAR, "start_falhou")
        ja_existia = instance.already_existed or instance.start_outcome is StartOutcome.ALREADY_ACTIVE
        aberto = replace(pedido, etapa="aberto")
        if not await self._gravar(conversation_id, aberto, atual=atual, protocolo=protocolo):
            # O processo JA' existe; so' o registro local perdeu a corrida. Responde a verdade e deixa o
            # proximo turno reencontrar a linha.
            logger.warning("reembolso_cas_perdido_depois_do_start", tenant_id=self._tenant)
        texto = (rp.TEXTO_PEDIDO_JA_REGISTRADO if ja_existia else rp.TEXTO_PEDIDO_RECEBIDO).format(
            protocolo=protocolo
        )
        return RespostaReembolso(texto, "ja_registrado" if ja_existia else "aberto", protocolo)

    # ------------------------------------------------------------------------------------------
    # Resposta a um pedido de informacao da equipe (DL-0161)
    # ------------------------------------------------------------------------------------------
    def _chave(self, protocolo: str) -> str:
        return f"REEMB-{self._tenant}-{protocolo}"

    async def _pendente(self, protocolo: str | None) -> str | None:
        """O id da instancia quando ela ESPERA a resposta da beneficiaria (`aguardando_resposta == true`).

        Le' do historico do motor SO' essa variavel booleana, por nome (nenhuma variavel PHI entra
        aqui). Sem a
        leitura de historico no transporte, ou com falha do motor: `None` — a mensagem segue o fluxo de
        sempre,
        nunca e' tomada como resposta sem prova de que ha' uma pendencia."""
        if not protocolo or not rp.protocolo_valido(protocolo):
            return None
        ler = getattr(self._cibseven, "read_historic_variables", None)
        if ler is None:
            return None
        try:
            instancia = await self._cibseven.find_active_instance(self._chave(protocolo))
            if instancia is None:
                return None
            historico = await ler(instancia.instance_id, ("aguardando_resposta",))
        except CibSevenError:
            logger.warning("reembolso_pendencia_nao_lida", tenant_id=self._tenant)
            return None
        if historico is None or historico.variables.get("aguardando_resposta") is not True:
            return None
        return str(instancia.instance_id)

    async def _texto_na_resposta(
        self, conversation_id: str, texto: str, atual: ColetaGravada
    ) -> RespostaReembolso | _CasPerdido:
        pedido = atual.pedido
        if rp.e_terminei(texto):
            return await self._entregar_resposta(conversation_id, atual)
        limpo = _limpar(texto)
        junto = f"{pedido.resposta_texto}\n{limpo}".strip() if pedido.resposta_texto else limpo
        if len(junto) > rp.MAX_RESPOSTA_TEXTO:
            return RespostaReembolso(rp.TEXTO_RESPOSTA_LONGA, "resposta_longa")
        if not await self._gravar(
            conversation_id, replace(pedido, resposta_texto=junto), atual=atual, protocolo=atual.protocolo
        ):
            return _CAS_PERDIDO
        return RespostaReembolso(rp.TEXTO_RESPOSTA_ANOTADA, "resposta_anotada", atual.protocolo)

    async def _entregar_resposta(
        self, conversation_id: str, atual: ColetaGravada
    ) -> RespostaReembolso | _CasPerdido:
        """O "terminei": UMA correlacao auditada de `msg.reembolso.docs_received` com o texto (ja' pelo
        scrub) e os
        arquivos da resposta. Falha do motor: nada se perde (a coleta fica na etapa `resposta`)."""
        pedido = atual.pedido
        protocolo = atual.protocolo or ""
        complementos = [d for d in pedido.documentos if d.tipo == rp.TIPO_DOC_COMPLEMENTO]
        if not pedido.resposta_texto and not complementos:
            return RespostaReembolso(rp.TEXTO_RESPOSTA_VAZIA, "resposta_vazia")
        sem_complementos = tuple(d for d in pedido.documentos if d.tipo != rp.TIPO_DOC_COMPLEMENTO)
        aberto = replace(pedido, etapa="aberto", resposta_texto="", documentos=sem_complementos)
        instancia = await self._pendente(protocolo)
        if instancia is None:
            # A instancia nao espera mais (outra resposta ja' entrou, ou a equipe seguiu sem ela).
            if not await self._gravar(conversation_id, aberto, atual=atual, protocolo=atual.protocolo):
                return _CAS_PERDIDO
            return RespostaReembolso(
                rp.TEXTO_PEDIDO_JA_REGISTRADO.format(protocolo=protocolo), "resposta_sem_pendencia", protocolo
            )
        variaveis: dict[str, object] = {
            "resposta_beneficiario": pedido.resposta_texto,
            "documentos_adicionais_refs": [
                {"tipo": d.tipo, "document_ref": d.document_ref, "sha256": d.sha256} for d in complementos
            ],
            "aguardando_resposta": False,
        }
        provenance = AgentDecisionProvenance(
            agent_id=rp.AGENT_ID_REEMBOLSO,
            agent_version=self._agent_version,
            tenant_id=self._tenant,
            model_id="deterministico",
            prompt_version=SYSTEM_PROMPT_VERSION_REEMBOLSO,
            decision_basis={"route": "reembolso_resposta", "origem_solicitacao": "agente_lucas"},
        )
        try:
            await correlate_message_audited(
                self._cibseven,
                process_key=rp.PROCESS_KEY_REEMBOLSO,
                message_name=MENSAGEM_RESPOSTA,
                business_key=self._chave(protocolo),
                instance_id=instancia,
                variables=variaveis,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except CibSevenError:
            logger.error(
                "reembolso_resposta_nao_entregue", tenant_id=self._tenant, conversation_id=conversation_id
            )
            return RespostaReembolso(rp.TEXTO_RESPOSTA_FALHOU, "resposta_falhou", protocolo)
        if not await self._gravar(conversation_id, aberto, atual=atual, protocolo=atual.protocolo):
            logger.warning("reembolso_cas_perdido_depois_da_resposta", tenant_id=self._tenant)
        return RespostaReembolso(
            rp.TEXTO_RESPOSTA_ENVIADA.format(protocolo=protocolo), "resposta_enviada", protocolo
        )

    async def _arquivo_na_resposta(
        self, conversation_id: str, atual: ColetaGravada, media_id: str
    ) -> RespostaReembolso | None | _CasPerdido:
        pedido = atual.pedido
        if pedido.etapa == "aberto" and await self._pendente(atual.protocolo) is None:
            return None
        if pedido.contagem(rp.TIPO_DOC_COMPLEMENTO) >= rp.MAX_ARQUIVOS_POR_TIPO:
            return RespostaReembolso(rp.TEXTO_LIMITE_ARQUIVOS, "resposta_limite_arquivos", atual.protocolo)
        guardado = await self._baixar_e_guardar(conversation_id, media_id)
        if isinstance(guardado, RespostaReembolso):
            return guardado
        novo = replace(
            pedido,
            etapa="resposta",
            documentos=(
                *pedido.documentos,
                rp.Documento(
                    tipo=rp.TIPO_DOC_COMPLEMENTO, document_ref=guardado.document_ref, sha256=guardado.sha256
                ),
            ),
        )
        if not await self._gravar(conversation_id, novo, atual=atual, protocolo=atual.protocolo):
            return _CAS_PERDIDO
        return RespostaReembolso(rp.TEXTO_RESPOSTA_ARQUIVO, "resposta_arquivo", atual.protocolo)

    async def _baixar_e_guardar(
        self, conversation_id: str, media_id: str
    ) -> AnexoGuardado | RespostaReembolso:
        try:
            midia = await self._baixador.baixar(media_id)
        except MidiaRecusada as exc:
            texto = (
                rp.TEXTO_ARQUIVO_INVALIDO if exc.motivo in ("mime", "tamanho") else rp.TEXTO_FALHA_AO_RECEBER
            )
            return RespostaReembolso(texto, f"arquivo_recusado_{exc.motivo}")
        try:
            return await self._custodia.guardar_anexo(
                conversation_id=conversation_id, mime=midia.mime, conteudo=midia.conteudo
            )
        except AnexoIndisponivelError:
            logger.error(
                "reembolso_anexo_nao_guardado", tenant_id=self._tenant, conversation_id=conversation_id
            )
            return RespostaReembolso(rp.TEXTO_FALHA_AO_RECEBER, "arquivo_nao_guardado")

    # ------------------------------------------------------------------------------------------
    async def tratar_arquivo(
        self, *, conversation_id: str, media_id: str, mime_declarado: str
    ) -> RespostaReembolso | None:
        """Um ARQUIVO recebido. `None` = nao ha coleta ativa: o aviso de sempre ("so' texto") segue."""
        for _ in range(_TENTATIVAS_DE_CAS):
            atual = await self._ler_vivo(conversation_id)
            if atual is not None and atual.pedido.etapa in ("aberto", "resposta"):
                # DL-0161: arquivo da resposta a um pedido de informacao da equipe.
                resposta = await self._arquivo_na_resposta(conversation_id, atual, media_id)
                if isinstance(resposta, _CasPerdido):
                    continue
                return resposta
            if atual is None or not atual.pedido.ativo:
                return None
            pedido = atual.pedido
            if not pedido.espera_documento:
                return RespostaReembolso(rp.arquivo_nao_esperado(pedido), "arquivo_fora_de_hora")
            try:
                midia = await self._baixador.baixar(media_id)
            except MidiaRecusada as exc:
                texto = (
                    rp.TEXTO_ARQUIVO_INVALIDO
                    if exc.motivo in ("mime", "tamanho")
                    else rp.TEXTO_FALHA_AO_RECEBER
                )
                return RespostaReembolso(texto, f"arquivo_recusado_{exc.motivo}")
            try:
                guardado = await self._custodia.guardar_anexo(
                    conversation_id=conversation_id, mime=midia.mime, conteudo=midia.conteudo
                )
            except AnexoIndisponivelError:
                logger.error(
                    "reembolso_anexo_nao_guardado", tenant_id=self._tenant, conversation_id=conversation_id
                )
                return RespostaReembolso(rp.TEXTO_FALHA_AO_RECEBER, "arquivo_nao_guardado")
            saida = rp.receber_documento(pedido, guardado.document_ref, guardado.sha256)
            if await self._gravar(conversation_id, saida.pedido, atual=atual):
                return RespostaReembolso(saida.texto, "arquivo_recebido")
        return RespostaReembolso(rp.TEXTO_FALHA_AO_RECEBER, "cas_perdido")


def _limpar(texto: str) -> str:
    """O texto da resposta pelo MESMO scrub de identificadores dos textos que vao ao motor (CPF,
    telefone...)."""
    return redact_free_text(" ".join(texto.split()), max_chars=sys.maxsize).strip()


class _CasPerdido:
    """Marcador: outra mensagem gravou antes; o turno rele' e refaz."""


_CAS_PERDIDO: Final[_CasPerdido] = _CasPerdido()


def _vencida(linha: ColetaGravada) -> ColetaGravada:
    """Uma linha vencida vale como 'encerrada': a pessoa recomeca, e a gravacao segue o CAS dela."""
    return ColetaGravada(
        pedido=rp.PedidoReembolso(etapa="encerrado"),
        protocolo=None,
        revisao=linha.revisao,
        atualizado_em=linha.atualizado_em,
        expira_em=linha.expira_em,
    )
